# inspect_client.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2014 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details
from __future__ import print_function

import logging

import tornado

import katcp.core

from collections import namedtuple, defaultdict

from concurrent.futures import Future
from tornado.gen import maybe_future, Return
from tornado.concurrent import Future as tornado_Future

from katcp.core import AttrDict

ic_logger = logging.getLogger("katcp.inspect_client")
RequestType = namedtuple('Request', 'name description')
InspectingClientStateType = namedtuple(
    'InspectingClientStateType', 'connected synced model_changed')

def until_any(*futures):
    any_future = tornado_Future()
    def handle_done(f):
        if not any_future.done():
            try:
                any_future.set_result(f.result())
            except Exception:
                any_future.set_exc_info(f.exc_info())

    for f in futures:
        f.add_done_callback(handle_done)

    return any_future

class SyncError(Exception):
    """Raised if an error occurs during syncing with a device"""


class _InformHookDeviceClient(katcp.AsyncClient):
    """DeviceClient that adds inform hooks."""

    def __init__(self, *args, **kwargs):
        super(_InformHookDeviceClient, self).__init__(*args, **kwargs)
        self._inform_hooks = defaultdict(list)

    def hook_inform(self, inform_name, callback):
        """Hookup a function to be called when an inform is received.

        Useful for interface-changed and sensor-status informs.

        Parameters
        ----------
        inform_name : str
            The name of the inform.
        callback : function
            The function to be called.

        """
        # Do not hook the same callback multiple times
        if not callback in self._inform_hooks[inform_name]:
            self._inform_hooks[inform_name].append(callback)

    def handle_inform(self, msg):
        """Call callbacks on hooked informs followed by normal processing"""
        try:
            for func in self._inform_hooks.get(msg.name, []):
                func(msg)
        except Exception:
            self._logger.warning('Call to function "{0}" with message "{1}".'
                                 .format(func, msg), exc_info=True)
        super(_InformHookDeviceClient, self).handle_inform(msg)


class InspectingClientAsync(object):
    """Higher-level client that inspects a KATCP interface.

    Note: This class is not threadsafe at present, it should only be called
    from the ioloop.

    """
    sensor_factory = katcp.Sensor
    """Factory that produces a KATCP Sensor compatible instance.

    signature: sensor_factory(name,
                              sensor_type,
                              description,
                              units,
                              params)

    Should be set before calling connect()/start().

    """
    request_factory = RequestType
    """Factory that produces KATCP Request objects

    signature: request_factory(name, description')

    Should be set before calling connect()/start().

    """

    sync_timeout = 5
    connect_timeout = 1

    def __init__(self, host, port, ioloop=None, initial_inspection=None,
                 auto_reconnect=True, logger=ic_logger):
        self._logger = logger
        if initial_inspection is None:
            initial_inspection = True
        self.initial_inspection = bool(initial_inspection)
        self._requests_index = {}
        self._sensors_index = {}
        self._connected = katcp.core.AsyncEvent()
        self._disconnected = katcp.core.AsyncEvent()
        self._interface_changed = katcp.core.AsyncEvent()
        # Set the default behaviour for update.
        self._update_on_lookup = True
        self._cb_register = {}  # Register to hold the possible callbacks.
        self._running = False

        # Setup KATCP device.
        self.katcp_client = _InformHookDeviceClient(
            host, port, auto_reconnect=auto_reconnect, logger=logger)
        if ioloop is False:
            # Called from the blocking client.
            self.ioloop = self.katcp_client.ioloop
        else:
            self.ioloop = ioloop or tornado.ioloop.IOLoop.current()
            self.katcp_client.set_ioloop(ioloop)

        self.katcp_client.hook_inform('sensor-status',
                                      self._cb_inform_sensor_status)
        self.katcp_client.hook_inform('interface-changed',
                                      self._cb_inform_interface_change)
        # Hook a callback for/to deprecated informs.
        # _cb_inform_deprecated will log a message when one of these informs
        # are received.
        self.katcp_client.hook_inform('device-changed',
                                      self._cb_inform_deprecated)
        self.katcp_client.notify_connected = self._cb_connection_state

        # User callback function to be called on state changes
        self._state_cb = None
        self.valid_states = frozenset((
            InspectingClientStateType(connected=False, synced=False, model_changed=False),
            InspectingClientStateType(connected=True, synced=False, model_changed=False),
            InspectingClientStateType(connected=True, synced=False, model_changed=True),
            InspectingClientStateType(connected=True, synced=True, model_changed=False)))
        self._state = katcp.core.AsyncState(self.valid_states)

    def __del__(self):
        self.close()

    @property
    def sensors(self):
        """A list of known sensors."""
        return self._sensors_index.keys()

    @property
    def requests(self):
        """A list of possible requests."""
        return self._requests_index.keys()

    @property
    def connected(self):
        """Connection status."""
        return self.katcp_client.is_connected()

    @property
    def synced(self):
        """Boolean indicating if the device has been synchronised."""
        return self._state.state == InspectingClientStateType(
            connected=True, synced=True, model_changed=False)

    def set_ioloop(self, ioloop):
        self.katcp_client.set_ioloop(ioloop)

    def is_connected(self):
        """Connection status."""
        return self.katcp_client.is_connected()

    def until_connected(self):
        # TODO (NM) Perhaps misleading to say until_protocol here? For debugging it is
        # useful to know if the TCP connection is established even if nothing else has
        # happened yet. Also, won't match is_connected()
        return self.katcp_client.until_protocol()

    @tornado.gen.coroutine
    def until_synced(self):
        yield self._state.until_state(InspectingClientStateType(
            connected=True, synced=True, model_changed=False))

    @tornado.gen.coroutine
    def connect(self, timeout=None):
        """Connect to KATCP interface, starting what is needed

        Parameters
        ----------
        timeout : float, None
            Time to wait until connected. No waiting if None.

        Raises
        ------

        :class:`tornado.gen.TimeoutError` if the connect timeout expires
        """
        # Start KATCP device client.
        assert not self._running
        t0 = self.ioloop.time()
        def maybe_timeout(f):
            # Helper function for yielding with a timeout if required, or not
            if not timeout:
                return f
            else:
                remaining = timeout - (self.ioloop.time() - t0)
                return tornado.gen.with_timeout(remaining, f)

        self._logger.debug('Starting katcp client')
        self.katcp_client.start()
        try:
            yield maybe_timeout(self.katcp_client.until_running())
            self._logger.debug('Katcp client running')
        except tornado.gen.TimeoutError:
            self.katcp_client.stop()
            raise
        yield maybe_timeout(self.katcp_client.until_connected())
        self._logger.debug('Katcp client connected')

        self._running = True
        self._state_loop()

    @katcp.core.log_coroutine_exceptions
    @tornado.gen.coroutine
    def _state_loop(self):
        # TODO (NM) Arrange for _running to be set to false and stopping the katcp client
        # if this loop exits a
        is_connected = self.katcp_client.is_connected
        while self._running:
            self._logger.debug('Sending intial state')
            yield self._send_state(
                connected=is_connected(), synced=False, model_changed=False)
            try:
                yield self.katcp_client.until_connected()
                self._logger.debug('Sending post-connected  state')
                yield self._send_state(
                    connected=is_connected(), synced=False, model_changed=False)
                yield until_any(self.katcp_client.until_protocol(),
                                self._disconnected.until_set())
                if not self.katcp_client.is_connected():
                    continue
                if self.initial_inspection:
                    model_changes = yield self.inspect()
                    model_changed = bool(model_changes)
                    yield self._send_state(
                        connected=is_connected(), synced=False,
                        model_changed=model_changed, model_changes=model_changes)
                else:
                    self.initial_inspection = True
                if not is_connected():
                    continue
                # We wait for the previous _send_state call (and user callbacks) to
                # complete before we change the state to synced=True
                yield self._send_state(connected=True, synced=True, model_changed=False)
                yield until_any(self._interface_changed.until_set(),
                                self._disconnected.until_set())
                self._interface_changed.clear()
                continue
                # Next loop through should cause re-inspection and handle state updates
            except SyncError, e:
                self._logger.warn("Error syncing with device : {0!s}".format(e))
                continue
            except Exception:
                retry_wait_time = self.connect_timeout
                self._logger.exception(
                    'Unhandled exception in client-sync loop. Triggering disconnect and '
                    'Retrying in {}s.'
                    .format(retry_wait_time))
                self.katcp_client.disconnect()
                yield katcp.core.until_later(retry_wait_time)
                continue

    @tornado.gen.coroutine
    def _send_state(self, connected, synced, model_changed,
                    model_changes=None):
        state = InspectingClientStateType(connected, synced, model_changed)
        self._state.set_state(state)
        self._logger.debug('InspectingClient State changed to {0}'.format(state))
        # Should only be called from _state_loop
        # TODO (NM) Perhaps avoid resending unchanged states?

        if self._state_cb:
            try:
                yield maybe_future(self._state_cb(state, model_changes))
                # TODO Perhaps allow _state_cb to abort loop?
            except Exception:
                self._logger.exception(
                    'Unhandled exception calling user state-change callback')

    def set_state_callback(self, cb):
        """Set user callback for state changes

        Called as cb(state, model_changes)

        where state is InspectingClientStateType instance, and model_changes ...

        TODO More docs on what the callback is called with
        """
        self._state_cb = cb

    def close(self):
        self.stop()
        self.join()

    def start(self, timeout=None):
        # Specific connect methods are defined in both the Async and
        # Blocking Inspect Client classes.
        return self.connect(timeout)

    def stop(self, timeout=None):
        self._running = False
        self.katcp_client.stop(timeout)

    def join(self, timeout=None):
        self.katcp_client.join(timeout)

    def handle_sensor_value(self):
        """Handle #sensor-value informs just like #sensor-status informs"""
        self.katcp_client.hook_inform('sensor-value',
                                      self._cb_inform_sensor_status)

    def _update_index(self, index, name, data):
        if name not in index:
            index[name] = data
        else:
            orig_data = index[name]
            for key, value in data.items():
                if orig_data.get(key) != value:
                    orig_data[key] = value
                    orig_data['_changed'] = True

    def handle_sensor_value(self):
        """Handle #sensor-value informs just like #sensor-status informs"""
        self.katcp_client.hook_inform('sensor-value',
                                      self._cb_inform_sensor_status)

    @tornado.gen.coroutine
    def inspect(self):
        request_changes = yield self.inspect_requests()
        sensor_changes = yield self.inspect_sensors()

        model_changes = AttrDict()
        if request_changes:
            model_changes.requests = request_changes
        if sensor_changes:
            model_changes.sensors = sensor_changes
        if model_changes:
            raise Return(model_changes)

    @tornado.gen.coroutine
    def inspect_requests(self, name=None):
        """Inspect all or one requests on the device.

        Parameters
        ----------
        name : str or None, optional
            Name of the sensor or None to get all requests.

        TODO Return value
        """
        if name is None:
            msg = katcp.Message.request('help')
        else:
            msg = katcp.Message.request('help', name)
        reply, informs = yield self.katcp_client.future_request(msg)
        requests_old = set(self._requests_index.keys())
        requests_updated = set()
        for msg in informs:
            req_name = msg.arguments[0]
            req = {'description': msg.arguments[1]}
            requests_updated.add(req_name)
            self._update_index(self._requests_index, req_name, req)

        added, removed = self._difference(
            requests_old, requests_updated, name, self._requests_index)
        if added or removed:
            raise Return(AttrDict(added=added, removed=removed))

    @tornado.gen.coroutine
    def inspect_sensors(self, name=None):
        """Inspect all or one sensor on the device.

        Parameters
        ----------
        name : str or None, optional
            Name of the sensor or None to get all sensors.

        TODO Return value

        """
        if name is None:
            msg = katcp.Message.request('sensor-list')
        else:
            msg = katcp.Message.request('sensor-list', name)

        reply, informs = yield self.katcp_client.future_request(msg)
        sensors_old = set(self._sensors_index.keys())
        sensors_updated = set()
        for msg in informs:
            sen_name = msg.arguments[0]
            sensors_updated.add(sen_name)
            sen = {'description': msg.arguments[1],
                   'unit': msg.arguments[2],
                   'sensor_type': msg.arguments[3],
                   'params':msg.arguments[4:]}
            self._update_index(self._sensors_index, sen_name, sen)

        added, removed = self._difference(
            sensors_old, sensors_updated, name, self._sensors_index)

        if added or removed:
            raise Return(AttrDict(added=added, removed=removed))

    @tornado.gen.coroutine
    def future_check_sensor(self, name, update=None):
        """Check if the sensor exists.

        Used internally by future_get_sensor. This method is aware of
        synchronisation in progress and if inspection of the server is allowed.

        Parameters
        ----------
        name : str
            Name of the sensor to verify.
        update : bool or None, optional
            If a katcp request to the server should be made to check if the
            sensor is on the server now.

        """
        exist = False
        yield self.until_synced()
        if name in self._sensors_index:
            exist = True
        else:
            if update or (update is None and self._update_on_lookup):
                yield self.inspect_sensors(name)
                exist = yield self.future_check_sensor(name, False)

        raise tornado.gen.Return(exist)

    @tornado.gen.coroutine
    def future_get_sensor(self, name, update=None):
        """Get the sensor object.

        Check if we have information for this sensor, if not connect to server
        and update (if allowed) to get information.

        Parameters
        ----------
        name : string
            Name of the sensor.
        update : bool or None, optional
            True allow inspect client to inspect katcp server if the sensor
            is not known.

        Returns
        -------
        Sensor created by :meth:`sensor_factory` or None if sensor not found.

        """
        obj = None
        exist = yield self.future_check_sensor(name, update)
        if exist:
            sensor_info = self._sensors_index[name]
            obj = sensor_info.get('obj')
            if obj is None:
                sensor_type = katcp.Sensor.parse_type(
                    sensor_info.get('sensor_type'))
                sensor_params = katcp.Sensor.parse_params(
                    sensor_type,
                    sensor_info.get('params'))
                obj = self.sensor_factory(
                    name=name,
                    sensor_type=sensor_type,
                    description=sensor_info.get('description'),
                    units=sensor_info.get('units'),
                    params=sensor_params)
                self._sensors_index[name]['obj'] = obj

        raise tornado.gen.Return(obj)

    @tornado.gen.coroutine
    def future_check_request(self, name, update=None):
        """Check if the request exists.

        Used internally by future_get_request. This method is aware of
        synchronisation in progress and if inspection of the server is allowed.

        Parameters
        ----------
        name : str
            Name of the request to verify.
        update : bool or None, optional
            If a katcp request to the server should be made to check if the
            sensor is on the server. True = Allow, False do not Allow, None
            use the class default.

        """
        exist = False
        yield self.until_synced()
        if name in self._requests_index:
            exist = True
        else:
            if update or (update is None and self._update_on_lookup):
                yield self.inspect_requests(name)
                exist = yield self.future_check_request(name, False)
        raise tornado.gen.Return(exist)

    @tornado.gen.coroutine
    def future_get_request(self, name, update=None):
        """Get the request object.

        Check if we have information for this request, if not connect to server
        and update (if allowed).

        Parameters
        ----------
        name : string
            Name of the request.
        update : bool or None, optional
            True allow inspect client to inspect katcp server if the request
            is not known.

        Returns
        -------
        Request created by :meth:`request_factory` or None if request not found.

        """
        obj = None
        exist = yield self.future_check_request(name, update)
        if exist:
            request_info = self._requests_index[name]
            obj = request_info.get('obj')
            if obj is None:
                obj = self.request_factory(
                    name, request_info.get('description', ''))
                self._requests_index[name]['obj'] = obj

        raise tornado.gen.Return(obj)

    @tornado.gen.coroutine
    def update_sensor(self, name, timestamp, status, value):
        sensor = yield self.future_get_sensor(name)
        if sensor:
            katcp_major = self.katcp_client.protocol_flags.major
            sensor.set_formatted(timestamp, status, value, katcp_major)
        else:
            self._logger.error('Received update for "%s", but could not create'
                               ' sensor object.' % name)

    def _cb_connection_state(self, connected):
        if connected:
            self._disconnected.clear()
            self._connected.set()
        else:
            self._connected.clear()
            self._disconnected.set()

    def _cb_inform_sensor_status(self, msg):
        """Update received for an sensor."""
        timestamp = msg.arguments[0]
        num_sensors = int(msg.arguments[1])
        assert len(msg.arguments) == 2 + num_sensors * 3
        for n in xrange(num_sensors):
            name = msg.arguments[2 + n * 3]
            status = msg.arguments[3 + n * 3]
            value = msg.arguments[4 + n * 3]
            self.update_sensor(name, timestamp, status, value)

    def _cb_inform_interface_change(self, msg):
        """Update the sensors and requests available."""
        self._interface_changed.set()

    def _cb_inform_deprecated(self, msg):
        """Log a message that an deprecated inform has been received.."""
        self._logger.warning("Received a deprecated inform: {0}."
                             .format(msg.name))

    def simple_request(self, request, *args, **kwargs):
        """Create and send a request to the server.

        This method implements a very small subset of the options
        possible to send an request. It is provided as a shortcut to
        sending a simple request.

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
            argument. Defaults to None
        use_mid : bool
            Use a mid for the request if True. Defaults to True if the server supports
            them.

        Returns
        -------
        future object.

        Example
        -------

        ::

        reply, informs = yield ic.simple_request('help', 'sensor-list')

        """
        use_mid = kwargs.get('use_mid')
        timeout = kwargs.get('timeout')
        mid = kwargs.get('mid')
        msg = katcp.Message.request(request, *args, mid=mid)
        return self.katcp_client.future_request(msg, timeout, use_mid)

    def _difference(self, original_keys, updated_keys, name, item_index):
        """Calculate difference between the original and updated sets of keys.

        Removed items will be removed from item_index, new items should have
        been added by the discovery process. (?help or ?sensor-list)

        This method is for use in inspect_requests and inspect_sensors only.

        Returns
        -------

        (added, removed)
        added : set of str
            Names of the keys that were added
        removed : set of str
            Names of the keys that were removed

        """
        original_keys = set(original_keys)
        updated_keys = set(updated_keys)
        added_keys = updated_keys.difference(original_keys)
        removed_keys = set()
        if name is None:
            removed_keys = original_keys.difference(updated_keys)
        elif name not in updated_keys and name in original_keys:
            removed_keys = set([name])

        for key in removed_keys:
            if key in item_index:
                del(item_index[key])

        # Check the keys that was not added now or not lined up for removal,
        # and see if they changed.
        for key in updated_keys.difference(added_keys.union(removed_keys)):
            if item_index[key].get('_changed'):
                item_index[key]['_changed'] = False
                removed_keys.add(key)
                added_keys.add(key)

        return added_keys, removed_keys


