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
    initial_value = 0
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
    initial_value = 0.0
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
    initial_value = False
    encode = staticmethod(lambda value: value and "1" or "0")

    @staticmethod
    def decode(value):
        if value not in ("0", "1"):
            raise ValueError("Boolean value must be 0 or 1.")
        return value == "1"


class Discrete(KatcpType):
    name = "discrete"
    initial_value = "unknown"
    encode = staticmethod(lambda value: value)
    decode = staticmethod(lambda value: value)

    def __init__(self, default=None, *args):
        super(Discrete, self).__init__(default=default)
        self._valid_values = set(args)

    def check(self, value):
        if not value in self._valid_values:
            raise ValueError("Discrete value '%s' is not one of %s"
                % (value, list(self._valid_values)))


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
    initial_value = LRU_NOMINAL

    encode = staticmethod(lambda value: LRU_VALUES[value])
    decode = staticmethod(lambda value: LRU_CONSTANTS[value])


class Timestamp(KatcpType):

    # TODO: Convert from KATCP integer timestamp (in ms)
    # to Python float timestamp (in s)

    name = "timestamp"
    initial_value = 0.0
    encode = lambda value: "%d" % (value,)
    decode = lambda value: int(value)


## Request and inform method decorators
#

def request(ret=None, *types):
    """Decorator for request handler methods.

       The method being decorated should take arguments matching
       the list of types. The decorator will unpack the request
       message into the arguments. The method should then return an
       iterable of results values, which the decorator will pack into
       a reply message using the list of types specified in ret.
       """
    def decorator(handler):

        def raw_handler(self, sock, msg):
            args = unpack_types(types, msg.arguments)
            reply_args = handler(sock, *args)
            return katcp.Message.reply(msg.name, *pack_types(ret, reply_args))

        return raw_handler

    return decorator

def inform(*types):
    """Decorator for inform handler methods.

       The method being decorated should take arguments matching
       the list of types. The decorator will unpack the request
       message into the arguments.
       """
    def decorator(handler):

        def raw_handler(self, sock, msg):
            args = unpack_types(type_str, msg.arguments)
            return handler(sock, *args)

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
