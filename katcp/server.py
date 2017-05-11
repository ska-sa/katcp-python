# servers.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Servers for the KAT device control language."""

from __future__ import division, print_function, absolute_import

import socket
import threading
import traceback
import logging
import sys
import re
import time

import tornado.ioloop
import tornado.tcpserver

from functools import partial, wraps
from collections import deque
from thread import get_ident as get_thread_ident

from tornado import gen, iostream
from tornado.concurrent import Future as tornado_Future
from tornado.concurrent import chain_future
from tornado.util import ObjectDict
from concurrent.futures import Future

from .ioloop_manager import IOLoopManager, with_relative_timeout
from .core import (DeviceServerMetaclass, Message, MessageParser,
                   FailReply, AsyncReply, ProtocolFlags)
from .sampling import SampleStrategy, SampleNone
from .sampling import format_inform_v5, format_inform_v4
from .core import (SEC_TO_MS_FAC, MS_TO_SEC_FAC, SEC_TS_KATCP_MAJOR,
                   VERSION_CONNECT_KATCP_MAJOR, DEFAULT_KATCP_MAJOR)
from .kattypes import (request, return_reply,
                       minimum_katcp_version,
                       has_katcp_protocol_flags,
                       Int, Str)

# 'import katcp' so that we can use katcp.__version__ later
# we cannot do this: 'from . import __version__' because __version__
# does not exist at this stage
import katcp

log = logging.getLogger("katcp.server")

BASE_REQUESTS = frozenset(['client-list',
                           'halt',
                           'help',
                           'log-level',
                           'new-command',
                           'raise-exception',
                           'raise-fail',
                           'restart',
                           'sensor-list',
                           'sensor-sampling-clear',
                           'sensor-value',
                           'version-list',
                           'watchdog',
                           'sensor-sampling', ])
"List of basic KATCP requests that a minimal device server should implement"


def return_future(fn):
    """Decorator that turns a synchronous function into one returning a future.

    This should only be applied to non-blocking functions. Will do set_result()
    with the return value, or set_exc_info() if an exception is raised.

    """
    @wraps(fn)
    def decorated(*args, **kwargs):
        return gen.maybe_future(fn(*args, **kwargs))

    return decorated


def construct_name_filter(pattern):
    """Return a function for filtering sensor names based on a pattern.

    Parameters
    ----------
    pattern : None or str
        If None, the returned function matches all names.
        If pattern starts and ends with '/' the text between the slashes
        is used as a regular expression to search the names.
        Otherwise the pattern must match the name of the sensor exactly.

    Returns
    -------
    exact : bool
        Return True if pattern is expected to match exactly. Used to
        determine whether having no matching sensors constitutes an error.
    filter_func : f(str) -> bool
        Function for determining whether a name matches the pattern.

    """
    if pattern is None:
        return False, lambda name: True
    if pattern.startswith('/') and pattern.endswith('/'):
        name_re = re.compile(pattern[1:-1])
        return False, lambda name: name_re.search(name) is not None
    return True, lambda name: name == pattern


class ClientConnection(object):
    """Encapsulates the connection between a single client and the server."""

    def __init__(self, server, conn_id):
        self._server = server
        self._conn_key = conn_id
        self._disconnect_called = False
        self._get_address = partial(server.get_address, conn_id)
        self._send_message = partial(server.send_message, conn_id)
        self._mass_send_message = server.mass_send_message
        self.flush_on_close = partial(server.flush_on_close, conn_id)

    @property
    def address(self):
        return self._get_address()

    @property
    def client_disconnect_called(self):
        return self._disconnect_called

    def disconnect(self, reason):
        """Disconnect this client connection for specified reason"""
        self._server._disconnect_client(self._conn_key, self, reason)

    def inform(self, msg):
        """Send an inform message to a particular client.

        Should only be used for asynchronous informs. Informs
        that are part of the response to a request should use
        :meth:`reply_inform` so that the message identifier
        from the original request can be attached to the
        inform.

        Parameters
        ----------
        msg : Message object
            The inform message to send.

        """
        assert (msg.mtype == Message.INFORM)
        return self._send_message(msg)

    def reply_inform(self, inform, orig_req):
        """Send an inform as part of the reply to an earlier request.

        Parameters
        ----------
        inform : Message object
            The inform message to send.
        orig_req : Message object
            The request message being replied to. The inform message's
            id is overridden with the id from orig_req before the
            inform is sent.

        """
        assert (inform.mtype == Message.INFORM)
        assert (inform.name == orig_req.name)
        inform.mid = orig_req.mid
        return self._send_message(inform)

    def mass_inform(self, msg):
        """Send an inform message to all clients.

        Parameters
        ----------
        msg : Message object
            The inform message to send.

        """
        assert (msg.mtype == Message.INFORM)
        return self._mass_send_message(msg)

    def reply(self, reply, orig_req):
        """Send an asynchronous reply to an earlier request.

        Parameters
        ----------
        reply : Message object
            The reply message to send.
        orig_req : Message object
            The request message being replied to. The reply message's
            id is overridden with the id from orig_req before the
            reply is sent.

        """
        assert (reply.mtype == Message.REPLY)
        assert reply.name == orig_req.name
        reply.mid = orig_req.mid
        return self._send_message(reply)

    def on_client_disconnect_was_called(self):
        """Prevent multiple calls to on_client_disconnect handler.

        Call this when an on_client_disconnect handler has been called.

        """
        if self._disconnect_called:
            raise RuntimeError('"on_client_disconnect" already called '
                               'for this connection')
        self._disconnect_called = True


class ThreadsafeClientConnection(ClientConnection):
    """Make ClientConnection compatible with messages sent from other threads."""
    def __init__(self, server, conn_id):
        super(ThreadsafeClientConnection, self).__init__(server, conn_id)
        self._send_message = partial(server.send_message_from_thread, conn_id)
        self._mass_send_message = server.mass_send_message_from_thread


class KATCPServer(object):
    """Tornado IO backend for a KATCP Device.

    Listens for connections on a server socket, reads KATCP messages off
    the wire and passes them on to a DeviceServer-like class.

    All class CONSTANT attributes can be changed until start() is called.

    """
    BACKLOG = 5              # Size of server socket backlog
    MAX_MSG_SIZE = 2*1024*1024
    """Maximum message size that can be received in bytes.

    If more than MAX_MSG_SIZE bytes are read from the client without
    encountering a message terminator (i.e. newline), the connection is closed.

    """
    MAX_WRITE_BUFFER_SIZE = 2*MAX_MSG_SIZE
    """Maximum outstanding bytes to be buffered by the server process.

    If more than MAX_WRITE_BUFFER_SIZE bytes are outstanding, the client
    connection is closed. Note that the OS also buffers socket writes,
    so more than MAX_WRITE_BUFFER_SIZE bytes may be untransmitted in total.

    """
    DISCONNECT_TIMEOUT = 1
    """How long to wait for the device on_client_disconnect() to complete.

    Note that this will only work if the device on_client_disconnect() method
    is non-blocking (i.e. returns a future immediately). Otherwise the ioloop
    will be blocked and unable to apply the timeout.

    """

    client_connection_factory = ClientConnection
    """Factory that produces a ClientConnection compatible instance.

    signature: client_connection_factory(server, conn_id)

    Should be set before calling start().

    """

    def __init__(self, device, host, port, tb_limit=20, logger=log):
        """Initialise the IO server instance.

        Parameters
        ----------
        device : object
            :class:`katcp.DeviceServer`-like KATCP message-eating device
        host : str
            IP to bind the server socket on
        port : int
            Port to listen on
        tb_limit : int, optional
            Maximum traceback length that is sent to clients when an unhandled
            exception is encountered
        logger : :class:`logging.Logger` object, optional
            Logger instance to use for logging, defaults to module log

        API with device
        ===============

        Device methods called
        ---------------------
        All device methods called should be non-blocking. Expects all
        device.on_* methods to return a future that resolves when it is done
        or ready to be called again.

        ready = device.on_client_connect(client_conn)

            client_conn -- ClientConnection-like instance returned by
                           self.client_connection_factory
            ready -- Future that resolves when device is ready to accept messages

        ready = device.on_client_disconnect(client_conn, reason, connection_open)

            client_conn -- as above
            reason -- string reason for disconnect
            connection_open -- True if the connection can still be written to
            ready -- Future that resolves when the connection can be closed

        ready = device.on_message(client_conn, msg)

            client_conn -- as above
            msg -- katcp.Message object representing the most recent message
            ready -- Future that resolves when device is ready to accept another message

        inform = device.create_log_inform(level_name, msg, name, timestamp=None)

            inform -- #log inform message

        Services provided to device
        ===========================

        It is expected that device will use the KATCPServer instance's ioloop.
        Devices would mainly interact using:

        set_ioloop(), start(), stop(), join(), send_message(),
        mass_send_message(), and _from_thread() versions of message sending
        methods. Except for the _from_thread() methods, all calls must be made
        from the ioloop thread.

        """
        self._device = device
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._logger = logger
        self._parser = MessageParser()
        # Indicate that server is running and ready to accept connections
        self._running = threading.Event()
        # Indicate that we are stopped, i.e. join() can return
        self._stopped = threading.Event()
        self.ioloop = None
        "The Tornado IOloop to use, set by self.set_ioloop()"
        # ID of Thread that hosts the IOLoop.
        # Used to check that we are running in the ioloop.
        self.ioloop_thread_id = None
        # Map from tornado IOStreams to ClientConnection objects
        self._connections = {}
        self._ioloop_manager = IOLoopManager(managed_default=True)

    @property
    def bind_address(self):
        """The (host, port) where the server is listening for connections."""
        return self._bindaddr

    def setDaemon(self, daemonic):
        """Set daemonic state of the managed ioloop thread to True / False

        Calling this method for a non-managed ioloop has no effect. Must be called before
        start(), or it will also have no effect
        """
        self._ioloop_manager.setDaemon(daemonic)

    def set_ioloop(self, ioloop=None):
        """Set the tornado IOLoop to use.

        Sets the tornado.ioloop.IOLoop instance to use, defaulting to
        IOLoop.current(). If set_ioloop() is never called the IOLoop is started
        in a new thread, and will be stopped if self.stop() is called.

        Notes
        -----
        Must be called before start() is called.

        """
        self._ioloop_manager.set_ioloop(ioloop, managed=False)

    def start(self, timeout=None):
        """Install the server on its IOLoop, optionally starting the IOLoop.

        Parameters
        ----------
        timeout : float or None, optional
            Time in seconds to wait for server thread to start.

        """
        if self._running.isSet():
            raise RuntimeError('Server already started')
        self._stopped.clear()
        # Make sure we have an ioloop
        self.ioloop = self._ioloop_manager.get_ioloop()
        self._ioloop_manager.start()
        # Set max_buffer_size to ensure streams are closed
        # if too-large messages are received
        self._tcp_server = tornado.tcpserver.TCPServer(
            self.ioloop, max_buffer_size=self.MAX_MSG_SIZE)
        self._tcp_server.handle_stream = self._handle_stream
        self._server_sock = self._bind_socket(self._bindaddr)
        self._bindaddr = self._server_sock.getsockname()

        self.ioloop.add_callback(self._install)
        if timeout:
            return self._running.wait(timeout)

    def stop(self, timeout=1.0):
        """Stop a running server (from another thread).

        Parameters
        ----------
        timeout : float or None, optional
            Seconds to wait for server to have *started*.

        Returns
        -------
        stopped : thread-safe Future
            Resolves when the server is stopped

        """
        if timeout:
            self._running.wait(timeout)
        return self._ioloop_manager.stop(callback=self._uninstall)

    def join(self, timeout=None):
        """Rejoin the server thread.

        Parameters
        ----------
        timeout : float or None, optional
            Time in seconds to wait for the thread to finish.

        Notes
        -----
        If the ioloop is not managed, this function will block until the server
        port is closed, meaning a new server can be started on the same port.

        """
        t0 = time.time()
        self._ioloop_manager.join(timeout=timeout)
        if timeout:
            self._stopped.wait(timeout - (time.time() - t0))

    def running(self):
        """Whether the handler thread is running."""
        return self._running.isSet()

    def wait_running(self, timeout=None):
        """Wait until the handler thread is running."""
        return self._running.wait(timeout)

    def _bind_socket(self, bindaddr):
        """Create a listening server socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)
        try:
            sock.bind(bindaddr)
        except Exception:
            self._logger.exception("Unable to bind to %s" % str(bindaddr))
            raise
        sock.listen(self.BACKLOG)
        return sock

    def _install(self):
        # Do stuff to put us on the IOLoop
        self.ioloop_thread_id = get_thread_ident()
        self._tcp_server.add_socket(self._server_sock)
        self._running.set()

    @gen.coroutine
    def _uninstall(self):
        # Stop listening, close all open connections and remove us from IOLoop
        assert get_thread_ident() == self.ioloop_thread_id
        try:
            self._tcp_server.stop()
            for stream, conn in self._connections.items():
                yield self._disconnect_client(stream, conn,
                                              'Device server shutting down.')
        finally:
            self.ioloop = None
            self._running.clear()
            self._stopped.set()

    @gen.coroutine
    def _handle_stream(self, stream, address):
        """Handle a new connection as a tornado.iostream.IOStream instance."""
        try:
            assert get_thread_ident() == self.ioloop_thread_id
            stream.set_close_callback(partial(self._stream_closed_callback,
                                              stream))
            # Our message packets are small, don't delay sending them.
            stream.set_nodelay(True)
            stream.max_write_buffer_size = self.MAX_WRITE_BUFFER_SIZE

            # Abuse IOStream object slightly by adding 'address' and 'closing'
            # attributes. Use nasty prefix to prevent naming collisions.
            stream.KATCPServer_address = address
            # Flag to indicate that no more write should be accepted so that
            # we can flush the write buffer when closing a connection
            stream.KATCPServer_closing = False

            client_conn = self.client_connection_factory(self, stream)
            self._connections[stream] = client_conn
            try:
                yield gen.maybe_future(self._device.on_client_connect(client_conn))
            except Exception:
                # If on_client_connect fails there is no reason to continue
                # trying to handle this connection. Try and send exception info
                # to the client and disconnect
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                log_msg = 'Device error initialising connection {0}'.format(reason)
                self._logger.error(log_msg)
                stream.write(str(Message.inform('log', log_msg)))
                stream.close(exc_info=True)
            else:
                self._line_read_loop(stream, client_conn)
        except Exception:
            self._logger.error('Unhandled exception trying '
                               'to handle new connection', exc_info=True)

    @gen.coroutine
    def _line_read_loop(self, stream, client_conn):
        assert get_thread_ident() == self.ioloop_thread_id
        client_address = self.get_address(stream)
        try:
            while True:
                try:
                    if stream.closed():
                        # read_until_regex() below will still give us the
                        # backlogged messages buffered by the iostream,
                        # resulting in message handlers being called with a
                        # closed connection. Exit early instead.
                        break
                    line = yield stream.read_until_regex('\n|\r')
                except iostream.StreamClosedError:
                    # Assume that _stream_closed_callback() will handle this
                    break
                except Exception:
                        self._logger.warn('Unhandled Exception '
                                          'while reading from client {0}:'
                                          .format(client_address), exc_info=True)
                try:
                    line = line.replace("\r", "\n").split("\n")[0]
                    msg = self._parser.parse(line) if line else None
                except Exception:
                    msg = None
                    e_type, e_value, trace = sys.exc_info()
                    reason = "\n".join(traceback.format_exception(
                        e_type, e_value, trace, self._tb_limit))
                    self._logger.error("BAD COMMAND: %s in line %r"
                                       % (reason, line))
                    self.send_message(
                        stream, self._device.create_log_inform("error", reason, "root"))
                    continue  # Wait for the next message and hope it is better
                try:
                    if msg:  # Ignore empty messages (i.e empty lines)
                        yield self._device.on_message(client_conn, msg)
                except Exception:
                    self._logger.error('Error handling message {0!s}'
                                       .format(msg), exc_info=True)
                # Allow the ioloop to run since we may be starving it if
                # there is buffered data in the stream, resulting in yield
                # stream.read_until_regex('\n|\r') never actually yielding
                # control to the ioloop
                yield gen.moment
        except Exception:
            self._logger.error('Unexpected exception in read-loop for client {0}:'
                               .format(client_address))
        finally:
            self._logger.info('Reading loop for client {0} completed'
                              .format(client_address))

    def _stream_closed_callback(self, stream):
        assert get_thread_ident() == self.ioloop_thread_id
        # Remove ClientConnection object for the current stream from our state
        conn = self._connections.pop(stream, None)
        error_repr = '{0!r}'.format(stream.error) if stream.error else ''
        if error_repr:
            self._logger.warn('Stream for client {0} closed with error {1}'
                              .format(self.get_address(stream), error_repr))
        if conn:
            reason = error_repr or "Socket EOF"
            # Return the future from _disconnect_client()
            return self._disconnect_client(stream, conn, reason)

    @gen.coroutine
    def _disconnect_client(self, stream, conn, reason):
        assert get_thread_ident() == self.ioloop_thread_id
        stream_open = not stream.closed()
        address = self.get_address(stream)
        try:
            if not conn.client_disconnect_called:
                try:
                    conn.on_client_disconnect_was_called()
                    f = gen.maybe_future(self._device.on_client_disconnect(
                        conn, reason, stream_open))
                    yield with_relative_timeout(self.DISCONNECT_TIMEOUT, f)
                except Exception:
                    self._logger.error('Error while calling '
                                       'on_client_disconnect for client {0}'
                                       .format(address), exc_info=True)

        finally:
            # Make sure stream is closed.
            stream.close()

    def get_address(self, stream):
        """Text representation of the network address of a connection stream.

        Notes
        -----
        This method is thread-safe

        """
        try:
            addr = ":".join(str(part) for part in stream.KATCPServer_address)
        except AttributeError:
            # Something weird happened, but keep trucking
            addr = '<error>'
            self._logger.warn('Could not determine address of stream',
                              exc_info=True)
        return addr

    def send_message(self, stream, msg):
        """Send an arbitrary message to a particular client.

        Parameters
        ----------
        stream : :class:`tornado.iostream.IOStream` object
            The stream to send the message to.
        msg : Message object
            The message to send.

        Notes
        -----
        This method can only be called in the IOLoop thread.

        Failed sends disconnect the client connection and calls the device
        on_client_disconnect() method. They do not raise exceptions, but they
        are logged. Sends also fail if more than self.MAX_WRITE_BUFFER_SIZE
        bytes are queued for sending, implying that client is falling behind.

        """
        assert get_thread_ident() == self.ioloop_thread_id
        try:
            if stream.KATCPServer_closing:
                raise RuntimeError('Stream is closing so we cannot '
                                   'accept any more writes')
            return stream.write(str(msg) + '\n')
        except Exception:
            addr = self.get_address(stream)
            self._logger.warn('Could not send message {0!r} to {1}'
                              .format(str(msg), addr), exc_info=True)
            stream.close(exc_info=True)

    def flush_on_close(self, stream):
        """Flush tornado iostream write buffer and prevent further writes.

        Returns a future that resolves when the stream is flushed.

        """
        assert get_thread_ident() == self.ioloop_thread_id
        # Prevent futher writes
        stream.KATCPServer_closing = True
        # Write empty message to get future that resolves when buffer is flushed
        return stream.write('\n')

    def call_from_thread(self, fn):
        """Allow thread-safe calls to ioloop functions.

        Uses add_callback if not in the IOLoop thread, otherwise calls
        directly. Returns an already resolved `tornado.concurrent.Future` if in
        ioloop, otherwise a `concurrent.Future`. Logs unhandled exceptions.
        Resolves with an exception if one occurred.

        """
        if self.in_ioloop_thread():
            f = tornado_Future()
            try:
                f.set_result(fn())
            except Exception, e:
                f.set_exception(e)
                self._logger.exception('Error executing callback '
                                       'in ioloop thread')
            finally:
                return f
        else:
            f = Future()
            try:
                f.set_running_or_notify_cancel()

                def send_message_callback():
                    try:
                        f.set_result(fn())
                    except Exception, e:
                        f.set_exception(e)
                        self._logger.exception(
                            'Error executing wrapped async callback')

                self.ioloop.add_callback(send_message_callback)
            finally:
                return f

    def send_message_from_thread(self, stream, msg):
        """Thread-safe version of send_message() returning a Future instance.

        Returns
        -------
        A Future that will resolve without raising an exception as soon as
        the call to send_message() completes. This does not guarantee that the
        message has been delivered yet. If the call to send_message() failed,
        the exception will be logged, and the future will resolve with the
        exception raised. Since a failed call to send_message() will result
        in the connection being closed, no real error handling apart from
        logging will be possible.

        Notes
        -----
        This method is thread-safe. If called from within the ioloop,
        send_message is called directly and a resolved tornado.concurrent.Future
        is returned, otherwise a callback is submitted to the ioloop that will
        resolve a thread-safe concurrent.futures.Future instance.

        """
        return self.call_from_thread(partial(self.send_message, stream, msg))

    def mass_send_message(self, msg):
        """Send a message to all connected clients.

        Notes
        -----
        This method can only be called in the IOLoop thread.

        """
        for stream in self._connections.keys():
            if not stream.closed():
                # Don't cause noise by trying to write to already closed streams
                self.send_message(stream, msg)

    def mass_send_message_from_thread(self, msg):
        """Thread-safe version of send_message() returning a Future instance.

        See return value and notes for send_message_from_thread().

        """
        return self.call_from_thread(partial(self.mass_send_message, msg))

    def in_ioloop_thread(self):
        """Return True if called in the IOLoop thread of this server."""
        return get_thread_ident() == self.ioloop_thread_id


class ClientRequestConnection(object):
    """Encapsulates specific KATCP request and associated client connection."""

    def __init__(self, client_connection, req_msg):
        self.client_connection = client_connection
        assert(req_msg.mtype == Message.REQUEST)
        self.msg = req_msg
        self.mass_inform = client_connection.mass_inform

    def inform(self, *args):
        inf_msg = Message.reply_inform(self.msg, *args)
        return self.client_connection.inform(inf_msg)

    def reply(self, *args):
        rep_msg = Message.reply_to_request(self.msg, *args)
        self._post_reply()
        return self.client_connection.reply(rep_msg, self.msg)

    def reply_with_message(self, rep_msg):
        """Send a pre-created reply message to the client connection.

        Will check that rep_msg.name matches the bound request.

        """
        self._post_reply()
        return self.client_connection.reply(rep_msg, self.msg)

    def _post_reply(self):
        # Future calls to reply_*() and inform() should error out.
        self.reply = self.reply_again
        self.reply_with_message = self.reply_again
        self.inform = self.inform_after_reply

    def reply_again(self, *args):
        raise RuntimeError('Reply to request %r already sent.' % self.msg)

    def inform_after_reply(self, *args):
        raise RuntimeError('Cannot send inform for '
                           'request %r after sending reply.' % self.msg)

    def make_reply(self, *args):
        return Message.reply_to_request(self.msg, *args)


class MessageHandlerThread(object):
    """Provides backwards compatibility for server expecting its own thread."""
    def __init__(self, handler, log_inform_formatter, logger=log):
        self.handler = handler
        try:
            owner = handler.im_self.name
        except AttributeError:
            owner = handler.im_self.__class__.__name__
        self.name = "{}.message_handler" .format(owner)
        self.log_inform_formatter = log_inform_formatter
        self._wake = threading.Event()
        self._msg_queue = deque()
        self._logger = logger
        self._running = threading.Event()
        self._thread = None
        self.ioloop = None
        super(MessageHandlerThread, self).__init__()

    def set_ioloop(self, ioloop):
        self.ioloop = ioloop

    def on_message(self, client_conn, msg):
        """Handle message.

        Returns
        -------
        ready : Future
            A future that will resolve once we're ready, else None.

        Notes
        -----
        *on_message* should not be called again until *ready* has resolved.

        """
        MAX_QUEUE_SIZE = 30
        if len(self._msg_queue) >= MAX_QUEUE_SIZE:
            # This should never happen if callers to handle_message wait
            # for its futures to resolve before sending another message.
            # NM 2014-10-06: Except when there are multiple clients. Oops.
            raise RuntimeError('MessageHandlerThread unhandled '
                               'message queue full, not handling message')
        ready_future = Future()
        self._msg_queue.append((ready_future, client_conn, msg))
        self._wake.set()
        return ready_future

    def run(self):
        self._running.set()
        # Set the default ioloop for anything using IOLoop.current()
        # in this thread gets self.ioloop
        self.ioloop.make_current()
        try:
            while self._running.isSet():
                if not self._msg_queue:
                    self._wake.wait()
                while self._msg_queue:
                    ready_future, client_conn, msg = self._msg_queue.popleft()
                    try:
                        res = self.handler(client_conn, msg)
                        if gen.is_future(res):
                            self.ioloop.add_callback(chain_future, res,
                                                     ready_future)
                        else:
                            ready_future.set_result(res)
                    except Exception, e:
                        err_msg = ('Error calling message '
                                   'handler for msg:\n {0!s}'.format(msg))
                        self._logger.error(err_msg, exc_info=True)
                        client_conn.inform(self.log_inform_formatter(
                            'error', 'See device logs:\n' + err_msg, 'root'))
                        ready_future.set_exception(e)
                self._wake.clear()

        except Exception:
            self._logger.error(
                'Unhandled exception in message handler thread: ', exc_info=True)
        finally:
            self._running.clear()
            self._logger.info('Message handler thread stopped.')

    def start(self, timeout=None):
        if self._thread and self._thread.isAlive():
            raise RuntimeError('Cannot start since thread is already running')
        self._thread = threading.Thread(target=self.run, name=self.name)
        self._thread.start()
        if timeout:
            return self.wait_running(timeout)

    def stop(self, timeout=1.0):
        """Stop the handler thread (from another thread).

        Parameters
        ----------
        timeout : float, optional
            Seconds to wait for server to have *started*.

        """
        if timeout:
            self._running.wait(timeout)
        self._running.clear()
        # Make sure to wake the run thread.
        self._wake.set()

    def join(self, timeout=None):
        """Rejoin the handler thread.

        Parameters
        ----------
        timeout : float or None, optional
            Time in seconds to wait for the thread to finish.

        """
        self._thread.join(timeout)

    def isAlive(self):
        return self._thread and self._thread.isAlive()

    def running(self):
        """Whether the handler thread is running."""
        return self._running.isSet()

    def wait_running(self, timeout=None):
        """Wait until the handler thread is running."""
        return self._running.wait(timeout)


class DeviceServerBase(object):
    """Base class for device servers.

    Subclasses should add .request\_* methods for dealing
    with request messages. These methods each take the client
    request connection and msg objects as arguments and should return
    the reply message or raise an exception as a result.

    Subclasses can also add .inform\_* and reply\_* methods to handle
    those types of messages.

    Should a subclass need to generate inform messages it should
    do so using either the .inform() or .mass_inform() methods.

    Finally, this class should probably not be subclassed directly
    but rather via subclassing DeviceServer itself which implements
    common .request\_* methods.

    Parameters
    ----------
    host : str
        Host to listen on.
    port : int
        Port to listen on.
    tb_limit : int, optional
        Maximum number of stack frames to send in error tracebacks.
    logger : logging.Logger object, optional
        Logger to log messages to.

    """

    __metaclass__ = DeviceServerMetaclass

    ## @brief Protocol versions and flags. Default to version 5, subclasses
    ## should override PROTOCOL_INFO
    PROTOCOL_INFO = ProtocolFlags(DEFAULT_KATCP_MAJOR, 0, set([
        ProtocolFlags.MULTI_CLIENT,
        ProtocolFlags.MESSAGE_IDS,
        ]))

    def __init__(self, host, port, tb_limit=20, logger=log):
        self._server = KATCPServer(self, host, port, tb_limit, logger)
        self._logger = logger
        self._tb_limit = tb_limit
        # Thread that will optionally be used to handle requests
        self._handler_thread = None
        # Set default concurrency options
        self.set_concurrency_options()

    @property
    def bind_address(self):
        return self._server.bind_address

    def setDaemon(self, daemonic):
        """Set daemonic state of the managed ioloop thread to True / False

        Calling this method for a non-managed ioloop has no effect. Must be called before
        start(), or it will also have no effect
        """
        self._server.setDaemon(daemonic)


    def create_log_inform(self, level_name, msg, name, timestamp=None):
        """Create a katcp logging inform message.

        Usually this will be called from inside a DeviceLogger object,
        but it is also used by the methods in this class when errors
        need to be reported to the client.

        """
        if timestamp is None:
            timestamp = time.time()

        katcp_version = self.PROTOCOL_INFO.major
        timestamp_msg = ('%.6f' % timestamp
                         if katcp_version >= SEC_TS_KATCP_MAJOR
                         else str(int(timestamp*1000)))
        return Message.inform("log", level_name, timestamp_msg, name, msg)

    def on_message(self, client_conn, msg):
        """Dummy implementation of on_message required by KATCPServer.

        Will be replaced by a handler with the appropriate concurrency
        semantics when set_concurrency_options is called
        (defaults are set in __init__()).

        """
        raise RuntimeError('set_concurrency_options() '
                           'need to be called once before on_message')

    def handle_message(self, client_conn, msg):
        """Handle messages of all types from clients.

        Parameters
        ----------
        client_conn : ClientConnection object
            The client connection the message was from.
        msg : Message object
            The message to process.

        """
        # log messages received so that no one else has to
        self._logger.debug('received: {0!s}'.format(msg))

        if msg.mtype == msg.REQUEST:
            return self.handle_request(client_conn, msg)
        elif msg.mtype == msg.INFORM:
            return self.handle_inform(client_conn, msg)
        elif msg.mtype == msg.REPLY:
            return self.handle_reply(client_conn, msg)
        else:
            reason = "Unexpected message type received by server ['%s']." \
                     % (msg,)
            client_conn.inform(self.create_log_inform("error", reason, "root"))

    def handle_request(self, connection, msg):
        """Dispatch a request message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnection object
            The client connection the message was from.
        msg : Message object
            The request message to process.

        Returns
        -------
        done_future : Future or None
            Returns Future for async request handlers that will resolve when
            done, or None for sync request handlers once they have completed.

        """
        send_reply = True
        # TODO Should check presence of Message-ids against protocol flags and
        # raise an error as needed.
        if msg.name in self._request_handlers:
            req_conn = ClientRequestConnection(connection, msg)
            handler = self._request_handlers[msg.name]
            try:
                reply = handler(self, req_conn, msg)
                # If we get a future, assume this is an async message handler
                # that will resolve the future with the reply message when it
                # is complete. Attach a message-sending callback to the future,
                # and return the future.
                if gen.is_future(reply):
                    concurrent = getattr(handler, '_concurrent_reply', False)
                    concurrent_str = ' CONCURRENT' if concurrent else ''

                    done_future = Future()
                    def async_reply(f):
                        try:
                            connection.reply(f.result(), msg)
                            self._logger.debug("%s FUTURE%s replied",
                                               msg.name, concurrent_str)
                        except FailReply, e:
                            reason = str(e)
                            self._logger.error("Request %s FUTURE%s FAIL: %s",
                                               msg.name, concurrent_str, reason)
                            reply = Message.reply(msg.name, "fail", reason)
                            connection.reply(reply, msg)
                        except AsyncReply:
                            self._logger.debug("%s FUTURE ASYNC OK"
                                               % (msg.name,))
                        except Exception:
                            error_reply = self.create_exception_reply_and_log(
                                msg, sys.exc_info())
                            connection.reply(error_reply, msg)
                        finally:
                            done_future.set_result(None)

                    # TODO When using the return_reply() decorator the future
                    # returned is not currently threadsafe, must either deal
                    # with it here, or in kattypes.py. Would be nice if we don't
                    # have to always fall back to adding a callback, or wrapping
                    # a thread-safe future. Supporting sync-with-thread and
                    # async futures is turning out to be a pain in the ass ;)
                    self.ioloop.add_callback(reply.add_done_callback, async_reply)
                    # reply.add_done_callback(async_reply)

                    if concurrent:
                        # Return immediately if this is a concurrent handler
                        self._logger.debug("%s FUTURE CONCURRENT OK", msg.name)
                        return
                    else:
                        self._logger.debug("%s FUTURE OK", msg.name)
                        return done_future
                else:
                    assert (reply.mtype == Message.REPLY)
                    assert (reply.name == msg.name)
                    self._logger.debug("%s OK" % (msg.name,))
            except AsyncReply, e:
                self._logger.debug("%s ASYNC OK" % (msg.name,))
                send_reply = False
            except FailReply, e:
                reason = str(e)
                self._logger.error("Request %s FAIL: %s" % (msg.name, reason))
                reply = Message.reply(msg.name, "fail", reason)
            except Exception:
                reply = self.create_exception_reply_and_log(msg, sys.exc_info())
        else:
            self._logger.error("%s INVALID: Unknown request." % (msg.name,))
            reply = Message.reply(msg.name, "invalid", "Unknown request.")

        if send_reply:
            connection.reply(reply, msg)

    def create_exception_reply_and_log(self, req_msg, exc_info):
        e_type, e_value, trace = exc_info
        reason = "\n".join(traceback.format_exception(
            e_type, e_value, trace, self._tb_limit))
        self._logger.error("Request %s FAIL: %s" % (req_msg.name, reason))
        return Message.reply(req_msg.name, "fail", reason)

    def handle_inform(self, connection, msg):
        """Dispatch an inform message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnection object
            The client connection the message was from.
        msg : Message object
            The inform message to process.

        """
        if msg.name in self._inform_handlers:
            try:
                self._inform_handlers[msg.name](self, connection, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("Inform %s FAIL: %s" % (msg.name, reason))
        else:
            self._logger.warn("%s INVALID: Unknown inform." % (msg.name,))

    def handle_reply(self, connection, msg):
        """Dispatch a reply message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnection object
            The client connection the message was from.
        msg : Message object
            The reply message to process.

        """
        if msg.name in self._reply_handlers:
            try:
                self._reply_handlers[msg.name](self, connection, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("Reply %s FAIL: %s" % (msg.name, reason))
        else:
            self._logger.warn("%s INVALID: Unknown reply." % (msg.name,))

    def inform(self, connection, msg):
        """Send an inform message to a particular client.

        Should only be used for asynchronous informs. Informs
        that are part of the response to a request should use
        :meth:`reply_inform` so that the message identifier
        from the original request can be attached to the
        inform.

        Parameters
        ----------
        connection : ClientConnection object
            The client to send the message to.
        msg : Message object
            The inform message to send.

        """
        if isinstance(connection, ClientRequestConnection):
            self._logger.warn(
                'Deprecation warning: do not use self.inform() '
                'within a reply handler context -- use req.inform()\n'
                'Traceback:\n %s', "".join(traceback.format_stack()))
            # Get the underlying ClientConnection instance
            connection = connection.client_connection
        connection.inform(msg)

    def mass_inform(self, msg):
        """Send an inform message to all clients.

        Parameters
        ----------
        msg : Message object
            The inform message to send.

        """
        assert (msg.mtype == Message.INFORM)
        self._server.mass_send_message_from_thread(msg)

    def reply(self, connection, reply, orig_req):
        """Send an asynchronous reply to an earlier request.

        Parameters
        ----------
        connection : ClientConnection object
            The client to send the reply to.
        reply : Message object
            The reply message to send.
        orig_req : Message object
            The request message being replied to. The reply message's
            id is overridden with the id from orig_req before the
            reply is sent.

        """
        if isinstance(connection, ClientRequestConnection):
            self._logger.warn(
                'Deprecation warning: do not use self.reply() '
                'within a reply handler context -- use req.reply(*msg_args)\n'
                'or req.reply_with_message(msg) Traceback:\n %s',
                "".join(traceback.format_stack()))
            # Get the underlying ClientConnection instance
            connection = connection.client_connection
        connection.reply(reply, orig_req)

    def reply_inform(self, connection, inform, orig_req):
        """Send an inform as part of the reply to an earlier request.

        Parameters
        ----------
        connection : ClientConnection object
            The client to send the inform to.
        inform : Message object
            The inform message to send.
        orig_req : Message object
            The request message being replied to. The inform message's
            id is overridden with the id from orig_req before the
            inform is sent.

        """
        if isinstance(connection, ClientRequestConnection):
            self._logger.warn(
                'Deprecation warning: do not use self.reply_inform() '
                'within a reply handler context -- '
                'use req.inform(*inform_arguments)\n'
                'Traceback:\n %s', "".join(traceback.format_stack()))
            # Get the underlying ClientConnection instance
            connection = connection.client_connection
        connection.reply_inform(inform, orig_req)

    def set_ioloop(self, ioloop=None):
        """Set the tornado IOLoop to use.

        Sets the tornado.ioloop.IOLoop instance to use, defaulting to
        IOLoop.current(). If set_ioloop() is never called the IOLoop is
        started in a new thread, and will be stopped if self.stop() is called.

        Notes
        -----
        Must be called before start() is called.

        """
        self._server.set_ioloop(ioloop)
        self.ioloop = self._server.ioloop

    def set_concurrency_options(self, thread_safe=True, handler_thread=True):
        """Set concurrency options for this device server.
        Must be called before :meth:`start`.

        Parameters
        ==========

        thread_safe : bool
            If True, make the server public methods thread safe. Incurs
            performance overhead.
        handler_thread : bool
            Can only be set if `thread_safe` is True. Handle all requests (even
            from different clients) in a separate, single, request-handling
            thread. Blocking request handlers will prevent the server from
            handling new requests from any client, but sensor strategies should
            still function. This more or less mimics the behaviour of a server
            in library versions before 0.6.0.

        """
        if handler_thread:
            assert thread_safe, "handler_thread=True requires thread_safe=True"
        self._server.client_connection_factory = (
            ThreadsafeClientConnection if thread_safe else ClientConnection)
        if handler_thread:
            self._handler_thread = MessageHandlerThread(
                self.handle_message, self.create_log_inform, self._logger)
            self.on_message = self._handler_thread.on_message
        else:
            self.on_message = return_future(self.handle_message)
            self._handler_thread = None

        self._concurrency_options = ObjectDict(
            thread_safe=thread_safe, handler_thread=handler_thread)

    def start(self, timeout=None):
        """Start the server in a new thread.

        Parameters
        ----------
        timeout : float or None, optional
            Time in seconds to wait for server thread to start.

        """
        if self._handler_thread and self._handler_thread.isAlive():
            raise RuntimeError('Message handler thread already started')
        self._server.start(timeout)
        self.ioloop = self._server.ioloop
        if self._handler_thread:
            self._handler_thread.set_ioloop(self.ioloop)
            self._handler_thread.start(timeout)

    def join(self, timeout=None):
        """Rejoin the server thread.

        Parameters
        ----------
        timeout : float or None, optional
            Time in seconds to wait for the thread to finish.

        """
        self._server.join(timeout)
        if self._handler_thread:
            self._handler_thread.join(timeout)

    def stop(self, timeout=1.0):
        """Stop a running server (from another thread).

        Parameters
        ----------
        timeout : float, optional
            Seconds to wait for server to have *started*.

        Returns
        -------
        stopped : thread-safe Future
            Resolves when the server is stopped

        """
        stopped = self._server.stop(timeout)
        if self._handler_thread:
            self._handler_thread.stop(timeout)

        return stopped

    def running(self):
        """Whether the server is running."""
        return self._server.running()

    def wait_running(self, timeout=None):
        """Wait until the server is running"""
        return self._server.wait_running(timeout)

    @return_future
    def on_client_connect(self, conn):
        """Called after client connection is established.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point.

        Parameters
        ----------
        conn : ClientConnection object
            The client connection that has been successfully established.

        Returns
        -------
        Future that resolves when the device is ready to accept messages.

        """
        pass

    @return_future
    def on_client_disconnect(self, conn, msg, connection_valid):
        """Called before a client connection is closed.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point. The server
        cannot guarantee this will be called (for example, the client
        might drop the connection). The message parameter contains
        the reason for the disconnection.

        Parameters
        ----------
        conn : ClientConnection object
            Client connection being disconnected.
        msg : str
            Reason client is being disconnected.
        connection_valid : boolean
            True if connection is still open for sending,
            False otherwise.

        Returns
        -------
        Future that resolves when the client connection can be closed.

        """
        return None

    def sync_with_ioloop(self, timeout=None):
        """Block for ioloop to complete a loop if called from another thread.

        Returns a future if called from inside the ioloop.

        Raises concurrent.futures.TimeoutError if timed out while blocking.

        """
        in_ioloop = self._server.in_ioloop_thread()
        if in_ioloop:
            f = tornado_Future()
        else:
            f = Future()

        def cb():
            f.set_result(None)

        self.ioloop.add_callback(cb)

        if in_ioloop:
            return f
        else:
            f.result(timeout)


class DeviceServer(DeviceServerBase):
    """Implements some standard messages on top of DeviceServerBase.

    Inform messages handled are:

      * version (sent on connect)
      * build-state (sent on connect)
      * log (via self.log.warn(...), etc)
      * disconnect
      * client-connected

    Requests handled are:

      * halt
      * help
      * log-level
      * restart [#restartf1]_
      * client-list
      * sensor-list
      * sensor-sampling
      * sensor-value
      * watchdog
      * version-list (only standard in KATCP v5 or later)
      * request-timeout-hint (pre-standard only if protocol flags indicates
                              timeout hints, supported for KATCP v5.1 or later)
      * sensor-sampling-clear (non-standard)

    .. [#restartf1] Restart relies on .set_restart_queue() being used to
      register a restart queue with the device. When the device needs to be
      restarted, it will be added to the restart queue.  The queue should
      be a Python Queue.Queue object without a maximum size.

    Unhandled standard requests are:

      * configure
      * mode

    Subclasses can define the tuple VERSION_INFO to set the interface
    name, major and minor version numbers. The BUILD_INFO tuple can
    be defined to give a string describing a particular interface
    instance and may have a fourth element containing additional
    version information (e.g. rc1).

    Subclasses may manipulate the versions returned by the ?version-list
    command by editing .extra_versions which is a dictionary mapping
    role or component names to (version, build_state_or_serial_no) tuples.
    The build_state_or_serial_no may be None.

    Subclasses must override the .setup_sensors() method. If they
    have no sensors to register, the method should just be a pass.

    """
    # DeviceServer has a lot of methods because there is a method
    # per request type and it's an abstract class which is only
    # used outside this module
    # pylint: disable-msg = R0904

    ## @brief Interface version information.
    VERSION_INFO = ("device_stub", 0, 1)

    ## @brief Device server build / instance information.
    BUILD_INFO = ("name", 0, 1, "")

    UNSUPPORTED_REQUESTS_BY_MAJOR_VERSION = {
        4: set(['version-list']),
    }

    SUPPORTED_PROTOCOL_MAJOR_VERSIONS = (4, 5)

    ## @var log
    # @brief DeviceLogger instance for sending log messages to the client.

    # * and ** magic fine here
    # pylint: disable-msg = W0142

    def __init__(self, *args, **kwargs):
        if self.PROTOCOL_INFO.major not in self.SUPPORTED_PROTOCOL_MAJOR_VERSIONS:
            raise ValueError('Device server only supports katcp procotol '
                             'versions %r, not %r as specified in self.PROTOCOL_INFO'
                             % (self.SUPPORTED_PROTOCOL_MAJOR_VERSIONS,
                                self.PROTOCOL_INFO.major))
        super(DeviceServer, self).__init__(*args, **kwargs)
        self.log = DeviceLogger(self, python_logger=self._logger)
        # map names to (version, build state/serial no.) tuples.
        #   None may used to indicate no build state or serial no.
        self.extra_versions = {}
        self._restart_queue = None
        self._sensors = {}  # map names to sensor objects
        # map client sockets to map of sensors -> sampling strategies
        self._strategies = {}
        # For holding ClientConnection* instances of active connections
        self._client_conns = set()

        self.setup_sensors()

    # pylint: enable-msg = W0142

    @return_future
    def on_client_connect(self, client_conn):
        """Inform client of build state and version on connect.

        Parameters
        ----------
        client_conn : ClientConnection object
            The client connection that has been successfully established.

        Returns
        -------
        Future that resolves when the device is ready to accept messages.

        """
        assert get_thread_ident() == self._server.ioloop_thread_id
        self._client_conns.add(client_conn)
        self._strategies[client_conn] = {}  # map sensors -> sampling strategies

        katcp_version = self.PROTOCOL_INFO.major
        if katcp_version >= VERSION_CONNECT_KATCP_MAJOR:
            client_conn.inform(Message.inform(
                "version-connect", "katcp-protocol", self.PROTOCOL_INFO))
            client_conn.inform(Message.inform(
                "version-connect", "katcp-library",
                "katcp-python-%s" % katcp.__version__))
            client_conn.inform(Message.inform(
                "version-connect", "katcp-device",
                self.version(), self.build_state()))
        else:
            client_conn.inform(Message.inform("version", self.version()))
            client_conn.inform(Message.inform("build-state", self.build_state()))

    def clear_strategies(self, client_conn, remove_client=False):
        """Clear the sensor strategies of a client connection.

        Parameters
        ----------
        client_connection : ClientConnection instance
            The connection that should have its sampling strategies cleared
        remove_client : bool, optional
            Remove the client connection from the strategies datastructure.
            Useful for clients that disconnect.

        """
        assert get_thread_ident() == self._server.ioloop_thread_id

        getter = (self._strategies.pop if remove_client
                  else self._strategies.get)
        strategies = getter(client_conn, None)
        if strategies is not None:
            for sensor, strategy in list(strategies.items()):
                strategy.cancel()
                del strategies[sensor]

    def on_client_disconnect(self, client_conn, msg, connection_valid):
        """Inform client it is about to be disconnected.

        Parameters
        ----------
        client_conn : ClientConnection object
            The client connection being disconnected.
        msg : str
            Reason client is being disconnected.
        connection_valid : bool
            True if connection is still open for sending,
            False otherwise.

        Returns
        -------
        Future that resolves when the client connection can be closed.

        """
        f = tornado_Future()
        @gen.coroutine
        def remove_strategies():
            self.clear_strategies(client_conn, remove_client=True)
            if connection_valid:
                client_conn.inform(Message.inform("disconnect", msg))
                yield client_conn.flush_on_close()

        try:
            self._client_conns.remove(client_conn)
            self.ioloop.add_callback(lambda: chain_future(remove_strategies(), f))
        except Exception:
            f.set_exc_info(sys.exc_info())
        return f

    def build_state(self):
        """Return build state string of the form name-major.minor[(a|b|rc)n]."""
        return "%s-%s.%s%s" % self.BUILD_INFO

    def version(self):
        """Return a version string of the form type-major.minor."""
        return "%s-%s.%s" % self.VERSION_INFO

    def add_sensor(self, sensor):
        """Add a sensor to the device.

        Usually called inside .setup_sensors() but may be called from
        elsewhere.

        Parameters
        ----------
        sensor : Sensor object
            The sensor object to register with the device server.

        """
        self._sensors[sensor.name] = sensor

    def has_sensor(self, sensor_name):
        """Whether the sensor with specified name is known."""
        return sensor_name in self._sensors

    def remove_sensor(self, sensor):
        """Remove a sensor from the device.

        Also deregisters all clients observing the sensor.

        Parameters
        ----------
        sensor : Sensor object or name string
            The sensor to remove from the device server.

        """
        if isinstance(sensor, basestring):
            sensor_name = sensor
        else:
            sensor_name = sensor.name
        sensor = self._sensors.pop(sensor_name)

        def cancel_sensor_strategies():
            for conn_strategies in self._strategies.values():
                strategy = conn_strategies.pop(sensor, None)
                if strategy:
                    strategy.cancel()
        self.ioloop.add_callback(cancel_sensor_strategies)

    def get_sensor(self, sensor_name):
        """Fetch the sensor with the given name.

        Parameters
        ----------
        sensor_name : str
            Name of the sensor to retrieve.

        Returns
        -------
        sensor : Sensor object
            The sensor with the given name.

        """
        sensor = self._sensors.get(sensor_name, None)
        if not sensor:
            raise ValueError("Unknown sensor '%s'." % (sensor_name,))
        return sensor

    def get_sensors(self):
        """Fetch a list of all sensors.

        Returns
        -------
        sensors : list of Sensor objects
            The list of sensors registered with the device server.

        """
        return self._sensors.values()

    def set_restart_queue(self, restart_queue):
        """Set the restart queue.

        When the device server should be restarted, it will be added to the
        queue.

        Parameters
        ----------
        restart_queue : Queue.Queue object
            The queue to add the device server to when it should be restarted.

        """
        self._restart_queue = restart_queue

    def setup_sensors(self):
        """Populate the dictionary of sensors.

        Unimplemented by default -- subclasses should add their sensors
        here or pass if there are no sensors.

        Examples
        --------
        >>> class MyDevice(DeviceServer):
        ...     def setup_sensors(self):
        ...         self.add_sensor(Sensor(...))
        ...         self.add_sensor(Sensor(...))
        ...

        """
        raise NotImplementedError("Device server subclasses must implement "
                                  "setup_sensors.")

    # request implementations

    # all requests take req and msg arguments regardless of whether
    # they're used
    # pylint: disable-msg = W0613

    def request_halt(self, req, msg):
        """Halt the device server.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether scheduling the halt succeeded.

        Examples
        --------
        ::

            ?halt
            !halt ok

        """
        f = Future()
        @gen.coroutine
        def _halt():
            req.reply("ok")
            yield gen.moment
            self.stop(timeout=None)
            raise AsyncReply

        self.ioloop.add_callback(lambda: chain_future(_halt(), f))
        return f

    def request_help(self, req, msg):
        """Return help on the available requests.

        Return a description of the available requests using a sequence of
        #help informs.

        Parameters
        ----------
        request : str, optional
            The name of the request to return help for (the default is to
            return help for all requests).

        Informs
        -------
        request : str
            The name of a request.
        description : str
            Documentation for the named request.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the help succeeded.
        informs : int
            Number of #help inform messages sent.

        Examples
        --------
        ::

            ?help
            #help halt ...description...
            #help help ...description...
            ...
            !help ok 5

            ?help halt
            #help halt ...description...
            !help ok 1

        """
        if not msg.arguments:
            for name, method in sorted(self._request_handlers.items()):
                doc = method.__doc__
                req.inform(name, doc)
            num_methods = len(self._request_handlers)
            return req.make_reply("ok", str(num_methods))
        else:
            name = msg.arguments[0]
            if name in self._request_handlers:
                method = self._request_handlers[name]
                doc = method.__doc__.strip()
                req.inform(name, doc)
                return req.make_reply("ok", "1")
            return req.make_reply("fail", "Unknown request method.")

    @request(Str(optional=True))
    @return_reply(Int())
    @has_katcp_protocol_flags(ProtocolFlags.REQUEST_TIMEOUT_HINTS)
    def request_request_timeout_hint(self, req, request):
        """Return timeout hints for requests

        KATCP requests should generally take less than 5s to complete, but some
        requests are unavoidably slow. This results in spurious client timeout
        errors. This request provides timeout hints that clients can use to
        select suitable request timeouts.

        Parameters
        ----------
        request : str, optional
            The name of the request to return a timeout hint for (the default is
            to return hints for all requests that have timeout hints). Returns
            one inform per request. Must be an existing request if specified.

        Informs
        -------
        request : str
            The name of the request.
        suggested_timeout : float
            Suggested request timeout in seconds for the request. If
            `suggested_timeout` is zero (0), no timeout hint is available.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the help succeeded.
        informs : int
            Number of #request-timeout-hint inform messages sent.

        Examples
        --------
        ::

            ?request-timeout-hint
            #request-timeout-hint halt 5
            #request-timeout-hint very-slow-request 500
            ...
            !request-timeout-hint ok 5

            ?request-timeout-hint moderately-slow-request
            #request-timeout-hint moderately-slow-request 20
            !request-timeout-hint ok 1

        Notes
        -----

        ?request-timeout-hint without a parameter will only return informs for
        requests that have specific timeout hints, so it will most probably be a
        subset of all the requests, or even no informs at all.

        """
        timeout_hints = {}
        if request:
            if request not in self._request_handlers:
                raise FailReply('Unknown request method')
            timeout_hint = getattr(
                self._request_handlers[request], 'request_timeout_hint', None)
            timeout_hint = timeout_hint or 0
            timeout_hints[request] = timeout_hint
        else:
            for request_, handler in self._request_handlers.items():
                timeout_hint = getattr(handler, 'request_timeout_hint', None)
                if timeout_hint:
                    timeout_hints[request_] = timeout_hint

        cnt = len(timeout_hints)
        for request_name, timeout_hint in sorted(timeout_hints.items()):
            req.inform(request_name, float(timeout_hint))

        return ('ok', cnt)

    def request_log_level(self, req, msg):
        """Query or set the current logging level.

        Parameters
        ----------
        level : {'all', 'trace', 'debug', 'info', 'warn', 'error', 'fatal', \
                 'off'}, optional
            Name of the logging level to set the device server to (the default
            is to leave the log level unchanged).

        Returns
        -------
        success : {'ok', 'fail'}
            Whether the request succeeded.
        level : {'all', 'trace', 'debug', 'info', 'warn', 'error', 'fatal', \
                 'off'}
            The log level after processing the request.

        Examples
        --------
        ::

            ?log-level
            !log-level ok warn

            ?log-level info
            !log-level ok info

        """
        if msg.arguments:
            try:
                self.log.set_log_level_by_name(msg.arguments[0])
            except ValueError, e:
                raise FailReply(str(e))
        return req.make_reply("ok", self.log.level_name())

    def request_restart(self, req, msg):
        """Restart the device server.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether scheduling the restart succeeded.

        Examples
        --------
        ::

            ?restart
            !restart ok

        """
        if self._restart_queue is None:
            raise FailReply("No restart queue registered -- cannot restart.")

        f = tornado_Future()

        @gen.coroutine
        def _restart():
            # .put should never block because queue should have no size limit
            self._restart_queue.put_nowait(self)
            req.reply('ok')
            raise AsyncReply

        self.ioloop.add_callback(lambda: chain_future(_restart(), f))
        return f

    def request_client_list(self, req, msg):
        """Request the list of connected clients.

        The list of clients is sent as a sequence of #client-list informs.

        Informs
        -------
        addr : str
            The address of the client as host:port with host in dotted quad
            notation. If the address of the client could not be determined
            (because, for example, the client disconnected suddenly) then
            a unique string representing the client is sent instead.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the client list succeeded.
        informs : int
            Number of #client-list inform messages sent.

        Examples
        --------
        ::

            ?client-list
            #client-list 127.0.0.1:53600
            !client-list ok 1

        """
        # TODO Get list of ClientConnection* instances and implement a standard
        # 'address-print' method in the ClientConnection class
        clients = self._client_conns
        num_clients = len(clients)
        for conn in clients:
            addr = conn.address
            req.inform(addr)
        return req.make_reply('ok', str(num_clients))


    @minimum_katcp_version(5, 0)
    def request_version_list(self, req, msg):
        """Request the list of versions of roles and subcomponents.

        Informs
        -------
        name : str
            Name of the role or component.
        version : str
            A string identifying the version of the component. Individual
            components may define the structure of this argument as they
            choose. In the absence of other information clients should
            treat it as an opaque string.
        build_state_or_serial_number : str
            A unique identifier for a particular instance of a component.
            This should change whenever the component is replaced or updated.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the version list succeeded.
        informs : int
            Number of #version-list inform messages sent.

        Examples
        --------
        ::

            ?version-list
            #version-list katcp-protocol 5.0-MI
            #version-list katcp-library katcp-python-0.4 katcp-python-0.4.1-py2
            #version-list katcp-device foodevice-1.0 foodevice-1.0.0rc1
            !version-list ok 3

        """
        versions = [
            ("katcp-protocol", (self.PROTOCOL_INFO, None)),
            ("katcp-library", ("katcp-python-%s" % katcp.__version__, katcp.__version__)),
            ("katcp-device", (self.version(), self.build_state())),
        ]
        extra_versions = sorted(self.extra_versions.items())

        for name, (version, build_state) in versions + extra_versions:
            if build_state is None:
                inform_args = (name, version)
            else:
                inform_args = (name, version, build_state)
            req.inform(*inform_args)

        num_versions = len(versions) + len(extra_versions)
        return req.make_reply("ok", str(num_versions))

    def request_sensor_list(self, req, msg):
        """Request the list of sensors.

        The list of sensors is sent as a sequence of #sensor-list informs.

        Parameters
        ----------
        name : str, optional
            Name of the sensor to list (the default is to list all sensors).
            If name starts and ends with '/' it is treated as a regular
            expression and all sensors whose names contain the regular
            expression are returned.

        Informs
        -------
        name : str
            The name of the sensor being described.
        description : str
            Description of the named sensor.
        units : str
            Units for the value of the named sensor.
        type : str
            Type of the named sensor.
        params : list of str, optional
            Additional sensor parameters (type dependent). For integer and
            float sensors the additional parameters are the minimum and maximum
            sensor value. For discrete sensors the additional parameters are
            the allowed values. For all other types no additional parameters
            are sent.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the sensor list succeeded.
        informs : int
            Number of #sensor-list inform messages sent.

        Examples
        --------
        ::

            ?sensor-list
            #sensor-list psu.voltage PSU\_voltage. V float 0.0 5.0
            #sensor-list cpu.status CPU\_status. \@ discrete on off error
            ...
            !sensor-list ok 5

            ?sensor-list cpu.power.on
            #sensor-list cpu.power.on Whether\_CPU\_hase\_power. \@ boolean
            !sensor-list ok 1

            ?sensor-list /voltage/
            #sensor-list psu.voltage PSU\_voltage. V float 0.0 5.0
            #sensor-list cpu.voltage CPU\_voltage. V float 0.0 3.0
            !sensor-list ok 2

        """
        exact, name_filter = construct_name_filter(msg.arguments[0]
                                                   if msg.arguments else None)
        sensors = [(name, sensor) for name, sensor in
                   sorted(self._sensors.iteritems()) if name_filter(name)]

        if exact and not sensors:
            return req.make_reply("fail", "Unknown sensor name.")

        self._send_sensor_value_informs(req, sensors)
        return req.make_reply("ok", str(len(sensors)))

    def _send_sensor_value_informs(self, req, sensors):
        for name, sensor in sensors:
            req.inform(name, sensor.description, sensor.units, sensor.stype,
                       *sensor.formatted_params)

    def request_sensor_value(self, req, msg):
        """Request the value of a sensor or sensors.

        A list of sensor values as a sequence of #sensor-value informs.

        Parameters
        ----------
        name : str, optional
            Name of the sensor to poll (the default is to send values for all
            sensors). If name starts and ends with '/' it is treated as a
            regular expression and all sensors whose names contain the regular
            expression are returned.

        Informs
        -------
        timestamp : float
            Timestamp of the sensor reading in seconds since the Unix
            epoch, or milliseconds for katcp versions <= 4.
        count : {1}
            Number of sensors described in this #sensor-value inform. Will
            always be one. It exists to keep this inform compatible with
            #sensor-status.
        name : str
            Name of the sensor whose value is being reported.
        value : object
            Value of the named sensor. Type depends on the type of the sensor.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the list of values succeeded.
        informs : int
            Number of #sensor-value inform messages sent.

        Examples
        --------
        ::

            ?sensor-value
            #sensor-value 1244631611.415231 1 psu.voltage 4.5
            #sensor-value 1244631611.415200 1 cpu.status off
            ...
            !sensor-value ok 5

            ?sensor-value cpu.power.on
            #sensor-value 1244631611.415231 1 cpu.power.on 0
            !sensor-value ok 1

        """
        exact, name_filter = construct_name_filter(msg.arguments[0]
                                                   if msg.arguments else None)
        sensors = [(name, sensor) for name, sensor in
                   sorted(self._sensors.iteritems()) if name_filter(name)]

        if exact and not sensors:
            return req.make_reply("fail", "Unknown sensor name.")

        katcp_version = self.PROTOCOL_INFO.major
        for name, sensor in sensors:
            timestamp, status, value = sensor.read_formatted(katcp_version)
            req.inform(timestamp, "1", name, status, value)
        return req.make_reply("ok", str(len(sensors)))

    def request_sensor_sampling(self, req, msg):
        """Configure or query the way a sensor is sampled.

        Sampled values are reported asynchronously using the #sensor-status
        message.

        Parameters
        ----------
        name : str
            Name of the sensor whose sampling strategy to query or configure.
        strategy : {'none', 'auto', 'event', 'differential', \
                    'period', 'event-rate'}, optional
            Type of strategy to use to report the sensor value. The
            differential strategy type may only be used with integer or float
            sensors. If this parameter is supplied, it sets the new strategy.
        params : list of str, optional
            Additional strategy parameters (dependent on the strategy type).
            For the differential strategy, the parameter is an integer or float
            giving the amount by which the sensor value may change before an
            updated value is sent.
            For the period strategy, the parameter is the sampling period
            in float seconds.
            The event strategy has no parameters. Note that this has changed
            from KATCPv4.
            For the event-rate strategy, a minimum period between updates and
            a maximum period between updates (both in float seconds) must be
            given. If the event occurs more than once within the minimum period,
            only one update will occur. Whether or not the event occurs, the
            sensor value will be updated at least once per maximum period.
            The differential-rate strategy is not supported in this release.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether the sensor-sampling request succeeded.
        name : str
            Name of the sensor queried or configured.
        strategy : {'none', 'auto', 'event', 'differential', 'period'}
            Name of the new or current sampling strategy for the sensor.
        params : list of str
            Additional strategy parameters (see description under Parameters).

        Examples
        --------
        ::

            ?sensor-sampling cpu.power.on
            !sensor-sampling ok cpu.power.on none

            ?sensor-sampling cpu.power.on period 500
            !sensor-sampling ok cpu.power.on period 500

        """
        f = Future()
        self.ioloop.add_callback(lambda: chain_future(
            self._handle_sensor_sampling(req, msg), f))
        return f

    @gen.coroutine
    def _handle_sensor_sampling(self, req, msg):
        if not msg.arguments:
            raise FailReply("No sensor name given.")

        name = msg.arguments[0]

        if name not in self._sensors:
            raise FailReply("Unknown sensor name: %s." % name)

        sensor = self._sensors[name]
        # The client connection that is not specific to this request context
        client = req.client_connection
        katcp_version = self.PROTOCOL_INFO.major

        if len(msg.arguments) > 1:
            # attempt to set sampling strategy
            strategy = msg.arguments[1]
            params = msg.arguments[2:]

            if strategy not in SampleStrategy.SAMPLING_LOOKUP_REV:
                raise FailReply("Unknown strategy name: %s." % strategy)

            if not self.PROTOCOL_INFO.strategy_allowed(strategy):
                raise FailReply("Strategy %s not allowed for version %d of katcp"
                                % (strategy, katcp_version))

            format_inform = (format_inform_v5
                             if katcp_version >= SEC_TS_KATCP_MAJOR
                             else format_inform_v4)

            def inform_callback(sensor, reading):
                """Inform callback for sensor strategy."""
                timestamp, status, value = reading
                cb_msg = format_inform(sensor, timestamp, status, value)
                client.inform(cb_msg)

            if katcp_version < SEC_TS_KATCP_MAJOR and strategy == 'period':
                # Slightly nasty hack, but since period is the only v4 strategy
                # involving timestamps it's not _too_ nasty :)
                params = [float(params[0]) * MS_TO_SEC_FAC] + params[1:]
            new_strategy = SampleStrategy.get_strategy(
                strategy, inform_callback, sensor, *params, ioloop=self.ioloop)

            # Remove and cancel old strategy
            old_strategy = self._strategies[client].pop(sensor, None)
            if old_strategy:
                old_strategy.cancel()

            # todo: replace isinstance check with something better
            if not isinstance(new_strategy, SampleNone):
                self._strategies[client][sensor] = new_strategy
                new_strategy.start()

        current_strategy = self._strategies[client].get(sensor, None)
        if not current_strategy:
            current_strategy = SampleStrategy.get_strategy(
                "none", lambda *args: None, sensor)

        strategy, params = current_strategy.get_sampling_formatted()
        if katcp_version < SEC_TS_KATCP_MAJOR and strategy == 'period':
            # Another slightly nasty hack, but since period is the only
            # v4 strategy involving timestamps it's not _too_ nasty :)
            params = [int(float(params[0]) * SEC_TO_MS_FAC)] + params[1:]

        # Let the ioloop run so that the #sensor-status inform is sent before
        # the reply. Not strictly neccesary, but a number of tests depend on
        # this behaviour, less effort to fix it here :-/
        yield gen.moment
        raise gen.Return(req.make_reply("ok", name, strategy, *params))

    @request()
    @return_reply()
    def request_sensor_sampling_clear(self, req):
        """Set all sampling strategies for this client to none.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the list of devices succeeded.

        Examples
        --------
        ?sensor-sampling-clear
        !sensor-sampling-clear ok

        """
        f = Future()
        @gen.coroutine
        def _clear_strategies():
            self.clear_strategies(req.client_connection)
            raise gen.Return(('ok',))

        self.ioloop.add_callback(lambda: chain_future(_clear_strategies(), f))
        return f

    def request_watchdog(self, req, msg):
        """Check that the server is still alive.

        Returns
        -------
            success : {'ok'}

        Examples
        --------
        ::

            ?watchdog
            !watchdog ok

        """
        # not a function, just doesn't use self
        # pylint: disable-msg = R0201
        return req.make_reply("ok")

    # pylint: enable-msg = W0613


class AsyncDeviceServer(DeviceServer):
    """`DeviceServer` that is automatically configured for async use.

    Same as instantiating a :class:`DeviceServer` instance and calling methods
    `set_concurrency_options(thread_safe=False, handler_thread=False)` and
    `set_ioloop(tornado.ioloop.IOLoop.current())` before starting.

    """
    def __init__(self, *args, **kwargs):
        super(AsyncDeviceServer, self).__init__(*args, **kwargs)
        self.set_concurrency_options(thread_safe=False, handler_thread=False)
        self.set_ioloop(tornado.ioloop.IOLoop.current())

class DeviceLogger(object):
    """Object for logging messages from a DeviceServer.

    Log messages are logged at a particular level and under
    a particular name. Names use dotted notation to form
    a virtual hierarchy of loggers with the device.

    Parameters
    ----------
    device_server : DeviceServerBase object
        The device server this logger should use for sending out logs.
    root_logger : str
        The name of the root logger.

    """

    # level values are used as indexes into the LEVELS list
    # so these to lists should be in the same order
    ALL, TRACE, DEBUG, INFO, WARN, ERROR, FATAL, OFF = range(8)

    ## @brief List of logging level names.
    LEVELS = ["all", "trace", "debug", "info", "warn",
              "error", "fatal", "off"]

    ## @brief Map of Python logging level to corresponding to KATCP levels
    PYTHON_LEVEL = {
        TRACE: 0,
        DEBUG: logging.DEBUG,
        INFO: logging.INFO,
        WARN: logging.WARN,
        ERROR: logging.ERROR,
        FATAL: logging.FATAL,
        OFF: logging.FATAL + 10 # OFF is the highest possible logging level
    }

    def __init__(self, device_server, root_logger="root", python_logger=None):
        self._device_server = device_server
        self._python_logger = python_logger
        self._log_level = self.WARN
        self._root_logger_name = root_logger

    def level_name(self, level=None):
        """Return the name of the given level value.

        If level is None, return the name of the current level.

        Parameters
        ----------
        level : logging level constant
            The logging level constant whose name to retrieve.

        Returns
        -------
        level_name : str
            The name of the logging level.

        """
        if level is None:
            level = self._log_level
        return self.LEVELS[level]

    def level_from_name(self, level_name):
        """Return the level constant for a given name.

        If the *level_name* is not known, raise a ValueError.

        Parameters
        ----------
        level_name : str
            The logging level name whose logging level constant
            to retrieve.

        Returns
        -------
        level : logging level constant
            The logging level constant associated with the name.

        """
        try:
            return self.LEVELS.index(level_name)
        except ValueError:
            raise ValueError("Unknown logging level name '%s'" % (level_name,))

    def set_log_level(self, level):
        """Set the logging level.

        Parameters
        ----------
        level : logging level constant
            The value to set the logging level to.

        """
        self._log_level = level
        if self._python_logger:
            try:
                level = self.PYTHON_LEVEL.get(level)
            except ValueError as err:
                raise FailReply("Unknown logging level '%s'" % (level))
            self._python_logger.setLevel(level)

    def set_log_level_by_name(self, level_name):
        """Set the logging level using a level name.

        Parameters
        ----------
        level_name : str
            The name of the logging level.

        """
        self.set_log_level(self.level_from_name(level_name))

    def log(self, level, msg, *args, **kwargs):
        """Log a message and inform all clients.

        Parameters
        ----------
        level : logging level constant
            The level to log the message at.
        msg : str
            The text format for the log message.
        args : list of objects
            Arguments to pass to log format string. Final message text is
            created using: msg % args.
        kwargs : additional keyword parameters
            Allowed keywords are 'name' and 'timestamp'. The name is the name
            of the logger to log the message to. If not given the name defaults
            to the root logger. The timestamp is a float in seconds. If not
            given the timestamp defaults to the current time.

        """
        timestamp = kwargs.get("timestamp")
        python_msg = msg
        if self._python_logger is not None:
            if timestamp is not None:
                python_msg = ' '.join((
                    'katcp timestamp: %r' % timestamp,
                    python_msg))
            self._python_logger.log(self.PYTHON_LEVEL[level], python_msg, *args)
        if level >= self._log_level:
            name = kwargs.get("name")
            if name is None:
                name = self._root_logger_name
            try:
                inform_msg = msg % args
            except TypeError:
                # Catch the "not enough arguments for format string" exception.
                inform_msg = "{} {}".format(
                    msg,
                    args if args else '').strip()

            self._device_server.mass_inform(
                self._device_server.create_log_inform(
                    self.level_name(level),
                    inform_msg,
                    name,
                    timestamp=timestamp))

    def trace(self, msg, *args, **kwargs):
        """Log a trace message."""
        self.log(self.TRACE, msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        """Log a debug message."""
        self.log(self.DEBUG, msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        """Log an info message."""
        self.log(self.INFO, msg, *args, **kwargs)

    def warn(self, msg, *args, **kwargs):
        """Log an warning message."""
        self.log(self.WARN, msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        """Log an error message."""
        self.log(self.ERROR, msg, *args, **kwargs)

    def fatal(self, msg, *args, **kwargs):
        """Log a fatal error message."""
        self.log(self.FATAL, msg, *args, **kwargs)

    @classmethod
    def log_to_python(cls, logger, msg):
        """Log a KATCP logging message to a Python logger.

        Parameters
        ----------
        logger : logging.Logger object
            The Python logger to log the given message to.
        msg : Message object
            The #log message to create a log entry from.

        """
        (level, timestamp, name, message) = tuple(msg.arguments)
        log_string = "%s %s: %s" % (timestamp, name, message)
        logger.log({"trace": 0,
                    "debug": logging.DEBUG,
                    "info": logging.INFO,
                    "warn": logging.WARN,
                    "error": logging.ERROR,
                    "fatal": logging.FATAL}[level], log_string)
