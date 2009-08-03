# client.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Clients for the KAT device control language.
   """

import threading
import socket
import sys
import traceback
import select
import time
import logging
import errno
from .katcp import DeviceMetaclass, MessageParser, Message, ExcepthookThread, \
                   KatcpClientError

#logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("katcp")


class DeviceClient(object):
    """Device client proxy.

    Subclasses should implement .reply\_*, .inform\_* and
    request\_* methods to take actions when messages arrive,
    and implement unhandled_inform, unhandled_reply and
    unhandled_request to provide fallbacks for messages for
    which there is no handler.

    Request messages can be sent by calling .request().

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
    >>> c = MyClient('localhost', 10000)
    >>> c.start()
    >>> c.request(katcp.Message.request('myreq'))
    >>> # expect reply to be printed here
    >>> # stop the client once we're finished with it
    >>> c.stop()
    >>> c.join()
    """

    __metaclass__ = DeviceMetaclass

    def __init__(self, host, port, tb_limit=20, logger=log,
                 auto_reconnect=True):
        self._parser = MessageParser()
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._sock = None
        self._waiting_chunk = ""
        self._running = threading.Event()
        self._connected = threading.Event()
        self._send_lock = threading.Lock()
        self._thread = None
        self._logger = logger
        self._auto_reconnect = auto_reconnect
        self._connect_failures = 0

    def request(self, msg):
        """Send a request messsage.

        Parameters
        ----------
        msg : Message object
            The request Message to send.
        """
        assert(msg.mtype == Message.REQUEST)
        self.send_message(msg)

    def send_message(self, msg):
        """Send any kind of message.

        Parameters
        ----------
        msg : Message object
            The message to send.
        """
        # TODO: should probably implement this as a queue of sockets and messages to send.
        #       and have the queue processed in the main loop
        data = str(msg) + "\n"
        datalen = len(data)
        totalsent = 0
        sock = self._sock

        # Log all sent messages here so no one else has to.
        self._logger.debug(data)

        # do not do anything inside here which could call send_message!
        send_failed = False
        self._send_lock.acquire()
        try:
            if sock is None:
                raise KatcpClientError("Client not connected")

            while totalsent < datalen:
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
            self._send_lock.release()

        if send_failed:
            try:
                server_name = sock.getpeername()
            except socket.error:
                server_name = "<disconnected server>"
            msg = "Failed to send message to server %s (%s)" % (server_name, e)
            self._logger.error(msg)
            self._disconnect()

    def _connect(self):
        """Connect to the server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(self._bindaddr)
            sock.setblocking(0)
            if hasattr(socket, 'TCP_NODELAY'):
                # our message packets are small, don't delay sending them.
                sock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
            if self._connect_failures >= 5:
                self._logger.warn("Reconnected to %r" % (self._bindaddr,))
            self._connect_failures = 0
        except Exception, e:
            self._connect_failures += 1
            if self._connect_failures % 5 == 0:
                # warn on every fifth failure
                self._logger.warn("Failed to connect to %r: %s" % (self._bindaddr, e))
            else:
                self._logger.debug("Failed to connect to %r: %s" % (self._bindaddr, e))
            sock.close()
            sock = None

        if sock is None:
            return

        self._sock = sock
        self._waiting_chunk = ""
        self._connected.set()

        try:
            self.notify_connected(True)
        except Exception:
            self._logger.exception("Notify connect failed. Disconnecting.")
            self._disconnect()

    def _disconnect(self):
        """Disconnect and cleanup."""
        # avoid disconnecting multiple times by immediately setting
        # self._sock to None
        sock = self._sock
        self._sock = None

        if sock is not None:
            sock.close()
            self._connected.clear()
            self.notify_connected(False)

    def _handle_chunk(self, chunk):
        """Handle a chunk of data from the server.

        Parameters
        ----------
        chunk : data
            The data string to process.
        """
        chunk = chunk.replace("\r", "\n")
        lines = chunk.split("\n")

        for line in lines[:-1]:
            full_line = self._waiting_chunk + line
            self._waiting_chunk = ""
            if full_line:
                try:
                    msg = self._parser.parse(full_line)
                # We do want to catch everything that inherits from Exception
                # pylint: disable-msg = W0703
                except Exception:
                    e_type, e_value, trace = sys.exc_info()
                    reason = "\n".join(traceback.format_exception(
                        e_type, e_value, trace, self._tb_limit
                    ))
                    self._logger.error("BAD COMMAND: %s" % (reason,))
                else:
                    self.handle_message(msg)

        self._waiting_chunk += lines[-1]

    def handle_message(self, msg):
        """Handle a message from the server.

        Parameters
        ----------
        msg : Message object
            The Message to dispatch to the handler methods.
        """
        # log messages received so that no one else has to
        self._logger.debug(msg)

        if msg.mtype == Message.INFORM:
            self.handle_inform(msg)
        elif msg.mtype == Message.REPLY:
            self.handle_reply(msg)
        elif msg.mtype == Message.REQUEST:
            self.handle_request(msg)
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
        method = self.__class__.unhandled_inform
        if msg.name in self._inform_handlers:
            method = self._inform_handlers[msg.name]

        try:
            method(self, msg)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit
            ))
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
            method(self, msg)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit
            ))
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
            assert (reply.mtype == Message.REPLY)
            assert (reply.name == msg.name)
            self._logger.info("%s OK" % (msg.name,))
            self.send_message(reply)
        # We do want to catch everything that inherits from Exception
        # pylint: disable-msg = W0703
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit
            ))
            self._logger.error("Request %s FAIL: %s" % (msg.name, reason))

    def unhandled_inform(self, msg):
        """Fallback method for inform messages without a registered handler

        Parameters
        ----------
        msg : Message object
            The inform message that wasn't processed by any handlers.
        """
        pass

    def unhandled_reply(self, msg):
        """Fallback method for reply messages without a registered handler

        Parameters
        ----------
        msg : Message object
            The reply message that wasn't processed by any handlers.
        """
        pass

    def unhandled_request(self, msg):
        """Fallback method for requests without a registered handler

        Parameters
        ----------
        msg : Message object
            The request message that wasn't processed by any handlers.
        """
        pass

    def run(self):
        """Process reply and inform messages from the server."""
        self._logger.debug("Starting thread %s" % (threading.currentThread().getName()))
        timeout = 1.0 # s

        # save globals so that the thread can run cleanly
        # even while Python is setting module globals to
        # None.
        _select = select.select
        _socket_error = socket.error
        _sleep = time.sleep

        if not self._auto_reconnect:
            self._connect()
            if not self.is_connected():
                raise KatcpClientError("Failed to connect to %r" % (self._bindaddr,))

        self._running.set()
        while self._running.isSet():
            if self.is_connected():
                try:
                    readers, _writers, errors = _select(
                        [self._sock], [], [self._sock], timeout
                    )
                except Exception, e:
                    # catch Exception because class of exception thrown
                    # various drastically between Mac and Linux
                    self._logger.debug("Select error: %s" % (e,))
                    errors = [self._sock]

                if errors:
                    self._disconnect()

                elif readers:
                    try:
                        chunk = self._sock.recv(4096)
                    except _socket_error:
                        # an error when sock was within ready list presumably
                        # means the client needs to be ditched.
                        chunk = ""
                    if chunk:
                        self._handle_chunk(chunk)
                    else:
                        # EOF from server
                        self._disconnect()
            else:
                # not currently connected so attempt to connect
                # if auto_reconnect is set
                if not self._auto_reconnect:
                    self._running.clear()
                    break
                else:
                    self._connect()
                    if not self.is_connected():
                        _sleep(timeout)

        self._disconnect()
        self._logger.debug("Stopping thread %s" % (threading.currentThread().getName()))

    def start(self, timeout=None, daemon=None, excepthook=None):
        """Start the client in a new thread.

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for client thread to start.
        daemon : boolean
            If not None, the thread's setDaemon method is called with this
            parameter before the thread is started.
        excepthook : function
            Function to call if the client throws an exception. Signature
            is as for sys.excepthook.
        """
        if self._thread:
            raise RuntimeError("Device client already started.")

        self._thread = ExcepthookThread(target=self.run, excepthook=excepthook)
        if daemon is not None:
            self._thread.setDaemon(daemon)
        self._thread.start()
        if timeout:
            self._connected.wait(timeout)
            if not self._connected.isSet():
                raise RuntimeError("Device client failed to start.")

    def join(self, timeout=None):
        """Rejoin the client thread.

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for thread to finish.
        """
        if not self._thread:
            raise RuntimeError("Device client thread not started.")

        self._thread.join(timeout)
        if not self._thread.isAlive():
            self._thread = None

    def stop(self, timeout=1.0):
        """Stop a running client (from another thread).

        Parameters
        ----------
        timeout : float in seconds
           Seconds to wait for client thread to have *started*.
        """
        self._running.wait(timeout)
        if not self._running.isSet():
            raise RuntimeError("Attempt to stop client that wasn't running.")
        self._running.clear()

    def running(self):
        """Whether the client is running.

        Returns
        -------
        running : bool
            Whether the server is running.
        """
        return self._running.isSet()

    def is_connected(self):
        """Check if the socket is currently connected.

        Returns
        -------
        connected : bool
            Whether the client is connected.
        """
        return self._sock is not None

    def notify_connected(self, connected):
        """Event handler that is called wheneved the connection status changes.

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


class BlockingClient(DeviceClient):
    """Implement blocking requests on top of DeviceClient.

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
    timeout : float in seconds
        Default number of seconds to wait before a blocking request times
        out. Can be overriden in individual calls to bocking_request.

    Examples
    --------
    >>> c = BlockingClient('localhost', 10000)
    >>> c.start()
    >>> reply, informs = c.blocking_request(katcp.Message.request('myreq'))
    >>> print reply
    >>> print [str(msg) for msg in informs]
    >>> c.stop()
    >>> c.join()
    """

    def __init__(self, host, port, tb_limit=20, timeout=5.0, logger=log,
                 auto_reconnect=True):
        super(BlockingClient, self).__init__(host, port, tb_limit=tb_limit,
            logger=logger,auto_reconnect=auto_reconnect)
        self._request_timeout = timeout

        self._request_end = threading.Event()
        self._request_lock = threading.Lock()
        self._current_name = None
        self._current_informs = None
        self._current_reply = None

    def blocking_request(self, msg, timeout=None):
        """Send a request messsage.

        Parameters
        ----------
        msg : Message object
            The request Message to send.
        timeout : float in seconds
            How long to wait for a reply. The default is the
            the timeout set when creating the BlockingClient.

        Returns
        -------
        reply : Message object
            The reply message received.
        informs : list of Message objects
            A list of the inform messages received.
        """
        try:
            self._request_lock.acquire()
            self._request_end.clear()
            self._current_name = msg.name
            self._current_informs = []
            self._current_reply = None
        finally:
            self._request_lock.release()

        if timeout is None:
            timeout = self._request_timeout

        try:
            self.request(msg)
            self._request_end.wait(timeout)
        finally:
            try:
                self._request_lock.acquire()

                success = self._request_end.isSet()
                informs = self._current_informs
                reply = self._current_reply

                self._request_end.clear()
                self._current_informs = None
                self._current_reply = None
                self._current_name = None
            finally:
                self._request_lock.release()

        if success:
            return reply, informs
        else:
            raise RuntimeError("Request %s timed out after %s seconds." %
                                (msg.name, timeout))

    def handle_inform(self, msg):
        """Handle inform messages related to any current requests.

        Inform messages not related to the current request go up to the
        base class method.

        Parameters
        ----------
        msg : Message object
            The inform message to handle.
        """
        try:
            self._request_lock.acquire()
            if msg.name == self._current_name:
                self._current_informs.append(msg)
                return
        finally:
            self._request_lock.release()

        super(BlockingClient, self).handle_inform(msg)

    def handle_reply(self, msg):
        """Handle a reply message related to the current request.

        Reply messages not related to the current request go up to the
        base class method.

        Parameters
        ----------
        msg : Message object
            The reply message to handle.
        """
        try:
            self._request_lock.acquire()
            if msg.name == self._current_name:
                # unset _current_name so that no more replies or informs
                # match this request
                self._current_name = None
                self._current_reply = msg
                self._request_end.set()
                return
        finally:
            self._request_lock.release()

        super(BlockingClient, self).handle_reply(msg)


class CallbackClient(DeviceClient):
    """Implement callback-based requests on top of DeviceClient.

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
    >>> def reply_cb(msg):
    ...     print "Reply:", msg
    ...
    >>> def inform_cb(msg):
    ...     print "Inform:", msg
    ...
    >>> c = CallbackClient('localhost', 10000)
    >>> c.start()
    >>> c.request(
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

    def __init__(self, host, port, tb_limit=20, logger=log,
                 auto_reconnect=True):
        super(CallbackClient, self).__init__(host, port, tb_limit=tb_limit,
            logger=logger,auto_reconnect=auto_reconnect)

        # stack mapping pending requests to (reply_cb, inform_cb) callback pairs
        self._async_queue = {}

    def _push_async_request(self, request_name, reply_cb, inform_cb, user_data):
        """Store the callbacks for a request we've sent so we
           can forward any replies and informs to them.
           """
        if request_name in self._async_queue:
            self._async_queue[request_name].append((reply_cb, inform_cb, user_data))
        else:
            self._async_queue[request_name] = [(reply_cb, inform_cb, user_data)]

    def _pop_async_request(self, request_name):
        """Pop the first set of callbacks to a request from
           the stack so the reply callback can be notified.
           """
        return self._async_queue[request_name].pop(0)

    def _peek_async_request(self, request_name):
        """Peek at the first set of callbacks to a request
           so that the associated inform callback can be notified.
           """
        return self._async_queue[request_name][0]

    def _has_async_request(self, request_name):
        """Return true if there is an async request to be popped."""
        return bool(request_name in self._async_queue and self._async_queue[request_name])

    def request(self, msg, reply_cb=None, inform_cb=None, user_data=None):
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
        """
        self._push_async_request(msg.name, reply_cb, inform_cb, user_data)
        super(CallbackClient, self).request(msg)

    def handle_inform(self, msg):
        """Handle inform messages related to any current requests.

        Inform messages not related to the current request go up
        to the base class method.

        Parameters
        ----------
        msg : Message object
            The inform message to dispatch.
        """
        if self._has_async_request(msg.name):
            # this may also result in inform_cb being None if no
            # inform_cb was passed to the request method.
            _reply_cb, inform_cb, user_data = self._peek_async_request(msg.name)
        else:
            inform_cb, user_data = None, None

        if inform_cb is None:
            inform_cb = super(CallbackClient, self).handle_inform
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
                e_type, e_value, trace, self._tb_limit
            ))
            self._logger.error("Callback inform %s FAIL: %s" % (msg.name, reason))

    def handle_reply(self, msg):
        """Handle a reply message related to the current request.

        Reply messages not related to the current request go up
        to the base class method.

        Parameters
        ----------
        msg : Message object
            The reply message to dispatch.
        """
        if self._has_async_request(msg.name):
            # this may also result in reply_cb being None if no
            # reply_cb was passed to the request method
            reply_cb, _inform_cb, user_data = self._pop_async_request(msg.name)
        else:
            reply_cb, user_data = None, None

        if reply_cb is None:
            reply_cb = super(CallbackClient, self).handle_reply
            # override user_data since handle_reply takes no user_data

        try:
            if user_data is None:
                reply_cb(msg)
            else:
                reply_cb(msg, *user_data)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit
            ))
            self._logger.error("Callback reply %s FAIL: %s" % (msg.name, reason))
