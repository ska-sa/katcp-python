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
from .core import DeviceMetaclass, ExcepthookThread, Message, MessageParser, \
                   FailReply, AsyncReply
from .sampling import SampleReactor, SampleStrategy, SampleNone

# logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("katcp")


def construct_name_filter(pattern):
    """Return a funciton for filtering sensor names based on a pattern.

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


class DeviceServerBase(object):
    """Base class for device servers.

    Subclasses should add .request\_* methods for dealing
    with request messages. These methods each take the client
    socket and msg objects as arguments and should return the
    reply message or raise an exception as a result. In these
    methods, the client socket should only be used as an argument
    to .inform().

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

    def __init__(self, host, port, tb_limit=20, logger=log):
        self._parser = MessageParser()
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._running = threading.Event()
        self._sock = None
        self._thread = None
        self._logger = logger
        self._deferred_queue = Queue.Queue(maxsize=self.MAX_DEFERRED_QUEUE_SIZE)

        # sockets and data
        self._data_lock = threading.Lock()
        self._socks = []  # list of client sockets
        self._waiting_chunks = {}  # map from client sockets to messages pieces
        self._sock_locks = {}  # map from client sockets to sending locks

    def _log_msg(self, level_name, msg, name, timestamp=None):
        """Create a katcp logging inform message.

           Usually this will be called from inside a DeviceLogger object,
           but it is also used by the methods in this class when errors
           need to be reported to the client.
           """
        if timestamp is None:
            timestamp = time.time()
        return Message.inform("log",
                level_name,
                str(int(timestamp * 1000.0)),  # time since epoch in ms
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
        sock.bind(bindaddr)
        sock.listen(5)
        return sock

    def _add_socket(self, sock):
        """Add a client socket to the socket and chunk lists."""
        self._data_lock.acquire()
        try:
            self._socks.append(sock)
            self._waiting_chunks[sock] = ""
            self._sock_locks[sock] = threading.Lock()
        finally:
            self._data_lock.release()

    def _remove_socket(self, sock):
        """Remove a client socket from the socket and chunk lists."""
        sock.close()
        self._data_lock.acquire()
        try:
            if sock in self._socks:
                self._socks.remove(sock)
                del self._waiting_chunks[sock]
                del self._sock_locks[sock]
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
                    self._logger.error("BAD COMMAND: %s" % (reason,))
                    self.inform(sock, self._log_msg("error", reason, "root"))
                else:
                    self.handle_message(sock, msg)

        self._data_lock.acquire()
        try:
            if sock in self._waiting_chunks:
                self._waiting_chunks[sock] = waiting_chunk + lines[-1]
        finally:
            self._data_lock.release()

    def handle_message(self, sock, msg):
        """Handle messages of all types from clients.

        Parameters
        ----------
        sock : socket.socket object
            The socket the message was from.
        msg : Message object
            The message to process.
        """
        # log messages received so that no one else has to
        self._logger.debug(msg)

        if msg.mtype == msg.REQUEST:
            self.handle_request(sock, msg)
        elif msg.mtype == msg.INFORM:
            self.handle_inform(sock, msg)
        elif msg.mtype == msg.REPLY:
            self.handle_reply(sock, msg)
        else:
            reason = "Unexpected message type received by server ['%s']." \
                     % (msg,)
            self.inform(sock, self._log_msg("error", reason, "root"))

    def handle_request(self, sock, msg):
        """Dispatch a request message to the appropriate method.

        Parameters
        ----------
        sock : socket.socket object
            The socket the message was from.
        msg : Message object
            The request message to process.
        """
        send_reply = True
        if msg.name in self._request_handlers:
            try:
                reply = self._request_handlers[msg.name](self, sock, msg)
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
            self.reply(sock, reply, msg)

    def handle_inform(self, sock, msg):
        """Dispatch an inform message to the appropriate method.

        Parameters
        ----------
        sock : socket.socket object
            The socket the message was from.
        msg : Message object
            The inform message to process.
        """
        if msg.name in self._inform_handlers:
            try:
                self._inform_handlers[msg.name](self, sock, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("Inform %s FAIL: %s" % (msg.name, reason))
        else:
            self._logger.warn("%s INVALID: Unknown inform." % (msg.name,))

    def handle_reply(self, sock, msg):
        """Dispatch a reply message to the appropriate method.

        Parameters
        ----------
        sock : socket.socket object
            The socket the message was from.
        msg : Message object
            The reply message to process.
        """
        if msg.name in self._reply_handlers:
            try:
                self._reply_handlers[msg.name](self, sock, msg)
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
        try:
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
            lock.release()

        if send_failed:
            try:
                client_name = sock.getpeername()
            except socket.error:
                client_name = "<disconnected client>"
            msg = "Failed to send message to client %s (%s)" % (client_name, e)
            self._logger.error(msg)
            self._remove_socket(sock)
            self.on_client_disconnect(sock, msg, False)

    def inform(self, sock, msg):
        """Send an inform messages to a particular client.

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
            self.inform(sock, msg)

    def reply(self, sock, reply, orig_req):
        """Send an asynchronous reply to an earlier request.

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

    def reply_inform(self, sock, inform, orig_req):
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
                        self._remove_socket(sock)
                        self.on_client_disconnect(sock, "Client socket died"
                                                  " with error %s" % (e,),
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
                    self._remove_socket(sock)
                    self.on_client_disconnect(sock, "Client socket died",
                                              False)

            for sock in readers:
                if sock is self._sock:
                    client, addr = sock.accept()
                    client.setblocking(0)
                    self.mass_inform(Message.inform("client-connected",
                        "New client connected from %s" % (addr,)))
                    self._add_socket(client)
                    self.on_client_connect(client)
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
                        self._remove_socket(sock)
                        self.on_client_disconnect(sock, "Socket EOF", False)

        for sock in list(self._socks):
            self.on_client_disconnect(sock, "Device server shutting down.",
                                      True)
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

    def on_client_connect(self, sock):
        """Called after client connection is established.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point.

        Parameters
        ----------
        sock : socket.socket object
            The client connection that has been successfully established.
        """
        pass

    def on_client_disconnect(self, sock, msg, sock_valid):
        """Called before a client connection is closed.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point. The server
        cannot guarantee this will be called (for example, the client
        might drop the connection). The message parameter contains
        the reason for the disconnection.

        Parameters
        ----------
        sock : socket.socket object
            Client socket being disconnected.
        msg : str
            Reason client is being disconnected.
        sock_valid : boolean
            True if sock is still open for sending,
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

    ## @var log
    # @brief DeviceLogger instance for sending log messages to the client.

    # * and ** magic fine here
    # pylint: disable-msg = W0142

    def __init__(self, *args, **kwargs):
        super(DeviceServer, self).__init__(*args, **kwargs)
        self.log = DeviceLogger(self, python_logger=self._logger)
        self._restart_queue = None
        self._sensors = {}  # map names to sensor objects
        self._reactor = None  # created in run
        # map client sockets to map of sensors -> sampling strategies
        self._strategies = {}
        # strat lock (should be held for updates to _strategies)
        self._strat_lock = threading.Lock()
        self.setup_sensors()

    # pylint: enable-msg = W0142

    def on_client_connect(self, sock):
        """Inform client of build state and version on connect.

        Parameters
        ----------
        sock : socket.socket object
            The client connection that has been successfully established.
        """
        self._strat_lock.acquire()
        try:
            self._strategies[sock] = {}  # map sensors -> sampling strategies
        finally:
            self._strat_lock.release()
        self.inform(sock, Message.inform("version", self.version()))
        self.inform(sock, Message.inform("build-state", self.build_state()))

    def on_client_disconnect(self, sock, msg, sock_valid):
        """Inform client it is about to be disconnected.

        Parameters
        ----------
        sock : socket.socket object
            Client socket being disconnected.
        msg : str
            Reason client is being disconnected.
        sock_valid : boolean
            True if sock is still open for sending,
            False otherwise.
        """

        def remove_strategies():
            with self._strat_lock:
                strategies = self._strategies.pop(sock, None)
                if strategies is not None:
                    for sensor, strategy in list(strategies.items()):
                        del strategies[sensor]
                        self._reactor.remove_strategy(strategy)
            if sock_valid:
                self.inform(sock, Message.inform("disconnect", msg))

        try:
            self._deferred_queue.put_nowait(remove_strategies)
        except Queue.Full:
            self._logger.error(
                'Deferred queue full when trying to add sensor '
                'strategy de-registration task for client %r' % socket)

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

    def remove_sensor(self, sensor):
        """Remove a sensor from the device.

        Also deregisters all clients observing the sensor.

        Parameters
        ----------
        sensor : Sensor object
            The sensor object to remove from the device server.
        """
        del self._sensors[sensor.name]

        self._strat_lock.acquire()
        try:
            for strategies in self._strategies.values():
                for other_sensor, strategy in list(strategies.items()):
                    if other_sensor.name == sensor.name:
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

    # all requests take sock and msg arguments regardless of whether
    # they're used
    # pylint: disable-msg = W0613

    def request_halt(self, sock, msg):
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
        return Message.reply("halt", "ok")

    def request_help(self, sock, msg):
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
                self.reply_inform(sock, Message.inform("help", name, doc), msg)
            num_methods = len(self._request_handlers)
            return Message.reply("help", "ok", str(num_methods))
        else:
            name = msg.arguments[0]
            if name in self._request_handlers:
                method = self._request_handlers[name]
                doc = method.__doc__.strip()
                self.reply_inform(sock, Message.inform("help", name, doc), msg)
                return Message.reply("help", "ok", "1")
            return Message.reply("help", "fail", "Unknown request method.")

    def request_log_level(self, sock, msg):
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
        return Message.reply("log-level", "ok", self.log.level_name())

    def request_restart(self, sock, msg):
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
        return Message.reply("restart", "ok")

    def request_client_list(self, sock, msg):
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
            self.reply_inform(sock, Message.inform("client-list", addr), msg)
        return Message.reply("client-list", "ok", str(num_clients))

    def request_sensor_list(self, sock, msg):
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
        """
        exact, name_filter = construct_name_filter(msg.arguments[0]
                    if msg.arguments else None)
        sensors = [(name, sensor) for name, sensor in
                    sorted(self._sensors.iteritems()) if name_filter(name)]

        if exact and not sensors:
            return Message.reply("sensor-list", "fail", "Unknown sensor name.")

        for name, sensor in sensors:
            self.reply_inform(sock, Message.inform("sensor-list",
                name, sensor.description, sensor.units, sensor.stype,
                *sensor.formatted_params), msg)
        return Message.reply("sensor-list", "ok", str(len(sensors)))

    def request_sensor_value(self, sock, msg):
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
            Timestamp of the sensor reading in milliseconds since the Unix
            epoch.
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
            #sensor-value 1244631611415.231 1 psu.voltage 4.5
            #sensor-value 1244631611415.200 1 cpu.status off
            ...
            !sensor-value ok 5

            ?sensor-value cpu.power.on
            #sensor-value 1244631611415.231 1 cpu.power.on 0
            !sensor-value ok 1
        """
        exact, name_filter = construct_name_filter(msg.arguments[0]
                    if msg.arguments else None)
        sensors = [(name, sensor) for name, sensor in
                    sorted(self._sensors.iteritems()) if name_filter(name)]

        if exact and not sensors:
            return Message.reply("sensor-value", "fail",
                                 "Unknown sensor name.")

        for name, sensor in sensors:
            timestamp_ms, status, value = sensor.read_formatted()
            self.reply_inform(sock, Message.inform("sensor-value",
                    timestamp_ms, "1", name, status, value), msg)
        return Message.reply("sensor-value", "ok", str(len(sensors)))

    def request_sensor_sampling(self, sock, msg):
        """Configure or query the way a sensor is sampled.

        Sampled values are reported asynchronously using the #sensor-status
        message.

        Parameters
        ----------
        name : str
            Name of the sensor whose sampling strategy to query or configure.
        strategy : {'none', 'auto', 'event', 'differential', \
                    'period'}, optional
            Type of strategy to use to report the sensor value. The
            differential strategy type may only be used with integer or float
            sensors.
        params : list of str, optional
            Additional strategy parameters (dependent on the strategy type).
            For the differential strategy, the parameter is an integer or float
            giving the amount by which the sensor value may change before an
            updated value is sent. For the period strategy, the parameter is
            the period to sample at in milliseconds. For the event strategy, an
            optional minimum time between updates in milliseconds may be given.

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
            raise FailReply("Unknown sensor name.")

        sensor = self._sensors[name]

        if len(msg.arguments) > 1:
            # attempt to set sampling strategy
            strategy = msg.arguments[1]
            params = msg.arguments[2:]

            if strategy not in SampleStrategy.SAMPLING_LOOKUP_REV:
                raise FailReply("Unknown strategy name.")

            def inform_callback(cb_msg):
                """Inform callback for sensor strategy."""
                self.inform(sock, cb_msg)

            new_strategy = SampleStrategy.get_strategy(strategy,
                                        inform_callback, sensor, *params)

            self._strat_lock.acquire()
            try:
                old_strategy = self._strategies[sock].get(sensor, None)
                if old_strategy is not None:
                    self._reactor.remove_strategy(old_strategy)

                # todo: replace isinstance check with something better
                if isinstance(new_strategy, SampleNone):
                    if sensor in self._strategies[sock]:
                        del self._strategies[sock][sensor]
                else:
                    self._strategies[sock][sensor] = new_strategy
                    # reactor.add_strategy() sends out an inform
                    # which is not great while the lock is held.
                    self._reactor.add_strategy(new_strategy)
            finally:
                self._strat_lock.release()

        current_strategy = self._strategies[sock].get(sensor, None)
        if not current_strategy:
            current_strategy = SampleStrategy.get_strategy("none",
                                                           lambda msg: None,
                                                           sensor)

        strategy, params = current_strategy.get_sampling_formatted()
        return Message.reply("sensor-sampling", "ok", name, strategy, *params)

    def request_watchdog(self, sock, msg):
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
        return Message.reply("watchdog", "ok")

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
