# katcp.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Utilities for dealing with KAT device control language messages."""

from __future__ import division, print_function, absolute_import

import re
import sys
import time
import warnings
import logging

import tornado

from collections import namedtuple, defaultdict
from functools import wraps, partial

from concurrent.futures import Future, TimeoutError
from tornado import gen
from tornado.gen import with_timeout
from tornado.concurrent import Future as tornado_Future

SEC_TO_MS_FAC = 1000
MS_TO_SEC_FAC = 1./1000
# The major version of the katcp protocol that is used by default
DEFAULT_KATCP_MAJOR = 5
# First major version to use seconds (in stead of milliseconds) for timestamps
SEC_TS_KATCP_MAJOR = 5
# First major version to allow floating point values for timestamps
FLOAT_TS_KATCP_MAJOR = 4
# First major version to support message IDs
MID_KATCP_MAJOR = 5
# First major version to support #version-connect informs
VERSION_CONNECT_KATCP_MAJOR = 5
# First major version to support #interface-changed informs
INTERFACE_CHANGED_KATCP_MAJOR = 5

logger = logging.getLogger(__name__)

class Reading(namedtuple('Reading', 'timestamp status value')):
    """Sensor reading as a (timestamp, status, value) tuple.

    Attributes
    ----------
    timestamp : float
       The time (in seconds) at which the sensor value was determined.
    status : Sensor status constant
        Whether the value represents an error condition or not.
    value : object
        The value of the sensor (the type will be appropriate to the
        sensor's type).

    """

def log_coroutine_exceptions(coro):
    """Coroutine (or any method that returns a future) decorator to log exceptions

    Example
    -------

    ::

    import logging

    class A(object):
        _logger = logging.getLogger(__name__)

        @log_coroutine_exceptions
        @tornado.gen.coroutine
        def raiser(self, arg):
            yield tornado.gen.moment
            raise Exception(arg)

    Assuming that your object (self) has a `_logger` attribute containing a
    logger instance

    """
    def log_cb(self, f):
        try:
            f.result()
        except Exception:
            self._logger.exception('Unhandled exception calling coroutine {0!r}'
                                   .format(coro))

    @wraps(coro)
    def wrapped_coro(self, *args, **kwargs):
        try:
            f = coro(self, *args, **kwargs)
        except Exception:
            f = tornado_Future()
            f.set_exc_info(sys.exc_info())
        f.add_done_callback(partial(log_cb, self))
        return f

    return wrapped_coro

def log_future_exceptions(logger, f, ignore=()):
    """Log any exceptions set to a future

    Parameters
    ----------
    logger : logging.Logger instance
        logger.exception(...) is called if the future resolves with an exception
    f : Future object
        Future to be monitored for exceptions
    ignore : Exception or tuple of Exception
        Exptected exception(s) to ignore, i.e. they will not be logged.

    Notes
    -----
    This is useful when an async task is started for its side effects without waiting for
    the result. The problem is that if the future's resolution is not checked for
    exceptions, unhandled exceptions in the async task will be silently ignored.

    """
    def log_cb(f):
        try:
            f.result()
        except ignore:
            pass
        except Exception:
            logger.exception('Unhandled exception returned by future')
    f.add_done_callback(log_cb)


def convert_method_name(prefix, name):
    """Convert a method name to the corresponding KATCP message name."""
    return name[len(prefix):].replace("_", "-")


def steal_docstring_from(obj):
    """Decorator that lets you steal a docstring from another object

    Example
    -------

    ::

    @steal_docstring_from(superclass.meth)
    def meth(self, arg):
        "Extra subclass documentation"
        pass

    In this case the docstring of the new 'meth' will be copied from superclass.meth, and
    if an additional dosctring was defined for meth it will be appended to the superclass
    docstring with a two newlines inbetween.
    """
    def deco(fn):
        docs = [obj.__doc__]
        if fn.__doc__:
            docs.append(fn.__doc__)
        fn.__doc__ = '\n\n'.join(docs)
        return fn

    return deco

class KatcpSyntaxError(ValueError):
    """Raised by parsers when encountering a syntax error."""


class KatcpClientError(Exception):
    """Raised by KATCP clients when an error occurs."""


class KatcpVersionError(KatcpClientError):
    """KATCP feature unsupported by KATCP version of the server/client."""


class KatcpClientDisconnected(KatcpClientError):
    """Raised when trying to send a message to a disconnected server."""


class Message(object):
    """Represents a KAT device control language message.

    Parameters
    ----------
    mtype : Message type constant
        The message type (request, reply or inform).
    name : str
        The message name.
    arguments : list of strings
        The message arguments.
    mid : str, digits only
        The message identifier. Replies and informs that
        are part of the reply to a request should have the
        same id as the request did.

    """
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
        "\\": "\\",
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

    ## @brief Attempt to optimize messages by specifying attributes up front
    __slots__ = ["mtype", "name", "mid", "arguments"]

    def __init__(self, mtype, name, arguments=None, mid=None):
        self.mtype = mtype
        self.name = name

        if mid is None:
            self.mid = None
        else:
            self.mid = str(mid)

        if arguments is None:
            self.arguments = []
        else:
            self.arguments = [self.format_argument(x) for x in arguments]

        # check message type

        if mtype not in self.TYPE_SYMBOLS:
            raise KatcpSyntaxError("Invalid command type %r." % (mtype,))

        # check message id

        if self.mid is not None and not self.mid.isdigit():
            raise KatcpSyntaxError("Invalid message id %r." % (mid,))

        # check command name validity

        if not name:
            raise KatcpSyntaxError("Command missing command name.")
        if not name.replace("-", "").isalnum():
            raise KatcpSyntaxError("Command name should consist only of "
                                   "alphanumeric characters and dashes (got %r)."
                                   % (name,))
        if not name[0].isalpha():
            raise KatcpSyntaxError("Command name should start with an "
                                   "alphabetic character (got %r)."
                                   % (name,))

    def format_argument(self, arg):
        """Format a Message argument to a string"""
        if isinstance(arg, float):
            return repr(arg)
        elif isinstance(arg, bool):
            return str(int(arg))
        else:
            try:
                return str(arg)
            except UnicodeEncodeError:
                # unicode characters will break the str cast, so
                # try to encode to ascii and replace the offending characters
                # with a '?' character
                logger.error("Error casting message argument to str! "
                             "Trying to encode argument to ascii.")
                if not isinstance(arg, unicode):
                    arg = arg.decode('utf-8')
                return arg.encode('ascii', 'replace')

    def copy(self):
        """Return a shallow copy of the message object and its arguments.

        Returns
        -------
        msg : Message
            A copy of the message object.

        """
        return Message(self.mtype, self.name, self.arguments)

    def __str__(self):
        """ Return Message serialized for transmission.

        Returns
        -------
        msg : str
           The message encoded as a ASCII string.

        """
        if self.arguments:
            escaped_args = [self.ESCAPE_RE.sub(self._escape_match, x)
                            for x in self.arguments]
            escaped_args = [x or "\\@" for x in escaped_args]
            arg_str = " " + " ".join(escaped_args)
        else:
            arg_str = ""

        if self.mid is not None:
            mid_str = "[%s]" % self.mid
        else:
            mid_str = ""

        return "%s%s%s%s" % (self.TYPE_SYMBOLS[self.mtype], self.name,
                             mid_str, arg_str)

    def __repr__(self):
        """ Return message displayed in a readable form."""
        tp = self.TYPE_NAMES[self.mtype].lower()
        name = self.name
        if self.arguments:
            escaped_args = [self.ESCAPE_RE.sub(self._escape_match, x)
                            for x in self.arguments]
            for arg in escaped_args:
                if len(arg) > 10:
                    arg = arg[:10] + "..."
            args = "(" + ", ".join(escaped_args) + ")"
        else:
            args = ""
        return "<Message %s %s %s>" % (tp, name, args)

    def __eq__(self, other):
        if not isinstance(other, Message):
            return NotImplemented
        for name in self.__slots__:
            if getattr(self, name) != getattr(other, name):
                return False
        return True

    def __ne__(self, other):
        return not self == other

    def _escape_match(self, match):
        """Given a re.Match object, return the escape code for it."""
        return "\\" + self.REVERSE_ESCAPE_LOOKUP[match.group()]

    def reply_ok(self):
        """Return True if this is a reply and its first argument is 'ok'."""
        return (self.mtype == self.REPLY and self.arguments and
                self.arguments[0] == self.OK)

    # * and ** magic useful here
    # pylint: disable-msg = W0142

    @classmethod
    def request(cls, name, *args, **kwargs):
        """Helper method for creating request messages.

        Parameters
        ----------
        name : str
            The name of the message.
        args : list of strings
            The message arguments.

        Keyword arguments
        -----------------
        mid : str or None
            Message ID to use or None (default) for no Message ID

        """
        mid = kwargs.pop('mid', None)
        if len(kwargs) > 0:
            raise TypeError('Invalid keyword argument(s): %r' % kwargs)
        return cls(cls.REQUEST, name, args, mid)

    @classmethod
    def reply(cls, name, *args, **kwargs):
        """Helper method for creating reply messages.

        Parameters
        ----------
        name : str
            The name of the message.
        args : list of strings
            The message arguments.

        Keyword Arguments
        -----------------
        mid : str or None
            Message ID to use or None (default) for no Message ID

        """
        mid = kwargs.pop('mid', None)
        if len(kwargs) > 0:
            raise TypeError('Invalid keyword argument(s): %r' % kwargs)
        return cls(cls.REPLY, name, args, mid)

    @classmethod
    def reply_to_request(cls, req_msg, *args):
        """Helper method for creating reply messages to a specific request.

        Copies the message name and message identifier from request message.

        Parameters
        ----------
        req_msg : katcp.core.Message instance
            The request message that this inform if in reply to
        args : list of strings
            The message arguments.

        """
        return cls(cls.REPLY, req_msg.name, args, req_msg.mid)

    @classmethod
    def inform(cls, name, *args, **kwargs):
        """Helper method for creating inform messages.

        Parameters
        ----------
        name : str
            The name of the message.
        args : list of strings
            The message arguments.

        """
        mid = kwargs.pop('mid', None)
        if len(kwargs) > 0:
            raise TypeError('Invalid keyword argument(s): %r' % kwargs)
        return cls(cls.INFORM, name, args, mid)

    @classmethod
    def reply_inform(cls, req_msg, *args):
        """Helper method for creating inform messages in reply to a request.

        Copies the message name and message identifier from request message.

        Parameters
        ----------
        req_msg : katcp.core.Message instance
            The request message that this inform if in reply to
        args : list of strings
            The message arguments except name

        """
        return cls(cls.INFORM, req_msg.name, args, req_msg.mid)

    # pylint: enable-msg = W0142


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

    ## @brief Regular expression matching name and ID
    NAME_RE = re.compile(
        r"^(?P<name>[a-zA-Z][a-zA-Z0-9\-]*)(\[(?P<id>[0-9]+)\])?$")

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

        Parameters
        ----------
        line : str
            The line to parse (should not contain the terminating newline
            or carriage return).

        Returns
        -------
        msg : Message object
            The resulting Message.

        """
        # find command type and check validity
        if not line:
            raise KatcpSyntaxError("Empty message received.")

        type_char = line[0]
        if type_char not in self.TYPE_SYMBOL_LOOKUP:
            raise KatcpSyntaxError("Bad type character %r." % (type_char,))

        mtype = self.TYPE_SYMBOL_LOOKUP[type_char]

        # find command and arguments name
        # (removing possible empty argument resulting from whitespace at end
        #  of command)
        parts = self.WHITESPACE_RE.split(line)
        if not parts[-1]:
            del parts[-1]

        name = parts[0][1:]
        arguments = [self._parse_arg(x) for x in parts[1:]]

        # split out message id
        match = self.NAME_RE.match(name)
        if match:
            name = match.group('name')
            mid = match.group('id')
        else:
            raise KatcpSyntaxError("Bad message name (and possibly id) %r." %
                                   (name,))

        return Message(mtype, name, arguments, mid)


class ProtocolFlags(object):
    """Utility class for handling KATCP protocol flags.

    .. note::

       This class was introduced in katcp version 0.4.

    Currently understood flags are:

    * M - server supports multiple clients
    * I - server supports message identifiers
    * T - server provides request timeout hints via ?request-timeout-hint

    Parameters
    ----------
    major : int
        Major version number.
    minor : int
        Minor version number.
    flags : set
        Set of supported flags.

    Attributes
    ----------
    multi_client : bool
        Whether the server the version string came from supports
        multiple clients.
    message_ids : bool
        Whether the server the version string came from supports
        message ids.

    """
    VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)"
                            r"(-(?P<flags>.*))?$")

    # flags

    MULTI_CLIENT = 'M'
    MESSAGE_IDS = 'I'
    # New proposal flag to indicate that a device supports ?request-timeout-hint
    # See CB-2051
    REQUEST_TIMEOUT_HINTS = 'T'

    STRATEGIES_V4 = frozenset(['none', 'auto', 'period', 'event',
                               'differential'])
    STRATEGIES_V5 = STRATEGIES_V4 | frozenset(
        ['event-rate', 'differential-rate'])

    STRATEGIES_ALLOWED_BY_MAJOR_VERSION = {
        4: STRATEGIES_V4,
        5: STRATEGIES_V5
    }

    REQUEST_TIMEOUT_HINTS_MIN_VERSION = (5, 1)

    def __init__(self, major, minor, flags):
        self.major = major
        self.minor = minor
        self.flags = set(list(flags))
        self.multi_client = self.MULTI_CLIENT in self.flags
        self.message_ids = self.MESSAGE_IDS in self.flags
        self.request_timeout_hints = self.REQUEST_TIMEOUT_HINTS in self.flags
        if self.message_ids and self.major < MID_KATCP_MAJOR:
            raise ValueError(
                'MESSAGE_IDS is only supported in katcp v5 and newer')
        version_supports_hints = ((self.major, self.minor) >=
                                  self.REQUEST_TIMEOUT_HINTS_MIN_VERSION)
        if self.request_timeout_hints and not version_supports_hints:
            raise ValueError(
                'REQUEST_TIMEOUT_HINTS only suported in katcp v{}.{} and newer'
                .format(*self.REQUEST_TIMEOUT_HINTS_MIN_VERSION))

    def strategy_allowed(self, strategy):
        return strategy in self.STRATEGIES_ALLOWED_BY_MAJOR_VERSION[self.major]

    def __eq__(self, other):
        if not isinstance(other, ProtocolFlags):
            return NotImplemented
        return (self.major == other.major and self.minor == other.minor and
                self.flags == other.flags)

    def __str__(self):
        flag_str = self.flags and ("-" + "".join(sorted(self.flags))) or ""
        return "%d.%d%s" % (self.major, self.minor, flag_str)

    def supports(self, flag):
        return flag in self.flags

    @classmethod
    def parse_version(cls, version_str):
        """Create a :class:`ProtocolFlags` object from a version string.

        Parameters
        ----------
        version_str : str
            The version string from a #version-connect katcp-protocol
            message.

        """
        match = cls.VERSION_RE.match(version_str)
        if match:
            major = int(match.group('major'))
            minor = int(match.group('minor'))
            flags = set(match.group('flags') or '')
        else:
            major, minor, flags = None, None, set()
        return cls(major, minor, flags)


class DeviceMetaclass(type):
    """Metaclass for DeviceServer and DeviceClient classes.

    Collects up methods named request\_* and adds
    them to a dictionary of supported methods on the class.
    All request\_* methods must have a doc string so that help
    can be generated.  The same is done for inform\_* and
    reply\_* methods.

    """
    def __init__(mcs, name, bases, dct):
        """Constructor for DeviceMetaclass. Should not be used directly.

        Parameters
        ----------
        mcs : class
            The metaclass instance
        name : str
            The metaclass name
        bases : list of classes
            List of base classes
        dct : dict
            Class dictionary

        """
        super(DeviceMetaclass, mcs).__init__(name, bases, dct)
        mcs._request_handlers = {}
        mcs._inform_handlers = {}
        mcs._reply_handlers = {}

        for name in dir(mcs):
            if not callable(getattr(mcs, name)):
                continue
            handler = getattr(mcs, name)
            if name.startswith("request_"):
                request_name = convert_method_name("request_", name)
                if mcs.check_protocol(handler):
                    mcs._request_handlers[request_name] = handler
                    error_msg = "Request '{}' has no docstring.".format(request_name)
                    assert(handler.__doc__ is not None), error_msg
            elif name.startswith("inform_"):
                inform_name = convert_method_name("inform_", name)
                if mcs.check_protocol(handler):
                    mcs._inform_handlers[inform_name] = handler
                    error_msg = "Inform '{}' has no docstring.".format(inform_name)
                    assert(handler.__doc__ is not None), error_msg
                # There is a bit of a name collision between the reply_*
                # convention and the server reply_inform() method
            elif name.startswith("reply_") and name != 'reply_inform':
                reply_name = convert_method_name("reply_", name)
                if mcs.check_protocol(handler):
                    mcs._reply_handlers[reply_name] = handler
                    error_msg = "Reply '{}' has no docstring.".format(reply_name)
                    assert(handler.__doc__ is not None), error_msg

    def check_protocol(mcs, handler):
        """Return False if `handler` should be filtered"""
        # One cannot know protocol flags at definition time for device clients.
        return True

class DeviceServerMetaclass(DeviceMetaclass):
    """Specialisation of DeviceMetaclass for Device Servers

    Adds functionality for protocol-flag based filtering of handlers

    """

    def check_protocol(mcs, handler):
        """
        True if the current server's protocol flags satisfy handler requirements

        """
        protocol_info = mcs.PROTOCOL_INFO
        protocol_version = (protocol_info.major, protocol_info.minor)
        protocol_flags = protocol_info.flags

        # Check if minimum protocol version requirement is met
        min_protocol_version = getattr(handler, '_minimum_katcp_version', None)
        protocol_version_ok = (min_protocol_version is None or
                               protocol_version >= min_protocol_version)

        # Check if required optional protocol flags are present
        required_katcp_protocol_flags = getattr(
            handler, '_has_katcp_protocol_flags', None)
        protocol_flags_ok = (
            required_katcp_protocol_flags is None or
            all(flag in protocol_flags
                for flag in required_katcp_protocol_flags))

        return protocol_version_ok and protocol_flags_ok


class KatcpDeviceError(Exception):
    """Raised by KATCP servers when errors occur.

    .. versionchanged:: 0.1
        Deprecated in 0.1. Servers should not raise errors if communication
        with a client fails -- errors are simply logged instead.

    """
    pass


class FailReply(Exception):
    """Raised by request handlers to indicate a failure.

    A custom exception which, when thrown in a request handler,
    causes DeviceServerBase to send a fail reply with the specified
    fail message, bypassing the generic exception handling, which
    would send a fail reply with a full traceback.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     def request_myreq(self, req, msg):
    ...         raise FailReply("This request always fails.")
    ...

    """
    pass


class AsyncReply(Exception):
    """Raised by a request handlers to indicate it will reply later.

    A custom exception which, when thrown in a request handler,
    indicates to DeviceServerBase that no reply has been returned
    by the handler but that the handler has arranged for a reply
    message to be sent at a later time.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     def request_myreq(self, req, msg):
    ...         self.callback_client.request(
    ...             Message.request("otherreq"),
    ...             reply_cb=self._send_reply,
    ...         )
    ...         raise AsyncReply()
    ...

    """
    pass


# Only Imported here to prevent circular import issues.
from .kattypes import Int, Float, Bool, Discrete, Lru, Str, Timestamp, Address

class Sensor(object):
    """Instantiate a new sensor object.

    Subclasses will usually pass in a fixed sensor_type which should
    be one of the sensor type constants. The list params if set will
    have its values formatter by the type formatter for the given
    sensor type.

    .. note::

       The LRU sensor type was deprecated in katcp 0.4.

    .. note::

       The ADDRESS sensor type was added in katcp 0.4.

    Parameters
    ----------
    sensor_type : Sensor type constant
        The type of sensor.
    name : str
        The name of the sensor.
    description : str
        A short description of the sensor.
    units : str
        The units of the sensor value. May be the empty string
        if there are no applicable units.
    params : list
        Additional parameters, dependent on the type of sensor:

          * For :const:`INTEGER` and :const:`FLOAT` the list should
            give the minimum and maximum that define the range
            of the sensor value.
          * For :const:`DISCRETE` the list should contain all
            possible values the sensor may take.
          * For all other types, params should be omitted.
    default : object
        An initial value for the sensor. By default this is
        determined by the sensor type.
    initial_status : int enum or None
        An initial status for the sensor. If None, defaults to
        Sensor.UNKNOWN. `initial_status` must be one of the keys in
        Sensor.STATUSES

    """
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
    (INTEGER, FLOAT, BOOLEAN, LRU, DISCRETE, STRING, TIMESTAMP,
     ADDRESS) = range(8)

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
        ADDRESS: (Address, Address.NULL),
    }

    SENSOR_SHORTCUTS = {
        int: INTEGER,
        float: FLOAT,
        str: STRING,
        bool: BOOLEAN,
    }

    # map type strings to types
    SENSOR_TYPE_LOOKUP = dict((v[0].name, k) for k, v in SENSOR_TYPES.items())

    # Sensor status constants
    UNKNOWN, NOMINAL, WARN, ERROR, FAILURE, UNREACHABLE, INACTIVE = range(7)

    ## @brief Mapping from sensor status to status name.
    STATUSES = {
        UNKNOWN: 'unknown',
        NOMINAL: 'nominal',
        WARN: 'warn',
        ERROR: 'error',
        FAILURE: 'failure',
        UNREACHABLE: 'unreachable',
        INACTIVE: 'inactive',
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
    # @brief List of strings containing the additional parameters (length and
    #        interpretation are specific to the sensor type)

    def __init__(self, sensor_type, name, description=None, units='',
                 params=None, default=None, initial_status=None):
        if params is None:
            params = []

        if initial_status is None:
            initial_status = Sensor.UNKNOWN

        sensor_type = self.SENSOR_SHORTCUTS.get(sensor_type, sensor_type)

        self._sensor_type = sensor_type
        self._observers = set()

        typeclass, default_value = self.SENSOR_TYPES[sensor_type]

        if self._sensor_type in [Sensor.INTEGER, Sensor.FLOAT]:
            # as of version 5 of the guidelines, integer and float
            # ranges are optional and informational
            if len(params) == 2:
                if not params[0] <= default_value <= params[1]:
                    default_value = params[0]
            self._kattype = typeclass()
        elif self._sensor_type == Sensor.DISCRETE:
            default_value = params[0]
            self._kattype = typeclass(params)
        else:
            if self._sensor_type == Sensor.TIMESTAMP and units:
                raise ValueError(
                    'Units cannot be specified for TIMESTAMP sensors since '
                    'their units is defined by the KATCP spec as either '
                    'seconds or, for katcp versions 4 and below, milliseconds')
            self._kattype = typeclass()

        if default is not None:
            default_value = default

        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # self._current_reading should be set and read in a single
        # bytecode to avoid situations were an update in one thread
        # causes another thread to read the timestamp from one update
        # and the value and/or status from a different update.
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        self._current_reading = Reading(time.time(), initial_status,
                                        default_value)
        self._formatter = self._kattype.pack
        self._parser = self._kattype.unpack
        # Also Expose `type` attribute to be compatible with resource.KATCPSensor
        self.type = self.stype = self._kattype.name

        self.name = name
        if description is None:
            description = '%(type)s sensor %(name)r %(unit_description)s' % (
                          dict(type=self.stype.capitalize(), name=self.name,
                               unit_description=('in unit '+units if units else
                                                 'with no unit')))

        self.description = description
        self.units = units
        self.params = params
        self.formatted_params = [self._formatter(p, True) for p in params]

    # support for legacy KATCP users that relied on being able to
    # read _timestamp, _status and _value. Such usage will be
    # deprecated in a future version of KATCP.

    def _reading_getter(i, name):
        def getter(self):
            warnings.warn("Use of katcp.Sensor.%s attribute is deprecated"
                          % name, DeprecationWarning)
            return self._current_reading[i]
        return getter

    _timestamp = property(_reading_getter(0, "_timestamp"))
    _status = property(_reading_getter(1, "_status"))
    _value = property(_reading_getter(2, "_value"))

    del _reading_getter

    def __repr__(self):
        cls = self.__class__
        return "<%s.%s object name=%r at 0x%x>" % (
            cls.__module__, cls.__name__, self.name, id(self))

    @classmethod
    def integer(cls, name, description=None, unit='', params=None,
                default=None, initial_status=None):
        """Instantiate a new integer sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. May be the empty string
            if there are no applicable units.
        params : list
            [min, max] -- miniumum and maximum values of the sensor
        default : int
            An initial value for the sensor. Defaults to 0.
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.INTEGER, name, description, unit, params,
                   default, initial_status)

    @classmethod
    def float(cls, name, description=None, unit='', params=None,
              default=None, initial_status=None):
        """Instantiate a new float sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. May be the empty string
            if there are no applicable units.
        params : list
            [min, max] -- miniumum and maximum values of the sensor
        default : float
            An initial value for the sensor. Defaults to 0.0.
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.FLOAT, name, description, unit, params,
                   default, initial_status)

    @classmethod
    def boolean(cls, name, description=None, unit='',
                default=None, initial_status=None):
        """Instantiate a new boolean sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. May be the empty string
            if there are no applicable units.
        default : bool
            An initial value for the sensor. Defaults to False.
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.BOOLEAN, name, description, unit, None,
                   default, initial_status)

    @classmethod
    def lru(cls, name, description=None, unit='',
            default=None, initial_status=None):
        """Instantiate a new lru sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. May be the empty string
            if there are no applicable units.
        default : enum, Sensor.LRU_*
            An initial value for the sensor. Defaults to self.LRU_NOMINAL
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.LRU, name, description, unit, None,
                   default, initial_status)

    @classmethod
    def string(cls, name, description=None, unit='',
               default=None, initial_status=None):
        """Instantiate a new string sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. May be the empty string
            if there are no applicable units.
        default : string
            An initial value for the sensor. Defaults to the empty string.
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.STRING, name, description, unit, None,
                   default, initial_status)

    @classmethod
    def discrete(cls, name, description=None, unit='', params=None,
                 default=None, initial_status=None):
        """Instantiate a new discrete sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. May be the empty string
            if there are no applicable units.
        params : [str]
            Sequence of all allowable discrete sensor states
        default : str
            An initial value for the sensor. Defaults to the first item
            of params
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.DISCRETE, name, description, unit, params,
                   default, initial_status)

    @classmethod
    def timestamp(cls, name, description=None, unit='',
                  default=None, initial_status=None):
        """Instantiate a new timestamp sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. For timestamp sensor may only be the
            empty string.
        default : string
            An initial value for the sensor in seconds since the Unix Epoch.
            Defaults to 0.
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.TIMESTAMP, name, description, unit, None,
                   default, initial_status)

    @classmethod
    def address(cls, name, description=None, unit='',
                default=None, initial_status=None):
        """Instantiate a new IP address sensor object.

        Parameters
        ----------
        name : str
            The name of the sensor.
        description : str
            A short description of the sensor.
        units : str
            The units of the sensor value. May be the empty string
            if there are no applicable units.
        default : (string, int)
            An initial value for the sensor. Tuple contaning (host, port).
            default is ("0.0.0.0", None)
        initial_status : int enum or None
            An initial status for the sensor. If None, defaults to
            Sensor.UNKNOWN. `initial_status` must be one of the keys in
            Sensor.STATUSES

        """
        return cls(cls.ADDRESS, name, description, unit, None,
                   default, initial_status)

    def attach(self, observer):
        """Attach an observer to this sensor.

        The observer must support a call to observer.update(sensor, reading),
        where *sensor* is the sensor object and *reading* is a (timestamp,
        status, value) tuple for this update (matching the return value of
        the :meth:`read` method).

        Parameters
        ----------
        observer : object
            Object with an .update(sensor, reading) method that will be called
            when the sensor value is set

        """
        self._observers.add(observer)

    def detach(self, observer):
        """Detach an observer from this sensor.

        Parameters
        ----------
        observer : object
            The observer to remove from the set of observers notified
            when the sensor value is set.

        """
        self._observers.discard(observer)

    def notify(self, reading):
        """Notify all observers of changes to this sensor."""
        # copy list before iterating in case new observers arrive
        for o in list(self._observers):
            o.update(self, reading)

    def parse_value(self, s_value, katcp_major=DEFAULT_KATCP_MAJOR):
        """Parse a value from a string.

        Parameters
        ----------
        s_value : str
            A string value to attempt to convert to a value for
            the sensor.

        Returns
        -------
        value : object
            A value of a type appropriate to the sensor.

        """
        return self._parser(s_value, katcp_major)

    def set(self, timestamp, status, value):
        """Set the current value of the sensor.

        Parameters
        ----------
        timestamp : float in seconds
           The time at which the sensor value was determined.
        status : Sensor status constant
            Whether the value represents an error condition or not.
        value : object
            The value of the sensor (the type should be appropriate to the
            sensor's type).

        """
        reading = self._current_reading = Reading(timestamp, status, value)
        self.notify(reading)

    def set_formatted(self, raw_timestamp, raw_status, raw_value,
                      major=DEFAULT_KATCP_MAJOR):
        """Set the current value of the sensor.

        Parameters
        ----------
        timestamp : str
            KATCP formatted timestamp string
        status : str
            KATCP formatted sensor status string
        value : str
            KATCP formatted sensor value
        major : int, default = 5
            KATCP major version to use for interpreting the raw values

        """
        timestamp = self.TIMESTAMP_TYPE.decode(raw_timestamp, major)
        status = self.STATUS_NAMES[raw_status]
        value = self.parse_value(raw_value, major)
        self.set(timestamp, status, value)

    def read_formatted(self, major=DEFAULT_KATCP_MAJOR):
        """Read the sensor and return a (timestamp, status, value) tuple.

        All values are strings formatted as specified in the Sensor Type
        Formats in the katcp specification.

        Parameters
        ----------
        major : int
            Major version of KATCP to use when interpreting types.
            Defaults to latest implemented KATCP version.

        Returns
        -------
        timestamp : str
            KATCP formatted timestamp string
        status : str
            KATCP formatted sensor status string
        value : str
            KATCP formatted sensor value

        """
        return self.format_reading(self.read(), major)

    def format_reading(self, reading, major=DEFAULT_KATCP_MAJOR):
        """Format sensor reading as (timestamp, status, value) tuple of strings.

        All values are strings formatted as specified in the Sensor Type
        Formats in the katcp specification.

        Parameters
        ----------
        reading : :class:`Reading` object
            Sensor reading as returned by :meth:`read`
        major : int
            Major version of KATCP to use when interpreting types.
            Defaults to latest implemented KATCP version.

        Returns
        -------
        timestamp : str
            KATCP formatted timestamp string
        status : str
            KATCP formatted sensor status string
        value : str
            KATCP formatted sensor value

        Note
        ----
        Should only be used for a reading obtained from the same sensor.

        """

        timestamp, status, value = reading
        return (self.TIMESTAMP_TYPE.encode(timestamp, major),
                self.STATUSES[status],
                self._formatter(value, True, major))

    def read(self):
        """Read the sensor and return a (timestamp, status, value) tuple.

        Returns
        -------
        reading : :class:`Reading` object
            Sensor reading as a (timestamp, status, value) tuple.

        """
        return self._current_reading

    def set_value(self, value, status=NOMINAL, timestamp=None,
                  major=DEFAULT_KATCP_MAJOR):
        """Check and then set the value of the sensor.

        Parameters
        ----------
        value : object
            Value of the appropriate type for the sensor.
        status : Sensor status constant
            Whether the value represents an error condition or not.
        timestamp : float in seconds or None
            The time at which the sensor value was determined.
            Uses current time if None.
        major : int
            Major version of KATCP to use when interpreting types.
            Defaults to latest implemented KATCP version.

        """
        self._kattype.check(value, major)
        if timestamp is None:
            timestamp = time.time()
        self.set(timestamp, status, value)

    def value(self):
        """Read the current sensor value.

        Returns
        -------
        value : object
            The value of the sensor (the type will be appropriate to the
            sensor's type).

        """
        return self.read().value

    def status(self):
        """Read the current sensor status.

        Returns
        -------
        status : enum (int)
            The status of the sensor, one of the keys in Sensor.STATUSES

        """
        return self.read().status

    @classmethod
    def parse_type(cls, type_string):
        """Parse KATCP formatted type code into Sensor type constant.

        Parameters
        ----------
        type_string : str
            KATCP formatted type code.

        Returns
        -------
        sensor_type : Sensor type constant
            The corresponding Sensor type constant.

        """
        if type_string in cls.SENSOR_TYPE_LOOKUP:
            return cls.SENSOR_TYPE_LOOKUP[type_string]
        else:
            raise KatcpSyntaxError("Invalid sensor type string %s" %
                                   type_string)

    @classmethod
    def parse_params(cls, sensor_type, formatted_params,
                     major=DEFAULT_KATCP_MAJOR):
        """Parse KATCP formatted parameters into Python values.

        Parameters
        ----------
        sensor_type : Sensor type constant
            The type of sensor the parameters are for.
        formatted_params : list of strings
            The formatted parameters that should be parsed.
        major : int
            Major version of KATCP to use when interpreting types.
            Defaults to latest implemented KATCP version.

        Returns
        -------
        params : list of objects
            The parsed parameters.

        """
        typeclass, _value = cls.SENSOR_TYPES[sensor_type]
        if sensor_type == cls.DISCRETE:
            kattype = typeclass([])
        else:
            kattype = typeclass()
        return [kattype.decode(x, major) for x in formatted_params]

class AttrDict(dict):
    """
    Based on JSObject : Python Objects that act like Javascript Objects
    based on James Robert blog entry:
    Making Python Objects that act like Javascript Objects
    http://jiaaro.com/making-python-objects-that-act-like-javascrip
    """

    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self

class DefaultAttrDict(defaultdict):
    """
    Similar to AttrDict but adds `collections.defaultdict` functionality

    Supports default values both for key and attribute access

    """
    def __init__(self, *args, **kwargs):
        super(DefaultAttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self

    def __getattr__(self, name):
        return self[name]

class AsyncEvent(object):
    """tornado.concurrent.Future Event based on threading.Event API

    Supports threading.Event API for setting / clearing / checking, but
    replaces the wait() method with until_set() that returns a tornado Future
    that resolves when the event is set.

    Note, this class is NOT THREAD SAFE! It will only work properly if all its
    methods (apart from isSet() and wait_with_ioloop()) are called from the
    same thread/ioloop.

    """
    def __init__(self, ioloop=None):
        """Init of async event.

        Parameters
        ----------
        ioloop : IOLoop instance or None
            tornado IOloop instance to use, or None for IOLoop.current()
        """
        self._ioloop = ioloop or tornado.ioloop.IOLoop.current()
        self._flag = False
        self._waiting_future = tornado_Future()

    def isSet(self):
        """Returns True if the event is set, else False"""
        return self._flag

    is_set = isSet

    def set(self):
        """Set event flag to true and resolve future(s) returned by until_set()

        Notes
        -----
        A call to set() may result in control being transferred to
        done_callbacks attached to the future returned by until_set().

        """
        self._flag = True
        old_future = self._waiting_future
        # Replace _waiting_future with a fresh one incase someone woken up by set_result()
        # sets this AsyncEvent to False before waiting on it to be set again.
        self._waiting_future = tornado_Future()
        old_future.set_result(True)

    def clear(self):
        self._flag = False

    def until_set(self, timeout=None):
        if not self._flag:
            if timeout:
                return with_timeout(self._ioloop.time() + timeout,
                                    self._waiting_future,
                                    self._ioloop)
            else:
                return self._waiting_future
        else:
            f = tornado_Future()
            f.set_result(True)
            return f

    def wait_with_ioloop(self, ioloop, timeout=None):
        """Do blocking wait until condition is event is set.

        Parameters
        ----------
        ioloop : tornadio.ioloop.IOLoop instance
            MUST be the same ioloop that set() / clear() is called from
        timeout : float, int or None
            If not None, only wait up to `timeout` seconds for event to be set.

        Return Value
        ------------
        flag : True if event was set within timeout, otherwise False.

        Notes
        -----
        This will deadlock if called in the ioloop!

        """
        f = Future()

        def cb():
            return gen.chain_future(self.until_set(), f)
        ioloop.add_callback(cb)
        try:
            f.result(timeout)
            return True
        except TimeoutError:
            return self._flag


class AsyncCallbackEvent(AsyncEvent):
    # Wanted to use @steal_docstring_from() here, but aparently Classes have read-only
    # __doc__ attributes... Apparently this is fixed in Python 3.3
    __doc__ = '\n\n'.join((
        AsyncEvent.__doc__,
        """Extend AsyncEvent with a callback on event state change

        callback is called with "True" if the event is set, "False" if it is cleared
        """))

    def __init__(self, callback=lambda x: x):
        self._callback = callback
        super(AsyncCallbackEvent, self).__init__()

    def set(self):
        super(AsyncCallbackEvent, self).set()
        try:
            self._callback(True)
        except Exception:
            logger.exception('Unhandled exception calling event change callback')

    def clear(self):
        super(AsyncCallbackEvent, self).clear()
        try:
            self._callback(True)
        except Exception:
            logger.exception('Unhandled exception calling event change callback')

class AsyncState(object):
    """Allow async waiting for a state to attain a certain value

    Note, this class is NOT THREAD SAFE! It will only work properly if all its methods are
    called from the same thread/ioloop.
    """

    @property
    def state(self):
        return self._state

    @property
    def valid_states(self):
        return self._valid_states

    def __init__(self, valid_states, initial_state=None, ioloop=None):
        """Init with a seq of valid states

        Parameters
        ----------
        valid_states : ordered seq of hashable types
            Valid states, will be turned into a frozen set
        initial_state: member of `valid_states`, or None
            If None, the initial state will be the first state in the seq
        ioloop : IOLoop instance or None
            tornado IOloop instance to use, or None for IOLoop.current()
        """
        self._ioloop = ioloop or tornado.ioloop.IOLoop.current()
        valid_states = tuple(valid_states)
        self._valid_states = frozenset(valid_states)
        if initial_state is None:
            self._state = valid_states[0]
        else:
            self._state = initial_state
        assert(self._state in valid_states)
        self._waiting_futures = {state: tornado_Future() for state in valid_states}

    def set_state(self, state):
        if state not in self._valid_states:
            raise ValueError('State must be one of {0}, not {1}'
                             .format(self._valid_states, state))
        self._state = state
        old_future = self._waiting_futures[state]
        # Replace _waiting_future with a fresh one incase someone woken up by set_result()
        # sets this AsyncState to another value before waiting on it to be set again.
        self._waiting_futures[state] = tornado_Future()
        old_future.set_result(True)

    def until_state(self, state, timeout=None):
        """Return a tornado Future that will resolve when the requested state is set"""
        if state not in self._valid_states:
            raise ValueError('State must be one of {0}, not {1}'
                             .format(self._valid_states, state))
        if state != self._state:
            if timeout:
                return with_timeout(self._ioloop.time() + timeout,
                                    self._waiting_futures[state],
                                    self._ioloop)
            else:
                return self._waiting_futures[state]
        else:
            f = tornado_Future()
            f.set_result(True)
            return f

    def until_state_in(self, *states, **kwargs):
        """Return a tornado Future, resolves when any of the requested states is set"""
        timeout = kwargs.get('timeout', None)
        state_futures = (self.until_state(s, timeout=timeout) for s in states)
        return until_any(*state_futures)

    # TODO Add until_not_state() ?

class LatencyTimer(object):
    """Track for how long already-resolved futures are yielded.

    In a tornado coroutine every yield statement does not guarantee a trip through
    the ioloop -- in many cases the result is immediately available. e.g. ::

      @tornado.gen.coroutine
      def a_coroutine(producer):
        while True:
            future = producer()
            result = yield future

    If producer's result is immediately available, it will return a future that is already
    resolved (i.e. `future.done() == True`). `torado.gen.coroutine` will the immediately
    produce the return value at the yield statement without going through the ioloop. This
    improves efficiency by reducing unnecessary ioloop round trips, but can harm latency
    if `producer` is very productive.

    Example
    =======
    ::

      @tornado.gen.coroutine
      def a_coroutine(producer):
        latency_timer = LatencyTimer(0.05)  # 50ms max latency
        while True:
            future = producer()
            latency_timer.check_future(future)
                if latency_timer.time_to_yield():
                    yield gen.moment
            result = yield future

    """
    def __init__(self, max_loop_latency, ioloop=None):
        """Initialise LatencyTimer

        Arguments
        =========
        max_loop_latency : float
            suggest yielding `tornado.gen.moment` if the loop has avoided the
            ioloop longer than `max_loop_latency` seconds.
        ioloop : tornado.ioloop.IOLoop instance
            Use `ioloop.time()` to get elapsed time. Defaults to
            tornado.ioloop.IOLoop.current()

        """
        self.max_loop_latency = max_loop_latency
        self.ioloop = ioloop or tornado.ioloop.IOLoop.current()
        self.prev_done = False
        self.done_since = self.ioloop.time()
        self.done = False

    def check_future(self, fut):
        """Call with each future that is to be yielded on"""
        done = self.done = fut.done()
        if done and not self.prev_done:
            self.done_since = self.ioloop.time()
        self.prev_done = done

    def time_to_yield(self):
        """Call after check_future(). If True, it is time to yield tornado.gen.moment"""
        delta = self.ioloop.time() - self.done_since
        if self.done and delta > self.max_loop_latency:
            return True
        return False

def hashable_identity(obj):

    """Generate a hashable ID that is stable for methods etc

    Approach borrowed from blinker. Why it matters: see e.g.
    http://stackoverflow.com/questions/13348031/python-bound-and-unbound-method-object
    """
    if hasattr(obj, '__func__'):
        return (id(obj.__func__), id(obj.__self__))
    elif hasattr(obj, 'im_func'):
        return (id(obj.im_func), id(obj.im_self))
    elif isinstance(obj, (basestring, unicode)):
        return obj
    else:
        return id(obj)

def until_later(delay, ioloop=None):
    ioloop = ioloop or tornado.ioloop.IOLoop.current()
    f = tornado_Future()

    def _done():
        f.set_result(None)
    ioloop.call_later(delay, _done)
    return f

def until_any(*futures, **kwargs):
    """Return a future that resolves when any of the passed futures resolves.

    Resolves with the value yielded by the first future to resolve.

    Note, this will only work with tornado futures.

    """
    timeout = kwargs.get('timeout', None)
    ioloop = kwargs.get('ioloop', None) or tornado.ioloop.IOLoop.current()
    any_future = tornado_Future()

    def handle_done(done_future):
        if not any_future.done():
            try:
                any_future.set_result(done_future.result())
            except Exception:
                any_future.set_exc_info(done_future.exc_info())
            # (NM) Nasty hack to remove handle_done from the callback list to prevent a
            # memory leak where one of the futures resolves quickly, particularly when
            # used together with AsyncState.until_state(). Also addresses Jira issue
            # CM-593
            for f in futures:
                if f._callbacks:
                    try:
                        f._callbacks.remove(handle_done)
                    except ValueError:
                        pass

    for f in futures:
        f.add_done_callback(handle_done)
        if any_future.done():
            break

    if timeout:
        return with_timeout(ioloop.time() + timeout, any_future, ioloop)
    else:
        return any_future

def future_timeout_manager(timeout=None, ioloop=None):
    """Create Helper function for yielding with a cumulative timeout if required

    Keeps track of time over multiple timeout calls so that a single timeout can
    be placed over multiple operations.

    Parameters
    ----------
    timeout : int or None
        Timeout, or None for no timeout
    ioloop : IOLoop instance or None
        tornado IOloop instance to use, or None for IOLoop.current()

    Return value
    ------------

    maybe_timeout : func
        Accepts a future, and wraps it in
        :func:tornado.gen.with_timeout. maybe_timeout raises
        :class:`tornado.gen.TimeoutError` if the timeout expires

        Has a function attribute `remaining()` that returns the remaining
        timeout or None if timeout == None

    Example
    -------

    ::

    @tornado.gen.coroutine
    def multi_op(timeout):
        maybe_timeout = future_timeout_manager(timeout)
        result1 = yield maybe_timeout(op1())
        result2 = yield maybe_timeout(op2())
        # If the cumulative time of op1 and op2 exceeds timeout,
        # :class:`tornado.gen.TimeoutError` is raised

    """
    ioloop = ioloop or tornado.ioloop.IOLoop.current()
    t0 = ioloop.time()

    def _remaining():
        return timeout - (ioloop.time() - t0) if timeout else None

    def maybe_timeout(f):
        """Applies timeout if timeout is not None"""
        if not timeout:
            return f
        else:
            remaining = _remaining()
            deadline = ioloop.time() + remaining
            return with_timeout(deadline, f, ioloop)

    maybe_timeout.remaining = _remaining

    return maybe_timeout

@tornado.gen.coroutine
def until_some(*args, **kwargs):
    """Return a future that resolves when some of the passed futures resolve.

    The futures can be passed as either a sequence of *args* or a dict of
    *kwargs* (but not both). Some additional keyword arguments are supported,
    as described below. Once a specified number of underlying futures have
    resolved, the returned future resolves as well, or a timeout could be
    raised if specified.

    Parameters
    ----------
    done_at_least : None or int
        Number of futures that need to resolve before this resolves or None
        to wait for all (default None)
    timeout : None or float
        Timeout in seconds, or None for no timeout (the default)

    Returns
    -------
    This command returns a tornado Future that resolves with a list of
    (index, value) tuples containing the results of all futures that resolved,
    with corresponding indices (numbers for *args* futures or keys for *kwargs*
    futures).

    Raises
    ------
    :class:`tornado.gen.TimeoutError`
        If operation times out before the requisite number of futures resolve

    """
    done_at_least = kwargs.pop('done_at_least', None)
    timeout = kwargs.pop('timeout', None)
    # At this point args and kwargs are either empty or contain futures only
    if done_at_least is None:
        done_at_least = len(args) + len(kwargs)
    wait_iterator = tornado.gen.WaitIterator(*args, **kwargs)
    maybe_timeout = future_timeout_manager(timeout)
    results = []
    while not wait_iterator.done():
        result = yield maybe_timeout(wait_iterator.next())
        results.append((wait_iterator.current_index, result))
        if len(results) >= done_at_least:
            break
    raise tornado.gen.Return(results)
