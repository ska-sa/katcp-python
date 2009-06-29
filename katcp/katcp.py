
"""Utilities for dealing with KAT device control
   language messages.

   @namespace za.ac.ska.katcp
   @author Simon Cross <simon.cross@ska.ac.za>
   """

import threading
import sys
import re
import time

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
       if low-level network exceptions occurs.

       Deprecated. Servers should not raise errors if communication with a
       client fails -- errors are simply logged instead.
       """
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


class KatcpClientError(Exception):
    """Exception raised by KATCP clients when errors occur while
       communicating with a device.  Note that socket.error can also be raised
       if low-level network exceptions occure."""
    pass


class ExcepthookThread(threading.Thread):
    """A custom Thread class that passes exceptions up to an
       excepthook callable that functions like sys.excepthook for
       threads.
       """
    def __init__(self, excepthook=None, *args, **kwargs):
        if excepthook is None:
            excepthook = getattr(threading.currentThread(), "_excepthook", None)
        self._excepthook = excepthook
        # evil hack to support subclasses that override run
        self._old_run = self.run
        self.run = self._wrapped_run
        super(ExcepthookThread, self).__init__(*args, **kwargs)

    def _wrapped_run(self):
        try:
            self._old_run()
        except:
            if self._excepthook is not None:
                self._excepthook(*sys.exc_info())
            else:
                raise


from .kattypes import Int, Float, Bool, Discrete, Lru, Str, Timestamp

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

    def __init__(self, sensor_type, name, description, units, params=None, default=None):
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
            if not params[0] <= self._value <= params[1]:
                self._value = params[0]
            self._kattype = typeclass(params[0], params[1])
        elif self._sensor_type == Sensor.DISCRETE:
            self._value = params[0]
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

        if default is not None:
            self._value = default

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
