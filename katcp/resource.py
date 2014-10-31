"""A high-level abstract interface to KATCP clients, sensors and requests."""

import abc
import collections

from katcp import Message
from katcp.sampling import SampleStrategy


def normalize_strategy_parameters(params):
    """Normalize strategy parameters to be a list of strings.

    Parameters
    ----------
    params : (space-delimited) string or number or sequence of strings/numbers
        Parameters expected by :class:`SampleStrategy` object, in various forms

    Returns
    -------
    params : list of strings
        Strategy parameters as a list of strings

    """
    if not params:
        return []
    def fixup_numbers(val):
        try:
            # See if it is a number
            return str(float(val))
        except ValueError:
            # ok, it is not a number we know of, perhaps a string
            return str(val)
    if isinstance(params, basestring):
        params = params.split(' ')
    elif not isinstance(params, collections.Iterable):
        params = (params,)
    return [fixup_numbers(p) for p in params]


def escape_name(name):
    """Escape sensor and request names to be valid Python identifiers."""
    return name.replace('.', '_').replace('-', '_')


class KATCPResource(object):
    """Base class to serve as the definition of the KATCPResource API.

    A class `C` implementing the KATCPResource API should register itself using
    KATCPResource.register(C) or subclass KATCPResource directly. A complication
    involved with subclassing is that all the abstract properties must be
    implemented as properties; normal instance attributes cannot be used.

    Attributes
    ----------
    Apart from the abstract properties described below

    TODO Describe how hierarchies are implemented. Also all other descriptions
    here so that the sphinx doc can be autogenerated from here.

    """
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def name(self):
        """Name of this KATCP resource."""

    @abc.abstractproperty
    def address(self):
        """Address of the underlying client/device.

        Type: tuple(host, port) or None, with host a string and port an integer.

        If this KATCPResource is not associated with a specific KATCP device
        (e.g. it is only a top-level container for a hierarchy of KATCP
        resources), the address should be None.

        """

    @abc.abstractproperty
    def req(self):
        """Attribute root/container for all KATCP request wrappers.

        Each KATCP request that is exposed on a KATCP device should have a
        corresponding :class:`KATCPRequest` object so that calling

          `resource.req.request_name(arg1, arg2, ...)`

        sends a '?request-name arg1 arg2 ...' message to the KATCP device and
        waits for the associated inform-reply and reply messages.

        For a :class:`KATCPResource` object that exposes a hierarchical device
        it can choose to include lower-level request handlers here such that
        `resource.req.dev_request()` maps to `resource.dev.req.request()`.

        """

    @abc.abstractproperty
    def sensor(self):
        """Attribute root/container for all KATCP sensor wrappers.

        Each KATCP sensor that is exposed on a KATCP device should have a
        corresponding :class:`KATCPSensor` object so that

          `resource.sensor.sensor_name`

        corresponds to a sensor named e.g. 'sensor-name', where the object or
        attribute name is an escaped/Pythonised version of the original sensor
        name (see :func:`escape_name` for the escape mechanism). Hopefully the
        device is not crazy enough to have multiple sensors that map to the
        same Python identifier.

        A :class:`KATCPResource` object that exposes a hierarchical device can
        choose to include lower-level sensors here such that
        `resource.sensor.dev_sensorname` maps to
        `resource.dev.sensor.sensorname`.

        """

    @abc.abstractproperty
    def parent(self):
        """Parent KATCPResource object of this subordinate resource, or None."""

    @abc.abstractproperty
    def children(self):
        """Dict of subordinate KATCPResource objects keyed by their names."""

    SensorResultTuple = collections.namedtuple('SensorResultTuple', [
        'name', 'object', 'value', 'seconds', 'type', 'units'])

    def list_sensors(self, filter="", strategy=False, status="",
                     expand_underscore=True):
        """List sensors available on this resource matching certain criteria.

        Parameters
        ----------
        filter : string, optional
            Filter each returned sensor's name against this regexp if specified.
            To ease the dichotomy between Python identifier names and actual
            sensor names, '_' is replaced by '[.-_]' unless `expand_underscore`
            is set to False. Note that the sensors of subordinate KATCPResource
            instances are only filtered on Python identifiers, e.g. a device
            sensor 'piano.tuning-state' is interpreted as 'piano_tuning_state'.
        strategy : {False, True}, optional
            Only list sensors with a set strategy if True
        status : string, optional
            Filter each returned sensor's status against this regexp if given
        expand_underscore : {True, False}, optional
            Expand '_' in `filter` parameter to '[.-_]' if True

        Returns
        -------
        sensors : list of (name, object, value, seconds, type, units) tuples
            List of matching sensors presented as named tuples. The `object`
            field is the :class:`KATCPSensor` object associated with the sensor.
            Note that the name of the object may not match `name` if it
            originates from a subordinate device.

        """


class KATCPRequest(object):
    """Abstract Base class to serve as the definition of the KATCPRequest API.

    Wrapper around a specific KATCP request to a given KATCP device. This
    supports two modes of operation: blocking and asynchronous. The blocking
    mode blocks on the KATCP request and returns the actual reply, while the
    asynchronous mode returns a tornado future that resolves with the reply.

    Each available KATCP request for a particular device has an associated
    :class:`KATCPRequest` object in the object hierarchy. This wrapper is
    mainly for interactive convenience. It provides the KATCP request help
    string as a docstring and pretty-prints the result of the request.

    """
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def name(self):
        """Name of the KATCP request."""

    @abc.abstractproperty
    def description(self):
        """Description of KATCP request as obtained from the ?help request."""

    @abc.abstractmethod
    def __call__(self, *args, **kwargs):
        """Execute the KATCP request described by this object.

        All positional arguments of this function are converted to KATCP string
        representations and passed on as space-separated parameters to the KATCP
        device.

        Keyword Arguments
        -----------------
        timeout : None or float, optional
            Timeout in seconds for the request. If None, use default for the
            :class:`KATCPResource` instance that contains the request.
        mid : None or int, optional
            Message identifier to use for the request message. If None, use
            either auto-incrementing value or no mid depending on the KATCP
            protocol version (mid's were only introduced with KATCP v5) and the
            default of the containing :class:`KATCPResource` instance.

        Returns
        -------
        reply : tornado future or :class:`KATCPReply` object
            KATCP request reply wrapped in KATCPReply object in blocking mode,
            and additionally wrapped in a future in asynchronous mode

        """


class KATCPSensor(object):
    """Abstract Base class to serve as the definition of the KATCPSensor API.

    Wrapper around a specific KATCP sensor on a given KATCP device.

    Each available KATCP sensor for a particular device has an associated
    :class:`KATCPSensor` object in the object hierarchy. This wrapper is mainly
    for interactive convenience. It provides the KATCP request help string as a
    docstring and registers listeners. Subclasses need to call the base class
    version of __init__().

    Implementors of KATCPSensor should include the following attributes:

    Attributes
    ----------
    name : str
        KATCP name of the sensor
    description : str
        KATCP description of the sensor
    units: str
        KATCP units of the sensor
    type: str
        KATCP type of the sensor

    """
    __metaclass__ = abc.ABCMeta

    def __init__(self):
        """Subclasses must arrange to call this in their __init__()."""
        self._listeners = set()

    @property
    def strategy(self):
        return SampleStrategy.SAMPLING_LOOKUP[self._strategy.get_sampling()]

    @property
    def strategy_params(self):
        return self._strategy_params

    @strategy_params.setter
    def strategy_params(self, val):
        self._strategy_params = normalize_strategy_parameters(val)

    def register_listener(self, listener):
        """Add a callback function that is called when sensor value is updated.

        Parameters
        ----------
        listener : function
            Callback signature:
            listener(update_seconds, value_seconds, status, value)

        """
        self._listeners.add(listener)

    def unregister_listener(self, listener):
        """Remove a listener callback added with register_listener().

        Parameters
        ----------
        listener : function
            Reference to the callback function that should be removed

        """
        self._listeners.discard(listener)

    def clear_listeners(self):
        """Clear any registered listeners to updates from this sensor."""
        self._listeners = set()


_KATCPReplyTuple = collections.namedtuple('_KATCPReplyTuple', 'reply informs')


class KATCPReply(_KATCPReplyTuple):
    """Container for return messages of KATCP request (reply and informs).

    This is based on a named tuple with 'reply' and 'informs' fields so that
    the :class:`KATCPReply` object can still be unpacked into a normal tuple.

    Parameters
    ----------
    reply : :class:`katcp.Message` object
        Reply message returned by katcp request
    informs : list of :class:`katcp.Message` objects
        List of inform messages returned by KATCP request

    Attributes
    ----------
    messages : list of :class:`katcp.Message` objects
        List of all messages returned by KATCP request, reply first
    reply : :class:`katcp.Message` object
        Reply message returned by KATCP request
    informs : list of :class:`katcp.Message` objects
        List of inform messages returned by KATCP request

    The instance evaluates to nonzero (i.e. truthy) if the request succeeded.

    """
    def __repr__(self):
        """String representation for pretty-printing in IPython."""
        return '\n'.join(["%s%s %s" % (Message.TYPE_SYMBOLS[m.mtype], m.name,
                                       ' '.join(m.arguments))
                          for m in self.messages])

    def __nonzero__(self):
        """True if request succeeded (i.e. first reply argument is 'ok')."""
        return self.messages[0].reply_ok()

    @property
    def messages(self):
        """List of all messages returned by KATCP request, reply first."""
        return [self.reply] + self.informs

    @property
    def succeeded(self):
        """True if request succeeded (i.e. first reply argument is 'ok')."""
        return bool(self)
