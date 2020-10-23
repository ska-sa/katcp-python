# Copyright 2014 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details
"""A high-level abstract interface to KATCP clients, sensors and requests."""

from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import abc
import collections
import logging
import sys

from builtins import object

import tornado

from future.utils import with_metaclass, PY2
from past.builtins import basestring
from tornado.concurrent import Future
from tornado.gen import Return, with_timeout

from katcp import Message, Sensor
from katcp.core import hashable_identity

logger = logging.getLogger(__name__)


class KATCPResourceError(Exception):
    """Error raised for resource-related errors"""


class KATCPResourceInactive(KATCPResourceError):
    """Raised when a request is made to an inactive resource"""


class KATCPSensorError(KATCPResourceError):
    """Raised if a problem occurred dealing with as KATCPSensor operation"""


class SensorResultTuple(collections.namedtuple(
        'SensorResultTuple',
        'object name python_identifier description type units reading')):
    """Per-sensor result of list_sensors() method

    Attributes
    ----------
    object : KATCPSensor instance
    name : str
        KATCP (i.e. unescaped) name of the sensor
    python_identifier : str
        Python-identifier name of the sensor.
    description : str
        KATCP description of the sensor
    type : str
        KATCP type of the sensor
    units : str
        KATCP units of the sensor
    reading : KATCPSensorReading instance
        Most recently received sensor reading
    """
    __slots__ = []  # Prevent dynamic attributes from being possible


def normalize_strategy_parameters(params):
    """Normalize strategy parameters to be a list of strings.

    Parameters
    ----------
    params : (space-delimited) string or sequence of strings/numbers Parameters
        expected by :class:`SampleStrategy` object, in various forms, where the first
        parameter is the name of the strategy.

    Returns
    -------
    params : tuple of strings
        Strategy parameters as a list of strings

    """
    def fixup_numbers(val):
        try:
            # See if it is a number
            return str(float(val))
        except ValueError:
            # ok, it is not a number we know of, perhaps a string
            return str(val)
    if isinstance(params, basestring):
        params = params.split(' ')
    # No number
    return tuple(fixup_numbers(p) for p in params)


def escape_name(name):
    """Escape sensor and request names to be valid Python identifiers."""
    return name.replace('.', '_').replace('-', '_')


class KATCPResource(with_metaclass(abc.ABCMeta, object)):

    """Base class to serve as the definition of the KATCPResource API.

    A class `C` implementing the KATCPResource API should register itself using
    KATCPResource.register(C) or subclass KATCPResource directly. A complication
    involved with subclassing is that all the abstract properties must be
    implemented as properties; normal instance attributes cannot be used.

    Attributes
    ----------
    Apart from the abstract properties described below

    TODO Describe how hierarchies are implemented. Also all other descriptions
    here so that the sphinx doc can be auto-generated from here.

    """

    def __init__(self):
        self._active = True

    @abc.abstractproperty
    def name(self):
        """Name of this KATCP resource."""

    @abc.abstractproperty
    def description(self):
        """Description of this KATCP resource."""

    @abc.abstractproperty
    def address(self):
        """Address of the underlying client/device.

        Type: tuple(host, port) or None, with host a string and port an integer.

        If this KATCPResource is not associated with a specific KATCP device
        (e.g. it is only a top-level container for a hierarchy of KATCP
        resources), the address should be None.

        """

    @abc.abstractproperty
    def is_connected(self):
        """Indicate whether the underlying client/device is connected or not."""

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
        """AttrDict of subordinate KATCPResource objects keyed by their names."""

    @tornado.gen.coroutine
    def wait(self, sensor_name, condition_or_value, timeout=5):
        """Wait for a sensor in this resource to satisfy a condition.

        Parameters
        ----------
        sensor_name : string
            The name of the sensor to check
        condition_or_value : obj or callable, or seq of objs or callables
            If obj, sensor.value is compared with obj. If callable,
            condition_or_value(reading) is called, and must return True if its
            condition is satisfied. Since the reading is passed in, the value,
            status, timestamp or received_timestamp attributes can all be used
            in the check.
        timeout : float or None
            The timeout in seconds (None means wait forever)

        Returns
        -------
        This command returns a tornado Future that resolves with True when the
        sensor value satisfies the condition, or False if the condition is
        still not satisfied after a given timeout period.

        Raises
        ------
        :class:`KATCPSensorError`
            If the sensor does not have a strategy set, or if the named sensor
            is not present

        """
        sensor_name = escape_name(sensor_name)
        sensor = self.sensor[sensor_name]
        try:
            yield sensor.wait(condition_or_value, timeout)
        except tornado.gen.TimeoutError:
            raise tornado.gen.Return(False)
        else:
            raise tornado.gen.Return(True)

    @abc.abstractmethod
    def list_sensors(self, filter="", strategy=False, status="",
                     use_python_identifiers=True, tuple=False, refresh=False):
        """List sensors available on this resource matching certain criteria.

        Parameters
        ----------
        filter : string, optional
            Filter each returned sensor's name against this regexp if specified.
            To ease the dichotomy between Python identifier names and actual
            sensor names, the default is to search on Python identifier names
            rather than KATCP sensor names, unless `use_python_identifiers`
            below is set to False. Note that the sensors of subordinate
            KATCPResource instances may have inconsistent names and Python
            identifiers, better to always search on Python identifiers in this
            case.
        strategy : {False, True}, optional
            Only list sensors with a set strategy if True
        status : string, optional
            Filter each returned sensor's status against this regexp if given
        use_python_identifiers : {True, False}, optional
            Match on python identifiers even the the KATCP name is available.
        tuple : {True, False}, optional, Default: False
            Return backwards compatible tuple instead of SensorResultTuples
        refresh : {True, False}, optional, Default: False
            If set the sensor values will be refreshed with get_value before
            returning the results.

        Returns
        -------
        sensors : list of SensorResultTuples, or list of tuples
            List of matching sensors presented as named tuples. The `object`
            field is the :class:`KATCPSensor` object associated with the sensor.
            Note that the name of the object may not match `name` if it
            originates from a subordinate device.
        """

    @tornado.gen.coroutine
    def set_sampling_strategies(self, filter, strategy_and_params):
        """Set a sampling strategy for all sensors that match the specified filter.

        Parameters
        ----------
        filter : string
            The regular expression filter to use to select the sensors to which
            to apply the specified strategy.  Use "" to match all sensors. Is
            matched using :meth:`list_sensors`.
        strategy_and_params : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.
        **list_sensor_args : keyword arguments
            Passed to the :meth:`list_sensors` call as kwargs

        Returns
        -------
        sensors_strategies : tornado Future
           resolves with a dict with the Python identifier names of the sensors
           as keys and the value a tuple:

           (success, info) with

           success : bool
              True if setting succeeded for this sensor, else False
           info : tuple
               normalised sensor strategy and parameters as tuple if success == True
               else, sys.exc_info() tuple for the error that occurred.
        """
        sensors_strategies = {}
        sensor_results = yield self.list_sensors(filter)
        for sensor_reslt in sensor_results:
            norm_name = sensor_reslt.object.normalised_name
            try:
                sensor_strat = yield self.set_sampling_strategy(norm_name, strategy_and_params)
                sensors_strategies[norm_name] = sensor_strat[norm_name]
            except Exception:
                sensors_strategies[norm_name] = (
                    False, sys.exc_info())
        raise tornado.gen.Return(sensors_strategies)

    @tornado.gen.coroutine
    def set_sampling_strategy(self, sensor_name, strategy_and_params):
        """Set a sampling strategy for a specific sensor.

        Parameters
        ----------
        sensor_name : string
            The specific sensor.
        strategy_and_params : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.

        Returns
        -------
        sensors_strategies : tornado Future
           resolves with a dict with the Python identifier names of the sensors
           as keys and the value a tuple:

           (success, info) with

           success : bool
              True if setting succeeded for this sensor, else False
           info : tuple
               normalised sensor strategy and parameters as tuple if success == True
               else, sys.exc_info() tuple for the error that occurred.
        """
        sensors_strategies = {}
        try:
            sensor_obj = self.sensor.get(sensor_name)
            yield sensor_obj.set_sampling_strategy(strategy_and_params)
            sensors_strategies[sensor_obj.normalised_name] = (
                    True, sensor_obj.sampling_strategy)
        except Exception:
            sensors_strategies[sensor_obj.normalised_name] = (
                    False, sys.exc_info())
        raise tornado.gen.Return(sensors_strategies)

    def set_active(self, active):
        self._active = bool(active)
        for child in dict.values(self.children):
            child.set_active(active)

    def is_active(self):
        return self._active


class KATCPRequest(with_metaclass(abc.ABCMeta, object)):

    """Abstract Base class to serve as the definition of the KATCPRequest API.

    Wrapper around a specific KATCP request to a given KATCP device.  Each
    available KATCP request for a particular device has an associated
    :class:`KATCPRequest` object in the object hierarchy. This wrapper is mainly
    for interactive convenience. It provides the KATCP request help string as a
    docstring and pretty-prints the result of the request.

    """

    def __init__(self, request_description, is_active=lambda: True):
        """Initialize request with given description and network client

        Parameters
        ----------
        request_description : dict
           name : str
            KATCP name of the request
           description : str
            KATCP request description (as returned by ?help <name>)
           timeout_hint : float or None
            Request timeout suggested by device or None if not provided
        is_active : callable, optional
            Returns True if this request is active, else False

        """
        for required_description_key in ('name', 'description', 'timeout_hint'):
            if required_description_key not in request_description:
                raise ValueError(
                    'Required request_description key {!r} not present'
                    .format(required_description_key))
        self._request_description = dict(request_description)
        self.__doc__ = '\n'.join(('KATCP Documentation',
                                  '===================',
                                  self.description,
                                  'KATCPRequest Documentation',
                                  '==========================',
                                  self.__doc__ or ''))
        self._is_active = is_active

    @property
    def name(self):
        """Name of the KATCP request."""
        return self._request_description['name']

    @property
    def description(self):
        """Description of KATCP request as obtained from the ?help request."""
        return self._request_description['description']

    @property
    def timeout_hint(self):
        """Request timeout suggested by device or None if not provided"""
        return self._request_description['timeout_hint']


    def __call__(self, *args, **kwargs):
        """Execute the KATCP request described by this object.

        All positional arguments of this function are converted to KATCP string
        representations and passed on as space-separated parameters to the KATCP
        device.

        Keyword Arguments
        -----------------
        timeout : None or float, optional
            Timeout in seconds for the request. If None, use request timeout
            hint received from server or default for the :class:`KATCPResource`
            instance that contains the request if no hint is available.
        mid : None or int, optional
            Message identifier to use for the request message. If None, use
            either auto-incrementing value or no mid depending on the KATCP
            protocol version (mid's were only introduced with KATCP v5) and the
            default of the containing :class:`KATCPResource` instance.

        Returns
        -------
        reply : tornado future resolving with :class:`KATCPReply` object
            KATCP request reply wrapped in KATCPReply object

        Raises
        ------
        :class:`ResourceInactive` if the resource is inactive when the request is made.
        """
        if self.is_active():
            return self.issue_request(*args, **kwargs)
        else:
            raise KATCPResourceInactive(
                "Can't make ?{} request; resource is inactive".format(self.name))

    @abc.abstractmethod
    def issue_request(self, *args, **kwargs):
        """Signature as for __call__

        Do the request immediately without checking active state.
        """

    def is_active(self):
        """True if resource for this request is active"""
        return self._is_active()

class KATCPDummyRequest(KATCPRequest):
    """Dummy counterpart to KATCPRequest that always returns a successful reply"""
    def issue_request(self, *args, **kwargs):
        reply_msg = Message.reply('fake', 'ok')
        reply = KATCPReply(reply_msg, [])
        fut = Future()
        fut.set_result(reply)
        return fut

class KATCPSensorReading(collections.namedtuple(
        'KATCPSensorReading', 'received_timestamp timestamp istatus value')):

    """Sensor reading as a (received_timestamp, timestamp, istatus, value) tuple.

    Attributes
    ----------
    received_timestamp : float
       Time (in seconds since UTC epoch) at which the sensor value was received.
    timestamp : float
       Time (in seconds since UTC epoch) at which the sensor value was determined.
    istatus : int Sensor status constant
       Whether the value represents an error condition or not, as in class:`katcp.Sensor`
       The status is stored as an int, but output as a string, eg 'nominal'.
    value : object
        The value of the sensor (the type will be appropriate to the
        sensor's type).
    """

    __slots__ = []              # Prevent dynamic attributes

    @property
    def status(self):
        " Returns the string representation of sensor status, eg 'nominal'"
        try:
            return Sensor.STATUSES[int(self.istatus)]
        except TypeError:
            return 'unknown'


class KATCPSensorsManager(with_metaclass(abc.ABCMeta, object)):

    """Sensor management class used by KATCPSensor. Abstracts communications details.

    This class should arrange:

    1. A mechanism for setting sensor strategies
    2. A mechanism for polling a sensor value
    3. Keeping track of- and reapplying sensor strategies after reconnect, etc.
    4. Providing local time. This is doing to avoid direct calls to time.time, allowing
       accelerated time testing / simulation / dry-running
    """

    @abc.abstractmethod
    def time(self):
        """Returns the current time (in seconds since UTC epoch)"""

    @abc.abstractmethod
    def get_sampling_strategy(self, sensor_name):
        """Get the current sampling strategy for the named sensor

        Parameters
        ----------

        sensor_name : str
            Name of the sensor (normal or escaped form)

        Returns
        -------

        strategy : tornado Future that resolves with tuple of str
            contains (<strat_name>, [<strat_parm1>, ...]) where the strategy names and
            parameters are as defined by the KATCP spec
        """

    @abc.abstractmethod
    def set_sampling_strategy(self, sensor_name, strategy_and_parms):
        """Set the sampling strategy for the named sensor

        Parameters
        ----------

        sensor_name : str
            Name of the sensor
        strategy : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.

        Returns
        -------

        done : tornado Future that resolves when done or raises KATCPSensorError

        Notes
        -----

        It is recommended that implementations use :func:`normalize_strategy_parameters`
        to process the strategy_and_parms parameter, since it will deal with both string
        and list versions and makes sure that numbers are represented as strings in a
        consistent format.

        This method should arrange for the strategy to be set on the underlying network
        device or whatever other implementation is used. This strategy should also be
        automatically re-set if the device is reconnected, etc. If a strategy is set for a
        non-existing sensor, it should still cache the strategy and ensure that is applied
        whenever said sensor comes into existence. This allows an applications to pre-set
        strategies for sensors before synced / connected to a device.

        """

    @abc.abstractmethod
    def drop_sampling_strategy(self, sensor_name):
        """Drop the sampling strategy for the named sensor from the cache

        Calling :meth:`set_sampling_strategy` requires the sensor manager to
        memorise the requested strategy so that it can automatically be reapplied.
        If the client is no longer interested in the sensor, or knows the sensor
        may be removed from the server, then it can use this method to ensure the
        manager forgets about the strategy.  This method will not change the current
        strategy.  No error is raised if there is no strategy to drop.

        Parameters
        ----------

        sensor_name : str
            Name of the sensor (normal or escaped form)

        """

    @abc.abstractmethod
    def poll_sensor(self, sensor_name):
        """Poll sensor and arrange for sensor object to be updated

        Returns
        -------

        done_future : tornado Future
            Resolves when the poll is complete, or raises KATCPSensorError
        """
        # TODO NM 2015-02-03 Might want to add a timeout parameter here, and to all the
        # other code that calls this

    @abc.abstractmethod
    def reapply_sampling_strategies(self):
        """Reapply all sensor strategies using cached values

        Would typically be called when a connection is re-established. Should
        not raise errors when resetting strategies for sensors that no longer
        exist on the KATCP resource.
        """


class KATCPSensor(with_metaclass(abc.ABCMeta, object)):
    """Wrapper around a specific KATCP sensor on a given KATCP device.

    Each available KATCP sensor for a particular device has an associated
    :class:`KATCPSensor` object in the object hierarchy. This wrapper is mainly
    for interactive convenience. It provides the KATCP request help string as a
    docstring and registers listeners. Subclasses need to call the base class
    version of __init__().
    """

    def __init__(self, sensor_description, sensor_manager):
        """Subclasses must arrange to call this in their __init__().

        Parameters
        ----------
        sensor_description : dict
           Description of the KATCP sensor, with keys same as the parameters of
           :class:`katcp.Sensor`
        sensor_manager : :class:`KATCPSensorsManager` instance
           Manages sensor strategies, allows sensor polling, and provides time
        """
        self._manager = sensor_manager
        self.clear_listeners()
        self._reading = KATCPSensorReading(0, 0, Sensor.UNKNOWN, None)
        # We'll be abusing a katcp.Sensor object slightly to make use of its
        # parsing and formatting functionality
        self._sensor = Sensor(**sensor_description)
        self._name = self._sensor.name
        # Overide the katpc.Sensor's set method with ours
        self._sensor.set = self.set
        # Steal the the katcp.Sensor's set_formatted method. Since we overrode
        # its set() method with ours, calling set_formatted will result in this
        # KATCPSensor object's value being set.
        self.set_formatted = self._sensor.set_formatted

    @property
    def parent_name(self):
        """Name of the parent of this KATCPSensor"""
        return self._manager.resource_name

    @property
    def name(self):
        """Name of this KATCPSensor"""
        return self._name

    @property
    def normalised_name(self):
        """Normalised name of this KATCPSensor that can be used as a python identifier"""
        return escape_name(self._name)

    @property
    def reading(self):
        """Most recently received sensor reading as KATCPSensorReading instance"""
        return self._reading

    @property
    def value(self):
        return self._reading.value

    @property
    def status(self):
        return self._reading.status

    @property
    def sampling_strategy(self):
        """Current sampling strategy"""
        return self._manager.get_sampling_strategy(self.name)

    @property
    def description(self):
        return self._sensor.description

    @property
    def units(self):
        return self._sensor.units

    @property
    def type(self):
        return self._sensor.type

    def parse_value(self, s_value):
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
        return self._sensor.parse_value(s_value)

    def set_strategy(self, strategy, params=None):
        """Set current sampling strategy for sensor.
        Add this footprint for backwards compatibility.

        Parameters
        ----------

        strategy : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.
        params : seq of str or str
            (<strat_name>, [<strat_parm1>, ...])

        Returns
        -------
        done : tornado Future that resolves when done or raises KATCPSensorError

        """
        if not params:
            param_args = []
        elif isinstance(params, basestring):
            param_args = [str(p) for p in params.split(' ')]
        else:
            if not isinstance(params, collections.Iterable):
                params = (params,)
            param_args = [str(p) for p in params]
        samp_strategy = " ".join([strategy] + param_args)
        return self._manager.set_sampling_strategy(self.name, samp_strategy)

    def set_sampling_strategy(self, strategy):
        """Set current sampling strategy for sensor

        Parameters
        ----------

        strategy : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.

        Returns
        -------
        done : tornado Future that resolves when done or raises KATCPSensorError

        """
        return self._manager.set_sampling_strategy(self.name, strategy)

    def drop_sampling_strategy(self):
        """Drop memorised sampling strategy for sensor, if any

        Calling this method ensures that the sensor manager does not attempt
        to reapply a sampling strategy.  It will not raise an error if no strategy
        has been set.  Use :meth:`set_sampling_strategy` to memorise a strategy again.
        """
        self._manager.drop_sampling_strategy(self.name)

    def register_listener(self, listener, reading=False):
        """Add a callback function that is called when sensor value is updated.

        The callback footprint is received_timestamp, timestamp, status, value.

        Parameters
        ----------
        listener : function
            Callback signature: if reading listener(katcp_sensor, reading) where
            `katcp_sensor` is this KATCPSensor instance `reading` is an instance of
            :class:`KATCPSensorReading`.

            Callback signature: default, if not reading listener(received_timestamp,
            timestamp, status, value)
        """
        listener_id = hashable_identity(listener)
        self._listeners[listener_id] = (listener, reading)
        logger.debug(
                    'Register listener for {}'
                    .format(self.name))

    def unregister_listener(self, listener):
        """Remove a listener callback added with register_listener().

        Parameters
        ----------
        listener : function
            Reference to the callback function that should be removed

        """
        listener_id = hashable_identity(listener)
        self._listeners.pop(listener_id, None)

    def is_listener(self, listener):
        listener_id = hashable_identity(listener)
        return listener_id in self._listeners

    def clear_listeners(self):
        """Clear any registered listeners to updates from this sensor."""
        self._listeners = {}

    def call_listeners(self, reading):
        logger.debug(
                    'Calling listeners {}'
                    .format(self.name))
        for listener, use_reading in list(self._listeners.values()):
            try:
                if use_reading:
                    listener(self, reading)
                else:
                    listener(reading.received_timestamp, reading.timestamp,
                             reading.status, reading.value)
            except Exception:
                logger.exception(
                    'Unhandled exception calling KATCPSensor callback {0!r}'
                    .format(listener))

    def set(self, timestamp, status, value):
        """Set sensor with a given received value, matches :meth:`katcp.Sensor.set`"""
        received_timestamp = self._manager.time()
        reading = KATCPSensorReading(received_timestamp, timestamp, status, value)
        self._reading = reading
        self.call_listeners(reading)

    def set_value(self, value, status=Sensor.NOMINAL, timestamp=None):
        """Set sensor value with optinal specification of status and timestamp"""
        if timestamp is None:
            timestamp = self._manager.time()
        self.set(timestamp, status, value)

    def set_formatted(self, raw_timestamp, raw_status, raw_value, major):
        """Set sensor using KATCP string formatted inputs

        Mirrors :meth:`katcp.Sensor.set_formatted`.

        This implementation is empty. Will, during instantiation, be overridden by the
        set_formatted() method of a katcp.Sensor object.
        """

    @tornado.gen.coroutine
    def get_reading(self):
        """Get a fresh sensor reading from the KATCP resource

        Returns
        -------
        reply : tornado Future resolving with  :class:`KATCPSensorReading` object

        Notes
        -----

        As a side-effect this will update the reading stored in this object, and result in
        registered listeners being called.
        """
        yield self._manager.poll_sensor(self._name)
        # By now the sensor manager should have set the reading
        raise Return(self._reading)

    @tornado.gen.coroutine
    def get_value(self):
        """Get a fresh sensor value from the KATCP resource

        Returns
        -------
        reply : tornado Future resolving with  :class:`KATCPSensorReading` object

        Notes
        -----

        As a side-effect this will update the reading stored in this object, and result in
        registered listeners being called.
        """
        yield self._manager.poll_sensor(self._name)
        # By now the sensor manager should have set the reading
        raise Return(self._reading.value)

    @tornado.gen.coroutine
    def get_status(self):
        """Get a fresh sensor status from the KATCP resource

        Returns
        -------
        reply : tornado Future resolving with  :class:`KATCPSensorReading` object

        Notes
        -----

        As a side-effect this will update the reading stored in this object, and result in
        registered listeners being called.
        """
        yield self._manager.poll_sensor(self._name)
        # By now the sensor manager should have set the reading
        raise Return(self._reading.status)

    def wait(self, condition_or_value, timeout=None):
        """Wait for the sensor to satisfy a condition.

        Parameters
        ----------
        condition_or_value : obj or callable, or seq of objs or callables
            If obj, sensor.value is compared with obj. If callable,
            condition_or_value(reading) is called, and must return True if its
            condition is satisfied. Since the reading is passed in, the value,
            status, timestamp or received_timestamp attributes can all be used
            in the check.
            TODO: Sequences of conditions (use SensorTransitionWaiter thingum?)
        timeout : float or None
            The timeout in seconds (None means wait forever)

        Returns
        -------
        This command returns a tornado Future that resolves with True when the
        sensor value satisfies the condition. It will never resolve with False;
        if a timeout is given a TimeoutError happens instead.

        Raises
        ------
        :class:`KATCPSensorError`
            If the sensor does not have a strategy set
        :class:`tornado.gen.TimeoutError`
            If the sensor condition still fails after a stated timeout period

        """
        if (isinstance(condition_or_value, collections.Sequence) and not
                isinstance(condition_or_value, basestring)):
            raise NotImplementedError(
                'Currently only single conditions are supported')
        condition_test = (condition_or_value if callable(condition_or_value)
                          else lambda s: s.value == condition_or_value)

        ioloop = tornado.ioloop.IOLoop.current()
        f = Future()
        if self.sampling_strategy == ('none', ):
            raise KATCPSensorError(
                'Cannot wait on a sensor that does not have a strategy set')

        def handle_update(sensor, reading):
            # This handler is called whenever a sensor update is received
            try:
                assert sensor is self
                if condition_test(reading):
                    self.unregister_listener(handle_update)
                    # Try and be idempotent if called multiple times after the
                    # condition is matched. This should not happen unless the
                    # sensor object is being updated in a thread outside of the
                    # ioloop.
                    if not f.done():
                        ioloop.add_callback(f.set_result, True)
            except Exception:
                f.set_exc_info(sys.exc_info())
                self.unregister_listener(handle_update)

        self.register_listener(handle_update, reading=True)
        # Handle case where sensor is already at the desired value
        ioloop.add_callback(handle_update, self, self._reading)

        if timeout:
            to = ioloop.time() + timeout
            timeout_f = with_timeout(to, f)
            # Make sure we stop listening if the wait times out to prevent a
            # buildup of listeners
            timeout_f.add_done_callback(
                lambda f: self.unregister_listener(handle_update))
            return timeout_f
        else:
            return f


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
    messages: list of :class:`katcp.Message` objects
        List of all messages returned by KATCP request, reply first
    reply:  :class:`katcp.Message` object
        Reply message returned by KATCP request
    informs: list of :class:`katcp.Message` objects
        List of inform messages returned by KATCP request

    The instance evaluates to nonzero (i.e. truthy) if the request succeeded.

    """

    __slots__ = []  # Prevent dynamic attributes from being possible

    def __repr__(self):
        """String representation for pretty-printing in IPython."""
        return '\n'.join(
            "%s%s %s" %
            (Message.TYPE_SYMBOLS[m.mtype], m.name, ' '.join(m.arguments))
            for m in self.messages)

    def __str__(self):
        """String representation using KATCP wire format"""
        return '\n'.join(str(m) for m in self.messages)

    def __bool__(self):
        """True if request succeeded (i.e. first reply argument is 'ok')."""
        return self.messages[0].reply_ok()

    if PY2:
        __nonzero__ = __bool__

    @property
    def messages(self):
        """List of all messages returned by KATCP request, reply first."""
        return [self.reply] + self.informs

    @property
    def succeeded(self):
        """True if request succeeded (i.e. first reply argument is 'ok')."""
        return bool(self)
