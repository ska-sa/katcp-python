###############################################################################
# SKA South Africa (http://ska.ac.za/)                                        #
# Author: cam@ska.ac.za                                                       #
# Copyright @ 2013 SKA SA. All rights reserved.                               #
#                                                                             #
# THIS SOFTWARE MAY NOT BE COPIED OR DISTRIBUTED IN ANY FORM WITHOUT THE      #
# WRITTEN PERMISSION OF SKA SA.                                               #
###############################################################################

import logging
import sys
import re

import tornado

from tornado.concurrent import Future as tornado_Future
from tornado.gen import Return

from katcp import resource, inspecting_client
from katcp.resource import KATCPReply, KATCPSensorError
from katcp.core import AttrDict

log = logging.getLogger(__name__)

def transform_future(transformation, future):
    """Returns a new future that will resolve with a transformed value

    Takes the resolution value of `future` and applies transformation() to it before
    setting the result of the new future with the transformed value. If future() resolves
    with an exception, it is passed through to the new future.

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
                new_future.set_exc_info(sys.exc_info())

    future.add_done_callback(_transform)

class AsyncState(object):
    """Allow async waiting for a state to attain a certain value

    Note, this class is NOT THREAD SAFE! It will only work properly if all its methods are
    called from the same thread/ioloop.
    """

    @property
    def state(self):
        return self._state

    @property
    def valid_states(self):
        return self._valid_states

    def __init__(self, valid_states):
        """Init with a seq of valid states

        Parameters
        ----------
        valid_states : ordered seq of hashable types
            Valid states, will be turned into a frozen set

        The initial state will be the first state in the seq
        """
        valid_states = tuple(valid_states)
        self._valid_states = frozenset(valid_states)
        self._state = valid_states[0]
        self._waiting_futures = {state: tornado_Future() for state in valid_states}

    def set_state(self, state):
        assert state in self._valid_states
        self._state = state
        old_future = self._waiting_futures[state]
        # Replace _waiting_future with a fresh one incase someone woken up by set_result()
        # sets this AsyncEvent to False before waiting on it to be set again.
        self._waiting_futures[state] = tornado_Future()
        old_future.set_result(True)

    def until_state(self, state):
        """Return a tornado Future that will resolve when the requested state is set"""
        assert state in self._valid_states
        if state != self._state:
            return self._waiting_futures[state]
        else:
            f = tornado_Future()
            f.set_result(True)
            return f


class ReplyWrappedInspectingClientAsync(inspecting_client.InspectingClientAsync):
    """Adds wrapped_request() method that wraps reply in a KATCPReply """

    reply_wrapper = KATCPReply

    def wrapped_request(self, request, *args, **kwargs):
        """Create and send a request to the server.

        This method implements a very small subset of the options
        possible to send an request. It is provided as a shortcut to
        sending a simple wrapped request.

        Parameters
        ----------
        request : str
            The request to call.
        args : list of objects
            Arguments to pass on to the request.
        timeout : float or None, optional
            Timeout after this amount of seconds (keyword argument).

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
        use_mid = kwargs.get('use_mid')
        timeout = kwargs.get('timeout')
        msg = katcp.Message.request(request, *args)
        f = tornado_Future()
        return transform_future(self.reply_wrapper,
                                self.katcp_client.future_request(msg, timeout, use_mid))

class KATCPResourceClient(object):
    """Class managing a client connection to a single KATCP resource

    Inspects the KATCP interface of the resources, exposing sensors and requests as per
    the :class:`katcp.resource.KATCPResource` API. Can also operate without exposin
    """

    def __init__(self, resource_spec, name, logger=log):
        """Initialise resource with given specification

        Parameters
        ----------
        name : str
          Name of the resource
        resource_spec : dict with resource specifications. Keys:
          address : (host, port), host as str, port as int
          always_allowed_requests : seq of str,
              KACTP requests that are always allowed, even when the resource is not
              controlled.
          always_excluded_requests : seq of str,
              KACTP requests that are never allowed, even if the resource is
              controlled. Overrides reqeusts in `always_allowed_requests`.
          controlled : bool, default: False
              True if control over the device (i.e. KATCP requests) is to be exposed.
          auto_reconnect : bool
              If True, auto-reconnect should the network connection be closed.
          auto_reconnect_delay : float seconds. Default : 0.5s
              Delay between reconnection retries.

          # TODO, not implemented, proposed below for light non-inspecting mode

          inspect : bool, default : True
              Inspect the resource's KATCP interface for sensors and requests
          assumed_requests : ...
          assumed_sensors : ...
        """

        self.address = resource_spec['address']
        self.always_allowed_requests = resource_spec.get(
            'always_allowed_requests', set())
        self.always_excluded_requests = resource_spec.get(
            'always_excluded_requests', set())
        self.controlled = resource_spec.get('controlled', False)
        self.auto_reconnect = resource_spec.get('auto_reconnect', True)
        self.auto_reconnect_delay = resource_spec.get('auto_reconnect_delay', 0.5)
        self._ioloop_set_to = None
        self._sensor = AttrDict()
        self._req = AttrDict()
        # Save the pop() / items() methods in case a sensor/request with the same name is
        # added
        self._sensor_pop = self.sensor.pop
        self._sensor_items = self.sensor.items
        self._req_pop = self.req.pop
        self._req_items = self.req.items
        self._state = AsyncState(("not synced", "syncing", "synced"))
        self._connected_state = AsyncState((False, True))

    @property
    def state(self):
        return self._state.state

    @property
    def req(self):
        return self._req

    @property
    def sensor(self):
        return self._sensor

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
        ic.set_request_added_callback(self._request_added_callback)
        ic.set_request_removed_callback(self._request_removed_callback)
        ic.set_sensor_added_callback(self._sensor_added_callback)
        ic.set_sensor_removed_callback(self._sensor_removed_callback)
        self._sensor_manager = KATCPResourceClientSensorsManager(ic)
        # Steal some methods from _sensor_manager
        self.reapply_sampling_strategies = self._sensor_manager.reapply_sampling_strategies
        ic.connect()

    def until_state(self, state):
        """Return a tornado Future that will resolve when the requested state is set

        State can be one of ("not synced", "syncing", "synced")
        """
        return self._state.until_state(state)

    @tornado.gen.coroutine
    def set_sensor_strategies(self, filter, strategy_and_params):
        """Set a sampling strategy for all sensors that match the specified filter.

        Parameters
        ----------
        filter : string
            The regular expression filter to use to select the sensors to which to apply
            the specified strategy.  Use "" to match all sensors. Is matched on the
            escaped (e.g. 'the_sensor', not 'the-sensor' sensor names.
        strategy_and_params : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form.

        Returns
        -------
        sensors_strategies : tornado Future
           resolves with a dict with the escaped names of the sensors as keys and the
           normalised sensor strategy and parameters as value if settings was successful,
           or the exception raised if it failed.

        TODO: Consider attaching a traceback to the exception  ala Python 3000.
        """
        sensors_success = {}
        filter_re = re.compile(filter)
        for sensor_name, sensor_obj in self._sensor_items():
            if filter_re.search(sensor_name):
                try:
                    yield sensor_obj.set_sampling_strategy(strategy_and_params)
                sensors_success[sensor_name] = success

    def _request_added_callback(self, request_keys):
        # Instantiate KATCPRequest instances and store on self.req
        pass

    def _request_removed_callback(self, request_keys):
        # Remove KATCPRequest instances from self.req
        pass

    def _sensor_added_callback(self, sensor_keys):
        # Create KATCPSensor instance, store in self.sensor
        # Reapply sensor strategies if they were seen before
        pass

    def _sensor_removed_callback(self, sensor_keys):
        # Remove KATCPSensor instances from self.sensor
        pass

    def _connection_status_callback(self, connected):
        self._connected_state.set_state(connected)
        if connected:
            self._sensor_manager.reapply_sampling_strategies()
        else:
            self._state.set('not synced')

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
        inspecting_client.handle_sensor_value()
        inspecting_client.sensor_factory = self._sensor_factory

    def _sensor_factory(self, **sensor_description):
        # kwargs as for inspecting_client.InspectingClientAsync.sensor_factory
        sens = resource.KATCPSensor(sensor_description, self)
        sensor_name = sensor_description['name']
        cached_strategy = self._strategy_cache.get(sensor_name)
        if cached_strategy:
            self.set_sampling_strategy(sensor_name, cached_strategy)
        return sens

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
            'sensor-sampling', *strategy_and_params)
        if not reply.succeeded:
            raise KATCPSensorError('Error setting strategy for sensor {0}: \n'
                                   '{1!s}'.format(sensor_name, reply))

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

resource.KATCPSensorsManager.register(KATCPResourceClientSensorsManager)
