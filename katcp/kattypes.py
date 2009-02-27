import katcp

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

    def pack(self, value):
        if value is None:
            return self.encode(self.get_default())
        self.check(value)
        return self.encode(value)

    def unpack(self, packed_value):
        if packed_value is None:
            return self.get_default()
        value = self.decode(packed_value)
        self.check(value)
        return value


class Int(KatcpType):
    name = "integer"
    encode = staticmethod(lambda value: "%d" % (value,))
    decode = staticmethod(lambda value: int(value))

    def __init__(self, max=None, min=None, default=None):
        super(Int, self).__init__(default=default)
        self._min = min
        self._max = max

    def check(self, value):
        if self._min is not None and value < self._min:
            raise ValueError("Integer %d is lower than minimum %d"
                % (value, self._min))
        if self._max is not None and value > self._max:
            raise ValueError("Integer %d is higher than maximum %d"
                % (value, self._max))


class Float(KatcpType):
    name = "float"
    encode = staticmethod(lambda value: "%e" % (value,))
    decode = staticmethod(lambda value: float(value))

    def __init__(self, max=None, min=None, default=None):
        super(Float, self).__init__(default=default)
        self._min = min
        self._max = max

    def check(self, value):
        if self._min is not None and value < self._min:
            raise ValueError("Float %f is lower than minimum %f"
                % (value, self._min))
        if self._max is not None and value > self._max:
            raise ValueError("Float %f is higher than maximum %f"
                % (value, self._max))


class Bool(KatcpType):
    name = "boolean"
    encode = staticmethod(lambda value: value and "1" or "0")

    @staticmethod
    def decode(value):
        if value not in ("0", "1"):
            raise ValueError("Boolean value must be 0 or 1.")
        return value == "1"


class Discrete(KatcpType):
    name = "discrete"
    encode = staticmethod(lambda value: value)
    decode = staticmethod(lambda value: value)

    def __init__(self, values, default=None):
        super(Discrete, self).__init__(default=default)
        self._values = list(values) # just to preserve ordering
        self._valid_values = set(values)

    def check(self, value):
        if not value in self._valid_values:
            raise ValueError("Discrete value '%s' is not one of %s"
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

    name = "lru"

    @staticmethod
    def encode(value):
        if value not in Lru.LRU_VALUES:
            raise ValueError("Lru value must be LRU_NOMINAL or LRU_ERROR")
        return Lru.LRU_VALUES[value]

    @staticmethod
    def decode(value):
        if value not in Lru.LRU_CONSTANTS:
            raise ValueError("Lru value must be 'nominal' or 'error'")
        return Lru.LRU_CONSTANTS[value]

class Str(KatcpType):
    name = "string"
    encode = staticmethod(lambda value: str(value))
    decode = staticmethod(lambda value: str(value))


class Timestamp(KatcpType):

    # TODO: Convert from KATCP integer timestamp (in ms)
    # to Python float timestamp (in s)

    name = "timestamp"
    encode = staticmethod(lambda value: "%i" % (int(float(value)*1000),))

    @staticmethod
    def decode(value):
        return float(value)/1000


## Request, return_reply and inform method decorators
#

def request(*types):
    """Decorator for request handler methods.

       The method being decorated should take a sock argument followed
       by arguments matching the list of types. The decorator will
       unpack the request message into the arguments.
       """
    def decorator(handler):

        def raw_handler(self, sock, msg):
            args = unpack_types(types, msg.arguments)
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
        def raw_handler(self, *args):
            if not handler.__name__.startswith("request_"):
                raise ValueError("This decorator can only be used on a katcp request.")
            msgname = handler.__name__[8:].replace("_","-")
            reply_args = handler(self, *args)
            status = reply_args[0]
            if status in ["fail", "error"]:
                return katcp.Message.reply(msgname, *pack_types((Str(),Str()), reply_args))
            if status == "ok":
                return katcp.Message.reply(msgname, *pack_types((Str(),) + types, reply_args))
            raise ValueError("First returned value must be 'ok', 'fail' or 'error'.")
        raw_handler.__name__ = handler.__name__
        raw_handler.__doc__ = handler.__doc__
        return raw_handler

    return decorator

def unpack_types(types, args):
    """Parse arguments according to types list.
       """
    if len(types) < len(args):
        raise ValueError("Too many arguments to unpack.")
    # if len(args) < len(types) this passes in None for missing args
    return map(lambda ktype, arg: ktype.unpack(arg), types, args)

def pack_types(types, args):
    """Pack arguments according the the types list.
       """
    if len(types) < len(args):
        raise ValueError("Too many arguments to pack.")
    # if len(args) < len(types) this passes in None for missing args
    return map(lambda ktype, arg: ktype.pack(arg), types, args)
