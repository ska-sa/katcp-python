import katcp
import inspect
import struct

"""Utilities for dealing with KATCP types.
   """

# KATCP Type Classes
#

class KatcpType(object):
    """Class representing a KATCP type."""

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
    encode = lambda self, value: "%e" % (value,)

    def decode(self, value):
        try:
            return float(value)
        except:
            raise ValueError("Could not parse value '%s' as float." % value)

    def __init__(self, min=None, max=None, default=None):
        super(Float, self).__init__(default=default)
        self._min = min
        self._max = max

    def format(self, value):
        return "%.1f" % value if abs(value) > 10**-1 or value == 0.0 else "%.1e" % value

    def check(self, value):
        if self._min is not None and value < self._min:
            raise ValueError("Float %s is lower than minimum %s."
                % (self.format(value), self.format(self._min)))
        if self._max is not None and value > self._max:
            raise ValueError("Float %s is higher than maximum %s."
                % (self.format(value), self.format(self._max)))


class Bool(KatcpType):
    encode = lambda self, value: value and "1" or "0"

    def decode(self, value):
        if value not in ("0", "1"):
            raise ValueError("Boolean value must be 0 or 1.")
        return value == "1"


class Discrete(KatcpType):
    encode = lambda self, value: value
    decode = lambda self, value: value

    def __init__(self, values, default=None):
        super(Discrete, self).__init__(default=default)
        self._values = list(values) # just to preserve ordering
        self._valid_values = set(values)

    def check(self, value):
        if not value in self._valid_values:
            raise ValueError("Discrete value '%s' is not one of %s."
                % (value, list(self._values)))


class Lru(KatcpType):
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

class Str(KatcpType):
    encode = staticmethod(lambda value: str(value))
    decode = staticmethod(lambda value: str(value))


class Timestamp(KatcpType):

    # TODO: Convert from KATCP integer timestamp (in ms)
    # to Python float timestamp (in s)

    encode = lambda self, value: "%i" % (int(float(value)*1000),)

    def decode(self, value):
        try:
            return float(value)/1000
        except:
            raise ValueError("Could not parse value '%s' as timestamp." % value)

class Struct(KatcpType):
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

    def __init__(self, fmt, default=None):
        super(Struct, self).__init__(default=default)
        self._fmt = fmt

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
            raise katcp.FailReply("Error in parameter %s (%s): %s" % (self.position, self.name, message))

## Request, return_reply and inform method decorators
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
        if orig_argnames:
            # If this decorator is on the outside, get the parameter names which have been preserved by the other decorator
            argnames = orig_argnames
        else:
            # Introspect the parameter names.  The first two are self and sock.
            argnames = inspect.getargspec(handler)[0][2:]

        def raw_handler(self, sock, msg):
            args = unpack_types(types, msg.arguments, argnames)
            return handler(self, sock, *args)

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
            raise ValueError("This decorator can only be used on a katcp request.")
        msgname = handler.__name__[8:].replace("_","-")
        def raw_handler(self, *args):
            reply_args = handler(self, *args)
            status = reply_args[0]
            if status == "fail":
                return katcp.Message.reply(msgname, *pack_types((Str(),Str()), reply_args))
            if status == "ok":
                return katcp.Message.reply(msgname, *pack_types((Str(),) + types, reply_args))
            raise ValueError("First returned value must be 'ok' or 'fail'.")
        raw_handler.__name__ = handler.__name__
        raw_handler.__doc__ = handler.__doc__
        # We must preserve the original function parameter names for the other decorator in case this decorator is on the inside
        # Introspect the parameter names.  The first two are self and sock.
        raw_handler._orig_argnames = inspect.getargspec(handler)[0][2:]
        return raw_handler

    return decorator

def unpack_types(types, args, argnames):
    """Parse arguments according to types list.
       """

    if len(types) < len(args):
        raise katcp.FailReply("Too many parameters given.")

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
