# client.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details
"""Clients for the KAT device control language."""

from __future__ import division, print_function, absolute_import

import sys
import traceback
import logging

import tornado.ioloop
import tornado.tcpclient
import tornado.iostream

from functools import partial, wraps
from thread import get_ident as get_thread_ident

from tornado import gen
from tornado.concurrent import Future as tornado_Future
from tornado.util import ObjectDict
from concurrent.futures import Future, TimeoutError

from .core import (DeviceMetaclass, MessageParser, Message,
                   KatcpClientError, KatcpVersionError, KatcpClientDisconnected,
                   ProtocolFlags, AsyncEvent, until_later, LatencyTimer,
                   SEC_TS_KATCP_MAJOR, FLOAT_TS_KATCP_MAJOR, SEC_TO_MS_FAC)
from .ioloop_manager import IOLoopManager


# logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("katcp.client")

def address_to_string(addr_tuple):
    return ":".join(str(part) for part in addr_tuple)


def make_threadsafe(meth):
    """Decorator for a DeviceClient method that should always run in ioloop.

    Used with DeviceClient.enable_thread_safety(). If not called the method
    will be unprotected and it is the user's responsibility to ensure that
    these methods are only called from the ioloop, otherwise the decorated
    methods are wrapped. Should only be used for functions that have no return
    value.

    """
    meth.make_threadsafe = True
    return meth


def make_threadsafe_blocking(meth):
    """Decorator for a DeviceClient method that will block.

    Used with DeviceClient.enable_thread_safety(). Used to provide blocking
    calls that can be made from other threads. If called in ioloop context,
    calls the original method directly to prevent deadlocks. Will route return
    value to caller. Add `timeout` keyword argument to limit blocking time.
    If `meth` returns a future, its result will be returned, otherwise its
    result will be passed back directly.

    """
    meth.make_threadsafe_blocking = True
    return meth


class DeviceClient(object):
    """Device client proxy.

    Subclasses should implement .reply\_*, .inform\_* and
    send_request\_* methods to take actions when messages arrive,
    and implement unhandled_inform, unhandled_reply and
    unhandled_request to provide fallbacks for messages for
    which there is no handler.

    Request messages can be sent by calling .send_request().

    Parameters
    ----------
    host : string
        Host to connect to.
    port : int
        Port to connect to.
    tb_limit : int
        Maximum number of stack frames to send in error traceback.
    logger : object
        Python Logger object to log to.
    auto_reconnect : bool
        Whether to automatically reconnect if the connection dies.

    Examples
    --------
    >>> MyClient(DeviceClient):
    ...     def reply_myreq(self, msg):
    ...         print str(msg)
    ...
    >>> c = MyClient('localhost', 10000)
    >>> c.start()
    >>> c.send_request(katcp.Message.request('myreq'))
    >>> # expect reply to be printed here
    >>> # stop the client once we're finished with it
    >>> c.stop()
    >>> c.join()

    Notes
    -----

    The client may block its ioloop if the default blocking tornado DNS
    resolver is used. When an ioloop is shared, it would make sens to configure
    one of the non-blocking resolver classes, see
    http://tornado.readthedocs.org/en/latest/netutil.html

    """
    __metaclass__ = DeviceMetaclass

    MAX_MSG_SIZE = 2*1024*1024
    """Maximum message size that can be received in bytes.

    If more than MAX_MSG_SIZE bytes are read from the socket without
    encountering a message terminator (i.e. newline), the connection is closed.

    """
    MAX_WRITE_BUFFER_SIZE = 2*MAX_MSG_SIZE
    """Maximum outstanding bytes to be buffered by the server process.

    If more than MAX_WRITE_BUFFER_SIZE bytes are outstanding, the connection is
    closed. Note that the OS also buffers socket writes, so more than
    MAX_WRITE_BUFFER_SIZE bytes may be untransmitted in total.

    """

    MAX_LOOP_LATENCY = 0.03
    """Do not spend more than this many seconds reading pipelined socket data

    IOStream inline-reading can result in ioloop starvation (see
    https://groups.google.com/forum/#!topic/python-tornado/yJrDAwDR_kA).

    """

    def __init__(self, host, port, tb_limit=20, logger=log,
                 auto_reconnect=True):
        self._parser = MessageParser()
        if not host:
            host = "0.0.0.0"
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._logger = logger
        # Indicate that client is running and ready connect
        self._running = AsyncEvent()
        # Indicate that the client is waiting for a reconnect. Purely for testing.
        self._waiting_to_retry = AsyncEvent()
        # Indicate that client is connected to its server
        self._connected = AsyncEvent()
        # Indicate that the client is disconnected
        self._disconnected = AsyncEvent()
        self._disconnected.set()
        # Indicate that client has received KATCP protocol info from its server
        self._received_protocol_info = AsyncEvent()
        self._auto_reconnect = auto_reconnect
        # Number of seconds to wait before retrying a connection
        self.auto_reconnect_delay = 0.5
        self._connect_failures = 0
        self._server_supports_ids = False
        self._protocol_flags = None
        self._static_protocol_configuration = False
        # Last used unique message id counter
        self._last_msg_id = 0

        self.ioloop = None
        "The Tornado IOloop to use, set by self.set_ioloop()"
        # ID of Thread that hosts the IOLoop.
        # Used to check that we are running in the ioloop.
        self.ioloop_thread_id = None
        self._ioloop_manager = IOLoopManager(managed_default=True)
        # Current iostream instance, set by _connect()
        self._stream = None
        # tornado.tcpclient.TCPClient TCP connection factory, set by _install()
        self._tcp_client = None
        # Indicate thread safety. Managed by self.enable_thread_safety()
        self._threadsafe = False
        # Version information as received from the server
        self.versions = ObjectDict()
        # Last time a connection was made in seconds since Unix Epoch
        self.last_connect_time = None

    @property
    def protocol_flags(self):
        return self._protocol_flags

    @protocol_flags.setter
    def protocol_flags(self, val):
        self._protocol_flags = val
        self._server_supports_ids = self.protocol_flags.supports(
            ProtocolFlags.MESSAGE_IDS)
        self._received_protocol_info.set()

    @property
    def bind_address(self):
        """(host, port) where the client is connecting"""
        return self._bindaddr

    @property
    def bind_address_string(self):
        return address_to_string(self._bindaddr)

    @property
    def sockname(self):
        if self._stream:
            return self._stream.socket.getsockname()
        else:
            return (self._bindaddr[0], -1)

    @property
    def sockname_string(self):
        return address_to_string(self.sockname)

    @property
    def threadsafe(self):
        return self._threadsafe

    def convert_seconds(self, time_seconds):
        """Convert a time in seconds to the device timestamp units.

        KATCP v4 and earlier, specified all timestamps in milliseconds. Since
        KATCP v5, all timestamps are in seconds. If the device KATCP version
        has been detected, this method converts a value in seconds to the
        appropriate (seconds or milliseconds) quantity. For version smaller
        than V4, the time value will be truncated to the nearest millisecond.

        """
        if self.protocol_flags.major >= SEC_TS_KATCP_MAJOR:
            return time_seconds
        else:
            device_time = time_seconds * SEC_TO_MS_FAC
            if self.protocol_flags.major < FLOAT_TS_KATCP_MAJOR:
                device_time = int(device_time)
            return device_time

    def _next_id(self):
        """Return the next available message id."""
        assert get_thread_ident() == self.ioloop_thread_id
        self._last_msg_id += 1
        return str(self._last_msg_id)

    def preset_protocol_flags(self, protocol_flags):
        """Preset server protocol flags.

        Sets the assumed server protocol flags and disables automatic server
        version detection.

        Parameters
        ----------
        protocol_flags : katcp.core.ProtocolFlags instance

        """
        self._static_protocol_configuration = True
        self.protocol_flags = protocol_flags

    def inform_version_connect(self, msg):
        """Process a #version-connect message."""
        if len(msg.arguments) < 2:
            return
        # Store version information.
        name = msg.arguments[0]
        self.versions[name] = tuple(msg.arguments[1:])
        if msg.arguments[0] == "katcp-protocol":
            protocol_flags = ProtocolFlags.parse_version(msg.arguments[1])
            self._set_protocol_from_inform(protocol_flags, msg)

    def inform_version(self, msg):
        """Handle katcp v4 and below version inform."""
        self._set_v4_protocol(msg)

    def inform_build_state(self, msg):
        """Handle katcp v4 and below build-state inform."""
        self._set_v4_protocol(msg)

    def _set_v4_protocol(self, inform):
        # We don't know if the server supports multiple connections (katcp v4
        # has no way of indicating this), but this should not really make any
        # difference to the client
        protocol_flags = ProtocolFlags(4, 0, '')
        self._set_protocol_from_inform(protocol_flags, inform)

    def _set_protocol_from_inform(self, protocol_flags, inform):
        if protocol_flags == self.protocol_flags:
            # New value matches old, no need to do consistency checking
            return
        if self.protocol_flags:
            # It seems that the protocol flags have been set before.
            # Now we need to do some consistency checking.
            if self._static_protocol_configuration:
                # Only warn if a static protocol definition is used
                self._logger.warn(
                    'Protocol Version Warning: Ignoring inform received from '
                    'server indicating a katcp protocol revision inconsistent '
                    'with the static configuration. Static configuration: %r.'
                    'Inform received: %r' % (
                        str(self.protocol_flags), str(inform)))
                return
            else:
                # Log an error and disconnect if we are in auto-detection mode
                self._logger.error(
                    'Protocol Version Error: Inform received from '
                    'server indicating a katcp protocol revision inconsistent '
                    'with the previously detected version. Disconnecting in '
                    'disgust. Previous version: %r. Inform received: %r' % (
                        str(self.protocol_flags), str(inform)))
                # Prevent an infinite loop
                self._auto_reconnect = False
                self._disconnect()

        self.protocol_flags = protocol_flags

    def _get_mid_and_update_msg(self, msg, use_mid):
        """Get message ID for current request and assign to msg.mid if needed.

        Parameters
        ----------
        msg : katcp.Message ?request message
        use_mid : bool or None

        If msg.mid is None, a new message ID will be created. msg.mid will be
        filled with this ID if use_mid is True or if use_mid is None and the
        server supports message ids. If msg.mid is already assigned, it will
        not be touched, and will be used as the active message ID.

        Return value
        ------------
        The active message ID

        """
        if use_mid is None:
            use_mid = self._server_supports_ids

        if msg.mid is None:
            mid = self._next_id()
            if use_mid:
                msg.mid = mid
            # An internal mid may be needed for the request/inform/response
            # machinery to work, so we return it
            return mid
        else:
            return msg.mid

    @make_threadsafe_blocking
    def request(self, msg, use_mid=None):
        """Send a request message, with automatic message ID assignment.

        Parameters
        ----------
        msg : katcp.Message request message
        use_mid : bool or None, default=None

        Returns
        -------
        mid : string or None
            The message id, or None if no msg id is used

        If use_mid is None and the server supports msg ids, or if use_mid is
        True a message ID will automatically be assigned msg.mid is None.

        if msg.mid has a value, and the server supports msg ids, that value
        will be used. If the server does not support msg ids, KatcpVersionError
        will be raised.

        """
        mid = self._get_mid_and_update_msg(msg, use_mid)
        self.send_request(msg)
        return mid

    def send_request(self, msg):
        """Send a request messsage.

        Parameters
        ----------
        msg : Message object
            The request Message to send.

        """
        assert(msg.mtype == Message.REQUEST)
        if msg.mid and not self._server_supports_ids:
            raise KatcpVersionError('Message IDs not supported by server')
        self.send_message(msg)

    @make_threadsafe_blocking
    def send_message(self, msg):
        """Send any kind of message.

        Parameters
        ----------
        msg : Message object
            The message to send.

        """
        assert get_thread_ident() == self.ioloop_thread_id
        data = str(msg) + "\n"
        # Log all sent messages here so no one else has to.
        if self._logger.isEnabledFor(logging.DEBUG):
            self._logger.debug("Sending to {}: {}"
                               .format(self.bind_address_string, repr(data)))
        if not self._connected.isSet():
            raise KatcpClientDisconnected('Not connected to device {0}'.format(
                self.bind_address_string))
        try:
            return self._stream.write(data)
        except Exception:
            self._logger.warn('Could not send message {0!r} to {1!r}'
                              .format(str(msg), self._bindaddr), exc_info=True)
            self._disconnect(exc_info=True)

    @gen.coroutine
    def _connect(self):
        """Connect to the server."""
        assert get_thread_ident() == self.ioloop_thread_id
        if self._stream:
            self._logger.warn('Disconnecting existing connection to {0!r} '
                              'to create a new connection')
            self._disconnect()
            yield self._disconnected.until_set()

        stream = None
        try:
            host, port = self._bindaddr
            stream = self._stream = yield self._tcp_client.connect(
                host, port, max_buffer_size=self.MAX_MSG_SIZE)
            stream.set_close_callback(partial(self._stream_closed_callback,
                                              stream))
            # our message packets are small, don't delay sending them.
            stream.set_nodelay(True)
            stream.max_write_buffer_size = self.MAX_WRITE_BUFFER_SIZE
            self._logger.debug('Connected to {0} with client addr {1}'
                               .format(self.bind_address_string,
                                       address_to_string(stream.socket.getsockname())))
            if self._connect_failures >= 5:
                self._logger.warn("Reconnected to {0}"
                                  .format(self.bind_address_string))
            self._connect_failures = 0
        except Exception, e:
            if self._connect_failures % 5 == 0:
                # warn on every fifth failure

                # TODO (NM 2015-03-04) This can get a bit verbose, and typically we have
                # other mechanisms for tracking failed connections. Consider doing some
                # kind of exponential backoff starting at 5 times the reconnect time up to
                # once per 5 minutes
                self._logger.debug("Failed to connect to {0!r}: {1}"
                                  .format(self._bindaddr, e))
            self._connect_failures += 1
            stream = None
            yield gen.moment
            # TODO some kind of error rate limiting?

            if self._stream:
                # Can't use _disconnect() and wait on self._disconnected, since
                # exception may have been raised before _stream_closed_callback
                # was attached to the iostream.
                self._logger.debug('stream was set even though connecting failed')
                self._stream.close()
                self._disconnected.set()

        if stream:
            self._disconnected.clear()
            self._connected.set()
            self.last_connect_time = self.ioloop.time()
            try:
                self.notify_connected(True)
            except Exception:
                self._logger.exception("Notify connect failed. Disconnecting.")
                self._disconnect()

    @make_threadsafe
    def disconnect(self):
        """Force client connection to close, reconnect if auto-connect set."""
        self._disconnect()

    def _disconnect(self, exc_info=False):
        """Disconnect and cleanup."""
        if self._stream:
            self._stream.close(exc_info=exc_info)
            # Cleanup should be handled by _stream_closed_callback()

    def _stream_closed_callback(self, stream):
        try:
            assert stream is self._stream
            self._stream = None
            self._connected.clear()
            self._disconnected.set()
            error_repr = '{0!r}'.format(stream.error) if stream.error else ''
            if error_repr:
                self._logger.warn('Client stream for server '
                                  '{0} closed with error {1}'
                                  .format(self.bind_address_string, error_repr))
            try:
                self.notify_connected(False)
            except Exception:
                self._logger.exception("Notify connect failed")
        except Exception:
            self._logger.exception('Unhandled exception closing client '
                                   'stream {0!r} to {1}'
                                   .format(stream, self.bind_address_string))

    def handle_message(self, msg):
        """Handle a message from the server.

        Parameters
        ----------
        msg : Message object
            The Message to dispatch to the handler methods.

        """
        # log messages received so that no one else has to
        if self._logger.isEnabledFor(logging.DEBUG):
            self._logger.debug(
                "received from {}: {}"
                .format(self.bind_address_string, repr(str(msg))))

        if msg.mtype == Message.INFORM:
            return self.handle_inform(msg)
        elif msg.mtype == Message.REPLY:
            return self.handle_reply(msg)
        elif msg.mtype == Message.REQUEST:
            return self.handle_request(msg)
        else:
            self._logger.error("Unexpected message type from server ['%s']."
                               % (msg,))

    def handle_inform(self, msg):
        """Dispatch an inform message to the appropriate method.

        Parameters
        ----------
        msg : Message object
            The inform message to dispatch.

        """
        method = self._inform_handlers.get(
            msg.name, self.__class__.unhandled_inform)

        try:
            return method(self, msg)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit))
            self._logger.error("Inform %s FAIL: %s" % (msg.name, reason))

    def handle_reply(self, msg):
        """Dispatch a reply message to the appropriate method.

        Parameters
        ----------
        msg : Message object
            The reply message to dispatch.

        """
        method = self.__class__.unhandled_reply
        if msg.name in self._reply_handlers:
            method = self._reply_handlers[msg.name]

        try:
            return method(self, msg)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit))
            self._logger.error("Reply %s FAIL: %s" % (msg.name, reason))

    def handle_request(self, msg):
        """Dispatch a request message to the appropriate method.

        Parameters
        ----------
        msg : Message object
            The request message to dispatch.

        """
        method = self.__class__.unhandled_request
        if msg.name in self._request_handlers:
            method = self._request_handlers[msg.name]

        try:
            reply = method(self, msg)
            if isinstance(reply, Message):
                # If it is a message object, assume it is a reply.
                reply.mid = msg.mid
                assert (reply.mtype == Message.REPLY)
                assert (reply.name == msg.name)
                self._logger.info("%s OK" % (msg.name,))
                self.send_message(reply)
            else:
                # Just pass on what is, potentially, a future. The implementor
                # of the request handler method must arrange for a reply to be
                # sent. Since clients have no business dealing with requests in
                # any case we don't do much to help them.
                return reply
        # We do want to catch everything that inherits from Exception
        # pylint: disable-msg = W0703
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit))
            self._logger.error("Request %s FAIL: %s" % (msg.name, reason))

    def unhandled_inform(self, msg):
        """Fallback method for inform messages without a registered handler.

        Parameters
        ----------
        msg : Message object
            The inform message that wasn't processed by any handlers.

        """
        pass

    def unhandled_reply(self, msg):
        """Fallback method for reply messages without a registered handler.

        Parameters
        ----------
        msg : Message object
            The reply message that wasn't processed by any handlers.

        """
        pass

    def unhandled_request(self, msg):
        """Fallback method for requests without a registered handler.

        Parameters
        ----------
        msg : Message object
            The request message that wasn't processed by any handlers.

        """
        pass

    @gen.coroutine
    def _install(self):
        ioloop_before = self.ioloop
        # Do stuff to put us on the IOLoop
        self._logger.debug("Starting client loop for {0!r}"
                           .format(self._bindaddr))
        self.ioloop_thread_id = get_thread_ident()
        self._tcp_client = tornado.tcpclient.TCPClient()
        self._running.set()
        yield self._connect()
        if self._stream is None and not self._auto_reconnect:
            raise KatcpClientError("Failed to connect to {0!r}"
                                   .format(self._bindaddr))
        try:
            yield self._connect_loop()
        except Exception:
            self._logger.exception('Unhandled exception in _connect_loop()')
        finally:
            try:
                # Make sure everything is torn down properly
                if ioloop_before == self.ioloop:
                    # But only if we are still using the same ioloop. If the ioloop has
                    # changed that means this client has been stopped and re-started
                    # before we could get back to this finally clause. This would result
                    # in us stopping the new ioloop. D'oh!
                    self.ioloop.add_callback(self.stop)
            except RuntimeError, e:
                if str(e) == 'IOLoop is closing':
                    # Seems the ioloop was stopped already, no worries.
                    self._running.clear()
            self._logger.info("Client connect loop for {0!r} finished."
                              .format(self._bindaddr))

    @gen.coroutine
    def _connect_loop(self):
        while self._running.isSet():
            if self._connected.isSet():
                try:
                    yield self._line_read_loop()
                except Exception:
                    self._logger.exception('Unhandled exception in _reading_loop()')
            elif self._auto_reconnect:
                self.ioloop.add_callback(self._waiting_to_retry.set)
                yield until_later(self.auto_reconnect_delay)
                self._waiting_to_retry.clear()
                yield self._connect()
            else:
                break
            # Allow stream-close handlers etc to run
            # before restarting _line_read_loop
            yield gen.moment

    @gen.coroutine
    def _line_read_loop(self):
        assert get_thread_ident() == self.ioloop_thread_id
        latency_timer = LatencyTimer(self.MAX_LOOP_LATENCY)
        while self._running.isSet():
            try:
                line_fut = self._stream.read_until_regex('\n|\r')
                latency_timer.check_future(line_fut)
                if latency_timer.time_to_yield():
                    yield gen.moment
                line = yield line_fut
            except tornado.iostream.StreamClosedError:
                # Assume that _stream_closed_callback() will handle this case
                break
            except Exception:
                if self._stream:
                    line = ''
                    self._logger.warn('Unhandled Exception while reading from {0}:'
                                      .format(self._bindaddr), exc_info=True)
                    # Prevent potential tight error loops from blocking ioloop
                    self._disconnect()
                    yield gen.moment
                else:
                    self._logger.warn('self._stream object seems to have disappeared.')
                    break
            try:
                line = line.replace("\r", "\n").split("\n")[0]
                msg = self._parser.parse(line) if line else None
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("BAD COMMAND: %s" % (reason,))
            else:
                try:
                    if msg:
                        yield gen.maybe_future(self.handle_message(msg))
                except Exception:
                    self._logger.exception(
                        'Unhandled exception in handle_message() from {0} for '
                        'message {1!r}'.format(self.bind_address_string, str(msg)))

        self._disconnect()

        self._logger.debug('client _line_read_loop() from {0} completed'
                           .format(self.bind_address_string))

    def set_ioloop(self, ioloop=None):
        """Set the tornado.ioloop.IOLoop instance to use.

        This defaults to IOLoop.current(). If set_ioloop() is never called the
        IOLoop is managed: started in a new thread, and will be stopped if
        self.stop() is called.

        Notes
        -----
        Must be called before start() is called

        """
        self._ioloop_manager.set_ioloop(ioloop, managed=False)
        self.ioloop = ioloop

    def enable_thread_safety(self):
        """Enable thread-safety features.

        Must be called before start().

        """
        if self.threadsafe:
            return                        # Already done!
        if self._running.isSet():
            raise RuntimeError('Cannot enable thread safety after start')

        def _getattr(obj, name):
            # use 'is True' so mock objects don't return true for everything
            return getattr(obj, name, False) is True

        for name in dir(self):
            try:
                meth = getattr(self, name)
            except AttributeError:
                # Subclasses may have computed attributes that don't work
                # before they are started, so let's ignore those
                pass
            if not callable(meth):
                continue
            make_threadsafe = _getattr(meth, 'make_threadsafe')
            make_threadsafe_blocking = _getattr(meth, 'make_threadsafe_blocking')

            if make_threadsafe:
                assert not make_threadsafe_blocking
                meth = self._make_threadsafe(meth)
                setattr(self, name, meth)
            elif make_threadsafe_blocking:
                meth = self._make_threadsafe_blocking(meth)
                setattr(self, name, meth)

        self._threadsafe = True

    def _make_threadsafe(self, meth):
        @wraps(meth)
        def wrapped(*args, **kwargs):
            if get_thread_ident() == self.ioloop_thread_id:
                return meth(*args, **kwargs)
            else:
                self.ioloop.add_callback(meth, *args, **kwargs)
        return wrapped

    def _make_threadsafe_blocking(self, meth):
        @wraps(meth)
        def wrapped(*args, **kwargs):
            timeout = kwargs.pop('timeout', 5)
            if get_thread_ident() == self.ioloop_thread_id:
                return meth(*args, **kwargs)
            else:
                f = Future()
                def cb():
                    try:
                        tf = gen.maybe_future(meth(*args, **kwargs))
                    except Exception:
                        tf = tornado_Future()
                        tf.set_exc_info(sys.exc_info())
                    finally:
                        gen.chain_future(tf, f)

                self.ioloop.add_callback(cb)
                return f.result(timeout)
        return wrapped

    def start(self, timeout=None):
        """Start the client in a new thread.

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for client thread to start. Do not specify a
            timeout if start() is being called from the same ioloop that this
            client will be installed on, since it will block the ioloop without
            progressing.

        """
        if self._running.isSet():
            raise RuntimeError("Device client already started.")
        # Make sure we have an ioloop
        self.ioloop = self._ioloop_manager.get_ioloop()
        if timeout:
            t0 = self.ioloop.time()
        self._ioloop_manager.start(timeout)
        self.ioloop.add_callback(self._install)
        if timeout:
            remaining_timeout = timeout - (self.ioloop.time() - t0)
            self.wait_running(remaining_timeout)

    def join(self, timeout=None):
        """Rejoin the client thread.

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for thread to finish.

        Notes
        -----
        Does nothing if the ioloop is not managed.

        """
        self._ioloop_manager.join(timeout)

    def stop(self, timeout=None):
        """Stop a running client (from another thread).

        Parameters
        ----------
        timeout : float in seconds
           Seconds to wait for client thread to have *started*.

        """
        ioloop = getattr(self, 'ioloop', None)
        if not ioloop:
            raise RuntimeError('Call start() before stop()')
        if timeout:
            if get_thread_ident() == self.ioloop_thread_id:
                raise RuntimeError('Cannot block inside ioloop')
            self._running.wait_with_ioloop(self.ioloop, timeout)

        def _cleanup():
            self._running.clear()
            self._disconnect()
        self._ioloop_manager.stop(timeout=timeout, callback=_cleanup)

    def running(self):
        """Whether the client is running.

        Returns
        -------
        running : bool
            Whether the client is running.

        """
        return self._running.isSet()

    def until_running(self, timeout=None):
        """Return future that resolves when the client is running.

        Notes
        -----

        Must be called from the same ioloop as the client.

        """
        return self._running.until_set(timeout=timeout)

    def wait_running(self, timeout=None):
        """Wait until the client is running.

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for the client to start running.

        Returns
        -------
        running : bool
            Whether the client is running

        Notes
        -----
        Do not call this from the ioloop, use until_running().

        """
        ioloop = getattr(self, 'ioloop', None)
        if not ioloop:
            raise RuntimeError('Call start() before wait_running()')
        return self._running.wait_with_ioloop(ioloop, timeout)

    def is_connected(self):
        """Check if the socket is currently connected.

        Returns
        -------
        connected : bool
            Whether the client is connected.

        """
        return self._connected.isSet()

    @tornado.gen.coroutine
    def until_connected(self, timeout=None):
        """Return future that resolves when the client is connected."""
        t0 = self.ioloop.time()
        yield self.until_running(timeout=timeout)
        t1 = self.ioloop.time()
        if timeout:
            timedelta = timeout - (t1 - t0)
        else:
            timedelta = None
        assert get_thread_ident() == self.ioloop_thread_id
        yield self._connected.until_set(timeout=timedelta)

    def wait_connected(self, timeout=None):
        """Wait until the client is connected.

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for the client to connect.

        Returns
        -------
        connected : bool
            Whether the client is connected.

        Notes
        -----
        Do not call this from the ioloop, use until_connected().

        """
        return self._connected.wait_with_ioloop(self.ioloop, timeout)

    @tornado.gen.coroutine
    def until_protocol(self, timeout=None):
        """Return future that resolves after receipt of katcp protocol info.

        If the returned future resolves, the server's protocol information is
        available in the ProtocolFlags instance self.protocol_flags.

        """
        t0 = self.ioloop.time()
        yield self.until_running(timeout=timeout)
        t1 = self.ioloop.time()
        if timeout:
            timedelta = timeout - (t1 - t0)
        else:
            timedelta = None
        assert get_thread_ident() == self.ioloop_thread_id
        yield self._received_protocol_info.until_set(timeout=timedelta)

    def wait_protocol(self, timeout=None):
        """Wait until katcp protocol information has been received from server.

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for the client to connect.

        Returns
        -------
        received : bool
            Whether protocol information was received

        If this method returns True, the server's protocol information is
        available in the ProtocolFlags instance self.protocol_flags.

        Notes
        -----
        Do not call this from the ioloop, use until_protocol().

        """
        return self._received_protocol_info.wait_with_ioloop(self.ioloop, timeout)

    def notify_connected(self, connected):
        """Event handler that is called whenever the connection status changes.

        Override in derived class for desired behaviour.

        .. note::

           This function should never block. Doing so will cause the client to
           cease processing data from the server until notify_connected
           completes.

        Parameters
        ----------
        connected : bool
            Whether the client has just connected (True) or just disconnected
            (False).

        """
        pass


class AsyncClient(DeviceClient):
    """Implement async and callback-based requests on top of DeviceClient.

    This client will use message IDs if the server supports them.

    Parameters
    ----------
    host : string
        Host to connect to.
    port : int
        Port to connect to.
    tb_limit : int, optional
        Maximum number of stack frames to send in error traceback.
    logger : object, optional
        Python Logger object to log to. Default is a logger named 'katcp'.
    auto_reconnect : bool, optional
        Whether to automatically reconnect if the connection dies.
    timeout : float in seconds, optional
        Default number of seconds to wait before a callback callback_request
        times out. Can be overridden in individual calls to callback_request.

    Examples
    --------
    >>> def reply_cb(msg):
    ...     print "Reply:", msg
    ...
    >>> def inform_cb(msg):
    ...     print "Inform:", msg
    ...
    >>> c = AsyncClient('localhost', 10000)
    >>> c.start()
    >>> c.ioloop.add_callback(
    ...     c.callback_request,
    ...     katcp.Message.request('myreq'),
    ...     reply_cb=reply_cb,
    ...     inform_cb=inform_cb,
    ... )
    ...
    >>> # expect reply to be printed here
    >>> # stop the client once we're finished with it
    >>> c.stop()
    >>> c.join()
    """

    def __init__(self, host, port, tb_limit=20, timeout=5.0, logger=log,
                 auto_reconnect=True):
        super(AsyncClient, self).__init__(host, port, tb_limit=tb_limit,
                                          logger=logger,
                                          auto_reconnect=auto_reconnect)

        self._request_timeout = timeout
        self._reset_async_requests()

    def _reset_async_requests(self):
        """Initialize / clear out async request structures.

        Good for a clean start after disconnection.

        """
        # pending requests
        # msg_id -> (request, reply_cb, inform_cb, user_data, timeout_handle)
        #           callback tuples
        self._async_queue = {}

        # stack mapping request names to a stack of message ids
        # msg_name -> [ list of msg_ids ]
        self._async_id_stack = {}

    def _push_async_request(self, msg_id, request, reply_cb, inform_cb,
                            user_data, timeout_handle):
        """Store reply / inform callbacks for request we've sent."""
        assert get_thread_ident() == self.ioloop_thread_id
        self._async_queue[msg_id] = (
            request, reply_cb, inform_cb, user_data, timeout_handle)
        if request.name in self._async_id_stack:
            self._async_id_stack[request.name].append(msg_id)
        else:
            self._async_id_stack[request.name] = [msg_id]

    def _pop_async_request(self, msg_id, msg_name):
        """Pop the set of callbacks for a request.

        Return tuple of Nones if callbacks already popped (or don't exist).

        """
        assert get_thread_ident() == self.ioloop_thread_id
        if msg_id is None:
            msg_id = self._msg_id_for_name(msg_name)
        if msg_id in self._async_queue:
            callback_tuple = self._async_queue[msg_id]
            del self._async_queue[msg_id]
            self._async_id_stack[callback_tuple[0].name].remove(msg_id)
            return callback_tuple
        else:
            return None, None, None, None, None

    def _peek_async_request(self, msg_id, msg_name):
        """Peek at the set of callbacks for a request.

        Return tuple of Nones if callbacks don't exist.

        """
        assert get_thread_ident() == self.ioloop_thread_id
        if msg_id is None:
            msg_id = self._msg_id_for_name(msg_name)
        if msg_id in self._async_queue:
            return self._async_queue[msg_id]
        else:
            return None, None, None, None, None

    def _msg_id_for_name(self, msg_name):
        """Find the msg_id for a given request name.

        Return None if no message id exists.

        """
        if msg_name in self._async_id_stack and self._async_id_stack[msg_name]:
            return self._async_id_stack[msg_name][0]

    @make_threadsafe
    def callback_request(self, msg, reply_cb=None, inform_cb=None,
                         user_data=None, timeout=None, use_mid=None):
        """Send a request messsage.

        Parameters
        ----------
        msg : Message object
            The request message to send.
        reply_cb : function
            The reply callback with signature reply_cb(msg)
            or reply_cb(msg, \*user_data)
        inform_cb : function
            The inform callback with signature inform_cb(msg)
            or inform_cb(msg, \*user_data)
        user_data : tuple
            Optional user data to send to the reply and inform
            callbacks.
        timeout : float in seconds
            How long to wait for a reply. The default is the
            the timeout set when creating the AsyncClient.
        use_mid : boolean, optional
            Whether to use message IDs. Default is to use message IDs
            if the server supports them.

        """
        if timeout is None:
            timeout = self._request_timeout

        mid = self._get_mid_and_update_msg(msg, use_mid)

        if timeout is None:  # deal with 'no timeout', i.e. None
            timeout_handle = None
        else:
            timeout_handle = self.ioloop.call_later(
                timeout, partial(self._handle_timeout, mid, self.ioloop.time()))

        self._push_async_request(
            mid, msg, reply_cb, inform_cb, user_data, timeout_handle)

        try:
            self.send_request(msg)
        except KatcpClientError, e:
            error_reply = Message.request(msg.name, "fail", str(e))
            error_reply.mid = mid
            self.handle_reply(error_reply)

    def future_request(self, msg, timeout=None, use_mid=None):
        """Send a request messsage, with future replies.

        Parameters
        ----------
        msg : Message object
            The request Message to send.
        timeout : float in seconds
            How long to wait for a reply. The default is the
            the timeout set when creating the AsyncClient.
        use_mid : boolean, optional
            Whether to use message IDs. Default is to use message IDs
            if the server supports them.

        Returns
        -------
        A tornado.concurrent.Future that resolves with:

        reply : Message object
            The reply message received.
        informs : list of Message objects
            A list of the inform messages received.

        """
        if timeout is None:
            timeout = self._request_timeout

        f = tornado_Future()
        informs = []

        def reply_cb(msg):
            f.set_result((msg, informs))

        def inform_cb(msg):
            informs.append(msg)

        try:
            self.callback_request(msg, reply_cb=reply_cb, inform_cb=inform_cb,
                                  timeout=timeout, use_mid=use_mid)
        except Exception:
            f.set_exc_info(sys.exc_info())
        return f

    def blocking_request(self, msg, timeout=None, use_mid=None):
        """Send a request messsage and wait for its reply.

        Parameters
        ----------
        msg : Message object
            The request Message to send.
        timeout : float in seconds
            How long to wait for a reply. The default is the
            the timeout set when creating the AsyncClient.
        use_mid : boolean, optional
            Whether to use message IDs. Default is to use message IDs
            if the server supports them.

        Returns
        -------
        reply : Message object
            The reply message received.
        informs : list of Message objects
            A list of the inform messages received.

        """
        assert (get_thread_ident() != self.ioloop_thread_id), (
            'Cannot call blocking_request() in ioloop')
        if timeout is None:
            timeout = self._request_timeout

        f = Future()  # for thread safety
        tf = [None]   # Placeholder for tornado Future for exception tracebacks
        def blocking_request_callback():
            try:
                tf[0] = frf = self.future_request(msg, timeout=timeout,
                                                  use_mid=use_mid)
            except Exception:
                tf[0] = frf = tornado_Future()
                frf.set_exc_info(sys.exc_info())
            gen.chain_future(frf, f)

        self.ioloop.add_callback(blocking_request_callback)

        # We wait on the future result that should be set by the reply
        # handler callback. If this does not occur within the
        # timeout it means something unexpected went wrong. We give it
        # an extra second to deal with (unlikely?) slowness in the
        # rest of the code.
        extra_wait = 1
        wait_timeout = timeout
        if wait_timeout is not None:
            wait_timeout = wait_timeout + extra_wait
        try:
            return f.result(timeout=wait_timeout)
        except TimeoutError:
            raise RuntimeError('Unexpected error: Async request handler did '
                               'not call reply handler within timeout period')
        except Exception:
            # Use the tornado future to give us a usable traceback
            tf[0].result()
            assert False                  # Should not get here

    def handle_inform(self, msg):
        """Handle inform messages related to any current requests.

        Inform messages not related to the current request go up
        to the base class method.

        Parameters
        ----------
        msg : Message object
            The inform message to dispatch.

        """
        # this may also result in inform_cb being None if no
        # inform_cb was passed to the request method.
        if msg.mid is not None:
            _request, _reply_cb, inform_cb, user_data, _timeout_handle = \
                self._peek_async_request(msg.mid, None)
        else:
            request, _reply_cb, inform_cb, user_data, _timeout_handle = \
                self._peek_async_request(None, msg.name)
            if request is not None and request.mid is not None:
                # we sent a mid but this inform doesn't have one
                inform_cb, user_data = None, None

        if inform_cb is None:
            inform_cb = super(AsyncClient, self).handle_inform
            # override user_data since handle_inform takes no user_data
            user_data = None

        try:
            if user_data is None:
                inform_cb(msg)
            else:
                inform_cb(msg, *user_data)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit))
            self._logger.error("Callback inform %s FAIL: %s" %
                               (msg.name, reason))

    def _do_fail_callback(
            self, reason, msg, reply_cb, inform_cb, user_data, timeout_handle):
        """Do callback for a failed request."""
        # this may also result in reply_cb being None if no
        # reply_cb was passed to the request method

        if reply_cb is None:
            # this happens if no reply_cb was passed in to the request
            return

        reason_msg = Message.reply(msg.name, "fail", reason, mid=msg.mid)

        try:
            if user_data is None:
                reply_cb(reason_msg)
            else:
                reply_cb(reason_msg, *user_data)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            exc_reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit))
            self._logger.error("Callback reply during failure %s, %s FAIL: %s" %
                               (reason, msg.name, exc_reason))

    def _handle_timeout(self, msg_id, start_time):
        """Handle a timed-out callback request.

        Parameters
        ----------
        msg_id : uuid.UUID for message
            The name of the reply which was expected.

        """
        msg, reply_cb, inform_cb, user_data, timeout_handle = \
            self._pop_async_request(msg_id, None)
        # We may have been racing with the actual reply handler if the reply
        # arrived close to the timeout expiry,
        # which means the self._pop_async_request() call gave us None's.
        # In this case, just bail.
        #
        # NM 2014-09-17 Not sure if this is true after porting to tornado,
        # but I'm too afraid to remove this code :-/
        if timeout_handle is None:
            return

        reason = "Request {0.name} timed out after {1:f} seconds.".format(
            msg, self.ioloop.time() - start_time)
        self._do_fail_callback(
            reason, msg, reply_cb, inform_cb, user_data, timeout_handle)

    def handle_reply(self, msg):
        """Handle a reply message related to the current request.

        Reply messages not related to the current request go up
        to the base class method.

        Parameters
        ----------
        msg : Message object
            The reply message to dispatch.

        """
        # this may also result in reply_cb being None if no
        # reply_cb was passed to the request method
        if msg.mid is not None:
            _request, reply_cb, _inform_cb, user_data, timeout_handle = \
                self._pop_async_request(msg.mid, None)
        else:
            request, _reply_cb, _inform_cb, _user_data, timeout_handle = \
                self._peek_async_request(None, msg.name)
            if request is not None and request.mid is None:
                # we didn't send a mid so this is the request we want
                _request, reply_cb, _inform_cb, user_data, timeout_handle = \
                    self._pop_async_request(None, msg.name)
            else:
                reply_cb, user_data = None, None

        if timeout_handle is not None:
            self.ioloop.remove_timeout(timeout_handle)

        if reply_cb is None:
            reply_cb = super(AsyncClient, self).handle_reply
            # override user_data since handle_reply takes no user_data
            user_data = None

        try:
            if user_data is None:
                reply_cb(msg)
            else:
                reply_cb(msg, *user_data)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit))
            self._logger.error("Callback reply %s FAIL: %s" %
                               (msg.name, reason))

    def stop(self, *args, **kwargs):
        self.ioloop.add_callback(self._fail_waiting_requests,
                                 'Client stopped before reply was received')
        super(AsyncClient, self).stop(*args, **kwargs)

    def _fail_waiting_requests(self, reason):
        # Fail all requests that have not yet received their replies
        for request_data in self._async_queue.values():
            # Last one should be timeout handle
            timeout_handle = request_data[-1]
            if timeout_handle is not None:
                self.ioloop.remove_timeout(timeout_handle)
            # Do add_callback to prevent callback functions from scheduling
            # new requests before we call _reset_async_requests()
            self.ioloop.add_callback(self._do_fail_callback, reason,
                                     *request_data)

        # Clear out the async data structures
        self._reset_async_requests()

    def _stream_closed_callback(self, stream):
        try:
            super(AsyncClient, self)._stream_closed_callback(stream)
            self._fail_waiting_requests('Connection closed before reply was received')
        except Exception:
            self._logger.exception('Unhandled exception closing client '
                                   'stream {0!r} to {1}'
                                   .format(stream, self.bind_address_string))


def request_check(client, exception, *msg_parms, **kwargs):
    """Make blocking request to client and raise exception if reply is not ok.

    Parameters
    ----------
    client : DeviceClient instance
    exception: Exception class to raise
    *msg_parms : Message parameters sent to the Message.request() call
    **kwargs : Keyword arguments
        Forwards kwargs['timeout'] to client.blocking_request().
        Forwards kwargs['mid'] to Message.request().

    Returns
    -------
    reply, informs : as returned by client.blocking_request

    Raises
    ------
    *exception* passed as parameter is raised if reply.reply_ok() is False

    Notes
    -----
    A typical use-case for this function is to use functools.partial() to bind
    a particular client and exception. The resulting function can then be used
    instead of direct client.blocking_request() calls to automate error
    handling.

    """
    timeout = kwargs.get('timeout', None)
    req_msg = Message.request(*msg_parms)
    if timeout is not None:
        reply, informs = client.blocking_request(req_msg, timeout=timeout)
    else:
        reply, informs = client.blocking_request(req_msg)

    if not reply.reply_ok():
        raise exception('Unexpected failure reply "{2}"\n'
                        ' with device at {0}, request \n"{1}"'
                        .format(client.bind_address_string, req_msg, reply))
    return reply, informs


# Compatibility classes

class CallbackClient(AsyncClient):
    def __init__(self, host, port, tb_limit=20, timeout=5.0, logger=log,
                 auto_reconnect=True):
        super(CallbackClient, self).__init__(host, port, tb_limit=tb_limit,
                                             logger=logger, timeout=timeout,
                                             auto_reconnect=auto_reconnect)
        self.enable_thread_safety()

    def setDaemon(self, daemonic):
        """Set daemonic state of the managed ioloop thread to True / False

        Calling this method for a non-managed ioloop has no effect. Must be called before
        start(), or it will also have no effect
        """
        self._ioloop_manager.setDaemon(daemonic)

class BlockingClient(CallbackClient):
    pass
