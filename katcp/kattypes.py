import katcp
import inspect
import struct
import re

"""Utilities for dealing with KATCP types.
   """

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
        return ",".join(sorted(value, key=str.lower))

    def decode(self, value):
        if self.all_keyword and value == self.all_keyword:
            return sorted(list(self._valid_values), key=str.lower)
        return sorted([v.strip() for v in value.split(",")], key=str.lower)

    def __init__(self, values, default=None, case_insensitive=False, all_keyword="all"):
        super(DiscreteMulti, self).__init__(values, default, case_insensitive)
        self.all_keyword = all_keyword

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
            raise katcp.FailReply("Error in parameter %s (%s): %s" % (self.position, self.name, message))


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
        return katcp.Message.reply(msgname, *pack_types((Str(),Str()), arguments))
    if status == "ok":
        return katcp.Message.reply(msgname, *pack_types((Str(),) + types, arguments))
    raise ValueError("First returned value must be 'ok' or 'fail'.")

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
