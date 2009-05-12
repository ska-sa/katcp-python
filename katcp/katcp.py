
"""Utilities for dealing with KAT device control
   language messages.

   @namespace za.ac.ska.katcp
   @author Simon Cross <simon.cross@ska.ac.za>
   """

import socket
import errno
import select
import threading
import traceback
import logging
import sys
import re
import time
from sampling import SampleReactor, SampleStrategy, SampleNone
from kattypes import Int, Float, Bool, Discrete, Lru, Str, Timestamp

# logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("katcp")


class Message(object):
    """Represents a KAT device control language message."""

    # Message types
    REQUEST, REPLY, INFORM = range(3)

    # Reply codes
    # TODO: make use of reply codes in device client and server
    OK, FAIL, INVALID = "ok", "fail", "invalid"

    ## @brief Mapping from message type to string name for the type.
    TYPE_NAMES = {
        REQUEST: "REQUEST",
        REPLY: "REPLY",
        INFORM: "INFORM",
    }

    ## @brief Mapping from message type to type code character.
    TYPE_SYMBOLS = {
        REQUEST: "?",
        REPLY: "!",
        INFORM: "#",
    }

    # pylint fails to realise TYPE_SYMBOLS is defined
    # pylint: disable-msg = E0602

    ## @brief Mapping from type code character to message type.
    TYPE_SYMBOL_LOOKUP = dict((v, k) for k, v in TYPE_SYMBOLS.items())

    # pylint: enable-msg = E0602

    ## @brief Mapping from escape character to corresponding unescaped string.
    ESCAPE_LOOKUP = {
        "\\" : "\\",
        "_": " ",
        "0": "\0",
        "n": "\n",
        "r": "\r",
        "e": "\x1b",
        "t": "\t",
        "@": "",
    }

    # pylint fails to realise ESCAPE_LOOKUP is defined
    # pylint: disable-msg = E0602

    ## @brief Mapping from unescaped string to corresponding escape character.
    REVERSE_ESCAPE_LOOKUP = dict((v, k) for k, v in ESCAPE_LOOKUP.items())

    # pylint: enable-msg = E0602

    ## @brief Regular expression matching all unescaped character.
    ESCAPE_RE = re.compile(r"[\\ \0\n\r\x1b\t]")

    ## @var mtype
    # @brief Message type.

    ## @var name
    # @brief Message name.

    ## @var arguments
    # @brief List of string message arguments.

    def __init__(self, mtype, name, arguments=None):
        """Create a KATCP Message.

           @param self This object.
           @param mtype Message::Type constant.
           @param name String: message name.
           @param arguments List of strings: message arguments.
           """
        self.mtype = mtype
        self.name = name
        if arguments is None:
            self.arguments = []
        else:
            self.arguments = [str(arg) for arg in arguments]

        # check message type

        if mtype not in self.TYPE_SYMBOLS:
            raise KatcpSyntaxError("Invalid command type %r." % (mtype,))

        # check command name validity

        if not name:
            raise KatcpSyntaxError("Command missing command name.")
        if not name.replace("-","").isalnum():
            raise KatcpSyntaxError("Command name should consist only of"
                                " alphanumeric characters and dashes (got %r)."
                                % (name,))
        if not name[0].isalpha():
            raise KatcpSyntaxError("Command name should start with an"
                                " alphabetic character (got %r)."
                                % (name,))

    def copy(self):
        """Return a shallow copy of the message object and its arguments."""
        return Message(self.mtype, self.name, self.arguments)

    def __str__(self):
        """Return Message serialized for transmission.

           @param self This object.
           @return Message encoded as a ASCII string.
           """
        if self.arguments:
            escaped_args = [self.ESCAPE_RE.sub(self._escape_match, x)
                            for x in self.arguments]
            escaped_args = [x or "\\@" for x in escaped_args]
            arg_str = " " + " ".join(escaped_args)
        else:
            arg_str = ""

        return "%s%s%s" % (self.TYPE_SYMBOLS[self.mtype], self.name, arg_str)

    def _escape_match(self, match):
        """Given a re.Match object, return the escape code for it."""
        return "\\" + self.REVERSE_ESCAPE_LOOKUP[match.group()]


    # * and ** magic useful here
    # pylint: disable-msg = W0142

    @classmethod
    def request(cls, name, *args):
        """Helper method for creating request messages."""
        return cls(cls.REQUEST, name, args)

    @classmethod
    def reply(cls, name, *args):
        """Helper method for creating reply messages."""
        return cls(cls.REPLY, name, args)

    @classmethod
    def inform(cls, name, *args):
        """Helper method for creating inform messages."""
        return cls(cls.INFORM, name, args)

    # pylint: enable-msg = W0142


class KatcpSyntaxError(ValueError):
    """Exception raised by parsers on encountering syntax errors."""
    pass


class MessageParser(object):
    """Parses lines into Message objects."""

    # We only want one public method
    # pylint: disable-msg = R0903

    ## @brief Copy of TYPE_SYMBOL_LOOKUP from Message.
    TYPE_SYMBOL_LOOKUP = Message.TYPE_SYMBOL_LOOKUP

    ## @brief Copy of ESCAPE_LOOKUP from Message.
    ESCAPE_LOOKUP = Message.ESCAPE_LOOKUP

    ## @brief Regular expression matching all special characters.
    SPECIAL_RE = re.compile(r"[\0\n\r\x1b\t ]")

    ## @brief Regular expression matching all escapes.
    UNESCAPE_RE = re.compile(r"\\(.?)")

    ## @brief Regular expresion matching KATCP whitespace (just space and tab)
    WHITESPACE_RE = re.compile(r"[ \t]+")

    def _unescape_match(self, match):
        """Given an re.Match, unescape the escape code it represents."""
        char = match.group(1)
        if char in self.ESCAPE_LOOKUP:
            return self.ESCAPE_LOOKUP[char]
        elif not char:
            raise KatcpSyntaxError("Escape slash at end of argument.")
        else:
            raise KatcpSyntaxError("Invalid escape character %r." % (char,))

    def _parse_arg(self, arg):
        """Parse an argument."""
        match = self.SPECIAL_RE.search(arg)
        if match:
            raise KatcpSyntaxError("Unescaped special %r." % (match.group(),))
        return self.UNESCAPE_RE.sub(self._unescape_match, arg)

    def parse(self, line):
        """Parse a line, return a Message.

           @param self This object.
           @param line a string to parse.
           @return the resulting Message.
           """
        # find command type and check validity
        if not line:
            raise KatcpSyntaxError("Empty message received.")

        type_char = line[0]
        if type_char not in self.TYPE_SYMBOL_LOOKUP:
            raise KatcpSyntaxError("Bad type character %r." % (type_char,))

        mtype = self.TYPE_SYMBOL_LOOKUP[type_char]

        # find command and arguments name
        # (removing possible empty argument resulting from whitespace at end of command)
        parts = self.WHITESPACE_RE.split(line)
        if not parts[-1]:
            del parts[-1]

        name = parts[0][1:]
        arguments = [self._parse_arg(x) for x in parts[1:]]

        return Message(mtype, name, arguments)


class DeviceMetaclass(type):
    """Metaclass for DeviceServer and DeviceClient classes.

       Collects up methods named request_* and adds
       them to a dictionary of supported methods on the class.
       All request_* methods must have a doc string so that help
       can be generated.  The same is done for inform_* and
       reply_* methods.
       """
    def __init__(mcs, name, bases, dct):
        """Constructor for DeviceMetaclass.  Should not be used directly.

           @param mcs The metaclass instance
           @param name The metaclass name
           @param bases List of base classes
           @param dct Class dict
        """
        super(DeviceMetaclass, mcs).__init__(name, bases, dct)
        mcs._request_handlers = {}
        mcs._inform_handlers = {}
        mcs._reply_handlers = {}
        def convert(prefix, name):
            """Convert a method name to the corresponding command name."""
            return name[len(prefix):].replace("_","-")
        for name in dir(mcs):
            if not callable(getattr(mcs, name)):
                continue
            if name.startswith("request_"):
                request_name = convert("request_", name)
                mcs._request_handlers[request_name] = getattr(mcs, name)
                assert(mcs._request_handlers[request_name].__doc__ is not None)
            elif name.startswith("inform_"):
                inform_name = convert("inform_", name)
                mcs._inform_handlers[inform_name] = getattr(mcs, name)
                assert(mcs._inform_handlers[inform_name].__doc__ is not None)
            elif name.startswith("reply_"):
                reply_name = convert("reply_", name)
                mcs._reply_handlers[reply_name] = getattr(mcs, name)
                assert(mcs._reply_handlers[reply_name].__doc__ is not None)


class KatcpDeviceError(Exception):
    """Exception raised by KATCP servers when errors occur will
       communicating with a device.  Note that socket.error can also be raised
       if low-level network exceptions occurs."""
    pass


class FailReply(Exception):
    """A custom exception which, when thrown in a request handler,
       causes DeviceServerBase to send a fail reply with the specified
       fail message, bypassing the generic exception handling, which
       would send a fail reply with a full traceback.
       """
    pass

class AsyncReply(Exception):
    """A custom exception which, when thrown in a request handler,
       indicates to DeviceServerBase that no reply has been returned
       by the handler but that the handler has arranged for a reply
       message to be sent at a later time.
       """
    pass

class DeviceServerBase(object):
    """Base class for device servers.

       Subclasses should add .request_* methods for dealing
       with request messages. These methods each take the client
       socket and msg objects as arguments and should return the
       reply message or raise an exception as a result. In these
       methods, the client socket should only be used as an argument
       to .inform().

       Subclasses can also add .inform_* and reply_* methods to handle
       those types of messages.

       Should a subclass need to generate inform messages it should
       do so using either the .inform() or .mass_inform() methods.

       Finally, this class should probably not be subclassed directly
       but rather via subclassing DeviceServer itself which implements
       common .request_* methods.
       """

    __metaclass__ = DeviceMetaclass

    def __init__(self, host, port, tb_limit=20, logger=log):
        """Create DeviceServer object.

           @param self This object.
           @param host String: host to listen on.
           @param port Integer: port to listen on.
           @param tb_limit Integer: maximum number of stack frames to
                           send in error traceback.
           @param logger Object: Logger to log to.
        """
        self._parser = MessageParser()
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._sock = self.bind(self._bindaddr)
        self._running = threading.Event()
        self._thread = None
        self._logger = logger

        # sockets and data
        self._socks = [] # list of client sockets
        self._waiting_chunks = {} # map from client sockets to partial messages
        self._sock_locks = {} # map from client sockets to socket sending locks

    def _log_msg(self, level_name, msg, name):
        """Create a katcp logging inform message.

           Usually this will be called from inside a DeviceLogger object,
           but it is also used by the methods in this class when errors
           need to be reported to the client.
           """
        return Message.inform("log",
                level_name,
                str(int(time.time() * 1000.0)), # time since epoch in ms
                name,
                msg,
        )

    def bind(self, bindaddr):
        """Create a listening server socket."""
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)
        sock.bind(bindaddr)
        sock.listen(5)
        return sock

    def add_socket(self, sock):
        """Add a client socket to the socket and chunk lists."""
        self._socks.append(sock)
        self._waiting_chunks[sock] = ""
        self._sock_locks[sock] = threading.Lock()

    def remove_socket(self, sock):
        """Remove a client socket from the socket and chunk lists."""
        sock.close()
        self._socks.remove(sock)
        if sock in self._waiting_chunks:
            del self._waiting_chunks[sock]
        if sock in self._sock_locks:
            del self._sock_locks[sock]

    def get_sockets(self):
        """Return the complete list of current client socket."""
        return list(self._socks)

    def handle_chunk(self, sock, chunk):
        """Handle a chunk of data for socket sock."""
        chunk = chunk.replace("\r", "\n")
        lines = chunk.split("\n")

        for line in lines[:-1]:
            full_line = self._waiting_chunks[sock] + line
            self._waiting_chunks[sock] = ""
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
                    self.inform(sock, self._log_msg("error", reason, "root"))
                else:
                    self.handle_message(sock, msg)

        self._waiting_chunks[sock] += lines[-1]

    def handle_message(self, sock, msg):
        """Handle messages of all types from clients."""
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
        """Dispatch a request message to the appropriate method."""
        send_reply = True
        if msg.name in self._request_handlers:
            try:
                reply = self._request_handlers[msg.name](self, sock, msg)
                assert (reply.mtype == Message.REPLY)
                assert (reply.name == msg.name)
                self._logger.info("%s OK" % (msg.name,))
            except AsyncReply, e:
                self._logger.info("%s ASYNC OK" % (msg.name,))
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
                    e_type, e_value, trace, self._tb_limit
                ))
                self._logger.error("Request %s FAIL: %s" % (msg.name, reason))
                reply = Message.reply(msg.name, "fail", reason)
        else:
            self._logger.error("%s INVALID: Unknown request." % (msg.name,))
            reply = Message.reply(msg.name, "invalid", "Unknown request.")
        if send_reply:
            try:
                self.send_message(sock, reply)
            except KatcpDeviceError, e:
                # already logged and client removed.
                pass

    def handle_inform(self, sock, msg):
        """Dispatch an inform message to the appropriate method."""
        if msg.name in self._inform_handlers:
            try:
                self._inform_handlers[msg.name](self, sock, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit
                ))
                self._logger.error("Inform %s FAIL: %s" % (msg.name, reason))
        else:
            self._logger.warn("%s INVALID: Unknown inform." % (msg.name,))

    def handle_reply(self, sock, msg):
        """Dispatch a reply message to the appropriate method."""
        if msg.name in self._reply_handlers:
            try:
                self._reply_handlers[msg.name](self, sock, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit
                ))
                self._logger.error("Reply %s FAIL: %s" % (msg.name, reason))
        else:
            self._logger.warn("%s INVALID: Unknown reply." % (msg.name,))

    def send_message(self, sock, msg):
        """Send an arbitrary message to a particular client."""
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        # TODO: should probably implement this as a queue of sockets and messages to send.
        #       and have the queue processed in the main loop
        data = str(msg) + "\n"
        datalen = len(data)
        totalsent = 0

        # sends a locked per-socket -- i.e. only one send per socket at a time
        lock = self._sock_locks.get(sock)
        if lock is None:
            raise KatcpDeviceError("Attempt to send to a socket which is no longer a client.")

        lock.acquire()
        try:
            while totalsent < datalen:
                try:
                    sent = sock.send(data[totalsent:])
                except socket.error, e:
                    if len(e.args) == 2 and e.args[0] == errno.EAGAIN:
                        continue
                    else:
                        try:
                            client_name = sock.getpeername()
                        except socket.error:
                            client_name = "<disconnected client>"
                        msg = "Failed to send message to client %s (%s)" % (client_name, e)
                        self._logger.error(msg)
                        self.remove_socket(sock)
                        raise KatcpDeviceError(msg)

                if sent == 0:
                    try:
                        client_name = sock.getpeername()
                    except socket.error:
                        client_name = "<disconnected client>"
                    msg = "Could not send data to client %s, closing socket." % (client_name,)
                    self._logger.error(msg)
                    self.remove_socket(sock)
                    raise KatcpDeviceError(msg)

                totalsent += sent
        finally:
            lock.release()

    def inform(self, sock, msg):
        """Send an inform messages to a particular client."""
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        assert (msg.mtype == Message.INFORM)
        self.send_message(sock, msg)

    def mass_inform(self, msg):
        """Send an inform message to all clients."""
        assert (msg.mtype == Message.INFORM)
        for sock in self._socks:
            if sock is self._sock:
                continue
            self.inform(sock, msg)

    def run(self):
        """Listen for clients and process their requests."""
        timeout = 0.5 # s

        self._running.set()
        while self._running.isSet():
            all_socks = self._socks + [self._sock]
            readers, _writers, errors = select.select(
                all_socks, [], all_socks, timeout
            )

            for sock in errors:
                if sock is self._sock:
                    # server socket died, attempt restart
                    self._sock = self.bind(self._bindaddr)
                else:
                    # client socket died, remove it
                    self.remove_socket(sock)
                    self.on_client_disconnect(sock, "Client socket died", False)

            for sock in readers:
                if sock is self._sock:
                    client, addr = sock.accept()
                    client.setblocking(0)
                    self.mass_inform(Message.inform("client-connected",
                        "New client connected from %s" % (addr,)))
                    self.add_socket(client)
                    self.on_client_connect(client)
                else:
                    try:
                        chunk = sock.recv(4096)
                    except socket.error:
                        # an error when sock was within ready list presumably
                        # means the client needs to be ditched.
                        chunk = ""
                    if chunk:
                        self.handle_chunk(sock, chunk)
                    else:
                        # no data, assume socket EOF
                        self.remove_socket(sock)
                        self.on_client_disconnect(sock, "Socket EOF", False)

        for sock in list(self._socks):
            self.on_client_disconnect(sock, "Device server shutting down.", True)
            self.remove_socket(sock)

        self._sock.close()

    def start(self, timeout=None):
        """Start the server in a new thread."""
        if self._thread:
            raise RuntimeError("Device server already started.")

        self._thread = threading.Thread(target=self.run)
        self._thread.start()
        if timeout:
            self._running.wait(timeout)
            if not self._running.isSet():
                raise RuntimeError("Device server failed to start.")

    def join(self, timeout=None):
        """Rejoin the server thread."""
        if not self._thread:
            raise RuntimeError("Device server thread not started.")

        self._thread.join(timeout)
        if not self._thread.isAlive():
            self._thread = None

    def stop(self, timeout=1.0):
        """Stop a running server (from another thread).

           @param self This object.
           @param timeout Seconds to wait for server to have *started* (as a float).
           @return None
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
           """
        pass

    def on_client_disconnect(self, sock, msg, sock_valid):
        """Called before a client connection is closed.

           Subclasses should override if they wish to send clients
           message or perform house-keeping at this point. The server
           cannot guarantee this will be called (for example, the client
           might drop the connection). The message parameter contains
           the reason for the disconnection.

           @param sock Client socket being disconnected.
           @param msg Reason client is being disconnected.
           @param sock_valid True if sock is still openf for sending,
                             False otherwise.
           """
        pass


class DeviceServer(DeviceServerBase):
    """Implements some standard messages on top of DeviceServerBase.

       Inform messages handled are:
         - version (sent on connect)
         - build-state (sent on connect)
         - log (via self.log.warn(...), etc)
         - disconnect
         - client-connected

       Requests handled are:
         - halt
         - help
         - log-level
         - restart (if self.schedule_restart(...) implemented)
         - client-list
         - sensor-list
         - sensor-sampling
         - sensor-value
         - watchdog

       Unhandled standard messages are:
         ?configure
         ?mode

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
        """Create a DeviceServer."""
        super(DeviceServer, self).__init__(*args, **kwargs)
        self.log = DeviceLogger(self)
        self._sensors = {} # map names to sensor objects
        self._reactor = SampleReactor()
        # map client sockets to map of sensors -> sampling strategies
        self._strategies = {}
        self.setup_sensors()

    # pylint: enable-msg = W0142

    def on_client_connect(self, sock):
        """Inform client of build state and version on connect."""
        self._strategies[sock] = {} # map of sensors -> sampling strategies
        self.inform(sock, Message.inform("version", self.version()))
        self.inform(sock, Message.inform("build-state", self.build_state()))

    def on_client_disconnect(self, sock, msg, sock_valid):
        """Inform client it is about to be disconnected."""
        if sock in self._strategies:
            for sensor, strategy in self._strategies[sock].items():
                self._reactor.remove_strategy(strategy)

        if sock_valid:
            self.inform(sock, Message.inform("disconnect", msg))

    def build_state(self):
        """Return a build state string in the form name-major.minor[(a|b|rc)n]"""
        return "%s-%s.%s%s" % self.BUILD_INFO

    def version(self):
        """Return a version string of the form type-major.minor."""
        return "%s-%s.%s" % self.VERSION_INFO

    def add_sensor(self, sensor):
        """Add a sensor to the device.

           Should only be called inside .setup_sensors().
           """
        name = sensor.name
        self._sensors[name] = sensor

    def get_sensor(self, sensor_name):
        """Fetch the sensor with the given name."""
        sensor = self._sensors.get(sensor_name, None)
        if not sensor:
            raise ValueError("Unknown sensor '%s'." % (sensor_name,))
        return sensor

    def get_sensors(self):
        """Fetch a list of all sensors"""
        return self._sensors.values()

    def schedule_restart(self):
        """Schedule a restart.

           Unimplemented by default since this depends on the details
           of how subclasses choose to manage the .run() and .stop()
           methods.
           """
        raise NotImplementedError("Server restarts not implemented for this"
                                    " device.")

    def setup_sensors(self):
        """Populate the dictionary of sensors.

           Unimplemented by default -- subclasses should add their sensors
           here or pass if there are no sensors.

           e.g. def setup_sensors(self):
                    self.add_sensor(Sensor(...))
                    self.add_sensor(Sensor(...))
                    ...
           """
        raise NotImplementedError("Device server subclasses must implement"
                                    " setup_sensors.")

    # request implementations

    # all requests take sock and msg arguments regardless of whether
    # they're used
    # pylint: disable-msg = W0613

    def request_halt(self, sock, msg):
        """Halt the server."""
        self.stop()
        # this message makes it through because stop
        # only registers in .run(...) after the reply
        # has been sent.
        return Message.reply("halt", "ok")

    def request_help(self, sock, msg):
        """Return help on the available request methods."""
        if not msg.arguments:
            for name, method in sorted(self._request_handlers.items()):
                doc = method.__doc__
                self.inform(sock, Message.inform("help", name, doc))
            num_methods = len(self._request_handlers)
            return Message.reply("help", "ok", str(num_methods))
        else:
            name = msg.arguments[0]
            if name in self._request_handlers:
                method = self._request_handlers[name]
                doc = method.__doc__
                self.inform(sock, Message.inform("help", name, doc))
                return Message.reply("help", "ok", "1")
            return Message.reply("help", "fail", "Unknown request method.")

    def request_log_level(self, sock, msg):
        """Query or set the current logging level."""
        if msg.arguments:
            self.log.set_log_level_by_name(msg.arguments[0])
        return Message.reply("log-level", "ok", self.log.level_name())

    def request_restart(self, sock, msg):
        """Restart the device server."""
        self.schedule_restart()
        return Message.reply("restart", "ok")

    def request_client_list(self, sock, msg):
        """Request the list of connected clients."""
        clients = self.get_sockets()
        num_clients = len(clients)
        for client in clients:
            addr = client.getsockname()
            self.inform(sock, Message.inform("client-list", addr))
        return Message.reply("client-list", "ok", str(num_clients))

    def request_sensor_list(self, sock, msg):
        """Request the list of sensors."""
        if not msg.arguments:
            for name, sensor in sorted(self._sensors.iteritems(), key=lambda x: x[0]):
                self.inform(sock, Message.inform("sensor-list",
                    name, sensor.description, sensor.units, sensor.stype,
                    *sensor.formatted_params))
            return Message.reply("sensor-list",
                    "ok", str(len(self._sensors)))
        else:
            name = msg.arguments[0]
            if name in self._sensors:
                sensor = self._sensors[name]
                self.inform(sock, Message.inform("sensor-list",
                    name, sensor.description, sensor.units, sensor.stype,
                    *sensor.formatted_params))
                return Message.reply("sensor-list", "ok", "1")
            else:
                return Message.reply("sensor-list", "fail",
                                                    "Unknown sensor name.")

    def request_sensor_value(self, sock, msg):
        """Request the value of a sensor."""
        if not msg.arguments:
            for name, sensor in sorted(self._sensors.iteritems(), key=lambda x: x[0]):
                timestamp_ms, status, value = sensor.read_formatted()
                self.inform(sock, Message.inform("sensor-value",
                    timestamp_ms, "1", name, status, value))
            return Message.reply("sensor-value",
                    "ok", str(len(self._sensors)))
        else:
            name = msg.arguments[0]
            if name in self._sensors:
                sensor = self._sensors[name]
                timestamp_ms, status, value = sensor.read_formatted()
                self.inform(sock, Message.inform("sensor-value",
                    timestamp_ms, "1", name, status, value))
                return Message.reply("sensor-value", "ok", "1")
            else:
                return Message.reply("sensor-value", "fail",
                                                    "Unknown sensor name.")

    def request_sensor_sampling(self, sock, msg):
        """Configure or query the way a sensor is sampled."""
        if not msg.arguments:
            return Message.reply("sensor-sampling", "fail",
                                                    "No sensor name given.")
        name = msg.arguments[0]

        if name not in self._sensors:
            return Message.reply("sensor-sampling", "fail",
                                                    "Unknown sensor name.")
        sensor = self._sensors[name]

        if len(msg.arguments) > 1:
            # attempt to set sampling strategy
            strategy = msg.arguments[1]
            params = msg.arguments[2:]

            def inform_callback(cb_msg):
                """Inform callback for sensor strategy."""
                self.inform(sock, cb_msg)

            new_strategy = SampleStrategy.get_strategy(strategy,
                                        inform_callback, sensor, *params)

            old_strategy = self._strategies[sock].get(sensor, None)
            if old_strategy is not None:
                self._reactor.remove_strategy(old_strategy)

            # todo: replace isinstance check with something better
            if isinstance(new_strategy, SampleNone):
                if sensor in self._strategies[sock]:
                    del self._strategies[sock][sensor]
            else:
                self._strategies[sock][sensor] = new_strategy
                self._reactor.add_strategy(new_strategy)

        current_strategy = self._strategies[sock].get(sensor, None)
        if not current_strategy:
            current_strategy = SampleStrategy.get_strategy("none", lambda msg: None, sensor)

        strategy, params = current_strategy.get_sampling_formatted()
        return Message.reply("sensor-sampling", "ok", name, strategy, *params)


    def request_watchdog(self, sock, msg):
        """Check that the server is still alive."""
        # not a function, just doesn't use self
        # pylint: disable-msg = R0201
        return Message.reply("watchdog", "ok")

    # pylint: enable-msg = W0613

    def run(self):
        """Override DeviceServerBase.run() to ensure that the reactor thread is
           running at the same time.
           """
        self._reactor.start()
        try:
            super(DeviceServer, self).run()
        finally:
            self._reactor.stop()
            self._reactor.join()


class Sensor(object):
    """Base class for sensor classes."""

    # Sensor needs the instance attributes it has and
    # is an abstract class used only outside this module
    # pylint: disable-msg = R0902

    # Type names and formatters
    #
    # Formatters take the sensor object and the value to
    # be formatted as arguments. They may raise exceptions
    # if the value cannot be formatted.
    #
    # Parsers take the sensor object and the value to
    # parse as arguments
    #
    # type -> (name, formatter, parser)
    INTEGER, FLOAT, BOOLEAN, LRU, DISCRETE, STRING, TIMESTAMP = range(7)

    ## @brief Mapping from sensor type to tuple containing the type name,
    #  a kattype with functions to format and parse a value and a
    #  default value for sensors of that type.
    SENSOR_TYPES = {
        INTEGER: (Int, 0),
        FLOAT: (Float, 0.0),
        BOOLEAN: (Bool, False),
        LRU: (Lru, Lru.LRU_NOMINAL),
        DISCRETE: (Discrete, "unknown"),
        STRING: (Str, ""),
        TIMESTAMP: (Timestamp, 0.0),
    }

    # map type strings to types
    SENSOR_TYPE_LOOKUP = dict((v[0].name, k) for k, v in SENSOR_TYPES.items())

    # Sensor status constants
    UNKNOWN, NOMINAL, WARN, ERROR, FAILURE = range(5)

    ## @brief Mapping from sensor status to status name.
    STATUSES = {
        UNKNOWN: 'unknown',
        NOMINAL: 'nominal',
        WARN: 'warn',
        ERROR: 'error',
        FAILURE: 'failure',
    }

    ## @brief Mapping from status name to sensor status.
    STATUS_NAMES = dict((v, k) for k, v in STATUSES.items())

    # LRU sensor values
    LRU_NOMINAL, LRU_ERROR = Lru.LRU_NOMINAL, Lru.LRU_ERROR

    ## @brief Mapping from LRU value constant to LRU value name.
    LRU_VALUES = Lru.LRU_VALUES

    # LRU_VALUES not found by pylint
    # pylint: disable-msg = E0602

    ## @brief Mapping from LRU value name to LRU value constant.
    LRU_CONSTANTS = dict((v, k) for k, v in LRU_VALUES.items())

    # pylint: enable-msg = E0602

    ## @brief Number of milliseconds in a second.
    MILLISECOND = 1000

    ## @brief kattype Timestamp instance for encoding and decoding timestamps
    TIMESTAMP_TYPE = Timestamp()

    ## @var stype
    # @brief Sensor type constant.

    ## @var name
    # @brief Sensor name.

    ## @var description
    # @brief String describing the sensor.

    ## @var units
    # @brief String contain the units for the sensor value.

    ## @var params
    # @brief List of strings containing the additional parameters (length and interpretation
    # are specific to the sensor type)

    def __init__(self, sensor_type, name, description, units, params=None):
        """Instantiate a new sensor object.

           Subclasses will usually pass in a fixed sensor_type which should
           be one of the sensor type constants. The list params if set will
           have its values formatter by the type formatter for the given
           sensor type.
           """
        if params is None:
            params = []

        self._sensor_type = sensor_type
        self._observers = set()
        self._timestamp = time.time()
        self._status = Sensor.UNKNOWN

        typeclass, self._value = self.SENSOR_TYPES[sensor_type]

        if self._sensor_type in [Sensor.INTEGER, Sensor.FLOAT]:
            self._kattype = typeclass(params[0], params[1])
        elif self._sensor_type == Sensor.DISCRETE:
            self._kattype = typeclass(params)
        else:
            self._kattype = typeclass()
        self._formatter = self._kattype.pack
        self._parser = self._kattype.unpack
        self.stype = self._kattype.name

        self.name = name
        self.description = description
        self.units = units
        self.params = params
        self.formatted_params = [self._formatter(p, True) for p in params]

        if self._sensor_type == Sensor.DISCRETE:
            self._value = self.params[0]

    def attach(self, observer):
        """Attach an observer to this sensor. The observer must support a call
           to update(sensor)
           """
        self._observers.add(observer)

    def detach(self, observer):
        """Detach an observer from this sensor."""
        self._observers.discard(observer)

    def notify(self):
        """Notify all observers of changes to this sensor."""
        # copy list before iterating in case new observers arrive
        for o in list(self._observers):
            o.update(self)

    def parse_value(self, s_value):
        """Parse a value from a string.

           @param self This object.
           @param s_value the value of the sensor (as a string)
           @return None
           """
        return self._parser(s_value)

    def set(self, timestamp, status, value):
        """Set the current value of the sensor.

           @param self This object.
           @param timestamp standard python time double
           @param status the status of the sensor
           @param value the value of the sensor
           @return None
           """
        self._timestamp, self._status, self._value = timestamp, status, value
        self.notify()

    def set_formatted(self, raw_timestamp, raw_status, raw_value):
        """Set the current value of the sensor.

           @param self This object
           @param timestamp KATCP formatted timestamp string
           @param status KATCP formatted sensor status string
           @param value KATCP formatted sensor value
           @return None
           """
        timestamp = self.TIMESTAMP_TYPE.decode(raw_timestamp)
        status = self.STATUS_NAMES[raw_status]
        value = self.parse_value(raw_value)
        self.set(timestamp, status, value)

    def read_formatted(self):
        """Read the sensor and return a timestamp_ms, status, value tuple.

           All values are strings formatted as specified in the Sensor Type
           Formats in the katcp specification.
           """
        timestamp, status, value = self.read()
        return (self.TIMESTAMP_TYPE.encode(timestamp),
                self.STATUSES[status],
                self._formatter(value, True))

    def read(self):
        """Read the sensor and return a timestamp, status, value tuple.

           - timestamp: the timestamp in since the Unix epoch as a float.
           - status: Sensor status constant.
           - value: int, float, bool, Sensor value constant (for lru values)
               or str (for discrete values)

           Subclasses should implement this method.
           """
        return (self._timestamp, self._status, self._value)

    def set_value(self, value, status=NOMINAL, timestamp=None):
        """Check and then set the value of the sensor."""
        self._kattype.check(value)
        if timestamp is None:
            timestamp = time.time()
        self.set(timestamp, status, value)

    def value(self):
        """Read the current sensor value."""
        return self.read()[2]

    @classmethod
    def parse_type(cls, type_string):
        """Parse KATCP formatted type code into Sensor type constant."""
        if type_string in cls.SENSOR_TYPE_LOOKUP:
            return cls.SENSOR_TYPE_LOOKUP[type_string]
        else:
            raise KatcpSyntaxError("Invalid sensor type string %s" % type_string)

    @classmethod
    def parse_params(cls, sensor_type, formatted_params):
        """Parse KATCP formatted parameters into Python values."""
        typeclass, _value = cls.SENSOR_TYPES[sensor_type]
        if sensor_type == cls.DISCRETE:
            kattype = typeclass([])
        else:
            kattype = typeclass()
        return [kattype.decode(x) for x in formatted_params]

class DeviceLogger(object):
    """Object for logging messages from a DeviceServer.

       Log messages are logged at a particular level and under
       a particular name. Names use dotted notation to form
       a virtual hierarchy of loggers with the device."""

    # level values are used as indexes into the LEVELS list
    # so these to lists should be in the same order
    ALL, TRACE, DEBUG, INFO, WARN, ERROR, FATAL, OFF = range(8)

    ## @brief List of logging level names.
    LEVELS = [ "all", "trace", "debug", "info", "warn",
               "error", "fatal", "off" ]

    def __init__(self, device_server, root_logger="root"):
        """Create a DeviceLogger.

           @param self This object.
           @param device_server DeviceServer this logger logs for.
           @param root_logger String containing root logger name.
           """
        self._device_server = device_server
        self._log_level = self.WARN
        self._root_logger_name = root_logger

    def level_name(self, level=None):
        """Return the name of the given level value.

           If level is None, return the name of the current level."""
        if level is None:
            level = self._log_level
        return self.LEVELS[level]

    def set_log_level(self, level):
        """Set the logging level."""
        self._log_level = level

    def set_log_level_by_name(self, level_name):
        """Set the logging level using a level name."""
        try:
            level = self.LEVELS.index(level_name)
        except ValueError:
            raise ValueError("Unknown logging level name '%s'" % (level_name,))
        self._log_level = level

    def log(self, level, msg, name=None):
        """Log a message and inform all clients."""
        if level >= self._log_level:
            if name is None:
                name = self._root_logger_name
            self._device_server.mass_inform(
                self._device_server._log_msg(self.level_name(level), msg, name)
            )

    def trace(self, msg, name=None):
        """Log a trace message."""
        self.log(self.TRACE, msg, name)

    def debug(self, msg, name=None):
        """Log a debug message."""
        self.log(self.DEBUG, msg, name)

    def info(self, msg, name=None):
        """Log an info message."""
        self.log(self.INFO, msg, name)

    def warn(self, msg, name=None):
        """Log an warning message."""
        self.log(self.WARN, msg, name)

    def error(self, msg, name=None):
        """Log an error message."""
        self.log(self.ERROR, msg, name)

    def fatal(self, msg, name=None):
        """Log a fatal error message."""
        self.log(self.FATAL, msg, name)

    @staticmethod
    def log_to_python(logger, msg):
        """Log a KATCP logging message to a Python logger."""
        (level, timestamp, name, message) = tuple(msg.arguments)
        #created = float(timestamp) * 1e-6
        #msecs = int(timestamp) % 1000
        log_string = "%s %s: %s" % (timestamp, name, message)
        logger.log({"trace": 0,
                    "debug": logging.DEBUG,
                    "info": logging.INFO,
                    "warn": logging.WARN,
                    "error": logging.ERROR,
                    "fatal": logging.FATAL}[level], log_string)#, extra={"created": created})

