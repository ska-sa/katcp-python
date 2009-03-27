"""Clients for the KAT device control language.

   @namespace za.ac.ska.katcp
   @author Simon Cross <simon.cross@ska.ac.za>
   """

import threading
import socket
import sys
import traceback
import select
import time
import logging
from katcp import DeviceMetaclass, MessageParser, Message

#logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("katcp")


class KatcpClientError(Exception):
    """Exception raised by KATCP clients when errors occur while
       communicating with a device.  Note that socket.error can also be raised
       if low-level network exceptions occure."""
    pass


class DeviceClient(object):
    """Device client proxy.

       Subclasses should implement .reply_*, .inform_* and
       request_* methods to take actions when messages arrive,
       and implement unhandled_inform, unhandled_reply and
       unhandled_request to provide fallbacks for messages for
       which there is no handler.

       Request messages can be sent by calling .request().
       """

    __metaclass__ = DeviceMetaclass

    def __init__(self, host, port, tb_limit=20, logger=log,
                 auto_reconnect=True):
        """Create a basic DeviceClient.

           @param self This object.
           @param host String: host to connect to.
           @param port Integer: port to connect to.
           @param tb_limit Integer: maximum number of stack frames to
                           send in error traceback.
           @param logger Object: Logger to log to.
           @param auto_reconnect Boolean: Whether to automattically
                                 reconnect if the connection dies.
           """
        self._parser = MessageParser()
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._sock = None
        self._waiting_chunk = ""
        self._running = threading.Event()
        self._connected = threading.Event()
        self._thread = None
        self._logger = logger
        self._auto_reconnect = auto_reconnect

    def request(self, msg):
        """Send a request messsage.

           @param self This object.
           @param msg The request Message to send.
           @return None
           """
        assert(msg.mtype == Message.REQUEST)
        self.send_message(msg)

    def send_message(self, msg):
        """Send any kind of message.

           @param self This object.
           @param msg The Message to send.
           @return None
           """
        if self._sock is None:
            raise KatcpClientError("Client not connected")
        self._sock.send(str(msg) + "\n")

    def _connect(self):
        """Connect to the server.

           @param self This object.
           @return None
           """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._sock.connect(self._bindaddr)
            self._waiting_chunk = ""
            self._connected.set()
            self.notify_connected(True)
        except Exception:
            self._logger.exception("DeviceClient failed to connect.")
            self._disconnect()

    def _disconnect(self):
        """Disconnect and cleanup.

           @param self This object
           @return None
           """
        if self._sock is not None:
            self._sock.close()
            self._connected.clear()
            self.notify_connected(False)
        self._sock = None

    def handle_chunk(self, chunk):
        """Handle a chunk of data from the server.

           @param self This object.
           @param chunk The data string to process.
           @return None
           """
        chunk = chunk.replace("\r", "\n")
        lines = chunk.split("\n")

        for line in lines[:-1]:
            full_line = self._waiting_chunk + line
            self._waiting_chunk = ""
            if full_line:
                msg = self._parser.parse(full_line)
                self.handle_message(msg)

        self._waiting_chunk += lines[-1]

    def handle_message(self, msg):
        """Handle a message from the server.

           @param self This object.
           @param msg The Message to process.
           @return None
           """
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
        """Dispatch an inform message to the appropriate method."""
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
        """Dispatch a reply message to the appropriate method."""
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
        """Dispatch a request message to the appropriate method."""
        method = self.__class__.unhandled_request
        if msg.name in self._request_handlers:
            method = self._request_handlers[msg.name]

        try:
            reply = method(self, msg)
            assert (reply.mtype == Message.REPLY)
            assert (reply.name == msg.name)
            self._logger.info("%s OK" % (msg.name,))
            self._sock.send(str(reply) + "\n")
        # We do want to catch everything that inherits from Exception
        # pylint: disable-msg = W0703
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit
            ))
            self._logger.error("Request %s FAIL: %s" % (msg.name, reason))

    def unhandled_inform(self, msg):
        """Fallback method for inform messages without a registered handler"""
        pass

    def unhandled_reply(self, msg):
        """Fallback method for reply messages without a registered handler"""
        pass

    def unhandled_request(self, msg):
        """Fallback method for requests without a registered handler"""
        pass

    def run(self):
        """Process reply and inform messages from the server.

           @param self This object.
           @return None
           """
        self._logger.debug("Starting thread %s" % (threading.currentThread().getName()))
        timeout = 1.0 # s

        self._running.set()
        self._connect()
        while self._running.isSet():
            if self.is_connected():
                readers, _writers, errors = select.select(
                    [self._sock], [], [self._sock], timeout
                )

                if errors:
                    self._disconnect()

                elif readers:
                    chunk = self._sock.recv(4096)
                    if chunk:
                        self.handle_chunk(chunk)
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
                        time.sleep(timeout)

        self._disconnect()
        self._logger.debug("Stopping thread %s" % (threading.currentThread().getName()))

    def start(self, timeout=None):
        """Start the client in a new thread.

           @param self This object.
           @param timeout Seconds to wait for server thread to start (as a float).
           @return None
           """
        if self._thread:
            raise RuntimeError("Device client already started.")

        self._thread = threading.Thread(target=self.run)
        self._thread.start()
        if timeout:
            self._connected.wait(timeout)
            if not self._connected.isSet():
                raise RuntimeError("Device client failed to start.")

    def join(self, timeout=None):
        """Rejoin the client thread.

           @param self This object.
           @param timeout Seconds to wait for server thread to complete (as a float).
           @return None
           """
        if not self._thread:
            raise RuntimeError("Device client thread not started.")

        self._thread.join(timeout)
        if not self._thread.isAlive():
            self._thread = None

    def stop(self, timeout=1.0):
        """Stop a running client (from another thread).

           @param self This object.
           @param timeout Seconds to wait for client to have *started* (as a float).
           @return None
           """
        self._running.wait(timeout)
        if not self._running.isSet():
            raise RuntimeError("Attempt to stop client that wasn't running.")
        self._running.clear()

    def running(self):
        """Whether the client is running.

           @param self This object.
           @return Whether the server is running (True or False).
           """
        return self._running.isSet()

    def is_connected(self):
        """Check if the socket is currently connected.

           @param self This object
           @return Whether the client is connected (True or False).
           """
        return self._sock is not None

    def notify_connected(self, connected):
        """Event handler that is called wheneved the connection status changes.
           Override in derived class for desired behaviour.

           @param self This object
           @param connected The current connection state (True or False)
           """
        pass


class BlockingClient(DeviceClient):
    """Implement blocking requests on top of DeviceClient."""

    def __init__(self, host, port, tb_limit=20, timeout=5.0, logger=log,
                 auto_reconnect=True):
        """Create a basic BlockingClient.

           @param self This object.
           @param host String: host to connect to.
           @param port Integer: port to connect to.
           @param tb_limit Integer: maximum number of stack frames to
                           send in error traceback.
           @param timeout Float: seconds to wait before a blocking request
                          times out.
           @param logger Object: Logger to log to.
           @param auto_reconnect Boolean: Whether to automattically
                                 reconnect if the connection dies.
           """
        super(BlockingClient, self).__init__(host, port, tb_limit=tb_limit,
            logger=logger,auto_reconnect=auto_reconnect)
        self._request_timeout = timeout

        self._request_end = threading.Event()
        self._request_lock = threading.Lock()
        self._current_name = None
        self._current_informs = None
        self._current_reply = None

    def blocking_request(self, msg):
        """Send a request messsage.

           @param self This object.
           @param msg The request Message to send.
           @return The a tuple containing the reply Message and a list of
                   inform messages.
           """
        try:
            self._request_lock.acquire()
            self._request_end.clear()
            self._current_name = msg.name
            self._current_informs = []
            self._current_reply = None
        finally:
            self._request_lock.release()

        try:
            self.request(msg)
            self._request_end.wait(self._request_timeout)
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
            raise RuntimeError("Request %s timeout out after %s seconds." %
                                (msg.name, self._request_timeout))

    def handle_inform(self, msg):
        """Handle inform messages related to any current requests.

           Inform messages not related to the current request go up
           to the base class method.
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

           Reply messages not related to the current request go up
           to the base class method.
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
    """Implement callback-based requests on top of DeviceClient."""

    def __init__(self, host, port, tb_limit=20, logger=log,
                 auto_reconnect=True):
        """Create a basic CallbackClient.

           @param self This object.
           @param host String: host to connect to.
           @param port Integer: port to connect to.
           @param tb_limit Integer: maximum number of stack frames to
                           send in error traceback.
           @param logger Object: Logger to log to.
           @param auto_reconnect Boolean: Whether to automattically
                                 reconnect if the connection dies.
           """
        super(CallbackClient, self).__init__(host, port, tb_limit=tb_limit,
            logger=logger,auto_reconnect=auto_reconnect)

        # stack mapping pending requests to (reply_cb, inform_cb) callback pairs
        self._async_stack = {}

    def _push_async_request(self, request_name, reply_cb, inform_cb):
        """Store the socket that we've sent a request from so we
           can forward any replies and informs to the right scoket.
           """
        if request_name in self._async_stack:
            self._async_stack[request_name].append((reply_cb, inform_cb))
        else:
            self._async_stack[request_name] = [(reply_cb, inform_cb)]

    def _pop_async_request(self, request_name):
        """Pop the last client socket to perform a request from
           the stack so a reply can be sent.
           """
        return self._async_stack[request_name].pop()

    def _peek_async_request(self, request_name):
        """Peek at the last client socket to perform a request
           so that associated informs can be sent.
           """
        return self._async_stack[request_name][-1]

    def _has_async_request(self, request_name):
        """Return true if there is an async request to be popped."""
        return bool(request_name in self._async_stack and self._async_stack[request_name])

    def request(self, msg, reply_cb=None, inform_cb=None):
        """Send a request messsage.

           @param self This object.
           @param msg The request Message to send.
           @return The a tuple containing the reply Message and a list of
                   inform messages.
           """
        self._push_async_request(msg.name, reply_cb, inform_cb)
        super(CallbackClient, self).request(msg)

    def handle_inform(self, msg):
        """Handle inform messages related to any current requests.

           Inform messages not related to the current request go up
           to the base class method.
           """
        if self._has_async_request(msg.name):
            _reply_cb, inform_cb = self._peek_async_request(msg.name)
        else:
            inform_cb = super(CallbackClient, self).handle_inform

        try:
            inform_cb(msg)
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
           """
        if self._has_async_request(msg.name):
            reply_cb, _inform_cb = self._pop_async_request(msg.name)
        else:
            reply_cb = super(CallbackClient, self).handle_reply

        try:
            reply_cb(msg)
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, self._tb_limit
            ))
            self._logger.error("Callback reply %s FAIL: %s" % (msg.name, reason))
