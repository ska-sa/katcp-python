# Copyright 2014 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

import logging
import sys
import re
import collections
import textwrap
import time

import tornado

from functools import wraps, partial
from thread import get_ident as get_thread_ident

from concurrent.futures import Future, TimeoutError
from tornado.concurrent import Future as tornado_Future
from tornado.gen import Return, maybe_future, chain_future, with_timeout
from peak.util.proxies import ObjectWrapper

from katcp import resource, inspecting_client, Message
from katcp.resource import KATCPReply, KATCPSensorError
from katcp.core import (AttrDict, AsyncCallbackEvent, steal_docstring_from,
                        AsyncState, AsyncEvent, until_any, log_future_exceptions)

log = logging.getLogger(__name__)

def _normalise_request_name_set(reqs):
    return set(resource.escape_name(r) for r in reqs)

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
            new_future.set_exc_info(f.exc_info())
        else:
            try:
                new_future.set_result(transformation(f.result()))
            except Exception:
                # An exception here idicates that the transformation was unsuccesful
                new_future.set_exc_info(sys.exc_info())

    future.add_done_callback(_transform)
    return new_future

@tornado.gen.coroutine
def list_sensors(parent_class, sensor_items, filter, strategy, status, use_python_identifiers, tuple, refresh):
    """Helper for implementing :meth:`katcp.resource.KATCPResource.list_sensors`

    Parameters
    ----------

    sensor_items : tuple of sensor-item tuples
        As would be returned the items() method of a dict containing KATCPSensor objects
        keyed by Python-identifiers.
    parent_class: KATCPClientResource or KATCPClientResourceContainer
        Is used for prefix calculation
    Rest of parameters as for :meth:`katcp.resource.KATCPResource.list_sensors`
    """
    filter_re = re.compile(filter)
    found_sensors = []
    none_strat = resource.normalize_strategy_parameters('none')
    sensor_dict = dict(sensor_items)
    for sensor_identifier in sorted(sensor_dict.keys()):
        sensor_obj = sensor_dict[sensor_identifier]
        search_name = (sensor_identifier if use_python_identifiers
                       else sensor_obj.name)
        name_match = filter_re.search(search_name)
        # Only include sensors with strategies
        strat_match = not strategy or sensor_obj.sampling_strategy != none_strat
        if name_match and strat_match:
            if refresh:
                # First refresh the sensor reading
                yield sensor_obj.get_value()
            # Determine the sensorname prefix:
            # parent_name. except for aggs when in KATCPClientResourceContinaer
            prefix = ""
            if isinstance(parent_class, KATCPClientResourceContainer):
                if sensor_obj.name.startswith("agg_"):
                    prefix = ""
                else:
                    prefix = sensor_obj.parent_name + "."
            if not status or (sensor_obj.reading.status in status):
                # Only include sensors of the given status
                if tuple:
                    # (sensor.name, sensor.value, sensor.value_seconds, sensor.type, sensor.units, sensor.update_seconds, sensor.status, strategy_and_params)
                    found_sensors.append((
                        prefix+sensor_obj.name,
                        sensor_obj.reading.value,
                        sensor_obj.reading.timestamp,
                        sensor_obj.type,
                        sensor_obj.units,
                        sensor_obj.reading.received_timestamp,
                        sensor_obj.reading.status,
                        sensor_obj.sampling_strategy
                        ))
                else:
                    found_sensors.append(resource.SensorResultTuple(
                        object=sensor_obj,
                        name=prefix+sensor_obj.name,
                        python_identifier=sensor_identifier,
                        description=sensor_obj.description,
                        units=sensor_obj.units,
                        type=sensor_obj.type,
                        reading=sensor_obj.reading))
    raise tornado.gen.Return(found_sensors)


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
            mid = kwargs.get('mid')
            msg = Message.request(request, *args, mid=mid)
        except Exception:
            f.set_exc_info(sys.exc_info())
            return f
        return transform_future(self.reply_wrapper,
                                self.katcp_client.future_request(msg, timeout, use_mid))

class KATCPClientResource(resource.KATCPResource):
    """Class managing a client connection to a single KATCP resource

    Inspects the KATCP interface of the resources, exposing sensors and requests as per
    the :class:`katcp.resource.KATCPResource` API. Can also operate without exposin
    """

    @property
    def state(self):
        return self._state.state

    @property
    def controlled(self):
        return self._controlled

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
    def host(self):
        return self._address[0]

    @property
    def port(self):
        return self._address[1]

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._description

    @property
    def parent(self):
        return self._parent

    @property
    def parent_name(self):
        return self._parent.name

    @property
    def children(self):
        return {}

    @property
    def synced(self):
        return self.state == 'synced'

    def __init__(self, resource_spec, parent=None, logger=log):
        """Initialise resource with given specification

        Parameters
        ----------
        resource_spec : dict with resource specifications. Keys:
          name : str
              Name of the resource.
          description : str, optional
              Description of the resource.
          address : (host, port), host as str, port as int
          always_allowed_requests : seq of str,
              KACTP requests that are always allowed, even when the resource is not
              controlled. '-' and '_' will be treated equivalently.
          always_excluded_requests : seq of str,
              KACTP requests that are never allowed, even if the resource is
              controlled. Overrides requests in `always_allowed_requests`. '-' and '_'
              will be treated equivalently.
          controlled : bool, default: False
              True if control over the device (i.e. KATCP requests) is to be exposed.
          auto_reconnect : bool, default: True
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

        logger : object, optional
           Python Logger object to log to. Default is the module logger

        """
        super(KATCPClientResource, self).__init__()
        self._address = resource_spec['address']
        self._name = resource_spec['name']
        self._description = resource_spec.get('description', '')
        self.always_allowed_requests = _normalise_request_name_set(
            resource_spec.get('always_allowed_requests', set()) )
        self.always_excluded_requests = _normalise_request_name_set(
            resource_spec.get('always_excluded_requests', set()) )
        self._controlled = resource_spec.get('controlled', False)
        self.auto_reconnect = resource_spec.get('auto_reconnect', True)
        self.auto_reconnect_delay = resource_spec.get('auto_reconnect_delay', 0.5)
        self._sensor_strategy_cache = {}
        self._sensor_listener_cache = collections.defaultdict(list)
        self._logger = logger
        self._parent = parent
        self._ioloop_set_to = None
        self._sensor = AttrDict()
        self._req = AttrDict()
        # Save the pop() / items() methods in case a sensor/request with the same name is
        # added
        self._state = AsyncState(("disconnected", "syncing", "synced"))
        self._connected = AsyncCallbackEvent(self._update_state)
        self._sensors_synced = AsyncCallbackEvent(self._update_state)
        self._requests_synced = AsyncCallbackEvent(self._update_state)

    def is_connected(self):
        """Indication of the connection state

        Returns True if state is not "disconnected", i.e "syncing" or "synced"
        """
        return not self.state == 'disconnected'

    def until_state(self, state, timeout=None):
        """Future that resolves when a certain client state is attained

        Parameters
        ----------

        state : str
            Desired state, one of ("disconnected", "syncing", "synced")
        timeout: float
            Timeout for operation in seconds.
        """
        return self._state.until_state(state, timeout=timeout)

    def wait_connected(self, timeout=None):
        """Future that resolves when the state is not 'disconnected'."""
        return self._state.until_state_in('syncing', 'synced', timeout=timeout)

    def _update_state(self, _flag=None):
        # Update self._state, optional _flag parameter is ignored to be compatible with
        # AsyncCallbackEvent
        if not self._connected.isSet():
            self._state.set_state('disconnected')
        else:
            if self._sensors_synced.isSet() and self._requests_synced.isSet():
                self._state.set_state('synced')
            else:
                self._state.set_state('syncing')

    def set_ioloop(self, ioloop=None):
        """Set the tornado ioloop to use

        Defaults to tornado.ioloop.IOLoop.current() if set_ioloop() is not called or if
        ioloop=None. Must be called before start()
        """
        self._ioloop_set_to = ioloop

    def start(self):
        """Start the client and connect"""
        # TODO (NM 2015-03-12) Some checking to prevent multiple calls to start()
        host, port = self.address
        ic = self._inspecting_client = self.inspecting_client_factory(
            host, port, self._ioloop_set_to)
        self.ioloop = ic.ioloop
        ic.katcp_client.auto_reconnect_delay = self.auto_reconnect_delay
        ic.set_state_callback(self._inspecting_client_state_callback)
        ic.request_factory = self._request_factory
        self._sensor_manager = KATCPClientResourceSensorsManager(ic, self.name, logger=self._logger)
        ic.handle_sensor_value()
        ic.sensor_factory = self._sensor_manager.sensor_factory

        # Steal some methods from _sensor_manager
        self.reapply_sampling_strategies = self._sensor_manager.reapply_sampling_strategies
        log_future_exceptions(self._logger, ic.connect())

    def inspecting_client_factory(self, host, port, ioloop_set_to):
        """Return an instance of :class:`ReplyWrappedInspectingClientAsync` or similar

        Provided to ease testing. Dynamically overriding this method after instantiation
        but before start() is called allows for deep brain surgery. See
        :class:`katcp.fake_clients.fake_inspecting_client_factory`

        """
        return ReplyWrappedInspectingClientAsync(
            host, port, ioloop=ioloop_set_to, auto_reconnect=self.auto_reconnect)

    def until_synced(self, timeout=None):
        """Convenience method to wait (with Future) until client is synced"""
        return self._state.until_state('synced', timeout=timeout)

    def until_not_synced(self, timeout=None):
        """Convenience method to wait (with Future) until client is not synced"""
        not_synced_states = [state for state in self._state.valid_states
                             if state != 'synced']
        not_synced_futures = [self._state.until_state(state)
                              for state in not_synced_states]
        return until_any(*not_synced_futures, timeout=timeout)

    @steal_docstring_from(resource.KATCPResource.list_sensors)
    def list_sensors(self, filter="", strategy=False, status="",
                     use_python_identifiers=True, tuple=False, refresh=False):
        return list_sensors(self,
            dict.items(self.sensor), filter, strategy, status, use_python_identifiers, tuple, refresh)

    @tornado.gen.coroutine
    def set_sampling_strategies(self, filter, strategy_and_parms):
        """Set a strategy for all sensors matching the filter even if it is not yet known.
        The strategy should persist across sensor disconnect/reconnect.

        filter : str
            Filter for sensor names
        strategy_and_params : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.

        Returns
        -------
        done : tornado Future
            Resolves when done
        """
        sensor_list = yield self.list_sensors(filter=filter)
        sensor_dict = {}
        for sens in sensor_list:
            # Set the strategy on each sensor
            try:
                sensor_name = sens.object.normalised_name
                yield self.set_sampling_strategy(sensor_name, strategy_and_parms)
                sensor_dict[sensor_name] = strategy_and_parms
            except Exception as exc:
                self._logger.exception(
                    'Unhandled exception trying to set sensor strategies {!r} for {} ({})'
                    .format(strategy_and_parms, sens, exc))
                sensor_dict[sensor_name] = None
        # Otherwise, depend on self._add_sensors() to handle it from the cache when the sensor appears\
        raise tornado.gen.Return(sensor_dict)

    @tornado.gen.coroutine
    def set_sampling_strategy(self, sensor_name, strategy_and_parms):
        """Set a strategy for a sensor even if it is not yet known.
        The strategy should persist across sensor disconnect/reconnect.

        sensor_name : str
            Name of the sensor
        strategy_and_params : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.

        Returns
        -------
        done : tornado Future
            Resolves when done
        """
        sensor_name = resource.escape_name(sensor_name)
        sensor_obj = dict.get(self._sensor, sensor_name)
        self._sensor_strategy_cache[sensor_name] = strategy_and_parms
        sensor_dict = {}
        self._logger.debug(
                'Cached strategy {} for sensor {}'
                .format(strategy_and_parms, sensor_name))
        if sensor_obj:
            # The sensor exists, so set the strategy and continue. Log errors,
            # but don't raise anything
            try:
                yield sensor_obj.set_sampling_strategy(strategy_and_parms)
                sensor_dict[sensor_name] = strategy_and_parms
            except Exception as exc:
                self._logger.exception(
                    'Unhandled exception trying to set sensor strategy {!r} for sensor {} ({})'
                    .format(strategy_and_parms, sensor_name, exc))
                sensor_dict[sensor_name] = str(exc)
        # Otherwise, depend on self._add_sensors() to handle it from the cache when the sensor appears
        raise tornado.gen.Return(sensor_dict)

    @tornado.gen.coroutine
    def set_sensor_listener(self, sensor_name, listener):
        """Set a sensor listener for a sensor even if it is not yet known
        The listener registration should persist across sensor disconnect/reconnect.

        sensor_name : str
            Name of the sensor
        listener : callable
            Listening callable that will be registered on the named sensor when it becomes
            available. Callable as for :meth:`KATCPSensor.register_listener`

        """

        sensor_name = resource.escape_name(sensor_name)
        sensor_obj = dict.get(self._sensor, sensor_name)
        self._sensor_listener_cache[sensor_name].append(listener)
        sensor_dict = {}
        self._logger.debug(
                'Cached listener {} for sensor {}'
                .format(listener, sensor_name))
        if sensor_obj:
            # The sensor exists, so register the listener and continue.
            try:
                sensor_obj.register_listener(listener, reading=True)
                sensor_dict[sensor_name] = listener
                self._logger.debug(
                    'Registered listener {} for sensor {}'
                    .format(listener, sensor_name))
            except Exception as exc:
                self._logger.exception(
                    'Unhandled exception trying to set sensor listener {} for sensor {} ({})'
                    .format(listener, sensor_name, exc))
                sensor_dict[sensor_name] = str(exc)
        # Otherwise, depend on self._add_sensors() to handle it from the cache when the sensor appears
        raise tornado.gen.Return(sensor_dict)

    def _request_factory(self, name, description):
        return KATCPClientResourceRequest(
            name, description, self._inspecting_client, self.is_active)

    @tornado.gen.coroutine
    def _inspecting_client_state_callback(self, state, model_changes):
        log.debug('Received {0}, {1}'.format(state, model_changes))
        if state.connected:
            if not state.synced:
                log.debug('Setting state to "syncing"')
                self._state.set_state('syncing')
                if model_changes:
                    log.debug('handling model updates')
                    yield self._update_model(model_changes)
                    log.debug('finished handling model updates')
                if state.data_synced:
                    # Reapply cached sensor strategies. Can only be done if
                    # data_synced==True, or else the
                    # self._inspecting_client.future_check_sensor() will deadlock
                    log.debug('Reapplying sampling strategies')
                    yield self._sensor_manager.reapply_sampling_strategies()
                    log.debug('Done Reapplying sampling strategies')
            else:
                log.debug('Setting state to "synced"')
                self._state.set_state('synced')
        else:
            log.debug('Setting state to "disconnected"')
            self._state.set_state('disconnected')

        log.debug('Done with _inspecting_client_state_callback')

    @tornado.gen.coroutine
    def _update_model(self, model_changes):
        if 'requests' in model_changes:
            log.debug('Removing requests')
            yield self._remove_requests(model_changes.requests.removed)
            log.debug('Adding requests')
            yield self._add_requests(model_changes.requests.added)
            log.debug('Done with requests')
        if 'sensors' in model_changes:
            log.debug('Removing sensors')
            yield self._remove_sensors(model_changes.sensors.removed)
            log.debug('Adding sensors')
            yield self._add_sensors(model_changes.sensors.added)
            log.debug('Done with sensors')
        log.debug('Done with model')


    @tornado.gen.coroutine
    def _add_requests(self, request_keys):
        # Instantiate KATCPRequest instances and store on self.req
        request_instances = yield {key: self._inspecting_client.future_get_request(key)
                                   for key in request_keys}
        added_names = []
        for r_name, r_obj in request_instances.items():
            r_name_escaped = resource.escape_name(r_name)
            if r_name_escaped in self.always_excluded_requests:
                continue
            if self.controlled or r_name_escaped in self.always_allowed_requests:
                self._req[r_name_escaped] = r_obj
                added_names.append(r_name_escaped)

        if self.parent and added_names:
            self.parent._child_add_requests(self, added_names)

    @tornado.gen.coroutine
    def _remove_requests(self, request_keys):
        # Remove KATCPRequest instances from self.req
        removed_names = []
        for r_name in request_keys:
            r_name_escaped = resource.escape_name(r_name)
            # Must not raise exception when popping a non-existing request, since it may
            # never have been added due to request exclusion rules.
            if dict.pop(self.req, r_name_escaped, None):
                removed_names.append(r_name_escaped)

        if self.parent and removed_names:
            self.parent._child_remove_requests(self, removed_names)

    @tornado.gen.coroutine
    def _add_sensors(self, sensor_keys):
        # Get KATCPSensor instance futures from inspecting client
        sensor_instances = yield {key: self._inspecting_client.future_get_sensor(key)
                                  for key in sensor_keys}
        # Store KATCPSensor instances in self.sensor
        added_names = []
        for s_name, s_obj in sensor_instances.items():
            s_name_escaped = resource.escape_name(s_name)
            self._sensor[s_name_escaped] = s_obj
            preset_strategy = self._sensor_strategy_cache.get(s_name_escaped)
            if preset_strategy:
                self._logger.debug('Setting preset strategy for sensor {} to {!r}'
                                   .format(s_name, preset_strategy))
                try:
                    yield s_obj.set_sampling_strategy(preset_strategy)
                except Exception:
                    self._logger.exception(
                        'Exception trying to pre-set sensor strategy for sensor {}'
                        .format(s_name))
            preset_listeners = self._sensor_listener_cache.get(s_name_escaped)
            if preset_listeners:
                try:
                    for listener in preset_listeners:
                        s_obj.register_listener(listener, reading=True)
                except Exception:
                    self._logger.exception(
                        'Exception trying to pre-set sensor listeners for sensor {}'
                        .format(s_name))

            added_names.append(s_name_escaped)

        if self.parent:
            self.parent._child_add_sensors(self, added_names)

    @tornado.gen.coroutine
    def _remove_sensors(self, sensor_keys):
        # Remove KATCPSensor instances from self.sensor
        removed_names = []
        for s_name in sensor_keys:
            s_name_escaped = resource.escape_name(s_name)
            dict.pop(self.sensor, s_name_escaped)
            removed_names.append(s_name_escaped)

        if self.parent:
            self.parent._child_remove_sensors(self, removed_names)

    def stop(self):
        self._inspecting_client.stop()

    def __repr__(self):
        return '<{module}.{classname}(name={name}) at 0x{id:x}>'.format(
            module=self.__class__.__module__,
            classname=self.__class__.__name__,
            name=self.name, id=id(self))

class KATCPClientResourceSensorsManager(object):
    """Implementation of KATSensorsManager ABC for a directly-connected client

    Assumes that all methods are called from the same ioloop context
    """

    def __init__(self, inspecting_client, resource_name, logger=log):
        self._inspecting_client = inspecting_client
        self.time = inspecting_client.ioloop.time
        self._strategy_cache = {}
        self._resource_name = resource_name
        self._logger = logger

    @property
    def resource_name(self):
        return self._resource_name

    def sensor_factory(self, **sensor_description):
        # kwargs as for inspecting_client.InspectingClientAsync.sensor_factory
        sens = resource.KATCPSensor(sensor_description, self)
        sensor_name = sensor_description['name']
        cached_strategy = self._strategy_cache.get(sensor_name)
        if cached_strategy:
            log_future_exceptions(self._logger, self.set_sampling_strategy(
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
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the
            strategy names and parameters are as defined by the KATCP spec. As
            str contains the same elements in space-separated form.

        Returns
        -------
        sensor_strategy : tuple
            (success, info) with

            success : bool
                True if setting succeeded for this sensor, else False
            info : tuple
               Normalibed sensor strategy and parameters as tuple if
               success == True else, sys.exc_info() tuple for the error
               that occured.
        """
        try:
            strategy_and_params = resource.normalize_strategy_parameters(
                strategy_and_params)
            self._strategy_cache[sensor_name] = strategy_and_params
            reply = yield self._inspecting_client.wrapped_request(
                'sensor-sampling', sensor_name, *strategy_and_params)
            if not reply.succeeded:
                raise KATCPSensorError('Error setting strategy for sensor {0}: \n'
                                       '{1!s}'.format(sensor_name, reply))
            sensor_strategy = (True, strategy_and_params)
        except Exception as e:
            self._logger.exception('Exception found!')
            sensor_strategy = (False, str(e))
        raise tornado.gen.Return(sensor_strategy)

    @tornado.gen.coroutine
    def reapply_sampling_strategies(self):
        """Reapply all sensor strategies using cached values"""
        check_sensor = self._inspecting_client.future_check_sensor
        for sensor_name, strategy in self._strategy_cache.items():
            try:
                sensor_exists = yield check_sensor(sensor_name)
                if not sensor_exists:
                    self._logger.warn('Did not set strategy for non-existing sensor {}'
                             .format(sensor_name))
                    continue

                result = yield self.set_sampling_strategy(sensor_name, strategy)
            except KATCPSensorError, e:
                self._logger.error('Error reapplying strategy for sensor {0}: {1!s}'
                                   .format(sensor_name, e))
            except Exception:
                self._logger.exception('Unhandled exception reapplying strategy for '
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
resource.KATCPSensorsManager.register(KATCPClientResourceSensorsManager)

class KATCPClientResourceRequest(resource.KATCPRequest):
    """Callable wrapper around a KATCP request

    """
    def __init__(self, name, description, client, is_active=lambda : True):
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
        is_active : callable, optional
            Returns True if this request is active, else False

        """
        self._client = client
        super(KATCPClientResourceRequest, self).__init__(name, description, is_active)

    def issue_request(self, *args, **kwargs):
        """Issue the wrapped request to the server.

        Parameters
        ----------
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
        future object that resolves with an :class:`katcp.resource.KATCPReply`
        instance

        """
        return self._client.wrapped_request(self.name, *args, **kwargs)

class GroupRequest(object):
    """Couroutine wrapper around a specific KATCP request for a group of clients.

    Each available KATCP request supported by group has an associated
    :class:`GroupRequest` object in the hierarchy. This wrapper is mainly for
    interactive convenience. It provides the KATCP request help string as a
    docstring accessible via IPython's question mark operator.

    Call Parameters
    ---------------

    Call parameters are all forwarded to the :class:`KATCPRequest` instance of each
    client in the group.

    Return Value
    ------------
    Returns a tornado future that resolves with a :class:`GroupResults` instance that
    contains the replies of each client. If a particular client does not have the request,
    its result is None.

    """
    def __init__(self, group, name, description):
        """Initialise the GroupRequest

        Parameters
        ----------
        group : :class:`ClientGroup` object
            Client group to which requests will be sent
        name : string
            Name of the KATCP request
        description : string
            Help string associated with this KATCP request

        """

        self.group = group
        self.name = name
        self.description = description
        self.__doc__ = '\n'.join(('KATCP Documentation',
                                  '===================',
                                  description,
                                  'GroupRequest Documentation',
                                  '==========================',
                                  self.__doc__ or ''))

    @tornado.gen.coroutine
    def __call__(self, *args, **kwargs):
        result_futures = {}
        none_future = Future()
        none_future.set_result(None)
        for client in self.group.clients:
            request_method = getattr(client.req, self.name, None)
            if request_method:
                result_futures[client.name] = request_method(*args, **kwargs)
            else:
                result_futures[client.name] = none_future

        results = yield result_futures
        raise Return(GroupResults(results))


class GroupResults(dict):
    """The result of a group request.

    This has a dictionary interface, with the client names as keys and the
    corresponding replies from each client as values. The replies are stored as
    :class:`KATCPReply` objects, or are None for clients
    that did not support the request.

    The result will evalue to a truthy value if all the requests succeeded, i.e.
    ::

        if result:
            handle_success()
        else:
            handle_failure()

    should work as expected.

    """
    def __nonzero__(self):
        """True if katcp request succeeded on all clients."""
        return all(self.itervalues())

    @property
    def succeeded(self):
        """True if katcp request succeeded on all clients."""
        return bool(self)


class ClientGroup(object):
    """Create a group of similar clients.

    Parameters
    ----------
    name : str
        Name of the group of clients.
    clients : list of :class:`KATCPResource` objects
        Clients to put into the group.
    """
    def __init__(self, name, clients):
        self.name = name
        self._clients_dirty = True
        self.clients = tuple(clients)

    def __iter__(self):
        """Iterate over client members of group."""
        return iter(self.clients)

    def __getitem__(self, index):
        """Get the client at specific index of group."""
        return self.clients[index]

    def __len__(self):
        """Number of client members in group."""
        return len(self.clients)

    @property
    def req(self):
        if self._clients_dirty:
            self._req = AttrDict()
            for client in self.clients:
                for name, request in dict.iteritems(client.req):
                    if name not in self._req:
                        self._req[name] = GroupRequest(self, name,
                                                       request.description)
            self._clients_dirty = False

        return self._req

    def client_updated(self, client):
        """Called to notify this group that a client has been updated."""
        assert client in self.clients
        self._clients_dirty = True

    def is_connected(self):
        """Indication of the connection state of all clients in the group"""
        return all([c.is_connected() for c in self.clients])

    @tornado.gen.coroutine
    def set_sampling_strategies(self, filter, strategy_and_params):
        """Set sampling strategy for the sensors of all the group's clients.

        Only sensors that match the specified filter are considered. See the
        `KATCPResource.set_sampling_strategies` docstring for parameter
        definitions and more info.

        Returns
        -------
        sensors_strategies : tornado Future
           Resolves with a dict with client names as keys and with the value as
           another dict. The value dict is similar to the return value
           described in the `KATCPResource.set_sampling_strategies` docstring.
        """
        futures_dict = {}
        for res_obj in self.clients:
            futures_dict[res_obj.name] = res_obj.set_sampling_strategies(
                filter, strategy_and_params)
        sensors_strategies = yield futures_dict
        raise tornado.gen.Return(sensors_strategies)

    @tornado.gen.coroutine
    def set_sampling_strategy(self, sensor_name, strategy_and_params):
        """Set sampling strategy for the sensors of all the group's clients.

        Only sensors that match the specified filter are considered. See the
        `KATCPResource.set_sampling_strategies` docstring for parameter
        definitions and more info.

        Returns
        -------
        sensors_strategies : tornado Future
           Resolves with a dict with client names as keys and with the value as
           another dict. The value dict is similar to the return value
           described in the `KATCPResource.set_sampling_strategies` docstring.
        """
        futures_dict = {}
        for res_obj in self.clients:
            futures_dict[res_obj.name] = res_obj.set_sampling_strategy(
                sensor_name, strategy_and_params)
        sensors_strategies = yield futures_dict
        raise tornado.gen.Return(sensors_strategies)

    @tornado.gen.coroutine
    def wait(self, sensor_name, condition_or_value, status=None, timeout=5):
        """Wait for a sensor present on all clients in the group to satisfy a
        condition.

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
        status : int enum, key of katcp.Sensor.SENSOR_TYPES or None
            Wait for this status, at the same time as value above, to be
            obtained. Ignore status if None
        timeout : float or None
            The timeout in seconds

        Returns
        -------
        This command returns a tornado Future that resolves with True if the
        sensor values satisifed the condition with the timeout, else resolves
        with False.

        Raises
        ------
        Resolves with a :class:`KATCPSensorError` exception if any of the
        sensors do not have a strategy set, or if the named sensor is not
        present

        """
        wait_result_futures = {}
        for client in self.clients:
            wait_result_futures[client.name] = client.wait(
                sensor_name, condition_or_value, status, timeout)
        wait_results = yield wait_result_futures
        raise tornado.gen.Return(all(wait_results.values()))

class KATCPClientResourceContainer(resource.KATCPResource):
    """Class for containing multiple :class:`KATCPClientResource` instances

    Provides aggregate `sensor` and `req` attributes containing the union of all the
    sensors in requests in the contained resources. Names are prefixed with <resname>_,
    where <resname> is the name of the resource to which the sensor / request belongs
    except for aggregate sensors that starts with 'agg_'.

    """
    @property
    def req(self):
        if self._children_dirty:
            self._req = self._create_attrdict_from_children('req')
            self._children_dirty = False

        return self._req

    @property
    def sensor(self):
        if self._children_dirty:
            self._sensor = self._create_attrdict_from_children('sensor')
            self._children_dirty = False

        return self._sensor

    @property
    def sensors(self):
        return self.sensor

    @property
    def address(self):
        return None

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._description

    @property
    def parent(self):
        return None

    @property
    def children(self):
        return self._children

    @property
    def groups(self):
        return self._groups

    def __init__(self, resources_spec, logger=log):
        """Initialise Container with specifications for all the child resources

        Parameters
        ----------

        resources_spec : dict containing the specs of the conained resources. Keys:
          "name" : str, name of this collection of resources
          "description : str (optional), description of this collection of resources
          "clients" : dict, with keys:
            <name> : resource specifications for :class:`KATCPClientResource` where <name>
                     is the name of the resource. Note that the `name` key in the
                     individual resource spec dicts should not be specified, and will
                     automatically be filled using the <name> key above.
          "groups" : optional dict of resource groupings with keys: <group-name> : seq of
            <group-member>* where <group-name> is the name of the group and
            <group-member>* is a subset of clients' names as in the "clients" dict
            above. Also, the <group-name> set must be disjoint from the <name> set above.

        logger : object, optional
           Python Logger object to log to. Default is the module logger
        """
        super(KATCPClientResourceContainer, self).__init__()
        self._resources_spec = resources_spec
        self._logger = logger
        self._name = resources_spec['name']
        self._description = resources_spec.get('description', '')
        self._children_dirty = True   # Are we out of sync with the children?
        self._init_resources()
        self._init_groups()
        self.set_ioloop()

    def _init_resources(self):
        resources = self._resources_spec['clients']
        children = AttrDict()
        for res_name, res_spec in resources.items():
            # Make a copy since we'll be modifying the dict
            res_spec = dict(res_spec)
            res_spec['name'] = res_name
            res = self.client_resource_factory(
                res_spec, parent=self, logger=self._logger)
            children[resource.escape_name(res_name)] = res
        self._children = children

    def client_resource_factory(self, res_spec, parent, logger):
        """Return an instance of :class:`KATCPClientResource` or similar

        Provided to ease testing. Overriding this method allows deep brain surgery. See
        :func:`katcp.fake_clients.fake_KATCP_client_resource_factory`

        """
        return KATCPClientResource(res_spec, parent=self, logger=logger)

    def _init_groups(self):
        group_configs = self._resources_spec.get('groups', {})
        groups = AttrDict()
        for group_name, group_client_names in group_configs.items():
            group_clients = tuple(self.children[resource.escape_name(cn)]
                                  for cn in group_client_names)
            group = ClientGroup(group_name, group_clients)
            groups[resource.escape_name(group_name)] = group

        self._groups = groups

    def set_ioloop(self, ioloop=None):
        """Set the tornado ioloop to use

        Defaults to tornado.ioloop.IOLoop.current() if set_ioloop() is not called or if
        ioloop=None. Must be called before start()
        """
        ioloop = ioloop or tornado.ioloop.IOLoop.current()
        self.ioloop = ioloop
        for res in dict.values(self.children):
            res.set_ioloop(ioloop)

    def is_connected(self):
        """Indication of the connection state of all children"""
        return all([r.is_connected() for r in dict.values(self.children)])

    def start(self):
        """Start and connect all the subordinate clients"""
        for res in dict.values(self.children):
            res.start()

    @tornado.gen.coroutine
    def until_synced(self, timeout=None):
        """Return a tornado Future; resolves when all subordinate clients are synced"""
        futures = []
        if timeout:
            for resource in dict.values(self.children):
                futures.append(with_timeout(self.ioloop.time() + timeout,
                                            resource.until_synced(),
                                            self.ioloop))
        else:
            futures = [r.until_synced() for r in dict.values(self.children)]
        yield futures

    @tornado.gen.coroutine
    def until_not_synced(self, timeout=None):
        """Return a tornado Future; resolves when any subordinate client is not synced"""
        yield until_any(*[r.until_not_synced() for r in dict.values(self.children)],
                        timeout=timeout)

    def until_any_child_in_state(self, state, timeout=None):
        """Return a tornado Future; resolves when any client is in specified state"""
        return until_any(*[r.until_state(state) for r in dict.values(self.children)],
                         timeout=timeout)

    @tornado.gen.coroutine
    def until_all_children_in_state(self, state, timeout=None):
        """Return a tornado Future; resolves when all clients are in specified state"""
        yield [r.until_state(state, timeout=timeout)
               for r in dict.values(self.children)]

    @steal_docstring_from(resource.KATCPResource.list_sensors)
    def list_sensors(self, filter="", strategy=False, status="",
                     use_python_identifiers=True, tuple=False, refresh=False):
        return list_sensors(self,
            dict.items(self.sensor), filter, strategy, status, use_python_identifiers, tuple, refresh)

    @tornado.gen.coroutine
    def _resource_set_sampling_strategies(self, resource_name, sensor_name, strategy_and_parms):
        resource_name = result.object.parent_name
        try:
            yield self.set_sampling_strategy(resource_name, sensor_name, strategy_and_parms)
        except:
            self._logger.error(
                'Cannot set sensor strategy for %s %s'
                % (resource_name, sensor_name))

    @tornado.gen.coroutine
    def set_sampling_strategies(self, filter, strategy_and_parms):
        """Set sampling strategies for filtered sensors - these sensors have to exsist"""
        result_list = yield self.list_sensors(filter=filter)
        sensor_dict = {}
        for result in result_list:
            sensor_name = result.object.normalised_name
            resource_name = result.object.parent_name
            if resource_name not in sensor_dict:
                sensor_dict[resource_name] = {}
            try:
                resource_obj = self.children[resource_name]
                yield resource_obj.set_sampling_strategy(sensor_name, strategy_and_parms)
                sensor_dict[resource_name][sensor_name] = strategy_and_parms
                self._logger.debug(
                    'Set sampling strategy on resource %s for %s'
                    % (resource_name, sensor_name))
            except Exception as exc:
                self._logger.error(
                    'Cannot set sampling strategy on resource %s for %s (%s)'
                    % (resource_name, sensor_name, exc))
                sensor_dict[resource_name][sensor_name] = None
        raise tornado.gen.Return(sensor_dict)

    @tornado.gen.coroutine
    def set_sampling_strategy(self, sensor_name, strategy_and_parms):
        """Set sampling strategies for the specific sensor - this sensor has to exsist"""
        result_list = yield self.list_sensors(filter="^"+sensor_name+"$") #exact match
        sensor_dict = {}
        for result in result_list:
            sensor_name = result.object.normalised_name
            resource_name = result.object.parent_name
            if resource_name not in sensor_dict:
                sensor_dict[resource_name] = {}
            try:
                resource_obj = self.children[resource_name]
                yield resource_obj.set_sampling_strategy(sensor_name, strategy_and_parms)
                sensor_dict[resource_name][sensor_name] = strategy_and_parms
                self._logger.debug(
                    'Set sampling strategy on resource %s for %s'
                    % (resource_name, sensor_name))
            except Exception as exc:
                self._logger.error(
                    'Cannot set sampling strategy on resource %s for %s (%s)'
                    % (resource_name, sensor_name, exc))
                sensor_dict[resource_name][sensor_name] = None
        raise tornado.gen.Return(sensor_dict)

    @tornado.gen.coroutine
    def set_sensor_listener(self, sensor_name, listener):
        """Set listener for the specific sensor - this sensor has to exsist"""
        result_list = yield self.list_sensors(filter="^"+sensor_name+"$") #exact match
        sensor_dict = {}
        for result in result_list:
            sensor_name = result.object.normalised_name
            resource_name = result.object.parent_name
            if resource_name not in sensor_dict:
                sensor_dict[resource_name] = {}
            try:
                resource_obj = self.children[resource_name]
                yield resource_obj.set_sensor_listener(sensor_name, listener)
                sensor_dict[resource_name][sensor_name] = listener
                self._logger.debug(
                    'Set sensor listener on resource %s for %s'
                    % (resource_name, sensor_name))
            except Exception as exc:
                self._logger.error(
                    'Cannot set sensor listener on resource %s for %s (%s)'
                    % (resource_name, sensor_name, exc))
                sensor_dict[resource_name][sensor_name] = None
        raise tornado.gen.Return(sensor_dict)

    def add_child_resource_client(self, res_name, res_spec):
        """Add a resource client to the container and start the resource connection"""
        res_spec = dict(res_spec)
        res_spec['name'] = res_name
        res = self.client_resource_factory(
                res_spec, parent=self, logger=self._logger)
        self.children[resource.escape_name(res_name)] = res;
        self._children_dirty = True
        res.set_ioloop(self.ioloop)
        res.start()
        return res

    def _create_attrdict_from_children(self, attr):
        attrdict = AttrDict()
        for child_name, child_resource in dict.items(self.children):
            prefix = resource.escape_name(child_name) + '_'
            for item_name, item in dict.items(getattr(child_resource, attr)):
                # Do not prefix aggregate sensors with "parent_name_"
                if item_name.startswith("agg_"):
                    full_item_name = item_name
                else:
                    full_item_name = prefix + item_name
                attrdict[full_item_name] = item
        return attrdict

    def stop(self):
        """Stop all child resources"""
        for child_name, child in dict.items(self.children):
            # Catch child exceptions when stopping so we make sure to stop all children
            # that want to listen.
            try:
                child.stop()
            except Exception:
                self._logger.exception('Exception stopping child {!r}'
                                       .format(child_name))

    @steal_docstring_from(resource.KATCPResource.wait)
    def wait(self, sensor_name, condition, timeout=5):
        raise NotImplementedError

    def _child_add_requests(self, child, sensor_keys):
        assert resource.escape_name(child.name) in self.children
        self._children_dirty = True
        self._dirty_groups(child)

    def _child_remove_requests(self, child, sensor_keys):
        assert resource.escape_name(child.name) in self.children
        self._children_dirty = True
        self._dirty_groups(child)

    def _child_add_sensors(self, child, sensor_keys):
        assert resource.escape_name(child.name) in self.children
        self._children_dirty = True

    def _child_remove_sensors(self, child, sensor_keys):
        assert resource.escape_name(child.name) in self.children
        self._children_dirty = True

    def _dirty_groups(self, child):
        groups_spec = self._resources_spec.get('groups', {})
        for group_name, group in dict.items(self.groups):
            if child.name in groups_spec[group_name]:
                group.client_updated(child)

    def __repr__(self):
        return '<{module}.{classname}(name={name}) at 0x{id:x}>'.format(
            module=self.__class__.__module__,
            classname=self.__class__.__name__,
            name=self.name, id=id(self))

class IOLoopThreadWrapper(object):
    default_timeout = None

    def __init__(self, ioloop=None):
        self.ioloop = ioloop = ioloop or tornado.ioloop.IOLoop.current()
        self._thread_id = None
        ioloop.add_callback(self._install)

    def call_in_ioloop(self, fn, args, kwargs, timeout=None):
        timeout = timeout or self.default_timeout
        if get_thread_ident() == self._thread_id:
            raise RuntimeError("Cannot call a thread-wrapped object from the ioloop")
        future, tornado_future = Future(), tornado_Future()
        self.ioloop.add_callback(
            self._ioloop_call, future, tornado_future, fn, args, kwargs)
        try:
            # Use the threadsafe future to block
            return future.result(timeout)
        except TimeoutError:
            raise
        except Exception:
            # If we have an exception use the tornado future instead since it will print a
            # nicer traceback.
            tornado_future.result()
            # Should never get here since the tornado future should raise
            assert False, 'Tornado Future should have raised'

    def decorate_callable(self, callable_):
        """Decorate a callable to use call_in_ioloop"""
        @wraps(callable_)
        def decorated(*args, **kwargs):
            # Extract timeout from request itself or use default for ioloop wrapper
            timeout = kwargs.get('timeout')
            return self.call_in_ioloop(callable_, args, kwargs, timeout)

        decorated.__doc__ = '\n\n'.join((
"""Wrapped async call. Will call in ioloop.

This call will block until the original callable has finished running on the ioloop, and
will pass on the return value. If the original callable returns a future, this call will
wait for the future to resolve and return the value or raise the exception that the future
resolves with.

Original Callable Docstring
---------------------------
""",
            textwrap.dedent(decorated.__doc__ or '')))

        return decorated

    def _install(self):
        self._thread_id = get_thread_ident()

    def _ioloop_call(self, future, tornado_future, fn, args, kwargs):
        chain_future(tornado_future, future)
        try:
            result_future = maybe_future(fn(*args, **kwargs))
            chain_future(result_future, tornado_future)
        except Exception:
            tornado_future.set_exc_info(sys.exc_info())

class ThreadSafeMethodAttrWrapper(ObjectWrapper):
    # Attributes must be in the class definition, or else they will be
    # proxied to __subject__
    _ioloop_wrapper = None

    def __init__(self, subject, ioloop_wrapper):
        self._ioloop_wrapper = ioloop_wrapper
        super(ThreadSafeMethodAttrWrapper, self).__init__(subject)

    def __getattr__(self, attr):
        val = super(ThreadSafeMethodAttrWrapper, self).__getattr__(attr)
        if callable(val):
            return self._ioloop_wrapper.decorate_callable(val)
        else:
            return val

    def _getattr(self, attr):
        return self._ioloop_wrapper.call_in_ioloop(getattr, (self.__subject__,
            attr), {})


class ThreadSafeKATCPSensorWrapper(ThreadSafeMethodAttrWrapper):

    @property
    def sampling_strategy(self):
        return self._getattr('sampling_strategy')


class ThreadSafeKATCPClientResourceRequestWrapper(ThreadSafeMethodAttrWrapper):
    @property
    def __call__(self):
        return self._ioloop_wrapper.decorate_callable(self.__subject__.__call__)


class MappingProxy(collections.Mapping):

    def __init__(self, mapping, wrapper):
        self._mapping = mapping
        self._wrapper = wrapper

    def __iter__(self):
        return iter(self._mapping)

    def __len__(self):
        return len(self._mapping)

    def __contains__(self, x):
        return x in self._mapping

    def __getitem__(self, key):
        return self._wrapper(self._mapping[key])


class AttrMappingProxy(MappingProxy):

    def __getattr__(self, attr):
        return self._wrapper(getattr(self._mapping, attr))

    def __dir__(self):
        return self.keys()


class ThreadSafeKATCPClientGroupWrapper(ThreadSafeMethodAttrWrapper):
    """Thread safe wrapper for :class:`ClientGroup`"""

    __slots__ = ['RequestWrapper', 'ResourceWrapper']

    def __init__(self, subject, ioloop_wrapper):
        self.RequestWrapper = partial(ThreadSafeKATCPClientResourceRequestWrapper,
                                      ioloop_wrapper=ioloop_wrapper)
        self.ResourceWrapper = partial(ThreadSafeKATCPClientResourceWrapper,
                                       ioloop_wrapper=ioloop_wrapper)
        super(ThreadSafeKATCPClientGroupWrapper, self).__init__(
            subject, ioloop_wrapper)

    def __iter__(self):
        """Iterate over client members of group."""
        return iter(self.clients)

    def __getitem__(self, index):
        """Get the client at specific index of group."""
        return self.clients[index]

    @property
    def req(self):
        return AttrMappingProxy(self.__subject__.req, self.RequestWrapper)

    @property
    def clients(self):
        async_clients = self.__subject__.clients
        blocking_clients = [self.ResourceWrapper(ac) for ac in async_clients]
        return blocking_clients


class ThreadSafeKATCPClientResourceWrapper(ThreadSafeMethodAttrWrapper):
    """Should work with both KATCPClientResource or KATCPClientResourceContainer"""

    __slots__ = ['ResourceWrapper', 'SensorWrapper', 'RequestWrapper',
                 'GroupWrapper']

    def __init__(self, subject, ioloop_wrapper):
        self.ResourceWrapper = partial(ThreadSafeKATCPClientResourceWrapper,
                                       ioloop_wrapper=ioloop_wrapper)
        self.SensorWrapper = partial(ThreadSafeKATCPSensorWrapper,
                                     ioloop_wrapper=ioloop_wrapper)
        self.RequestWrapper = partial(ThreadSafeKATCPClientResourceRequestWrapper,
                                      ioloop_wrapper=ioloop_wrapper)
        self.GroupWrapper = partial(ThreadSafeKATCPClientGroupWrapper,
                                       ioloop_wrapper=ioloop_wrapper)
        super(ThreadSafeKATCPClientResourceWrapper, self).__init__(
            subject, ioloop_wrapper)

    def __getattr__(self, attr):
        val = super(ThreadSafeKATCPClientResourceWrapper, self).__getattr__(attr)
        if isinstance(val, resource.KATCPResource):
            return self.ResourceWrapper(val)
        elif isinstance(val, ClientGroup):
            return self.GroupWrapper(val)
        else:
            return val

    @property
    def sensor(self):
        return AttrMappingProxy(self.__subject__.sensor, self.SensorWrapper)

    @property
    def sensors(self):
        return self.sensor

    @property
    def req(self):
        return AttrMappingProxy(self.__subject__.req, self.RequestWrapper)

    @property
    def groups(self):
        return AttrMappingProxy(self.__subject__.groups, self.GroupWrapper)

    @property
    def children(self):
        if self.__subject__.children:
            return AttrMappingProxy(self.__subject__.children, self.ResourceWrapper)
        else:
            return AttrDict()

    @property
    def parent(self):
        if self.__subject__.parent:
            return self.ResourceWrapper(self.__subject__.parent)


@tornado.gen.coroutine
def monitor_resource_sync_state(resource, callback, exit_event=None):
    """Coroutine that monitors a KATCPResource's sync state.

    Calls callback(True/False) whenever the resource becomes synced or unsynced. Will
    always do an initial callback(False) call. Exits without calling callback() if
    exit_event is set
    """
    exit_event = exit_event or AsyncEvent()
    callback(False)        # Initial condition, assume resource is not connected

    while not exit_event.is_set():
        # Wait for resource to be synced
        yield until_any(resource.until_synced(), exit_event.until_set())
        if exit_event.is_set():
            break       # If exit event is set we stop without calling callback
        else:
            callback(True)

        # Wait for resource to be un-synced
        yield until_any(resource.until_not_synced(), exit_event.until_set())
        if exit_event.is_set():
            break       # If exit event is set we stop without calling callback
        else:
            callback(False)
