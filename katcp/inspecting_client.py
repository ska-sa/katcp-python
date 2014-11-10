# inspect_client.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2014 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

from __future__ import print_function

import logging

import tornado

import katcp

from collections import namedtuple, defaultdict

from concurrent.futures import Future

ic_logger = logging.getLogger("katcp.inspect_client")
RequestType = namedtuple('Request', ['name', 'description'])

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
        if not any(callback == hook
                   for hook in self._inform_hooks[inform_name]):
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

    def __init__(self, host, port, ioloop=None, full_inspection=None, auto_reconnect=True,
                 logger=ic_logger):
        self._logger = logger
        if full_inspection is None:
            full_inspection = True
        self.full_inspection = bool(full_inspection)
        self._requests_index = {}
        self._sensors_index = {}
        self._request_sync = katcp.client.AsyncEvent()
        self._sensor_sync = katcp.client.AsyncEvent()
        # Set the default behaviour for update.
        self._update_on_lookup = True
        self._cb_register = {}  # Register to hold the possible callbacks.

        # Setup KATCP device.
        self.katcp_client = _InformHookDeviceClient(host, port, logger=logger)
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
        return self._sensor_sync.is_set() and self._request_sync.is_set()

    def set_ioloop(self, ioloop):
        self.katcp_client.set_ioloop(ioloop)

    def is_connected(self):
        """Connection status."""
        return self.katcp_client.is_connected()

    def until_connected(self):
        return self.katcp_client.until_protocol()

    @tornado.gen.coroutine
    def until_synced(self):
        yield [self._sensor_sync.until_set(), self._request_sync.until_set()]

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
        self.katcp_client.start(timeout)
        t0 = self.ioloop.time()
        def maybe_timeout(f):
            # Helper function for yielding with a timeout if required, or not
            if not timeout:
                return f
            else:
                remaining = timeout - (self.ioloop.time() - t0)
                return tornado.gen.with_timeout(remaining, f)

        yield maybe_timeout(self.katcp_client.until_running())
        yield maybe_timeout(self.until_connected())
        if self.full_inspection:
            self.inspect()
        else:
            # set synced.
            self._sensor_sync.set()
            self._request_sync.set()

    def close(self):
        self.katcp_client.stop()
        self.katcp_client.join()

    def start(self, timeout=None):
        # Specific connect methods are defined in both the Async and
        # Blocking Inspect Client classes.
        self.connect(timeout)

    def stop(self, timeout=None):
        self.katcp_client.stop(timeout)

    def join(self, timeout=None):
        self.katcp_client.join(timeout)

    def _update_index(self, kind, name, data):
        if kind == 'sensor':
            index = self._sensors_index
        elif kind == 'request':
            index = self._requests_index
        else:
            raise ValueError('kind must be either sensor or request not "{0}"'.
                             format(kind))

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
        yield self.inspect_requests()
        yield self.inspect_sensors()

    @tornado.gen.coroutine
    def inspect_requests(self, name=None):
        """Inspect all or one requests on the device.

        Parameters
        ----------
        name : str or None, optional
            Name of the sensor or None to get all requests.

        """
        yield self.until_connected()
        self._request_sync.clear()
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
            self._update_index('request', req_name, req)

        self._difference(requests_old,
                         requests_updated,
                         name,
                         'request')

        self._request_sync.set()

    @tornado.gen.coroutine
    def inspect_sensors(self, name=None):
        """Inspect all or one sensor on the device.

        Parameters
        ----------
        name : str or None, optional
            Name of the sensor or None to get all sensors.

        """
        yield self.until_connected()
        self._sensor_sync.clear()
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
                   'params': []}
            if len(msg.arguments) > 4:
                sen['params'] = msg.arguments[4:]
            self._update_index('sensor', sen_name, sen)

        self._difference(sensors_old,
                         sensors_updated,
                         name,
                         'sensor')

        self._sensor_sync.set()

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
        if self.full_inspection:
            # Clear sync flags
            self._sensor_sync.clear()
            self._request_sync.clear()
            self.inspect()
        else:
            # TODO(MS): Look inside msg and update / clear flags only what is required.
            # Clear sync flags
            self._sensor_sync.clear()
            self._request_sync.clear()
            self.inspect()

    def _cb_inform_deprecated(self, msg):
        """Log a message that an deprecated inform has been received.."""
        self._logger.warning("Received a deprecated inform: {0}."
                             .format(msg.name))

    def set_sensor_added_callback(self, callback):
        """Set the Callback to be called when new sensors are added.

        Parameters
        ----------
        callback : function(sensor_keys)
            Reference to the function/method to be called, where `sensor_keys` is a seq
            of string sensor keys. A sensor object can be retrieved using
            :meth:`future_get_sensor(sensor_key)`

        """
        self._cb_register['sensor_added'] = callback

    def set_sensor_removed_callback(self, callback):
        """Set the Callback to be called when existing sensors are removed.

        Parameters
        ----------
        callback : function
            Reference to the function/method to be called, where `sensor_names` is a seq
            of string sensor keys.
        """
        self._cb_register['sensor_removed'] = callback

    def set_request_added_callback(self, callback):
        """Set the Callback to be called when a new sensor is added.

        Parameters
        ----------
        callback : function(request_keys)
            Reference to the function/method to be called, where `request_keys` is a seq
            of string sensor keys. A sensor object can be retrieved using
            :meth:`future_get_request(request_key)`

        """
        self._cb_register['request_added'] = callback

    def set_request_removed_callback(self, callback):
        """Set the Callback to be called when a new sensor is removed.

        Parameters
        ----------
        callback : function(request_names)
            Reference to the function/method to be called, where `request_names` is a seq
            of string request keys.

        """
        self._cb_register['request_removed'] = callback

    def set_connection_status_change_callback(self, callback):
        """Set the Callback to be called when the connection status changes.

       Parameters
        ----------
        callback : function(connected)
            Reference to the function/method to be called, where `connected` indicates
            whether the client has just connected (True) or just disconnected (False).

        """
        self.katcp_client.notify_connected = callback

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

    def _call_cb(self, cb, parameter):
        """Lookup the callback with the key cb in _cb_register and call it.

        Only one parameter is passed to the call back.

        Parameters
        ----------

        cb: str
            Use cb to look for callback in _cb_register.
        parameter: any
            Parameter if callback function. eg. func(parameter)

        """
        func = self._cb_register.get(cb)
        if func:
            try:
                # Using ioloop.add_callback is a bit safer here,
                # want to explicitly put the given function on the ioloop.
                self.ioloop.add_callback(func, parameter)
            except Exception:
                self._logger.warning('Calling function "{0}"'
                                     .format(func), exc_info=True)

    def _difference(self, original_keys, updated_keys, name, kind):
        """Calculate difference between the original and updated sets of keys.

        Removed items will be removed from item_index, new items should have
        been added by the discovery process. (?help or ?sensor-list)

        Update and remove callbacks as set by the set_*callback methods of this
        class will be called from here. `add_cb` and `rem_cb` are the names of
        the callbacks in the register, not the callables themselves.
        the callbacks in the register, not the callables themselves.

        This method is for use in inspect_requests and inspect_sensors only.

        """
        if kind == 'sensor':
            add_cb = 'sensor_added'
            rem_cb = 'sensor_removed'
            item_index = self._sensors_index
        else:
            add_cb = 'request_added'
            rem_cb = 'request_removed'
            item_index = self._requests_index

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

        # Call the appropriate callback for the remove action.
        if removed_keys:
            self._call_cb(rem_cb, list(removed_keys))

        # Call the appropriate callback for the added action.
        if added_keys:
            self._call_cb(add_cb, list(added_keys))

        return added_keys, removed_keys


class InspectingClientBlocking(InspectingClientAsync):
    """Higher-level client that inspects KATCP interfaces and is thread-safe."""

    def __init__(self, host, port, full_inspection=None, auto_reconnect=True,
                 logger=ic_logger):
        super(InspectingClientBlocking, self).__init__(
            host, port, False, full_inspection, auto_reconnect, logger)

    def connect(self, timeout=None):
        """Connect to the KATCP device."""
        self.katcp_client.start(timeout)
        self.katcp_client.wait_running()
        self.katcp_client.wait_protocol()
        self.ioloop = self.katcp_client.ioloop
        if self.full_inspection:
            self.ioloop.add_callback(self.inspect)
        else:
            # set synced true.
            self._sensor_sync.set()
            self._request_sync.set()

    def get_sensor(self, name, update=True):
        """Get the sensor object.

        Check if we have information for this sensor, if not connect to server
        and update (if allowed) to get information.

        Parameters
        ----------
        name : string
            Name of the sensor.
        update : boolean, optional
            True allow inspect client to inspect katcp server if the sensor
            is not known.

        Returns
        -------
        Sensor object or None if sensor could not be found.

        """

        f = Future()

        def cb():
            return tornado.gen.chain_future(
                self.future_get_sensor(name, update), f)

        self.katcp_client.ioloop.add_callback(cb)
        return f.result()
        # TODO(MS): Handle Timeouts...

    def get_request(self, name, update=True):
        """Get the request information.

        Check if we have information for this request, if not connect to server
        and update (if allowed).

        Parameters
        ----------
        name : string
            Name of the request.
        update : boolean, optional
            True allow inspecting client to inspect katcp server if the
            request is not known.

        Returns
        -------
        Sensor object or None if sensor could not be found.

        """

        f = Future()

        def cb():
            return tornado.gen.chain_future(
                self.future_get_request(name, update), f)

        self.katcp_client.ioloop.add_callback(cb)
        return f.result()
        # TODO(MS): Handle Timeouts...

    def wait_synced(self, timeout=None):
        """Wait until the client is synced.

        Parameters
        ----------

        timeout : float or None, optional
            Seconds to wait for the client to start synced.

        Returns
        -------

        running : bool
            Whether the client is synced

        Notes
        -----

        Do not call this from the ioloop, use until_synced()

        """
        ioloop = getattr(self, 'ioloop', None)
        if not ioloop:
            raise RuntimeError('Call connect() before wait_synced()')

        return all([self._sensor_sync.wait_with_ioloop(ioloop, timeout),
                    self._request_sync.wait_with_ioloop(ioloop, timeout)])

    def simple_request(self, request, *args, **kwargs):
        """Create and send a request to the server.

        This method implements a very small subset of the options
        possible to send an request, it is provided as a shortcut to
        sending a simple request.

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

        reply : Message object
            The reply message received.
        informs : list of Message objects
            A list of the inform messages received.

        """
        use_mid = kwargs.get('use_mid')
        timeout = kwargs.get('timeout')
        msg = katcp.Message.request(request, *args)
        return self.katcp_client.blocking_request(msg, timeout, use_mid)
