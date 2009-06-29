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
import time
from .katcp import Message, FailReply, KatcpSyntaxError

# KATCP Type Classes
#

class KatcpType(object):
    """Class representing a KATCP type."""

    name = "unknown"

    def __init__(self, default=None):
        """Construct a KATCP type.

           @param self This object.
           @param default Default value.
           """
        self._default = default

    def get_default(self):
        if self._default is None:
            raise ValueError("No value or default given")
        return self._default

    def check(self, value):
        pass

    def pack(self, value, nocheck=False):
        if value is None:
            value = self.get_default()
        if not nocheck:
            self.check(value)
        return self.encode(value)

    def unpack(self, packed_value):
        if packed_value is None:
            value = self.get_default()
        else:
            value = self.decode(packed_value)
        self.check(value)
        return value


class Int(KatcpType):

    name = "integer"

    encode = lambda self, value: "%d" % (value,)

    def decode(self, value):
        try:
            return int(value)
        except:
            raise ValueError("Could not parse value '%s' as integer." % value)

    def __init__(self, min=None, max=None, default=None):
        super(Int, self).__init__(default=default)
        self._min = min
        self._max = max

    def check(self, value):
        if self._min is not None and value < self._min:
            raise ValueError("Integer %d is lower than minimum %d."
                % (value, self._min))
        if self._max is not None and value > self._max:
            raise ValueError("Integer %d is higher than maximum %d."
                % (value, self._max))


class Float(KatcpType):

    name = "float"

    encode = lambda self, value: "%g" % (value,)

    def decode(self, value):
        try:
            return float(value)
        except:
            raise ValueError("Could not parse value '%s' as float." % value)

    def __init__(self, min=None, max=None, default=None):
        super(Float, self).__init__(default=default)
        self._min = min
        self._max = max

    def check(self, value):
        if self._min is not None and value < self._min:
            raise ValueError("Float %g is lower than minimum %g."
                % (value, self._min))
        if self._max is not None and value > self._max:
            raise ValueError("Float %g is higher than maximum %g."
                % (value, self._max))


class Bool(KatcpType):

    name = "boolean"

    encode = lambda self, value: value and "1" or "0"

    def decode(self, value):
        if value not in ("0", "1"):
            raise ValueError("Boolean value must be 0 or 1.")
        return value == "1"


class Str(KatcpType):

    name = "string"

    encode = lambda self, value: value
    decode = lambda self, value: value


class Discrete(Str):

    name = "discrete"

    def __init__(self, values, default=None, case_insensitive=False):
        super(Discrete, self).__init__(default=default)
        self._values = list(values) # just to preserve ordering
        self._valid_values = set(values)
        self._case_insensitive = case_insensitive
        if self._case_insensitive:
            self._valid_values_lower = set([val.lower() for val in self._values])

    def check(self, value):
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

    name = "timestamp"

    encode = lambda self, value: "%i" % (int(float(value)*1000),)

    def decode(self, value):
        try:
            return float(value)/1000
        except:
            raise ValueError("Could not parse value '%s' as timestamp." % value)


class Struct(KatcpType):

    name = "struct"

    def __init__(self, fmt, default=None):
        super(Struct, self).__init__(default=default)
        self._fmt = fmt

    def encode(self, value):
        try:
            return struct.pack(self._fmt, *value)
        except struct.error, e:
            raise ValueError("Could not pack %s into struct with format %s: %s" % (value, self._fmt, e))

    def decode(self, value):
        try:
            return struct.unpack(self._fmt, value)
        except struct.error, e:
            raise ValueError("Could not unpack %s from struct with format %s: %s" % (value, self._fmt, e))


class Regex(Str):

    name = "regex"

    def __init__(self, regex, default=None):
        self._regex = regex
        self._compiled = re.compile(regex)
        super(Regex, self).__init__(default=default)

    def check(self, value):
        if not self._compiled.match(value):
            raise ValueError("Value '%s' does not match regex '%s'." % (value, self._regex))


class Or(KatcpType):
    """This is intended for use with individual parameters with multiple
       allowed formats.

       e.g. Or(Float(), Regex("\d\d:\d\d"))

       Types are evaluated left-to-right, and the first type to pack /
       unpack the parameter successfully will be used.  To specify a
       default value, specify it in whichever individual type you would
       like to be used by default: the first type with a default will
       always pack / unpack None successfully.
    """

    name = "or"

    def __init__(self, types, default=None):
        self._types = types
        # disable default
        super(Or, self).__init__(default=None)

    def try_type_methods(self, methodname, params):
        returnvalue = None
        errors = {}
        for t in self._types:
            try:
                m = getattr(t, methodname)
                returnvalue = m(*params)
                break
            except (ValueError, TypeError, KeyError), e:
                errors[t.name] = str(e)
        if returnvalue is None:
            raise ValueError("; ".join(["%s: %s" % (name, errors[name]) for name in errors]))
        return returnvalue

    def pack(self, value, nocheck=False):
        try:
            return self.try_type_methods("pack", (value, nocheck))
        except ValueError, e:
            raise ValueError("Unable to pack value '%s' using any type in list. %s" % (value, e))

    def unpack(self, packed_value):
        try:
            return self.try_type_methods("unpack", (packed_value,))
        except ValueError, e:
            raise ValueError("Unable to unpack value '%s' using any type in list. %s" % (packed_value, e))


class DiscreteMulti(Discrete):

    name = "discretemulti"

    def encode(self, value):
        return self.separator.join(sorted(value, key=str.lower))

    def decode(self, value):
        if self.all_keyword and value == self.all_keyword:
            return sorted(list(self._valid_values), key=str.lower)
        return sorted([v.strip() for v in value.split(self.separator)], key=str.lower)

    def __init__(self, values, default=None, case_insensitive=False, separator=",", all_keyword="all"):
        super(DiscreteMulti, self).__init__(values, default, case_insensitive)
        self.all_keyword = all_keyword
        self.separator = separator

    def check(self, value):
        for v in value:
            super(DiscreteMulti, self).check(v)


class Parameter(object):
    """Wrapper for kattypes which holds parameter-specific information"""

    def __init__(self, position, name, kattype):
        """Construct a Parameter.

           @param self This object.
           @param position The parameter's position (starts at 1)
           @param name The parameter's name (introspected)
           @param kattype The parameter's kattype
           """
        self.position = position
        self.name = name
        self._kattype = kattype

    def pack(self, value):
        return self._kattype.pack(value)

    def unpack(self, value):
        # Wrap errors in FailReplies with information identifying the parameter
        try:
            return self._kattype.unpack(value)
        except ValueError, message:
            raise FailReply("Error in parameter %s (%s): %s" % (self.position, self.name, message))


## request, return_reply and inform method decorators
#

def request(*types):
    """Decorator for request handler methods.

       The method being decorated should take a sock argument followed
       by arguments matching the list of types. The decorator will
       unpack the request message into the arguments.
       """
    def decorator(handler):
        argnames = []
        orig_argnames = getattr(handler, "_orig_argnames", None)

        if orig_argnames is not None:
            # If this decorator is on the outside, get the parameter names which have been preserved by the other decorator
            argnames = orig_argnames
            # and the sock flag
            has_sock = getattr(handler, "_has_sock")
        else:
            # Introspect the parameter names.
            # Slightly hacky way of determining whether there is a sock
            has_sock = inspect.getargspec(handler)[0][1] == "sock"
            params_start = 2 if has_sock else 1
            # Get other parameter names
            argnames = inspect.getargspec(handler)[0][params_start:]

        def raw_handler(self, *args):
            if has_sock:
                (sock, msg) = args
                new_args = unpack_types(types, msg.arguments, argnames)
                return handler(self, sock, *new_args)
            else:
                (msg,) = args
                new_args = unpack_types(types, msg.arguments, argnames)
                return handler(self, *new_args)

        raw_handler.__name__ = handler.__name__
        raw_handler.__doc__ = handler.__doc__
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
    """

    def decorator(handler):
        if not handler.__name__.startswith("request_"):
            raise ValueError("This decorator can only be used on a katcp request handler.")
        msgname = handler.__name__[8:].replace("_","-")
        def raw_handler(self, *args):
            reply_args = handler(self, *args)
            return make_reply(msgname, types, reply_args)
        raw_handler.__name__ = handler.__name__
        raw_handler.__doc__ = handler.__doc__
        try:
            # We must preserve the original function parameter names for the other decorator in case this decorator is on the inside
            # Slightly hacky way of determining whether there is a sock
            has_sock = inspect.getargspec(handler)[0][1] == "sock"
            params_start = 2 if has_sock else 1
            # Get other parameter names
            raw_handler._orig_argnames = inspect.getargspec(handler)[0][params_start:]
            # we must also note whether there is a sock
            raw_handler._has_sock = has_sock
        except IndexError:
            # This probably means that this decorator is on the outside.
            pass
        return raw_handler

    return decorator

def send_reply(msgname, *types):
    """Decorator for sending replies from request callback methods

       This decorator constructs a reply from a list or tuple returned
       from a callback method, but unlike the return_reply decorator it
       also sends the reply rather than returning it.  The message name
       must be passed in explicitly, since the callback method is not
       expected to have a predictable name or input parameters.

       The list/tuple returned from the callback method must have a sock
       as its first parameter.

       The device with the callback method must have a send_message
       method.
    """

    def decorator(handler):
        def raw_handler(self, *args):
            reply_args = handler(self, *args)
            sock = reply_args[0]
            reply = make_reply(msgname, types, reply_args[1:])
            self.send_message(sock, reply)
        return raw_handler

    return decorator

def make_reply(msgname, types, arguments):
    """Helper method for constructing a reply message from a list or tuple"""
    status = arguments[0]
    if status == "fail":
        return Message.reply(msgname, *pack_types((Str(), Str()), arguments))
    if status == "ok":
        return Message.reply(msgname, *pack_types((Str(),) + types, arguments))
    raise ValueError("First returned value must be 'ok' or 'fail'.")

def unpack_types(types, args, argnames):
    """Parse arguments according to types list.
       """

    if len(types) < len(args):
        raise FailReply("Too many parameters given.")

    # Wrap the types in parameter objects
    params = []
    for i, kattype in enumerate(types):
        name = ""
        if i < len(argnames):
            name = argnames[i]
        params.append(Parameter(i+1, name, kattype))

    # if len(args) < len(types) this passes in None for missing args
    return map(lambda param, arg: param.unpack(arg), params, args)

def pack_types(types, args):
    """Pack arguments according the the types list.
       """
    if len(types) < len(args):
        raise ValueError("Too many arguments to pack.")
    # if len(args) < len(types) this passes in None for missing args
    return map(lambda ktype, arg: ktype.pack(arg), types, args)


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
