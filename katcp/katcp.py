"""Utilities for dealing with KAT device control
   language messages.
   """

import socket
import select
import threading
import traceback
import logging
import sys
import re
import time
from sampling import SampleReactor, SampleStrategy

# logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("katcp")


class Message(object):
    """Represents a KAT device control language message."""

    # Message types
    REQUEST, REPLY, INFORM = range(3)

    # Reply codes
    # TODO: make use of reply codes in device client and server
    OK, FAIL, INVALID = "ok", "fail", "invalid"

    TYPE_NAMES = {
        REQUEST: "REQUEST",
        REPLY: "REPLY",
        INFORM: "INFORM",
    }

    TYPE_SYMBOLS = {
        REQUEST: "?",
        REPLY: "!",
        INFORM: "#",
    }

    # pylint fails to realise TYPE_SYMBOLS is defined
    # pylint: disable-msg = E0602
    TYPE_SYMBOL_LOOKUP = dict((v, k) for k, v in TYPE_SYMBOLS.items())
    # pylint: enable-msg = E0602

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
    REVERSE_ESCAPE_LOOKUP = dict((v, k) for k, v in ESCAPE_LOOKUP.items())
    # pylint: enable-msg = E0602

    ESCAPE_RE = re.compile(r"[\\ \0\n\r\x1b\t]")

    def __init__(self, mtype, name, arguments=None):
        self.mtype = mtype
        self.name = name
        if arguments is None:
            self.arguments = []
        else:
            self.arguments = list(arguments)

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

    def __str__(self):
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

    TYPE_SYMBOL_LOOKUP = Message.TYPE_SYMBOL_LOOKUP
    ESCAPE_LOOKUP = Message.ESCAPE_LOOKUP
    SPECIAL_RE = re.compile(r"[\0\n\r\x1b\t ]")
    UNESCAPE_RE = re.compile(r"\\(.?)")

    def __init__(self):
        pass

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
        """Parse a line, return a Message."""
        # find command type and check validity
        if not line:
            raise KatcpSyntaxError("Empty message received.")

        type_char = line[0]
        if type_char not in self.TYPE_SYMBOL_LOOKUP:
            raise KatcpSyntaxError("Bad type character %r." % (type_char,))

        mtype = self.TYPE_SYMBOL_LOOKUP[type_char]

        # find command and arguments name
        parts = line.split()

        name = parts[0][1:]
        arguments = [self._parse_arg(x) for x in parts[1:]]

        return Message(mtype, name, arguments)


class DeviceClient(object):
    """Device client proxy.

       Subclasses should implement .reply() and .inform() to
       take actions when reply and inform message arrive.

       Request messages can be sent by calling .request().
       """

    def __init__(self, host, port):
        self._parser = MessageParser()
        self._bindaddr = (host, port)
        self._sock = None
        self._waiting_chunk = ""
        self._running = threading.Event()
        self._thread = None

    def reply(self, msg):
        """Called when a reply message arrives."""
        pass

    def inform(self, msg):
        """Called when an inform message arrives."""
        pass

    def request(self, msg):
        """Send a request messsage."""
        assert(msg.mtype == Message.REQUEST)
        self._sock.send(str(msg) + "\n")

    def send_message(self, msg):
        """Send any kind of message."""
        self._sock.send(str(msg) + "\n")

    def connect(self):
        """Connect or reconnect to the server."""
        if self._sock is not None:
            self._sock.close()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect(self._bindaddr)
        self._waiting_chunk = ""

    def handle_chunk(self, chunk):
        """Handle a chunk of data from the server."""
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
        """Handle a message from the server."""
        if msg.mtype == Message.INFORM:
            self.inform(msg)
        elif msg.mtype == Message.REPLY:
            self.reply(msg)
        else:
            log.error("Unexpect message type from server ['%s']." % (msg,))

    def run(self):
        """Process reply and inform messages from the server."""
        timeout = 1.0 # s
        self.connect()

        self._running.set()
        while self._running.isSet():
            readers, _writers, errors = select.select(
                [self._sock], [], [self._sock], timeout
            )

            if errors:
                # attempt to reconnect
                self.connect()

            if readers:
                chunk = self._sock.recv(4096)
                if chunk:
                    self.handle_chunk(chunk)
                else:
                    # EOF from server - attempt reconnect
                    self.connect()

        self._sock.close()
        self._sock = None

    def start(self, timeout=None):
        """Start the client in a new thread."""
        if self._thread:
            raise RuntimeError("Device client already started.")

        self._thread = threading.Thread(target=self.run)
        self._thread.start()
        if timeout:
            self._running.wait(timeout)
            if not self._running.isSet():
                raise RuntimeError("Device client failed to start.")

    def join(self, timeout=None):
        """Rejoin the client thread."""
        if not self._thread:
            raise RuntimeError("Device client thread not started.")

        self._thread.join(timeout)
        if not self._thread.isAlive():
            self._thread = None

    def stop(self):
        """Stop a running client (from another thread)."""
        self._running.wait(1.0)
        if not self._running.isSet():
            raise RuntimeError("Attempt to stop client that wasn't running.")
        self._running.clear()

    def running(self):
        """Whether the client is running."""
        return self._running.isSet()


class DeviceServerMetaclass(type):
    """Metaclass for DeviceServer classes.

       Collects up methods named request_* and adds
       them to a dictionary off supported methods on the class.
       All request_* methods must have a doc string so that help
       can be generated.
       """
    def __init__(mcs, name, bases, dct):
        super(DeviceServerMetaclass, mcs).__init__(name, bases, dct)
        mcs._request_handlers = {}
        mcs._inform_handlers = {}
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


class DeviceServerBase(object):
    """Base class for device servers.

       Subclasses should add .request_* methods for dealing
       with requests messages. These methods each take the client
       socket and msg objects as arguments and should return the
       reply message or raise an exception as a result. The client
       socket should only be used as an argument to .inform(). 
       
       Should a subclass need to generate inform messages it should
       do so using either the .inform() or .mass_inform() methods.

       Finally, this class should probably not be subclassed directly
       but rather via subclassing DeviceServer itself which implements
       common .request_* methods.
       """

    __metaclass__ = DeviceServerMetaclass

    def __init__(self, host, port, tb_limit=20):
        """Create DeviceServer object.

           host, port: where to listen.
           tb_limit: maximum number of stack frames to send in error
                     traceback.
        """
        self._parser = MessageParser()
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._sock = self.bind(self._bindaddr)
        self._running = threading.Event()
        self._thread = None

        # sockets and data
        self._socks = [] # list of client sockets
        self._waiting_chunks = {} # map from client sockets to partial messages

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

    def remove_socket(self, sock):
        """Remove a client socket from the socket and chunk lists."""
        sock.close()
        self._socks.remove(sock)
        if sock in self._waiting_chunks:
            del self._waiting_chunks[sock]

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
                    log.error("BAD COMMAND: %s" % (reason,))
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
        else:
            reason = "Unexpected reply message !%s received by server." \
                     % (msg.name,)
            self.inform(sock, self._log_msg("error", reason, "root"))

    def handle_request(self, sock, msg):
        """Dispatch a request message to the appropriate method."""
        if msg.name in self._request_handlers:
            try:
                reply = self._request_handlers[msg.name](self, sock, msg)
                assert (reply.mtype == Message.REPLY)
                assert (reply.name == msg.name)
                log.info("%s OK" % (msg.name,))
            # We do want to catch everything that inherits from Exception
            # pylint: disable-msg = W0703
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit
                ))
                log.error("Request %s FAIL: %s" % (msg.name, reason))
                reply = Message.reply(msg.name, "fail", reason)
        else:
            log.error("%s INVALID: Unknown request." % (msg.name,))
            reply = Message.reply(msg.name, "invalid", "Unknown request.")
        sock.send(str(reply) + "\n")

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
                log.error("Inform %s FAIL: %s" % (msg.name, reason))
        else:
            log.warn("%s INVALID: Unknown inform." % (msg.name,))

    def inform(self, sock, msg):
        """Send an inform messages to a particular client."""
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        assert (msg.mtype == Message.INFORM)
        sock.send(str(msg) + "\n")

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

            for sock in readers:
                if sock is self._sock:
                    client, addr = sock.accept()
                    client.setblocking(0)
                    if self._socks:
                        old_client = self._socks[0]
                        self.on_client_disconnect(old_client,
                            "New client connected from %s" % (addr,))
                        self.remove_socket(old_client)
                    self.add_socket(client)
                    self.on_client_connect(client)
                else:
                    chunk = sock.recv(4096)
                    if chunk:
                        self.handle_chunk(sock, chunk)
                    else:
                        # no data, assume socket EOF
                        self.remove_socket(sock)

        for sock in list(self._socks):
            self.on_client_disconnect(sock, "Device server shutting down.")
            self.remove_socket(sock)

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

    def stop(self):
        """Stop a running server (from another thread)."""
        self._running.wait(1.0)
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

    def on_client_disconnect(self, sock, msg):
        """Called before a client connection is closed.

           Subclasses should override if they wish to send clients
           message or perform house-keeping at this point. The server
           cannot guarantee this will be called (for example, the client
           might drop the connection). The message parameter contains
           the reason for the disconnection.
           """
        pass


class DeviceServer(DeviceServerBase):
    """Implements some standard messages on top of DeviceServerBase.

       Inform messages handled are:
         - version (sent on connect)
         - build-state (sent on connect)
         - log (via self.log.warn(...), etc)
         - disconnect

       Requests handled are:
         - halt
         - help
         - log-level
         - restart (if self.schedule_restart(...) implemented)
         - sensor-list
         - sensor-sampling
         - watchdog

       Unhandled standard messages are:
         ?configure
         ?mode

       Subclasses can define the tuple VERSION_INFO to set the server
       name, major and minor version numbers. EXTRA_VERSION_INFO can
       be defined to give a string describing releases within a
       version, for example "rc1".

       Subclasses must override the .setup_sensors() method. If they
       have no sensors to register, the method should just be a pass.
       """

    # DeviceServer has a lot of methods because there is a method
    # per request type and it's an abstract class which is only
    # used outside this module
    # pylint: disable-msg = R0904

    VERSION_INFO = ("device_stub", 0, 1)
    BUILD_INFO = ("name", 0, 1, "")

    # * and ** magic fine here
    # pylint: disable-msg = W0142

    def __init__(self, *args, **kwargs):
        super(DeviceServer, self).__init__(*args, **kwargs)
        self.log = DeviceLogger(self)
        self._sensors = {} # map names to sensor objects
        self._reactor = SampleReactor()
        self.setup_sensors()

    # pylint: enable-msg = W0142

    def on_client_connect(self, sock):
        """Inform client of build state and version on connect."""
        self.inform(sock, Message.inform("version", self.version()))
        self.inform(sock, Message.inform("build-state", self.build_state()))

    def on_client_disconnect(self, sock, msg):
        """Inform client it is about to be disconnected."""
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
        self._reactor.add_sensor(SampleStrategy.get_strategy("none", self, sensor))

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
                    self.add_sensor("a.b.sensor_c", Sensor(...))
                    self.add_sensor("a.c.d", Sensor(...))
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

    def request_sensor_list(self, sock, msg):
        """Request the list of sensors."""
        if not msg.arguments:
            for name, sensor in self._sensors.iteritems():
                self.inform(sock, Message.inform("sensor-type",
                    name, sensor.stype, sensor.description, sensor.units,
                    *sensor.params))
                timestamp_ms, status, value = sensor.read_formatted()
                self.inform(sock, Message.inform("sensor-status",
                    timestamp_ms, "1", name, status, value))
            return Message.reply("sensor-list",
                    "ok", str(len(self._sensors)))
        else:
            name = msg.arguments[0]
            if name in self._sensors:
                sensor = self._sensors[name]
                self.inform(sock, Message.inform("sensor-type",
                    name, sensor.stype, sensor.description, sensor.units,
                    *sensor.params))
                timestamp_ms, status, value = sensor.read_formatted()
                self.inform(sock, Message.inform("sensor-status",
                    timestamp_ms, "1", name, status, value))
                return Message.reply("sensor-list", "ok", "1")
            else:
                return Message.reply("sensor-list", "fail",
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
            self._reactor.add_sensor(SampleStrategy.get_strategy(strategy,
                                        self, sensor, *params))

        strategy, params = self._reactor.get_sampling_formatted(name)
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
    INTEGER, FLOAT, BOOLEAN, LRU, DISCRETE = range(5)
    SENSOR_TYPES = {
        INTEGER: ("integer", lambda sensor, value: "%d" % (value,),
                             lambda sensor, value: int(value), 0),
        FLOAT: ("float", lambda sensor, value: "%e" % (value,),
                         lambda sensor, value: float(value), 0.0),
        BOOLEAN: ("boolean", lambda sensor, value: value and "1" or "0",
                             lambda sensor, value: value == "1", "0"),
        LRU: ("lru", lambda sensor, value: sensor.LRU_VALUES[value],
                     lambda sensor, value: sensor.LRU_CONSTANTS[value], 
                     lambda sensor: sensor.LRU_NOMINAL),
        DISCRETE: ("discrete", lambda sensor, value: value,
                               lambda sensor, value: value, "unknown"),
    }

    # Sensor status constants
    UNKNOWN, NOMINAL, WARN, ERROR, FAILURE = range(5)
    STATUSES = {
        UNKNOWN: 'unknown',
        NOMINAL: 'nominal',
        WARN: 'warn',
        ERROR: 'error',
        FAILURE: 'failure',
    }

    # LRU sensor values
    LRU_NOMINAL, LRU_ERROR = range(2)
    LRU_VALUES = {
        LRU_NOMINAL: "nominal",
        LRU_ERROR: "error",
    }

    # LRU_VALUES not found by pylint
    #
    # pylint: disable-msg = E0602
    LRU_CONSTANTS = dict((v, k) for k, v in LRU_VALUES.items())
    # pylint: enable-msg = E0602
    
    MILLISECOND = 1000

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
        
        self.stype, self._formatter, self._parser, self._value = \
            self.SENSOR_TYPES[sensor_type]
        self.name = name
        self.description = description
        self.units = units
        self.params = [self._formatter(self, p) for p in params]
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
        for o in self._observers:
            o.update(self)
            
    def set(self, timestamp, status, value):
        """Set the current value of the sensor. 
           @param timestamp standard python time double
           @param status the status of the sensor
           @param value the value of the sensor
           """
        self._timestamp, self._status, self._value = timestamp, status, value
        self.notify()

    def read_formatted(self):
        """Read the sensor and return a timestamp_ms, status, value tuple.

           All values are strings formatted as specified in the Sensor Type
           Formats in the katcp specification.
           """
        timestamp, status, value = self.read()
        return ("%d" % (int(timestamp * Sensor.MILLISECOND),),
                self.STATUSES[status],
                self._formatter(self, value))

    def read(self):
        """Read the sensor and return a timestamp, status, value tuple.

           - timestamp: the timestamp in since the Unix epoch as a float.
           - status: Sensor status constant.
           - value: int, float, bool, Sensor value constant (for lru values)
               or str (for discrete values)

           Subclasses should implement this method.
           """
        return (self._timestamp, self._status, self._value)


class DeviceLogger(object):
    """Object for logging messages from a DeviceServer.

       Log messages are logged at a particular level and under
       a particular name. Names use dotted notation to form
       a virtual hierarchy of loggers with the device."""

    # level values are used as indexes into the LEVELS list
    # so these to lists should be in the same order 
    ALL, TRACE, DEBUG, INFO, WARN, ERROR, FATAL, OFF = range(8)
    LEVELS = [ "all", "trace", "debug", "info", "warn",
               "error", "fatal", "off" ]

    def __init__(self, device_server, root_logger="root"):
        self._device_server = device_server
        self._log_level = self.OFF
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
