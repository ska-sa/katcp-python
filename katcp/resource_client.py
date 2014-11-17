# Copyright 2014 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

import logging
import sys
import re

import tornado

from functools import wraps

from tornado.concurrent import Future as tornado_Future
from tornado.gen import Return

from katcp import resource, inspecting_client, Message
from katcp.resource import KATCPReply, KATCPSensorError
from katcp.core import (AttrDict, AsyncCallbackEvent, steal_docstring_from,
                        AsyncState)

log = logging.getLogger(__name__)


def log_coroutine_exceptions(coro):
    """Coroutine (or any function that returns a future) decorator to log exceptions

    Using the module logger

    Example
    -------

    ::

    @log_coroutine_exceptions
    @tornado.gen.coroutine
    def raiser(self, arg):
        yield tornado.gen.moment
        raise Exception(arg)

    """
    def log_cb(f):
        try:
            f.result()
        except Exception:
            log.exception('Unhandled exception calling coroutine {0!r}'
                               .format(coro))

    @wraps(coro)
    def wrapped_coro(*args, **kwargs):
        f = coro(*args, **kwargs)
        f.add_done_callback(log_cb)
        return f

    return wrapped_coro

def log_future_exceptions(f):
    def log_cb(f):
        try:
            f.result()
        except Exception:
            log.exception('Unhandled exception returned by future')
    f.add_done_callback(log_cb)

def transform_future(transformation, future):
    """Returns a new future that will resolve with a transformed value

    Takes the resolution value of `future` and applies transformation(*future.result())
    to it before setting the result of the new future with the transformed value. If
    future() resolves with an exception, it is passed through to the new future.

    Assumes `future` is a tornado Future.

    """
    new_future = tornado_Future()
    def _transform(f):
        assert f is future
        if f.exc_info() is not None:
            new_futures.set_exc_info(f.exc_info())
        else:
            try:
                new_future.set_result(transformation(f.result()))
            except Exception:
                # An exception here idicates that the transformation was unsuccesful
                new_future.set_exc_info(sys.exc_info())

    future.add_done_callback(_transform)
    return new_future

class ReplyWrappedInspectingClientAsync(inspecting_client.InspectingClientAsync):
    """Adds wrapped_request() method that wraps reply in a KATCPReply """

    reply_wrapper = staticmethod(lambda x : KATCPReply(*x))

    def wrapped_request(self, request, *args, **kwargs):
        """Create and send a request to the server.

        This method implements a very small subset of the options
        possible to send an request. It is provided as a shortcut to
        sending a simple wrapped request.

        Parameters
        ----------
        request : str
            The request to call.
        *args : list of objects
            Arguments to pass on to the request.

        Keyword Arguments
        -----------------
        timeout : float or None, optional
            Timeout after this amount of seconds (keyword argument).
        mid : None or int, optional
            Message identifier to use for the request message. If None, use either
            auto-incrementing value or no mid depending on the KATCP protocol version
            (mid's were only introduced with KATCP v5) and the value of the `use_mid`
            argument. Defaults to None.
        use_mid : bool
            Use a mid for the request if True.

        Returns
        -------
        future object that resolves with the
        :meth:`katcp.client.DeviceClient.future_request` response wrapped in
        self.reply_wrapper

        Example
        -------

        ::

        wrapped_reply = yield ic.simple_request('help', 'sensor-list')

        """
        f = tornado_Future()
        try:
            use_mid = kwargs.get('use_mid')
            timeout = kwargs.get('timeout')
            msg = Message.request(request, *args)
        except Exception:
            f.set_exc_info(sys.exc_info())
            return f
        return transform_future(self.reply_wrapper,
                                self.katcp_client.future_request(msg, timeout, use_mid))

class KATCPResourceClient(resource.KATCPResource):
    """Class managing a client connection to a single KATCP resource

    Inspects the KATCP interface of the resources, exposing sensors and requests as per
    the :class:`katcp.resource.KATCPResource` API. Can also operate without exposin
    """

    @property
    def state(self):
        return self._state.state

    @property
    def req(self):
        return self._req

    @property
    def sensor(self):
        return self._sensor

    @property
    def address(self):
        return self._address

    @property
    def name(self):
        return self._name

    @property
    def parent(self):
        return self._parent

    @property
    def children(self):
        return {}

    def __init__(self, resource_spec, parent=None,logger=log):
        """Initialise resource with given specification

        Parameters
        ----------
        resource_spec : dict with resource specifications. Keys:
          name : str
              Name of the resource
          address : (host, port), host as str, port as int
          always_allowed_requests : seq of str,
              KACTP requests that are always allowed, even when the resource is not
              controlled.
          always_excluded_requests : seq of str,
              KACTP requests that are never allowed, even if the resource is
              controlled. Overrides requests in `always_allowed_requests`.
          controlled : bool, default: False
              True if control over the device (i.e. KATCP requests) is to be exposed.
          auto_reconnect : bool
              If True, auto-reconnect should the network connection be closed.
          auto_reconnect_delay : float seconds. Default : 0.5s
              Delay between reconnection retries.
          # TODO(NM) 'keep', ie. katcorelib behaviour where requests / sensors never
          # disappear even if the device looses them. Or was it only sensors? Should look
          # at katcorelib

          # TODO, not implemented, proposed below for light non-inspecting mode

          inspect : bool, default : True
              Inspect the resource's KATCP interface for sensors and requests
          assumed_requests : ...
          assumed_sensors : ...

        parent : :class:`KATCPResource` or None
            Parent KATCPResource object if this client is a child in a resource
            hierarcy
        """
        self._address = resource_spec['address']
        self._name = resource_spec['name']
        self.always_allowed_requests = resource_spec.get(
            'always_allowed_requests', set())
        self.always_excluded_requests = resource_spec.get(
            'always_excluded_requests', set())
        self.controlled = resource_spec.get('controlled', False)
        self.auto_reconnect = resource_spec.get('auto_reconnect', True)
        self.auto_reconnect_delay = resource_spec.get('auto_reconnect_delay', 0.5)
        self._parent = parent
        self._ioloop_set_to = None
        self._sensor = AttrDict()
        self._req = AttrDict()
        # Save the pop() / items() methods in case a sensor/request with the same name is
        # added
        self._sensor_pop = self.sensor.pop
        self._sensor_items = self.sensor.items
        self._req_pop = self.req.pop
        self._req_items = self.req.items
        self._state = AsyncState(("disconnected", "syncing", "synced"))
        self._connected = AsyncCallbackEvent(self._update_state)
        self._sensors_synced = AsyncCallbackEvent(self._update_state)
        self._requests_synced = AsyncCallbackEvent(self._update_state)

    def until_state(self, state):
        """Future that resolves when a certain client state is attained

        Parameters
        ----------

        state : str
            Desired state, one of ("disconnected", "syncing", "synced")
        """
        return self._state.until_state(state)

    def _update_state(self, _flag=None):
        # Update self._state, optional _flag parameter is ignored to be compatible with
        # AsyncCallbackEvent
        if not self._connected.isSet():
            self._state.set('disconnected')
        else:
            if self._sensors_synced.isSet() and self._requests_synced.isSet():
                self._state.set('synced')
            else:
                self._state.set('syncing')

    def set_ioloop(self, ioloop=None):
        """Set the tornado ioloop to use

        Defaults to tornado.ioloop.IOLoop.current() if set_ioloop() is not called or if
        ioloop=None. Must be called before start()
        """
        self._ioloop_set_to = ioloop

    def start(self):
        """Start the client and connect"""
        host, port = self.address
        ic = self._inspecting_client = ReplyWrappedInspectingClientAsync(
            host, port, ioloop=self._ioloop_set_to, auto_reconnect=self.auto_reconnect)
        self.ioloop = ic.ioloop
        ic.katcp_client.auto_reconnect_delay = self.auto_reconnect_delay
        # TODO Commented out since it is not compatable with inspecting_client state-loop
        # changes
        # ic.set_request_added_callback(self._requests_added_callback)
        # ic.set_request_removed_callback(self._requests_removed_callback)
        # ic.set_sensor_added_callback(self._sensors_added_callback)
        # ic.set_sensor_removed_callback(self._sensors_removed_callback)
        # ic.set_connection_status_change_callback(self._connection_status_callback)
        ic.request_factory = self._request_factory
        self._sensor_manager = KATCPResourceClientSensorsManager(ic)
        ic.handle_sensor_value()
        ic.sensor_factory = self._sensor_manager.sensor_factory

        # Steal some methods from _sensor_manager
        self.reapply_sampling_strategies = self._sensor_manager.reapply_sampling_strategies
        log_future_exceptions(ic.connect())

    def until_state(self, state):
        """Return a tornado Future that will resolve when the requested state is set

        State can be one of ("disconnected", "syncing", "synced")
        """
        return self._state.until_state(state)

    @steal_docstring_from(resource.KATCPResource.list_sensors)
    def list_sensors(self, filter="", strategy=False, status="",
                     use_python_identifiers=True):
        filter_re = re.compile(filter)
        found_sensors = []
        for sensor_attr, sensor_obj in self._sensor_items():
            search_name = (sensor_attr if use_python_identifiers
                           else sensor_obj.name)
            if filter_re.search(search_name):
                found_sensors.append(resource.SensorResultTuple(
                    object=sensor_obj,
                    name=sensor_obj.name,
                    python_identifier=sensor_attr,
                    description=sensor_obj.description,
                    units=sensor_obj.units,
                    type=sensor_obj.type,
                    reading=sensor_obj.reading))
        return found_sensors


    # TODO Design for state transitions. Since the sensors and requests updates will
    # happen one after the other, it may result in in states blipping if sensors and then
    # requests are changed at the same time. perhaps immediately clear the 'synced' flags
    # as soon as a callbackalways defer work that changes sensor or requests sync states
    # to 'true' to coroutines / callbacks that always yield to the ioloop before setting
    # the sync state to true. Should allow sync-clearing to always happen immediately (so
    # the 'and' condition for full-sync will be false) until both sensors and requests
    # have been updated without blips.

    def _request_factory(self, name, description):
        return KATCPResourceClientRequest(name, description, self._inspecting_client)

    def _requests_added_callback(self, request_keys):
        self._requests_synced.clear()
        # Instantiate KATCPRequest instances and store on self.req
        self._add_requests({key: self._inspecting_client.future_get_request(key)
                            for key in request_keys})

    @tornado.gen.coroutine
    def _requests_removed_callback(self, request_keys):
        self._requests_synced.clear()
        # Remove KATCPRequest instances from self.req
        for r_name in request_keys:
            r_name_escaped = resource.escape_name(r_name)
            self._request_pop(r_name_escaped)
        # TODO Uh-oh, I think we are going to get all kinds of blipping here. Perhaps have
        # something like a "callbacks outstanding" on the inspecting client, or else
        # investigate the IC code to see if this won't be a problem
        self.ioloop.add_callback(self._requests_synced.set)

    def _sensors_added_callback(self, sensor_keys):
        self._sensors_synced.clear()
        # Get KATCPSensor instance futures from inspecting client
        self._add_sensors({key: self._inspecting_client.future_get_sensor(key)
                           for key in sensor_keys})

    @log_coroutine_exceptions
    @tornado.gen.coroutine
    def _add_sensors(self, sensor_futures):
        # Store KATCPSensor instances in self.sensor
        sensor_instances = yield sensor_futures
        for s_name, s_obj in sensor_instances.items():
            s_name_escaped = resource.escape_name(s_name)
            self._sensor[s_name_escaped] = s_obj

        # TODO Notify parent that sensors were added? Or have a dynamic parent sensor
        # object that inspects all children?

        # Reapply sensor strategies if they were seen before
        yield self._sensor_manager.reapply_sampling_strategies()
        self._sensors_synced.set()

    @log_coroutine_exceptions
    @tornado.gen.coroutine
    def _add_requests(self, request_futures):
        request_instances = yield request_futures
        for r_name, r_obj in request_instances.items():
            r_name_escaped = resource.escape_name(r_name)
            self._req[r_name_escaped] = r_obj
        self._requests_synced.set()

    def _sensors_removed_callback(self, sensor_keys):
        # Remove KATCPSensor instances from self.sensor
        # TODO Call update state thing for overall state
        for s_name in sensor_keys:
            s_name_escaped = resource.escape_name(s_name)
            self._sensor_pop(s_name_escaped)
        # TODO Notify parent that sensors were removed? Or have a dynamic parent sensor
        # object that inspects all children? Or perhaps just watch chiled synced state

    @log_coroutine_exceptions
    @tornado.gen.coroutine
    def _connection_status_callback(self, connected):
        log.info('connected: {0}'.format(connected))
        self._connected_state.set_state(connected)
        if connected:
            yield self._sensor_manager.reapply_sampling_strategies()
        else:
            self._requests_synced.clear()
            self._sensor_synced.clear()

    def stop(self):
        self._inspecting_client.stop()

resource.KATCPResource.register(KATCPResourceClient)

class KATCPResourceClientSensorsManager(object):
    """Implementation of KATSensorsManager ABC for a directly-connected client

    Assumes that all methods are called from the same ioloop context
    """

    def __init__(self, inspecting_client):
        self._inspecting_client = inspecting_client
        self.time = inspecting_client.ioloop.time
        self._strategy_cache = {}

    def sensor_factory(self, **sensor_description):
        # kwargs as for inspecting_client.InspectingClientAsync.sensor_factory
        sens = resource.KATCPSensor(sensor_description, self)
        sensor_name = sensor_description['name']
        cached_strategy = self._strategy_cache.get(sensor_name)
        if cached_strategy:
            log_future_exceptions(self.set_sampling_strategy(
                sensor_name, cached_strategy))
        return sens

    def get_sampling_strategy(self, sensor_name):
        """Get the current sampling strategy for the named sensor

        Parameters
        ----------

        sensor_name : str
            Name of the sensor

        Returns
        -------

        strategy : tuple of str
            contains (<strat_name>, [<strat_parm1>, ...]) where the strategy names and
            parameters are as defined by the KATCP spec
        """
        cached = self._strategy_cache.get(sensor_name)
        if not cached:
            return resource.normalize_strategy_parameters('none')
        else:
            return cached

    @tornado.gen.coroutine
    def set_sampling_strategy(self, sensor_name, strategy_and_params):
        """Set the sampling strategy for the named sensor

        Parameters
        ----------

        sensor_name : str
            Name of the sensor
        strategy_and_params : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.

        Returns
        -------
        done : tornado Future that resolves when done or raises KATCPSensorError

        """

        strategy_and_params = resource.normalize_strategy_parameters(strategy_and_params)
        self._strategy_cache[sensor_name] = strategy_and_params
        reply = yield self._inspecting_client.wrapped_request(
            'sensor-sampling', sensor_name, *strategy_and_params)
        if not reply.succeeded:
            raise KATCPSensorError('Error setting strategy for sensor {0}: \n'
                                   '{1!s}'.format(sensor_name, reply))

    @tornado.gen.coroutine
    def reapply_sampling_strategies(self):
        """Reapply all sensor strategies using cached values"""
        check_sensor = self._inspecting_client.future_check_sensor
        for sensor_name, strategy in self._strategy_cache.items():
            try:
                sensor_exists = yield check_sensor(sensor_name)
                if not sensor_exists:
                    log.warn('Did not set strategy for no-longer-existant sensor {}'
                             .format(sensor_name))
                    continue
                result = yield self.set_sampling_strategy(sensor_name, strategy)
            except KATCPSensorError, e:
                log.error('Error reapplying strategy for sensor {0}: {1!s}'
                          .format(sensor_name, e))
            except Exception:
                log.exception('Unhandled exception reapplying strategy for '
                              'sensor {}'.format(sensor_name), exc_info=True)

    @tornado.gen.coroutine
    @steal_docstring_from(resource.KATCPSensorsManager.poll_sensor)
    def poll_sensor(self, sensor_name):
        reply = yield self._inspecting_client.wrapped_request(
            'sensor-value', sensor_name)
        if not reply.succeeded:
            raise KATCPSensorError('Error polling sensor {0}: \n'
                                   '{1!s}'.format(sensor_name, reply))
# Register with the ABC
resource.KATCPSensorsManager.register(KATCPResourceClientSensorsManager)

class KATCPResourceClientRequest(resource.KATCPRequest):

    @property
    @steal_docstring_from(resource.KATCPRequest.name)
    def name(self):
        return self._name

    @property
    @steal_docstring_from(resource.KATCPRequest.description)
    def description(self):
        return self._description

    def __init__(self, name, description, client):
        """Initialize request with given description and network client

        Parameters
        ----------
        name : str
            KATCP name of the request
        description : str
            KATCP request description (as returned by ?help <name>)
        client : client obj
            KATCP client connected to the KATCP resource that exposes a wrapped_request()
            method like :meth:`ReplyWrappedInspectingClientAsync.wrapped_request`.

        """
        self._name = name
        self._description = description
        self._client = client

    def __call__(self, *args, **kwargs):
        return self._client.wrapped_request(self.name, *args, **kwargs)

