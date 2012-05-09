# kattypes.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Utilities for dealing with KATCP types.
   """

import inspect
import struct
import re
from .core import Message, FailReply

# KATCP Type Classes
#

# XXX how about a mapping from python types -> kattypes, so creating
#     a sensor would not require importing kattypes


class KatcpType(object):
    """Class representing a KATCP type.

    Sub-classes should:

      * Set the :attr:`name` attribute.
      * Implement the :meth:`encode` method.
      * Implement the :meth:`decode` method.

    Parameters
    ----------
    default : object
        The default value for this type.
    optional : boolean
        Whether the value is allowed to be None.
    multiple : boolean
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

    def check(self, value):
        """Check whether the value is valid.

        Do nothing if the value is valid. Raise an exception
        if the value is not valid.
        """
        pass

    def pack(self, value, nocheck=False):
        """Return the value formatted as a KATCP parameter.

        Parameters
        ----------
        value : object
            The value to pack.
        nocheck : bool
            Whether to check that the value is valid before
            packing it.

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
            self.check(value)
        return self.encode(value)

    def unpack(self, packed_value):
        """Parse a KATCP parameter into an object.

        Parameters
        ----------
        packed_value : str
            The unescaped KATCP string to parse into a value.

        Returns
        -------
        value : object
            The value the KATCP string represented.
        """
        if packed_value is None:
            value = self.get_default()
        else:
            value = self.decode(packed_value)
        if value is not None:
            self.check(value)
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

    encode = lambda self, value: "%d" % (value,)

    def decode(self, value):
        try:
            return int(value)
        except:
            raise ValueError("Could not parse value '%s' as integer." % value)

    def __init__(self, min=None, max=None, **kwargs):
        super(Int, self).__init__(**kwargs)
        self._min = min
        self._max = max

    def check(self, value):
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
    encode = lambda self, value: "%.15g" % (value,)

    def decode(self, value):
        try:
            return float(value)
        except:
            raise ValueError("Could not parse value '%s' as float." % value)

    def __init__(self, min=None, max=None, **kwargs):
        super(Float, self).__init__(**kwargs)
        self._min = min
        self._max = max

    def check(self, value):
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

    encode = lambda self, value: value and "1" or "0"

    def decode(self, value):
        if value not in ("0", "1"):
            raise ValueError("Boolean value must be 0 or 1.")
        return value == "1"


class Str(KatcpType):
    """The KATCP string type."""

    name = "string"

    encode = lambda self, value: value
    decode = lambda self, value: value


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

    def check(self, value):
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
        if not value in values:
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

    def encode(self, value):
        if value not in Lru.LRU_VALUES:
            raise ValueError("Lru value must be LRU_NOMINAL or LRU_ERROR.")
        return Lru.LRU_VALUES[value]

    def decode(self, value):
        if value not in Lru.LRU_CONSTANTS:
            raise ValueError("Lru value must be 'nominal' or 'error'.")
        return Lru.LRU_CONSTANTS[value]


class Timestamp(KatcpType):
    """The KATCP timestamp type."""

    name = "timestamp"

    encode = lambda self, value: "%i" % (int(float(value) * 1000),)

    def decode(self, value):
        try:
            return float(value) / 1000
        except:
            raise ValueError("Could not parse value '%s' as timestamp." %
                             value)


class TimestampOrNow(Timestamp):
    """KatcpType representing either a Timestamp or the special value
       :const:`katcp.kattypes.TimestampOrNow.NOW`.

       Floats are encoded as for :class:`katcp.kattypes.Timestamp`.
       :const:`katcp.kattypes.TimestampOrNow.NOW` is encoded as the string
       "now".
       """

    name = "timestamp_or_now"

    NOW = object()

    def encode(self, value):
        if value is self.NOW:
            return "now"
        return super(TimestampOrNow, self).encode(value)

    def decode(self, value):
        if value == "now":
            return self.NOW
        return super(TimestampOrNow, self).decode(value)


class StrictTimestamp(KatcpType):
    """The a timestamp that enforces the XXXX.YYY format for timestamps.
    """

    name = "strict_timestamp"

    def encode(self, value):
        try:
            return "%.15g" % (value * 1000.0)
        except:
            raise ValueError("Could not encode value %r as strict timestamp." %
                             value)

    def decode(self, value):
        try:
            parts = value.split(".", 1)
            _int_parts = [int(x) for x in parts]
            return float(value) / 1000.0
        except:
            raise ValueError("Could not parse value '%s' as strict timestamp."
                             % value)

    def check(self, value):
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

    def encode(self, value):
        try:
            return struct.pack(self._fmt, *value)
        except struct.error, e:
            raise ValueError("Could not pack %s into struct with format "
                             "%s: %s" % (value, self._fmt, e))

    def decode(self, value):
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

    def check(self, value):
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
    all_keyword : str
        The string which represents the list of all allowed values.
    separator : str
        The separator used in the packed value string.
    """

    name = "discretemulti"

    def encode(self, value):
        return self.separator.join(sorted(value, key=str.lower))

    def decode(self, value):
        if self.all_keyword and value == self.all_keyword:
            return sorted(list(self._valid_values), key=str.lower)
        return sorted([v.strip() for v in value.split(self.separator)],
                      key=str.lower)

    def __init__(self, values, all_keyword="all", separator=",", **kwargs):
        self.all_keyword = all_keyword
        self.separator = separator
        super(DiscreteMulti, self).__init__(values, **kwargs)

    def check(self, value):
        """Check that each item in the value list is in the allowed set."""
        for v in value:
            super(DiscreteMulti, self).check(v)


class Parameter(object):
    """Wrapper for kattypes which holds parameter-specific information

    Parameters
    ----------
    position : int
        The parameter's position (starts at 1)
    name : str
        The parameter's name (introspected)
    kattype : KatcpType object
        The parameter's kattype
    """

    def __init__(self, position, name, kattype):
        self.position = position
        self.name = name
        self._kattype = kattype

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
        return self._kattype.pack(value)

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
            return self._kattype.unpack(value)
        except ValueError, message:
            raise FailReply("Error in parameter %s (%s): %s" %
                            (self.position, self.name, message))


## request, return_reply and inform method decorators
#

def request(*types, **options):
    """Decorator for request handler methods.

    The method being decorated should take a sock argument followed
    by arguments matching the list of types. The decorator will
    unpack the request message into the arguments.

    Parameters
    ----------
    types : list of kattypes
        The types of the request message parameters (in order). A type
        with multiple=True has to be the last type.

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @request(Int(), Float(), Bool())
    ...     @reply(Int(), Float())
    ...     def request_myreq(self, sock, my_int, my_float, my_bool):
    ...         return ("ok", my_int + 1, my_float / 2.0)
    ...
    """
    include_msg = options.get('include_msg', False)
    # Check that only the last type has multiple=True
    if len(types) > 1:
        for type_ in types[:-1]:
            if type_._multiple:
                raise TypeError(
                    'Only the last parameter type can accept multiple arguments.')

    def decorator(handler):
        argnames = []

        # If this decorator is on the outside, get the parameter names which
        # have been preserved by the other decorator
        all_argnames = getattr(handler, "_orig_argnames", None)

        if all_argnames is None:
            # We must be on the inside. Introspect the parameter names.
            all_argnames = inspect.getargspec(handler)[0]

        # Slightly hacky way of determining whether there is a sock
        has_sock = len(all_argnames) > 1 and all_argnames[1] == "sock"

        params_start = 1
        if has_sock:
            params_start += 1
        if include_msg:
            params_start += 1
        # Get other parameter names
        argnames = all_argnames[params_start:]

        def raw_handler(self, *args):
            if has_sock:
                (sock, msg) = args
                new_args = unpack_types(types, msg.arguments, argnames)
                if include_msg:
                    return handler(self, sock, msg, *new_args)
                else:
                    return handler(self, sock, *new_args)
            else:
                (msg,) = args
                new_args = unpack_types(types, msg.arguments, argnames)
                if include_msg:
                    return handler(self, msg, *new_args)
                else:
                    return handler(self, *new_args)

        raw_handler.__name__ = handler.__name__
        raw_handler.__doc__ = handler.__doc__
        # explicitly note that this decorator has been run, so that
        # return_reply can know if it's on the outside.
        raw_handler._request_decorated = True
        return raw_handler

    return decorator

inform = request
inform.__doc__ = """Decorator for inform handler methods.

       This is currently identical to the request decorator, and is
       thus an alias.
       """


def return_reply(*types):
    """Decorator for returning replies from request handler methods

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

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @request(Int())
    ...     @reply(Int(), Float())
    ...     def request_myreq(self, sock, my_int):
    ...         return ("ok", my_int + 1, my_int * 2.0)
    ...
    """
    # Check that only the last type has multiple=True
    if len(types) > 1:
        for type_ in types[:-1]:
            if type_._multiple:
                raise TypeError(
                    'Only the last parameter type can accept multiple arguments.')

    def decorator(handler):
        if not handler.__name__.startswith("request_"):
            raise ValueError("This decorator can only be used on a katcp"
                             " request handler.")
        msgname = handler.__name__[8:].replace("_", "-")

        def raw_handler(self, *args):
            reply_args = handler(self, *args)
            return make_reply(msgname, types, reply_args)
        raw_handler.__name__ = handler.__name__
        raw_handler.__doc__ = handler.__doc__

        if not getattr(handler, "_request_decorated", False):
            # We are on the inside.
            # We must preserve the original function parameter names for the
            # request decorator
            raw_handler._orig_argnames = inspect.getargspec(handler)[0]

        return raw_handler

    return decorator


def send_reply(*types):
    """Decorator for sending replies from request callback methods

    This decorator constructs a reply from a list or tuple returned
    from a callback method, but unlike the return_reply decorator it
    also sends the reply rather than returning it.

    The list/tuple returned from the callback method must have a sock
    as its first parameter and the original message as the second. The
    original message is needed to determine the message name and ID.

    The device with the callback method must have a reply method.

    Parameters
    ----------
    types : list of kattypes
        The types of the reply message parameters (in order).

    Examples
    --------
    >>> class MyDevice(DeviceServer):
    ...     @send_reply('myreq', Int(), Float())
    ...     def my_callback(self, msg, sock):
    ...         return (sock, msg, "ok", 5, 2.0)
    ...
    """
    def decorator(handler):
        def raw_handler(self, *args):
            reply_args = handler(self, *args)
            sock = reply_args[0]
            msg = reply_args[1]
            reply = make_reply(msg.name, types, reply_args[2:])
            self.reply(sock, reply, msg)
        return raw_handler

    return decorator


def make_reply(msgname, types, arguments):
    """Helper method for constructing a reply message from a list or tuple

    Parameters
    ----------
    msgname : str
        Name of the reply message.
    types : list of kattypes
        The types of the reply message parameters (in order).
    arguments : list of objects
        The (unpacked) reply message parameters.
    """
    status = arguments[0]
    if status == "fail":
        return Message.reply(msgname, *pack_types((Str(), Str()), arguments))
    if status == "ok":
        return Message.reply(msgname, *pack_types((Str(),) + types, arguments))
    raise ValueError("First returned value must be 'ok' or 'fail'.")


def unpack_types(types, args, argnames):
    """Parse arguments according to types list.

    Parameters
    ----------
    types : list of kattypes
        The types of the arguments (in order).
    args : list of strings
        The arguments to parse.
    argnames : list of strings
        The names of the arguments.
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
        params.append(Parameter(i+1, name, kattype))


    if len(args) > len(types) and multiple:
        for i in range(len(types), len(args)):
            params.append(Parameter(i+1, name, kattype))

    # if len(args) < len(types) this passes in None for missing args
    return map(lambda param, arg: param.unpack(arg), params, args)

def pack_types(types, args):
    """Pack arguments according the the types list.

    Parameters
    ----------
    types : list of kattypes
        The types of the arguments (in order).
    args : list of objects
        The arguments to format.
    """
    if len(types) > 0:
        multiple = types[-1]._multiple
    else:
        multiple = False

    if len(types) < len(args) and not multiple:
        raise ValueError("Too many arguments to pack.")

    if len(args) < len(types):
        # this passes in None for missing args
        retvals = map(lambda ktype, arg: ktype.pack(arg), types, args)
    else:
        retvals = [ktype.pack(arg) for ktype, arg in zip(types, args)]

    if len(args) > len(types) and multiple:
        last_ktype = types[-1]
        for arg in args[len(types):]:
            retvals.append(last_ktype.pack(arg))

    return retvals
