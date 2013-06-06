# servers.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Servers for the KAT device control language.
"""

import socket
import errno
import select
import threading
import Queue
import traceback
import logging
import sys
import re
import time
from functools import partial

from .core import (DeviceMetaclass, ExcepthookThread, Message, MessageParser,
                   FailReply, AsyncReply, ProtocolFlags)
from .sampling import SampleReactor, SampleStrategy, SampleNone
from .sampling import format_inform_v5, format_inform_v4
from .core import (SEC_TO_MS_FAC, MS_TO_SEC_FAC, SEC_TS_KATCP_MAJOR,
                   VERSION_CONNECT_KATCP_MAJOR, DEFAULT_KATCP_MAJOR)
from .version import VERSION, VERSION_STR
from .kattypes import (request, return_reply)

log = logging.getLogger("katcp")

def construct_name_filter(pattern):
    """Return a function for filtering sensor names based on a pattern.

    Parameters
    ----------
    pattern : None or str
        If None, returned function matches all names.
        If pattern starts and ends with '/' the text between the slashes
        is used a regular expression to search the names.
        Otherwise the pattern must match the name of the sensor exactly.

    Return
    ------
    exact : bool
        Return True if pattern is expected to matche exactly. Used
        to determine whether no matching sensors constitutes an error.
    filter_func : f(str) -> bool
        Function for determining whether a name matches the pattern.
    """
    if pattern is None:
        return False, lambda name: True
    if pattern.startswith('/') and pattern.endswith('/'):
        name_re = re.compile(pattern[1:-1])
        return False, lambda name: name_re.search(name) is not None
    return True, lambda name: name == pattern

class ClientConnectionTCP(object):
    # XXX TODO We should factor the whole TCP select loop (or future twisted
    # implementation?) out of the server class and into a Connection class that
    # spews katcp messages and ClientConnection* objects onto the katcp
    # server. This will allow us to abstract out the connection and allow us to
    # support serial connections cleanly
    def __init__(self, server, raw_socket):
        self.inform = partial(server.tcp_inform, raw_socket)
        self.inform.__doc__ = (
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
    """)
        self.reply_inform = partial(server.tcp_reply_inform, raw_socket)
        self.reply_inform.__doc__ = (
"""Send an inform as part of the reply to an earlier request.

Parameters
----------
inform : Message object
    The inform message to send.
orig_req : Message object
    The request message being replied to. The inform message's
    id is overridden with the id from orig_req before the
    inform is sent.
""")
        self.reply = partial(server.tcp_reply, raw_socket)
        self.reply.__doc__ = (
"""Send an asynchronous reply to an earlier request.

Parameters
----------
reply : Message object
    The reply message to send.
orig_req : Message object
    The request message being replied to. The reply message's
    id is overridden with the id from orig_req before the
    reply is sent.
""")


class ClientRequestConnection(object):
    def __init__(self, client_connection, req_msg):
        self.client_connection = client_connection
        assert(req_msg.mtype == Message.REQUEST)
        self.msg = req_msg

    def inform(self, *args):
        inf_msg = Message.reply_inform(self.msg, *args)
        self.client_connection.inform(inf_msg)

    def reply(self, *args):
        rep_msg = Message.reply_to_request(self.msg, *args)
        self.client_connection.reply(rep_msg, self.msg)
        self._post_reply()

    def reply_with_message(self, rep_msg):
        """
        Send a pre-created reply message to the client connection

        Will check that rep_msg.name matches the bound request
        """
        assert rep_msg.name == self.msg.name
        self.client_connection.reply(rep_msg, self.msg)
        self._post_reply()

    def _post_reply(self):
        # Future calls to reply_*() should error out.
        self.reply = self.reply_again
        self.reply_with_message = self.reply_again

    def reply_again(self, *args):
        raise RuntimeError('Reply to request %r already sent.' % self.msg)

    def make_reply(self, *args):
        return Message.reply_to_request(self.msg, *args)

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
    tb_limit : int
        Maximum number of stack frames to send in error tracebacks.
    logger : logging.Logger object
        Logger to log messages to.
    """

    __metaclass__ = DeviceMetaclass
    MAX_DEFERRED_QUEUE_SIZE = 100000      # Maximum size of deferred action queue

    ## @brief Protocol versions and flags. Default to version 5, subclasses
    ## should override PROTOCOL_INFO
    PROTOCOL_INFO = ProtocolFlags(DEFAULT_KATCP_MAJOR, 0, set([
        ProtocolFlags.MULTI_CLIENT,
        ProtocolFlags.MESSAGE_IDS,
        ]))


    def __init__(self, host, port, tb_limit=20, logger=log):
        self._parser = MessageParser()
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._running = threading.Event()
        self._sock = None
        self._thread = None
        self._logger = logger
        self._deferred_queue = Queue.Queue(maxsize=self.MAX_DEFERRED_QUEUE_SIZE)
        self.send_timeout = 5 # Timeout to catch spinning sends

        # sockets and data
        self._data_lock = threading.Lock()
        self._socks = []  # list of client sockets
        self._waiting_chunks = {}  # map from client sockets to messages pieces
        self._sock_locks = {}  # map from client sockets to sending locks
        # map from sockets to ClientConnectionTCP objects
        self._sock_connections = {}

    def _log_msg(self, level_name, msg, name, timestamp=None):
        """Create a katcp logging inform message.

           Usually this will be called from inside a DeviceLogger object,
           but it is also used by the methods in this class when errors
           need to be reported to the client.
           """

        if timestamp is None:
            timestamp = time.time()

        katcp_version = self.PROTOCOL_INFO.major
        timestamp_msg = ('%.6f' % timestamp if katcp_version >= SEC_TS_KATCP_MAJOR
                         else str(int(timestamp*1000)) )
        return Message.inform("log",
                level_name,
                timestamp_msg,  # time since epoch in seconds
                name,
                msg,
        )

    def _bind(self, bindaddr):
        """Create a listening server socket."""
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)
        if hasattr(socket, 'TCP_NODELAY'):
            # our message packets are small, don't delay sending them.
            sock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
        try:
            sock.bind(bindaddr)
        except Exception, e:
            self._logger.exception("Unable to bind to %s" % str(bindaddr))
            raise
        sock.listen(5)
        return sock

    def _add_socket(self, sock):
        """Add a client socket to the socket and chunk lists."""
        with self._data_lock:
            self._socks.append(sock)
            self._waiting_chunks[sock] = ""
            self._sock_locks[sock] = threading.Lock()
            self._sock_connections[sock] = ClientConnectionTCP(self, sock)

    def _remove_socket(self, sock):
        """Remove a client socket from the socket and chunk lists."""
        sock.close()
        self._data_lock.acquire()
        try:
            if sock in self._socks:
                self._socks.remove(sock)
                del self._waiting_chunks[sock]
                del self._sock_locks[sock]
                del self._sock_connections[sock]
        finally:
            self._data_lock.release()

    def get_sockets(self):
        """Return the complete list of current client socket.

        Returns
        -------
        sockets : list of socket.socket objects
            A list of connected client sockets.
        """
        return list(self._socks)

    def _handle_chunk(self, sock, chunk):
        """Handle a chunk of data for socket sock."""
        chunk = chunk.replace("\r", "\n")
        lines = chunk.split("\n")

        waiting_chunk = self._waiting_chunks.get(sock, "")

        for line in lines[:-1]:
            full_line = waiting_chunk + line
            waiting_chunk = ""

            if full_line:
                try:
                    msg = self._parser.parse(full_line)
                # We do want to catch everything that inherits from Exception
                # pylint: disable-msg = W0703
                except Exception:
                    e_type, e_value, trace = sys.exc_info()
                    reason = "\n".join(traceback.format_exception(
                        e_type, e_value, trace, self._tb_limit))
                    self._logger.error("BAD COMMAND: %s in line %r" % (
                        reason, full_line))
                    self.tcp_inform(sock, self._log_msg("error", reason, "root"))
                else:
                    try:
                        client_conn = self._sock_connections[sock]
                    except KeyError:
                        self._logger.warn(
                        'Client disconnected while handling received message: %r'
                        % sock)
                    else:
                        self.handle_message(client_conn, msg)

        with self._data_lock:
            if sock in self._waiting_chunks:
                self._waiting_chunks[sock] = waiting_chunk + lines[-1]

    def handle_message(self, client_conn, msg):
        """Handle messages of all types from clients.

        Parameters
        ----------
        client_conn : ClientConnectionTCP object
            The client connection the message was from.
        msg : Message object
            The message to process.
        """
        # log messages received so that no one else has to
        self._logger.debug(msg)

        if msg.mtype == msg.REQUEST:
            self.handle_request(client_conn, msg)
        elif msg.mtype == msg.INFORM:
            self.handle_inform(client_conn, msg)
        elif msg.mtype == msg.REPLY:
            self.handle_reply(client_conn, msg)
        else:
            reason = "Unexpected message type received by server ['%s']." \
                     % (msg,)
            client_conn.inform(self._log_msg("error", reason, "root"))

    def handle_request(self, connection, msg):
        """Dispatch a request message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client connection the message was from.
        msg : Message object
            The request message to process.
        """
        send_reply = True
        if msg.name in self._request_handlers:
            req_conn = ClientRequestConnection(connection, msg)
            try:
                reply = self._request_handlers[msg.name](self, req_conn, msg)
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
            # We do want to catch everything that inherits from Exception
            # pylint: disable-msg = W0703
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("Request %s FAIL: %s" % (msg.name, reason))
                reply = Message.reply(msg.name, "fail", reason)
        else:
            self._logger.error("%s INVALID: Unknown request." % (msg.name,))
            reply = Message.reply(msg.name, "invalid", "Unknown request.")

        if send_reply:
            connection.reply(reply, msg)

    def handle_inform(self, connection, msg):
        """Dispatch an inform message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnectionTCP object
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
        connection : ClientConnectionTCP object
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

    def _send_message(self, sock, msg):
        """Send an arbitrary message to a particular client.

        Note that failed sends disconnect the client sock and call
        on_client_disconnect. They do not raise exceptions.

        Parameters
        ----------
        sock : socket.socket object
            The socket to send the message to.
        msg : Message object
            The message to send.
        """
        # TODO: should probably implement this as a queue of sockets and
        #       messages to send and have the queue processed in the main
        #       loop.
        data = str(msg) + "\n"
        datalen = len(data)
        totalsent = 0

        # Log all sent messages here so no one else has to.
        self._logger.debug(data)

        # sends are locked per-socket -- i.e. only one send per socket at
        # a time
        lock = self._sock_locks.get(sock)
        if lock is None:
            try:
                client_name = sock.getpeername()
            except socket.error:
                client_name = "<disconnected client>"
            msg = "Attempt to send to a socket %s which is no longer a" \
                " client." % (client_name,)
            self._logger.warn(msg)
            return

        # do not do anything inside here which could call send_message!
        send_failed = False
        lock.acquire()
        t0 = time.time()
        try:
            while totalsent < datalen:
                if time.time()-t0 > self.send_timeout:
                    self._logger.error(
                        'server._send_msg() timing out after %fs, sent %d/%d bytes'
                        % (self.send_timeout, totalsent, datalen) )
                    send_failed = True
                    break
                try:
                    sent = sock.send(data[totalsent:])
                except socket.error, e:
                    if len(e.args) == 2 and e.args[0] == errno.EAGAIN:
                        continue
                    else:
                        send_failed = True
                        break

                if sent == 0:
                    send_failed = True
                    break

                totalsent += sent
        finally:
            lock.release()

        if send_failed:
            try:
                client_name = sock.getpeername()
            except socket.error:
                client_name = "<disconnected client>"
            msg = "Failed to send message to client %s" % (client_name,)
            self._logger.error(msg)
            # Need to get connection before calling _remove_socket()
            conn = self._sock_connections.get(sock)
            self._remove_socket(sock)
            # Don't run on_client_disconnect if another thread has beaten us to
            # the punch of removing the connection object
            if conn:
                self.on_client_disconnect(conn, msg, False)

    def inform(self, connection, msg):
        """Send an inform message to a particular client.

        Should only be used for asynchronous informs. Informs
        that are part of the response to a request should use
        :meth:`reply_inform` so that the message identifier
        from the original request can be attached to the
        inform.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client to send the message to.
        msg : Message object
            The inform message to send.
        """
        if isinstance(connection, ClientRequestConnection):
            self._logger.warn(
                'Deprecation warning: do not use self.inform() '
                'within a reply handler context -- use conn.reply()\n'
                'Traceback:\n %s', "".join(traceback.format_stack() ))
            # Get the underlying ClientConnectionTCP instance
            connection = connection.client_connection
        connection.inform(msg)

    def tcp_inform(self, sock, msg):
        """Send an inform message to a particular TCP client.

        Should only be used for asynchronous informs. Informs
        that are part of the response to a request should use
        :meth:`reply_inform` so that the message identifier
        from the original request can be attached to the
        inform.

        Parameters
        ----------
        sock : socket.socket object
            The client to send the message to.
        msg : Message object
            The inform message to send.
        """
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        assert (msg.mtype == Message.INFORM)
        self._send_message(sock, msg)

    def mass_inform(self, msg):
        """Send an inform message to all clients.

        Parameters
        ----------
        msg : Message object
            The inform message to send.
        """
        assert (msg.mtype == Message.INFORM)
        for sock in list(self._socks):
            if sock is self._sock:
                continue
            self.tcp_inform(sock, msg)


    def reply(self, connection, reply, orig_req):
        """Send an asynchronous reply to an earlier request.

        Parameters
        ----------
        connection : ClientConnectionTCP object
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
                'within a reply handler context -- use conn.reply(*msg_args)\n'
                'or conn.reply_with_message(msg) Traceback:\n %s',
                "".join(traceback.format_stack() ))
            # Get the underlying ClientConnectionTCP instance
            connection = connection.client_connection
        connection.reply(reply, orig_req)

    def tcp_reply(self, sock, reply, orig_req):
        """Send an asynchronous reply to an earlier request for a tcp socket.

        Parameters
        ----------
        sock : socket.socket object
            The client to send the reply to.
        reply : Message object
            The reply message to send.
        orig_req : Message object
            The request message being replied to. The reply message's
            id is overridden with the id from orig_req before the
            reply is sent.
        """
        assert (reply.mtype == Message.REPLY)
        reply.mid = orig_req.mid
        self._send_message(sock, reply)

    def reply_inform(self, connection, inform, orig_req):
        """Send an inform as part of the reply to an earlier request.

        Parameters
        ----------
        connection : ClientConnectionTCP object
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
                'use conn.inform(*inform_arguments)\n'
                'Traceback:\n %s', "".join(traceback.format_stack() ))
            # Get the underlying ClientConnectionTCP instance
            connection = connection.client_connection
        connection.reply_inform(inform, orig_req)

    def tcp_reply_inform(self, sock, inform, orig_req):
        """Send an inform as part of the reply to an earlier request.

        Parameters
        ----------
        sock : socket.socket object
            The client to send the inform to.
        inform : Message object
            The inform message to send.
        orig_req : Message object
            The request message being replied to. The inform message's
            id is overridden with the id from orig_req before the
            inform is sent.
        """
        assert (inform.mtype == Message.INFORM)
        inform.mid = orig_req.mid
        self._send_message(sock, inform)

    def _process_deferred_queue(self, max_items=0):
        """
        Process deferred queue

        Items in self._deferred_queue are assumed to be functions that
        are to be called once.

        Parameters
        ==========

        max_items: int
            Maximum number of items to process, 0 if the whole
            queue is to be processed
        """
        processed = 0
        while max_items == 0 or processed < max_items:
            try:
                task = self._deferred_queue.get_nowait()
            except Queue.Empty:
                break
            task()                        # Execute the task
            self._deferred_queue.task_done()   # tell the queue that it is done
            processed = processed + 1

    def run(self):
        """Listen for clients and process their requests."""
        timeout = 0.5  # s

        # save globals so that the thread can run cleanly
        # even while Python is setting module globals to
        # None.
        _select = select.select
        _socket_error = socket.error

        self._sock = self._bind(self._bindaddr)
        # replace bindaddr with real address so we can rebind
        # to the same port.
        self._bindaddr = self._sock.getsockname()

        self._running.set()
        while self._running.isSet():
            self._process_deferred_queue()
            all_socks = self._socks + [self._sock]
            try:
                readers, _writers, errors = _select(
                    all_socks, [], all_socks, timeout)
            except Exception, e:
                # catch Exception because class of exception thrown
                # varies drastically between Mac and Linux
                self._logger.debug("Select error: %s" % (e,))

                # search for broken socket
                for sock in list(self._socks):
                    try:
                        _readers, _writers, _errors = _select([sock], [], [],
                                                              0)
                    except Exception, e:
                        # Need to get connection before calling _remove_socket()
                        conn = self._sock_connections[sock]
                        self._remove_socket(sock)
                        self.on_client_disconnect(
                            conn, "Client socket died" " with error %s" % (e,),
                            False)
                # check server socket
                try:
                    _readers, _writers, _errors = _select([self._sock], [], [],
                                                          0)
                except:
                    self._logger.warn("Server socket died, attempting to"
                                      " restart it.")
                    self._sock = self._bind(self._bindaddr)
                # try select again
                continue

            for sock in errors:
                if sock is self._sock:
                    # server socket died, attempt restart
                    self._sock = self._bind(self._bindaddr)
                else:
                    # client socket died, remove it
                    # Need to get connection before calling _remove_socket()
                    conn = self._sock_connections.get(sock)
                    self._remove_socket(sock)
                    # Don't call on_client_disconnect if the connection has
                    # already been removed in another thread
                    if conn:
                        self.on_client_disconnect(conn, "Client socket died", False)

            for sock in readers:
                if sock is self._sock:
                    client, addr = sock.accept()
                    client.setblocking(0)
                    self.mass_inform(Message.inform("client-connected",
                        "New client connected from %s" % (addr,)))
                    self._add_socket(client)
                    conn = self._sock_connections.get(client)
                    if client:
                        self.on_client_connect(conn)
                    else:
                        self._logger.warn(
                            'Client connection for socket %s dissappeared before '
                            'on_client_connect could be called' % (client,))
                else:
                    try:
                        chunk = sock.recv(4096)
                    except _socket_error:
                        # an error when sock was within ready list presumably
                        # means the client needs to be ditched.
                        chunk = ""
                    if chunk:
                        self._handle_chunk(sock, chunk)
                    else:
                        # no data, assume socket EOF
                        # Need to get connection before calling _remove_socket()
                        conn = self._sock_connections.get(sock)
                        self._remove_socket(sock)
                        # Don't run on_client_disconnect if another thread has
                        # beaten us to the punch of removing the connection
                        # object
                        if conn:
                            self.on_client_disconnect(conn, "Socket EOF", False)

        for sock in list(self._socks):
            conn = self._sock_connections.get(sock)
            if conn:
                self.on_client_disconnect(
                    conn, "Device server shutting down.", True)
            self._process_deferred_queue()
            self._remove_socket(sock)

        self._sock.close()

    def start(self, timeout=None, daemon=None, excepthook=None):
        """Start the server in a new thread.

        Parameters
        ----------
        timeout : float in seconds
            Time to wait for server thread to start.
        daemon : boolean
            If not None, the thread's setDaemon method is called with this
            parameter before the thread is started.
        excepthook : function
            Function to call if the client throws an exception. Signature
            is as for sys.excepthook.
        """
        if self._thread:
            raise RuntimeError("Device server already started.")

        self._thread = ExcepthookThread(target=self.run, excepthook=excepthook)
        if daemon is not None:
            self._thread.setDaemon(daemon)
        self._thread.start()
        if timeout:
            self._running.wait(timeout)
            if not self._running.isSet():
                raise RuntimeError("Device server failed to start.")

    def join(self, timeout=None):
        """Rejoin the server thread.

        Parameters
        ----------
        timeout : float in seconds
            Time to wait for the thread to finish.
        """
        if not self._thread:
            raise RuntimeError("Device server thread not started.")

        self._thread.join(timeout)
        if not self._thread.isAlive():
            self._thread = None

    def stop(self, timeout=1.0):
        """Stop a running server (from another thread).

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for server to have *started*.
        """
        self._running.wait(timeout)
        if not self._running.isSet():
            raise RuntimeError("Attempt to stop server that wasn't running.")
        self._running.clear()

    def running(self):
        """Whether the server is running."""
        return self._running.isSet()

    def wait_running(self, timeout=None):
        """Wait until the server is running"""
        return self._running.wait(timeout=timeout)

    def on_client_connect(self, conn):
        """Called after client connection is established.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point.

        Parameters
        ----------
        conn : ClientConnectionTCP object
            The client connection that has been successfully established.
        """
        pass

    def on_client_disconnect(self, conn, msg, connection_valid):
        """Called before a client connection is closed.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point. The server
        cannot guarantee this will be called (for example, the client
        might drop the connection). The message parameter contains
        the reason for the disconnection.

        Parameters
        ----------
        conn : ClientConnectionTCP object
            Client connection being disconnected.
        msg : str
            Reason client is being disconnected.
        connection_valid : boolean
            True if connection is still open for sending,
            False otherwise.
        """
        pass


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

    SUPPORTED_PROTOCOL_MAJOR_VERSIONS = (4,5)

    ## @var log
    # @brief DeviceLogger instance for sending log messages to the client.

    # * and ** magic fine here
    # pylint: disable-msg = W0142

    def __init__(self, *args, **kwargs):
        if self.PROTOCOL_INFO.major not in self.SUPPORTED_PROTOCOL_MAJOR_VERSIONS:
            raise ValueError(
        'Device server only supports katcp procotol versions %r, not '
        '%r as specified in self.PROTOCOL_INFO' % (
            self.SUPPORTED_PROTOCOL_MAJOR_VERSIONS, self.PROTOCOL_INFO.major) )
        super(DeviceServer, self).__init__(*args, **kwargs)
        self.log = DeviceLogger(self, python_logger=self._logger)
        # map names to (version, build state/serial no.) tuples.
        #   None may used to indicate no build state or serial no.
        self.extra_versions = {}
        self._restart_queue = None
        self._sensors = {}  # map names to sensor objects
        self._reactor = None  # created in run
        # map client sockets to map of sensors -> sampling strategies
        self._strategies = {}
        # strat lock (should be held for updates to _strategies)
        self._strat_lock = threading.Lock()
        self.setup_sensors()

    # pylint: enable-msg = W0142

    def on_client_connect(self, client_conn):
        """Inform client of build state and version on connect.

        Parameters
        ----------
        client_conn : ClientConnectionTCP object
            The client connection that has been successfully established.
        """
        with self._strat_lock:
            self._strategies[client_conn] = {}  # map sensors -> sampling strategies

        katcp_version = self.PROTOCOL_INFO.major
        if katcp_version >= VERSION_CONNECT_KATCP_MAJOR:
            client_conn.inform(Message.inform(
                "version-connect", "katcp-protocol", self.PROTOCOL_INFO))
            client_conn.inform(Message.inform(
                "version-connect", "katcp-library",
                "katcp-python-%s" % VERSION_STR))
            client_conn.inform(Message.inform(
                "version-connect", "katcp-device",
                self.version(), self.build_state() ))
        else:
            client_conn.inform(Message.inform("version", self.version()))
            client_conn.inform(Message.inform("build-state", self.build_state()))

    def clear_strategies(self, client_conn, remove_client=False):
        """
        Clear the sensor strategies of a client connection

        Parameters
        ----------
        client_connection : ClientConnectionTCP instance
            The connection that should have its sampling strategies cleared
        remove_client : bool, default=False
            Remove the client connection from the strategies datastructure.
            Usefull for clients that disconnect.
        """
        with self._strat_lock:
            getter = (self._strategies.pop if remove_client
                      else self._strategies.get)
            strategies = getter(client_conn, None)
            if strategies is not None:
                for sensor, strategy in list(strategies.items()):
                    del strategies[sensor]
                    self._reactor.remove_strategy(strategy)

    def on_client_disconnect(self, client_conn, msg, connection_valid):
        """Inform client it is about to be disconnected.

        Parameters
        ----------
        client_conn : ClientConnectionTCP object
            The client connection being disconnected.
        msg : str
            Reason client is being disconnected.
        connection_valid : boolean
            True if connection is still open for sending,
            False otherwise.
        """

        def remove_strategies():
            self.clear_strategies(client_conn, remove_client=True)
            if connection_valid:
                self.inform(client_conn, Message.inform("disconnect", msg))

        try:
            self._deferred_queue.put_nowait(remove_strategies)
        except Queue.Full:
            self._logger.error(
                'Deferred queue full when trying to add sensor '
                'strategy de-registration task for client %r' % conn)

    def build_state(self):
        """Return a build state string in the form
        name-major.minor[(a|b|rc)n]
        """
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
        """Whether a sensor_name is known."""
        return sensor_name in self._sensors

    def remove_sensor(self, sensor):
        """Remove a sensor from the device.

        Also deregisters all clients observing the sensor.

        Parameters
        ----------
        sensor : Sensor object or name string
            The sensor object (or name of sensor) to remove from the device server.
        """
        if isinstance(sensor, basestring):
            sensor_name = sensor
        else:
            sensor_name = sensor.name
        del self._sensors[sensor_name]

        self._strat_lock.acquire()
        try:
            for strategies in self._strategies.values():
                for other_sensor, strategy in list(strategies.items()):
                    if other_sensor.name == sensor_name:
                        del strategies[other_sensor]
                        self._reactor.remove_strategy(strategy)
        finally:
            self._strat_lock.release()

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
        """Fetch a list of all sensors

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
        raise NotImplementedError("Device server subclasses must implement"
                                    " setup_sensors.")

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
        self.stop()
        # this message makes it through because stop
        # only registers in .run(...) after the reply
        # has been sent.
        return req.make_reply("ok")

    def request_help(self, req, msg):
        """Return help on the available requests.

        Return a description of the available requests using a seqeunce of
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
        # .put should never block because the queue should have no size limit.
        self._restart_queue.put(self)
        # this message makes it through because stop
        # only registers in .run(...) after the reply
        # has been sent.
        return req.make_reply("ok")

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
        clients = self.get_sockets()
        num_clients = len(clients)
        for client in clients:
            try:
                addr = ":".join(str(part) for part in client.getpeername())
            except socket.error:
                # client may be gone, in which case just send a description
                addr = repr(client)
            req.inform(addr)
        return req.make_reply('ok', str(num_clients))

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
            ("katcp-library", ("katcp-python-%d.%d" % VERSION[:2],
                               VERSION_STR)),
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
            timestamp, status, value = sensor.read_formatted()
            if katcp_version <= 4:
                timestamp = int(SEC_TO_MS_FAC*float(timestamp))
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
            from KATCPv4,
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
                                % (strategy, katcp_version) )

            format_inform = (format_inform_v5 if katcp_version >= SEC_TS_KATCP_MAJOR
                             else format_inform_v4)

            def inform_callback(sensor_name, timestamp, status, value):
                """Inform callback for sensor strategy."""
                cb_msg = format_inform(
                sensor_name, timestamp, status, value)
                client.inform(cb_msg)

            if katcp_version < SEC_TS_KATCP_MAJOR and strategy == 'period':
                # Slightly nasty hack, but since period is the only v4 strategy
                # involving timestamps it's not _too_ nasty :)
                params = [float(params[0]) * MS_TO_SEC_FAC] + params[1:]
            new_strategy = SampleStrategy.get_strategy(
                strategy, inform_callback, sensor, *params)

            with self._strat_lock:
                old_strategy = self._strategies[client].get(sensor, None)
                if old_strategy is not None:
                    self._reactor.remove_strategy(old_strategy)

                # todo: replace isinstance check with something better
                if isinstance(new_strategy, SampleNone):
                    if sensor in self._strategies[client]:
                        del self._strategies[client][sensor]
                else:
                    self._strategies[client][sensor] = new_strategy
                    # reactor.add_strategy() sends out an inform
                    # which is not great while the lock is held.
                    self._reactor.add_strategy(new_strategy)

        current_strategy = self._strategies[client].get(sensor, None)
        if not current_strategy:
            current_strategy = SampleStrategy.get_strategy(
                "none", lambda msg: None, sensor)

        strategy, params = current_strategy.get_sampling_formatted()
        if katcp_version < SEC_TS_KATCP_MAJOR and strategy == 'period':
            # Another slightly nasty hack, but since period is the only
            # v4 strategy involving timestamps it's not _too_ nasty :)
            params = [int(float(params[0])* SEC_TO_MS_FAC)] + params[1:]
        return req.make_reply("ok", name, strategy, *params)

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
        self.clear_strategies(req.client_connection)

        return ["ok"]


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

    def run(self):
        """Override DeviceServerBase.run() to ensure that the reactor thread is
           running at the same time.
           """
        self._reactor = SampleReactor()
        self._reactor.start()
        try:
            super(DeviceServer, self).run()
        finally:
            self._reactor.stop()
            self._reactor.join(timeout=0.5)
            self._reactor = None


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

        If the level_name is not known, raise a ValueError.

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

    def set_log_level_by_name(self, level_name):
        """Set the logging level using a level name.

        Parameters
        ----------
        level_name : str
            The name of the logging level.
        """
        self._log_level = self.level_from_name(level_name)

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
            if not timestamp is None:
                python_msg = ' '.join((
                    'katcp timestamp: %r' % timestamp,
                    python_msg))
            self._python_logger.log(self.PYTHON_LEVEL[level], python_msg, *args)
        if level >= self._log_level:
            name = kwargs.get("name")
            if name is None:
                name = self._root_logger_name
            self._device_server.mass_inform(
                self._device_server._log_msg(self.level_name(level),
                                             msg % args, name,
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
