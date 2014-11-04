###############################################################################
# SKA South Africa (http://ska.ac.za/)                                        #
# Author: cam@ska.ac.za                                                       #
# Copyright @ 2013 SKA SA. All rights reserved.                               #
#                                                                             #
# THIS SOFTWARE MAY NOT BE COPIED OR DISTRIBUTED IN ANY FORM WITHOUT THE      #
# WRITTEN PERMISSION OF SKA SA.                                               #
###############################################################################

import logging

from katcp import resource, inspecting_client
from katcp.core import AttrDict

log = logging.getLogger(__name__)

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
        self.sensor = AttrDict()
        self.req = AttrDict()
        # Save the pop() methods in case a sensor/request with name 'pop' is added
        self._sensor_pop = self.sensor.pop
        self._req_pop = self.req.pop

    def set_ioloop(self, ioloop=None):
        """Set the tornado ioloop to use

        Defaults to tornado.ioloop.IOLoop.current() if set_ioloop() is not called or if
        ioloop=None. Must be called before start()
        """
        self._ioloop_set_to = ioloop

    def start(self):
        """Start the client and connect"""
        host, port = self.address
        ic = self._inspecting_client = inspecting_client.InspectingClientAsync(
            host, port, ioloop=self._ioloop_set_to, auto_reconnect=self.auto_reconnect)
        ic.katcp_client.auto_reconnect_delay = self.auto_reconnect_delay
        ic.set_request_added_callback(self._request_added_callback)
        ic.set_request_removed_callback(self._request_removed_callback)
        ic.set_sensor_added_callback(self._sensor_added_callback)
        ic.set_sensor_removed_callback(self._sensor_removed_callback)

        self._sensor_manager = sds
        ic.connect()


    def _request_added_callback(self, request_keys):
        pass

    def _request_removed_callback(self, request_keys):
        pass

    def _sensor_added_callback(self, sensor_keys):
        pass

    def _sensor_removed_callback(self, sensor_keys):
        pass


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

    def set_sampling_strategy(self, sensor_name, strategy_and_parms):
        """Set the sampling strategy for the named sensor

        Parameters
        ----------

        sensor_name : str
            Name of the sensor
        strategy : seq of str or str
            As tuple contains (<strat_name>, [<strat_parm1>, ...]) where the strategy
            names and parameters are as defined by the KATCP spec. As str contains the
            same elements in space-separated form
        """

        strategy_and_parms = resource.normalize_strategy_parameters(strategy_and_parms)
        self._strategy_cache[sensor_name] = abc

    def get_sampling_strategy(self, sensor_name):
        """Get the current sampling strategy for the named sensor

        Parameters
        ----------

        sensor_name : str
            Name of the sensor

        Return Value
        ------------

        strategy : tuple of str
            contains (<strat_name>, [<strat_parm1>, ...]) where the strategy names and
            parameters are as defined by the KATCP spec
        """
        cached = self._strategy_cache.get(sensor_name)
        if not cached:
            return resource.normalize_strategy_parameters('none')
        else:
            return cached


resource.KATCPSensorsManager.register(KATCPResourceClientSensorsManager)
