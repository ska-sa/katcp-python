# kattypes.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Utilities for dealing with KATCP types.
   """

from __future__ import division, print_function, absolute_import

import inspect
import struct
import re
import logging

from functools import partial, wraps, update_wrapper

from tornado import gen

from .core import (Message, FailReply, DEFAULT_KATCP_MAJOR,
                   SEC_TS_KATCP_MAJOR, SEC_TO_MS_FAC, MS_TO_SEC_FAC,
                   convert_method_name)


logger = logging.getLogger(__name__)


# KATCP Type Classes
#
# TODO (NM) Consider a 'validate_only' flag where a type is only validated, but
# not encoded when sent through to a handler.

class KatcpType(object):
    """Class representing a KATCP type.

    Sub-classes should:

      * Set the :attr:`name` attribute.
      * Implement the :meth:`encode` method.
      * Implement the :meth:`decode` method.

    Parameters
    ----------
    default : object, optional
        The default value for this type.
    optional : boolean, optional
        Whether the value is allowed to be None.
    multiple : boolean, optional
        Whether multiple values of this type are expected. Must be the
        last type parameter if this is True.

    """

    name = "unknown"

    def __init__(self, default=None, optional=False, multiple=False):
        self._default = default
        self._optional = optional
        self._multiple = multiple

    def get_default(self):
        """Return the default value.

        Raise a ValueError if the value is not optional
        and there is no default.

        Returns
        -------
        default : object
            The default value.

        """
        if self._default is None and not self._optional:
            raise ValueError("No value or default given")
        return self._default

    def check(self, value, major):
        """Check whether the value is valid.

        Do nothing if the value is valid. Raise an exception if the value is not
        valid. Parameter major describes the KATCP major version to use when
        interpreting the validity of a value.

        """
        pass

    def pack(self, value, nocheck=False, major=DEFAULT_KATCP_MAJOR):
        """Return the value formatted as a KATCP parameter.

        Parameters
        ----------
        value : object
            The value to pack.
        nocheck : bool, optional
            Whether to check that the value is valid before
            packing it.
        major : int, optional
            Major version of KATCP to use when interpreting types.
            Defaults to latest implemented KATCP version.

        Returns
        -------
        packed_value : str
            The unescaped KATCP string representing the value.

        """
        if value is None:
            value = self.get_default()
        if value is None:
            raise ValueError("Cannot pack a None value.")
        if not nocheck:
            self.check(value, major)
        return self.encode(value, major)

    def unpack(self, packed_value, major=DEFAULT_KATCP_MAJOR):
        """Parse a KATCP parameter into an object.

        Parameters
        ----------
        packed_value : str
            The unescaped KATCP string to parse into a value.
        major : int, optional
            Major version of KATCP to use when interpreting types.
            Defaults to latest implemented KATCP version.

        Returns
        -------
        value : object
            The value the KATCP string represented.

        """
        if packed_value is None:
            value = self.get_default()
        else:
            try:
                value = self.decode(packed_value, major)
            except Exception:
                raise
        if value is not None:
            self.check(value, major)
        return value


class Int(KatcpType):
    """The KATCP integer type.

    Parameters
    ----------
    min : int
        The minimum allowed value. Ignored if not given.
    max : int
        The maximum allowed value. Ignored if not given.

    """

    name = "integer"

    encode = lambda self, value, major: "%d" % (value,)

    def decode(self, value, major):
        try:
            return int(value)
        except:
            raise ValueError("Could not parse value '%s' as integer." % value)

    def __init__(self, min=None, max=None, **kwargs):
        super(Int, self).__init__(**kwargs)
        self._min = min
        self._max = max

    def check(self, value, major):
        """Check whether the value is between the minimum and maximum.

        Raise a ValueError if it is not.

        """
        if self._min is not None and value < self._min:
            raise ValueError("Integer %d is lower than minimum %d."
                             % (value, self._min))
        if self._max is not None and value > self._max:
            raise ValueError("Integer %d is higher than maximum %d."
                             % (value, self._max))


class Float(KatcpType):
    """The KATCP float type.

    Parameters
    ----------
    min : float
        The minimum allowed value. Ignored if not given.
    max : float
        The maximum allowed value. Ignored if not given.

    """

    name = "float"
    encode = lambda self, value, major: "%.15g" % (value,)

    def decode(self, value, major):
        try:
            return float(value)
        except:
            raise ValueError("Could not parse value '%s' as float." % value)

    def __init__(self, min=None, max=None, **kwargs):
        super(Float, self).__init__(**kwargs)
        self._min = min
        self._max = max

    def check(self, value, major):
        """Check whether the value is between the minimum and maximum.

        Raise a ValueError if it is not.

        """
        if self._min is not None and value < self._min:
            raise ValueError("Float %g is lower than minimum %g."
                             % (value, self._min))
        if self._max is not None and value > self._max:
            raise ValueError("Float %g is higher than maximum %g."
                             % (value, self._max))


class Bool(KatcpType):
    """The KATCP boolean type."""

    name = "boolean"

    encode = lambda self, value, major: value and "1" or "0"

    def decode(self, value, major):
        if value not in ("0", "1"):
            raise ValueError("Boolean value must be '0' or '1' but is '%s'."
                             % (value,))
        return value == "1"


class Str(KatcpType):
    """The KATCP string type."""

    name = "string"

    encode = lambda self, value, major: value
    decode = lambda self, value, major: value


class Discrete(Str):
    """The KATCP discrete type.

    Parameters
    ----------
    values : list of str
        List of the values the discrete type may accept.
    case_insensitive : bool
        Whether case-insensitive value matching should be used.

    """

    name = "discrete"

    def __init__(self, values, case_insensitive=False, **kwargs):
        super(Discrete, self).__init__(**kwargs)
        self._case_insensitive = case_insensitive
        self._values = list(values)  # just to preserve ordering
        self._valid_values = set(values)
        if self._case_insensitive:
            self._valid_values_lower = set([val.lower()
                                            for val in self._values])

    def check(self, value, major):
        """Check whether the value in the set of allowed values.

        Raise a ValueError if it is not.

        """
        if self._case_insensitive:
            value = value.lower()
            values = self._valid_values_lower
            caseflag = " (case-insensitive)"
        else:
            values = self._valid_values
            caseflag = ""
        if value not in values:
            raise ValueError("Discrete value '%s' is not one of %s%s."
                             % (value, list(self._values), caseflag))


class Lru(KatcpType):
    """The KATCP lru type"""

    name = "lru"

    # LRU sensor values
    LRU_NOMINAL, LRU_ERROR = range(2)

    ## @brief Mapping from LRU value constant to LRU value name.
    LRU_VALUES = {
        LRU_NOMINAL: "nominal",
        LRU_ERROR: "error",
    }

    # LRU_VALUES not found by pylint
    # pylint: disable-msg = E0602

    ## @brief Mapping from LRU value name to LRU value constant.
    LRU_CONSTANTS = dict((v, k) for k, v in LRU_VALUES.items())

    def encode(self, value, major):
        if value not in Lru.LRU_VALUES:
            raise ValueError("Lru value must be LRU_NOMINAL or LRU_ERROR.")
        return Lru.LRU_VALUES[value]

    def decode(self, value, major):
        if value not in Lru.LRU_CONSTANTS:
            raise ValueError("Lru value must be 'nominal' or 'error'.")
        return Lru.LRU_CONSTANTS[value]


class Address(KatcpType):
    """The KATCP address type.

    .. note::

       The address type was added in katcp 0.4.

    """

    name = "address"

    NULL = ("0.0.0.0", None)  # null address for use as an initial value

    IPV4_RE = re.compile(r"^(?P<host>[^:]*)(:(?P<port>\d+))?$")
    IPV6_RE = re.compile(r"^\[(?P<host>[^[]*)\](:(?P<port>\d+))?$")

    def encode(self, value, major):
        try:
            host, port = value
        except (ValueError, TypeError):
            raise ValueError("Could not extract host and port from value %r" %
                             (value,))
        if ':' in host:
            # IPv6
            host = "[%s]" % host
        return "%s:%s" % (host, port) if port is not None else host

    def decode(self, value, major):
        if value.startswith("["):
            match = self.IPV6_RE.match(value)
        else:
            match = self.IPV4_RE.match(value)
        if match is None:
            raise ValueError("Could not parse '%s' as an address." % value)
        port = match.group('port')
        if port is not None:
            port = int(port)
        return match.group('host'), port


class Timestamp(KatcpType):
    """The KATCP timestamp type."""

    name = "timestamp"

    # Use microsecond precision, which is about as much as you can expect with a
    # 64-bit float representing epoch-seconds
    def encode(self, value, major):
        if major >= SEC_TS_KATCP_MAJOR:
            # In seconds, please!
            return "%.6f" % float(value)
        else:
            # In milliseconds please!
            return "%i" % int(float(value) * SEC_TO_MS_FAC)

    def decode(self, value, major):
        try:
            decoded = float(value)
        except:
            raise ValueError("Could not parse value '%s' as timestamp." %
                             value)
        if major < SEC_TS_KATCP_MAJOR:
            # Convert milliseconds to seconds
            decoded = decoded * MS_TO_SEC_FAC
        return decoded


class TimestampOrNow(Timestamp):
    """KatcpType representing either a Timestamp or the special value for now.

    Floats are encoded as for :class:`katcp.kattypes.Timestamp`. The special
    value for now, :const:`katcp.kattypes.TimestampOrNow.NOW`, is encoded as
    the string "now".

    """

    name = "timestamp_or_now"

    NOW = object()

    def encode(self, value, major):
        if value is self.NOW:
            return "now"
        return super(TimestampOrNow, self).encode(value, major)

    def decode(self, value, major):
        if value == "now":
            return self.NOW
        return super(TimestampOrNow, self).decode(value, major)


class StrictTimestamp(KatcpType):
    """A timestamp that enforces the XXXX.YYY format for timestamps."""

    name = "strict_timestamp"

    def encode(self, value, major):
        try:
            return "%.15g" % value
        except:
            raise ValueError("Could not encode value %r as strict timestamp." %
                             value)

    def decode(self, value, major):
        try:
            # Presumably these parts are only used to trigger an exception
            parts = value.split(".", 1)
            _int_parts = [int(x) for x in parts]
            return float(value)
        except:
            raise ValueError("Could not parse value '%s' as strict timestamp."
                             % value)

    def check(self, value, major):
        """Check whether the value is positive.

        Raise a ValueError if it is not.

        """
        if value < 0:
            raise ValueError("Strict timestamps may not be negative.")


class Struct(KatcpType):
    """KatcpType for parsing and packing values using the :mod:`struct` module.

    Parameters
    ----------
    fmt : str
        Format to use for packing and unpacking values. It is passed directly
        into :func:`struct.pack` and :func:`struct.unpack`.

    """

    name = "struct"

    def __init__(self, fmt, **kwargs):
        super(Struct, self).__init__(**kwargs)
        self._fmt = fmt

    def encode(self, value, major):
        try:
            return struct.pack(self._fmt, *value)
        except struct.error, e:
            raise ValueError("Could not pack %s into struct with format "
                             "%s: %s" % (value, self._fmt, e))

    def decode(self, value, major):
        try:
            return struct.unpack(self._fmt, value)
        except struct.error, e:
            raise ValueError("Could not unpack %s from struct with format "
                             "%s: %s" % (value, self._fmt, e))


class Regex(Str):
    """String type that checks values using a regular expression.

    Parameters
    ----------
    regex : str or regular expression object
        Regular expression that values should match.

    """

    name = "regex"

    _re_flags = [
        ('I', re.I), ('L', re.L), ('M', re.M),
        ('S', re.S), ('U', re.U), ('X', re.X),
    ]

    def __init__(self, regex, **kwargs):
        if hasattr(regex, 'pattern'):
            self._pattern = regex.pattern
            self._compiled = regex
        else:
            self._pattern = regex
            self._compiled = re.compile(regex)
        self._flags = ",".join([name for name, value in self._re_flags
                                if self._compiled.flags & value])
        super(Regex, self).__init__(**kwargs)

    def check(self, value, major):
        if not self._compiled.match(value):
            raise ValueError("Value '%s' does not match regex '%s' with flags"
                             " '%s'." % (value, self._pattern, self._flags))


class DiscreteMulti(Discrete):
    """Discrete type which can accept multiple values.

    Its value is always a list.

    Parameters
    ----------
    values : list of str
        Set of allowed values.
    all_keyword : str, optional
        The string which represents the list of all allowed values.
    separator : str, optional
        The separator used in the packed value string.

    """

    name = "discretemulti"

    def encode(self, value, major):
        return self.separator.join(sorted(value, key=str.lower))

    def decode(self, value, major):
        if self.all_keyword and value == self.all_keyword:
            return sorted(list(self._valid_values), key=str.lower)
        return sorted([v.strip() for v in value.split(self.separator)],
                      key=str.lower)

    def __init__(self, values, all_keyword="all", separator=",", **kwargs):
        self.all_keyword = all_keyword
        self.separator = separator
        super(DiscreteMulti, self).__init__(values, **kwargs)

    def check(self, value, major):
        """Check that each item in the value list is in the allowed set."""
        for v in value:
            super(DiscreteMulti, self).check(v, major)


class Parameter(object):
    """Wrapper for kattypes which holds parameter-specific information.

    Parameters
    ----------
    position : int
        The parameter's position (starts at 1)
    name : str
        The parameter's name (introspected)
    kattype : KatcpType object
        The parameter's kattype
    major : integer
        Major version of KATCP to use when interpreting types

    """

    def __init__(self, position, name, kattype, major):
        self.position = position
        self.name = name
        self._kattype = kattype
        self.major = major

    def pack(self, value):
        """Pack the parameter using its kattype.

        Parameters
        ----------
        value : object
            The value to pack

        Returns
        -------
        packed_value : str
            The unescaped KATCP string representing the value.

        """
        return self._kattype.pack(value, self.major)

    def unpack(self, value):
        """Unpack the parameter using its kattype.

        Parameters
        ----------
        packed_value : str
            The unescaped KATCP string to unpack.

        Returns
        -------
        value : object
            The unpacked value.

        """
        # Wrap errors in FailReplies with information identifying the parameter
        try:
            return self._kattype.unpack(value, self.major)
        except ValueError, message:
            raise FailReply("Error in parameter %s (%s): %s" %
                            (self.position, self.name, message))


# request, return_reply and inform method decorators

def request(*types, **options):
    """Decorator for request handler methods.

    The method being decorated should take a req argument followed
    by arguments matching the list of types. The decorator will
    unpack the request message into the arguments.

    Parameters
    ----------
    types : list of kattypes
        The types of the request message parameters (in order). A type
        with multiple=True has to be the last type.

    Keyword Arguments
    -----------------
    include_msg : bool, optional
        Pass the request message as the third parameter to the decorated
        request handler function (default is False).
    major : int, optional
        Major version of KATCP to use when interpreting types.
        Defaults to latest implemented KATCP version.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @request(Int(), Float(), Bool())
    ...     @return_reply(Int(), Float())
    ...     def request_myreq(self, req, my_int, my_float, my_bool):
    ...         '''?myreq my_int my_float my_bool'''
    ...         return ("ok", my_int + 1, my_float / 2.0)
    ...
    ...     @request(Int(), include_msg=True)
    ...     @return_reply(Bool())
    ...     def request_is_odd(self, req, msg, my_int):
                '''?is-odd <my_int>, reply '1' if <my_int> is odd, else 0'''
    ...         req.inform('Checking oddity of %d' % my_int)
    ...         return ("ok", my_int % 2)
    ...

    """
    include_msg = options.pop('include_msg', False)
    has_req = options.pop('has_req', True)
    major = options.pop('major', DEFAULT_KATCP_MAJOR)
    check_req = options.pop('_check_req', True)
    if len(options) > 0:
        raise TypeError('does not take keyword argument(s) %r.'
                        % options.keys())
    # Check that only the last type has multiple=True
    if len(types) > 1:
        for type_ in types[:-1]:
            if type_._multiple:
                raise TypeError('Only the last parameter type '
                                'can accept multiple arguments.')

    def decorator(handler):
        argnames = []

        # If this decorator is on the outside, get the parameter names which
        # have been preserved by the other decorator
        all_argnames = getattr(handler, "_orig_argnames", None)

        if all_argnames is None:
            # We must be on the inside. Introspect the parameter names.
            all_argnames = inspect.getargspec(handler)[0]

        params_start = 1        # Skip 'self' parameter
        if has_req:         # Skip 'req' parameter
            params_start += 1
        if include_msg:
            params_start += 1
        # Get other parameter names
        argnames = all_argnames[params_start:]

        if has_req and include_msg:
            def raw_handler(self, req, msg):
                new_args = unpack_types(types, msg.arguments, argnames, major)
                return handler(self, req, msg, *new_args)

        elif has_req and not include_msg:
            def raw_handler(self, req, msg):
                new_args = unpack_types(types, msg.arguments, argnames, major)
                return handler(self, req, *new_args)

        elif not has_req and include_msg:
            def raw_handler(self, msg):
                new_args = unpack_types(types, msg.arguments, argnames, major)
                return handler(self, msg, *new_args)

        elif not has_req and not include_msg:
            def raw_handler(self, msg):
                new_args = unpack_types(types, msg.arguments, argnames, major)
                return handler(self, *new_args)

        update_wrapper(raw_handler, handler)
        # explicitly note that this decorator has been run, so that
        # return_reply can know if it's on the outside.
        raw_handler._request_decorated = True
        return raw_handler

    return decorator

# partial calls below 'copy' the function, letting us change the docstring without
# affecting the original function's docstring
inform = partial(request, has_req=False)
update_wrapper(inform, request)
inform.__name__ = 'inform'
inform.__doc__ = """Decorator for inform handler methods.

The method being decorated should take arguments matching the list of types.
The decorator will unpack the request message into the arguments.

Parameters
----------
types : list of kattypes
    The types of the request message parameters (in order). A type
    with multiple=True has to be the last type.

Keyword Arguments
-----------------
include_msg : bool, optional
    Pass the request message as the third parameter to the decorated
    request handler function (default is False).
major : int, optional
    Major version of KATCP to use when interpreting types.
    Defaults to latest implemented KATCP version.


Examples
--------
>>> class MyDeviceClient(katcp.client.AsyncClient):
...     @inform(Int(), Float())
...     def inform_myinf(self, my_int, my_float):
...         '''Handle #myinf <my_int> <my_float> inform received from server'''
...         # Call some code here that reacts to my_inf and my_float

"""

unpack_message = partial(request, has_req=False)
update_wrapper(unpack_message, request)
update_wrapper.__name__ = 'unpack_message'
unpack_message.__doc__ = (
"""Decorator that unpacks katcp.Messages to function arguments.

The method being decorated should take arguments matching the list of types.
The decorator will unpack the request message into the arguments.

Parameters
----------
types : list of kattypes
    The types of the request message parameters (in order). A type
    with multiple=True has to be the last type.

Keyword Arguments
-----------------
include_msg : bool, optional
    Pass the request message as the third parameter to the decorated
    request handler function (default is False).
major : int, optional
    Major version of KATCP to use when interpreting types.
    Defaults to latest implemented KATCP version.

Examples
--------
>>> class MyClient(DeviceClient):
...     @unpack_message(Str(), Int(), Float(), Bool())
...     def reply_myreq(self, status, my_int, my_float, my_bool):
...         print 'myreq replied with ', (status, my_int, my_float, my_bool)
...
...     @unpack_message(Str(), Int(), include_msg=True)
...     def inform_fruit_picked(self, msg, fruit, no_picked):
...         print no_picked, 'of fruit ', fruit, ' picked.'
...         print 'Raw inform message: ', str(msg)

""")


def return_reply(*types, **options):
    """Decorator for returning replies from request handler methods.

    The method being decorated should return an iterable of result
    values. If the first value is 'ok', the decorator will check the
    remaining values against the specified list of types (if any).
    If the first value is 'fail' or 'error', there must be only
    one remaining parameter, and it must be a string describing the
    failure or error  In both cases, the decorator will pack the
    values into a reply message.

    Parameters
    ----------
    types : list of kattypes
        The types of the reply message parameters (in order).

    Keyword Arguments
    -----------------
    major : int, optional
        Major version of KATCP to use when interpreting types.
        Defaults to latest implemented KATCP version.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @request(Int())
    ...     @return_reply(Int(), Float())
    ...     def request_myreq(self, req, my_int):
    ...         return ("ok", my_int + 1, my_int * 2.0)
    ...

    """
    major = options.pop('major', DEFAULT_KATCP_MAJOR)
    if len(options) > 0:
        raise TypeError('return_reply does not take keyword argument(s) %r.'
                        % options.keys())

    # Check that only the last type has multiple=True
    if len(types) > 1:
        for type_ in types[:-1]:
            if type_._multiple:
                raise TypeError('Only the last parameter type '
                                'can accept multiple arguments.')

    def decorator(handler):
        if not handler.__name__.startswith("request_"):
            raise ValueError("This decorator can only be used on a katcp"
                             " request handler (method name should start"
                             " with 'request_').")
        msgname = convert_method_name('request_', handler.__name__)

        @wraps(handler)
        def raw_handler(self, *args):
            reply_args = handler(self, *args)
            if gen.is_future(reply_args):
                return async_make_reply(msgname, types, reply_args, major)
            else:
                return make_reply(msgname, types, reply_args, major)


        # TODO NM 2017-01-12 Consider using the decorator module to create
        # signature preserving decorators that would avoid the need for this
        # trickery
        if not getattr(handler, "_request_decorated", False):
            # We are on the inside.
            # We must preserve the original function parameter names for the
            # request decorator
            raw_handler._orig_argnames = inspect.getargspec(handler)[0]

        return raw_handler

    return decorator


def send_reply(*types, **options):
    """Decorator for sending replies from request callback methods.

    This decorator constructs a reply from a list or tuple returned
    from a callback method, but unlike the return_reply decorator it
    also sends the reply rather than returning it.

    The list/tuple returned from the callback method must have req (a
    ClientRequestConnection instance) as its first parameter and the original
    message as the second. The original message is needed to determine the
    message name and ID.

    The device with the callback method must have a reply method.

    Parameters
    ----------
    types : list of kattypes
        The types of the reply message parameters (in order).

    Keyword Arguments
    -----------------
    major : int, optional
        Major version of KATCP to use when interpreting types.
        Defaults to latest implemented KATCP version.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @send_reply(Int(), Float())
    ...     def my_callback(self, req):
    ...         return (req, "ok", 5, 2.0)
    ...

    """
    major = options.pop('major', DEFAULT_KATCP_MAJOR)
    if len(options) > 0:
        raise TypeError('send_reply does not take keyword argument(s) %r.'
                        % options.keys())

    def decorator(handler):
        @wraps(handler)
        def raw_handler(self, *args):
            reply_args = handler(self, *args)
            req = reply_args[0]
            reply = make_reply(req.msg.name, types, reply_args[1:], major)
            req.reply_with_message(reply)
        return raw_handler

    return decorator


def make_reply(msgname, types, arguments, major):
    """Helper method for constructing a reply message from a list or tuple.

    Parameters
    ----------
    msgname : str
        Name of the reply message.
    types : list of kattypes
        The types of the reply message parameters (in order).
    arguments : list of objects
        The (unpacked) reply message parameters.
    major : integer
        Major version of KATCP to use when packing types

    """
    status = arguments[0]
    if status == "fail":
        return Message.reply(
            msgname, *pack_types((Str(), Str()), arguments, major))
    if status == "ok":
        return Message.reply(
            msgname, *pack_types((Str(),) + types, arguments, major))
    raise ValueError("First returned value must be 'ok' or 'fail'.")

def concurrent_reply(handler):
    """Decorator for concurrent async request handlers

    By default async request handlers that return a Future are serialised
    per-connection, i.e. until the most recent handler resolves its future, the
    next message will not be read from the client stream. A handler decorated
    with this decorator allows the next message to be read before it has
    resolved its future, allowing multiple requests from a single client to be
    handled concurrently. This is similar to raising AsyncReply.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @return_reply(Int())
    ...     @concurrent_reply
    ...     @tornado.gen.coroutine
    ...     def request_myreq(self, req):
    ...         '''A slow request'''
    ...         result = yield self.slow_operation()
    ...         raise tornado.gen.Return((req, result))
    ...

    """

    handler._concurrent_reply = True
    return handler

def minimum_katcp_version(major, minor=0):
    """Decorator; exclude handler if server's protocol version is too low

    Useful for including default handler implementations for KATCP features that
    are only present in certain KATCP protocol versions

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     '''This device server will expose ?myreq'''
    ...     PROTOCOL_INFO = katcp.core.ProtocolFlags(5, 1)
    ...
    ...     @minimum_katcp_version(5, 1)
    ...     def request_myreq(self, req, msg):
    ...         '''A request that should only be present for KATCP >v5.1'''
    ...         # Request handler implementation here.
    ...
    >>> class MyOldDevice(MyDevice):
    ...     '''This device server will not expose ?myreq'''
    ...
    ...     PROTOCOL_INFO = katcp.core.ProtocolFlags(5, 0)
    ...

    """

    version_tuple = (major, minor)
    def decorator(handler):
        handler._minimum_katcp_version = version_tuple
        return handler

    return decorator

def has_katcp_protocol_flags(protocol_flags):
    """Decorator; only include handler if server has these protocol flags

    Useful for including default handler implementations for KATCP features that
    are only present when certain server protocol flags are set.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     '''This device server will expose ?myreq'''
    ...     PROTOCOL_INFO = katcp.core.ProtocolFlags(5, 0, [
                        katcp.core.ProtocolFlags.MULTI_CLIENT])
    ...
    ...     @has_katcp_protocol_flags([katcp.core.ProtocolFlags.MULTI_CLIENT])
    ...     def request_myreq(self, req, msg):
    ...         '''A request that requires multi-client support'''
    ...         # Request handler implementation here.
    ...
    >>> class MySingleClientDevice(MyDevice):
    ...     '''This device server will not expose ?myreq'''
    ...
    ...     PROTOCOL_INFO = katcp.core.ProtocolFlags(5, 0, [])
    ...

    """
    def decorator(handler):
        handler._has_katcp_protocol_flags = protocol_flags
        return handler

    return decorator

def request_timeout_hint(timeout_hint):
    """Decorator; add recommended client timeout hint to a request for request

    Useful for requests that take longer than average to reply. Hint is provided
    to clients via ?request-timeout-hint. Note this is only exposed if the
    device server sets the protocol version to KATCP v5.1 or higher and enables
    the REQUEST_TIMEOUT_HINTS flag in its PROTOCOL_INFO class attribute

    Parameters
    ----------
    timeout_hint : float (seconds) or None
        How long the decorated request should reasonably take to reply. No
        timeout hint if None, similar to never using the decorator, provided for
        consistency.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @return_reply(Int())
    ...     @request_timeout_hint(15) # Set request timeout hint to 15 seconds
    ...     @tornado.gen.coroutine
    ...     def request_myreq(self, req):
    ...         '''A slow request'''
    ...         result = yield self.slow_operation()
    ...         raise tornado.gen.Return((req, result))
    ...

    """
    if timeout_hint is not None:
        timeout_hint = float(timeout_hint)

    def decorator(handler):
        handler.request_timeout_hint = timeout_hint
        return handler

    return decorator

@gen.coroutine
def async_make_reply(msgname, types, arguments_future, major):
    """Wrap future that will resolve with arguments needed by make_reply()."""
    arguments = yield arguments_future
    raise gen.Return(make_reply(msgname, types, arguments, major))


def unpack_types(types, args, argnames, major):
    """Parse arguments according to types list.

    Parameters
    ----------
    types : list of kattypes
        The types of the arguments (in order).
    args : list of strings
        The arguments to parse.
    argnames : list of strings
        The names of the arguments.
    major : integer
        Major version of KATCP to use when packing types

    """
    if len(types) > 0:
        multiple = types[-1]._multiple
    else:
        multiple = False

    if len(types) < len(args) and not multiple:
        raise FailReply("Too many parameters given.")

    # Wrap the types in parameter objects
    params = []
    for i, kattype in enumerate(types):
        name = ""
        if i < len(argnames):
            name = argnames[i]
        params.append(Parameter(i+1, name, kattype, major))

    if len(args) > len(types) and multiple:
        for i in range(len(types), len(args)):
            params.append(Parameter(i+1, name, kattype, major))

    # if len(args) < len(types) this passes in None for missing args
    return map(lambda param, arg: param.unpack(arg), params, args)


def pack_types(types, args, major):
    """Pack arguments according the the types list.

    Parameters
    ----------
    types : list of kattypes
        The types of the arguments (in order).
    args : list of objects
        The arguments to format.
    major : integer
        Major version of KATCP to use when packing types

    """
    if len(types) > 0:
        multiple = types[-1]._multiple
    else:
        multiple = False

    if len(types) < len(args) and not multiple:
        raise ValueError("Too many arguments to pack.")

    if len(args) < len(types):
        # this passes in None for missing args
        retvals = map(lambda ktype, arg: ktype.pack(arg, major=major),
                      types, args)
    else:
        retvals = [ktype.pack(arg, major=major)
                   for ktype, arg in zip(types, args)]

    if len(args) > len(types) and multiple:
        last_ktype = types[-1]
        for arg in args[len(types):]:
            retvals.append(last_ktype.pack(arg, major=major))

    return retvals
