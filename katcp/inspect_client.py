from __future__ import print_function
import logging
from collections import namedtuple
from concurrent.futures import Future
import tornado
import katcp

logger = logging.getLogger("katcp.inspect_client")
RequestType = namedtuple('Request', ['name', 'description'])


class KatCPDevice(katcp.AsyncClient):

    def __init__(self, *args, **kwargs):
        super(KatCPDevice, self).__init__(*args, **kwargs)
        self._inform_hooks = {}

    def hook_inform(self, inform_name, callback):
        """Hookup a function to be called when an inform is received.

        eg. Useful for interface-changed and sensor-status informs.

        Parameters
        ----------

        inform_name: str
            The name of the inform.
        callback: function
            The function to be called.

        """
        name = self._slug(inform_name)
        if name not in self._inform_hooks:
            self._inform_hooks[name] = []
        if callback not in self._inform_hooks[name]:
            self._inform_hooks[name].append(callback)

    def unhandled_inform(self, msg):
        """Call a callback on informs."""
        for func in self._inform_hooks.get(self._slug(msg.name), []):
            func(msg)

    def _slug(self, name):
        """Turn a name in to a slug."""
        return str(name).strip().lower().replace('-', '_')


class AsyncSemaphore(katcp.client.AsyncEvent):

    """Extend the AsyncEvent Class to be a semaphore.

    The Semaphore value is used to set the Event, a value of 0 set the event
    any other value will clear the event.

    """

    def __init__(self, val):
        super(AsyncSemaphore, self).__init__()
        self._value = 0
        self.value = val

    def set_bit(self, pos):
        """Set a bit at position."""
        weight = 2 ** int(pos)
        self.value = self._value | weight

    def clear_bit(self, pos):
        """Clear a bit at position."""
        weight = 2 ** int(pos)
        self.value = (self._value | weight) ^ weight

    def inc(self, value=1):
        """Increment the value."""
        self.value = self._value + value

    def dec(self, value=1):
        """Decrement value."""
        self.value = self._value - value

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, val):
        val = int(val)
        self._value = val
        if not self.is_set() and self._value == 0:
            self.set()
        elif self.is_set() and self._value:
            self.clear()


class InspectClientAsync(object):

    def __init__(self, host, port, io_loop=None, full_inspection=None):
        if full_inspection is None:
            full_inspection = True
        self.full_inspection = bool(full_inspection)
        self._requests_index = {}
        self._sensors_index = {}
        # Use AsyncSemaphore a extended AsyncEvent class to keep track of
        # the sync status, request and sensor updates could happen in parallel.
        self._sync = AsyncSemaphore(3)
        # Set the default behaviour for update.
        self._update_on_lookup = True
        self._cb_register = {}  # Register to hold the possible callbacks.

        # Setup KATCP device.
        self.katcp_client = KatCPDevice(host, port)
        if io_loop is False:
            # Called from the blocking client.
            self.ioloop = self.katcp_client.ioloop
        else:
            self.ioloop = io_loop or tornado.ioloop.IOLoop.current()
            self.katcp_client.set_ioloop(io_loop)

        self.katcp_client.hook_inform('sensor-status',
                                      self._cb_inform_sensor_status)
        self.katcp_client.hook_inform('interface-change',
                                      self._cb_inform_interface_change)

    def __del__(self):
        self.close()

    def is_connected(self):
        """Connection status."""
        return self.katcp_client.is_connected()

    @property
    def connected(self):
        """Connection status."""
        return self.katcp_client.is_connected()

    @property
    def synced(self):
        """Boolean indicating if the device has been synchronised."""
        return self._sync.is_set()

    @property
    def sensors(self):
        """A list of known sensors."""
        return self._sensors_index.keys()

    @property
    def requests(self):
        """A list of possible requests."""
        return self._requests_index.keys()

    def set_ioloop(self, ioloop):
        self.katcp_client.set_ioloop(ioloop)

    @tornado.gen.coroutine
    def update_sensor(self, name, timestamp, status, value):
        sensor = yield self.future_get_sensor(name)
        if sensor:
            sensor.set(timestamp, status, sensor.parse_value(value))
        else:
            logger.error('Received update for "%s", but could not create'
                         ' sensor object.' % name)

    def _cb_inform_sensor_status(self, msg):
        """Update received for an sensor."""
        timestamp = float(msg.arguments[0])
        num_sensors = int(msg.arguments[1])
        assert len(msg.arguments) == 2 + num_sensors * 3
        for n in xrange(num_sensors):
            name = msg.arguments[2 + n * 3]
            status = msg.arguments[3 + n * 3]
            value = msg.arguments[4 + n * 3]
            self.ioloop.add_callback(self.update_sensor,
                                     name, timestamp, status, value)

    def _cb_inform_interface_change(self, msg):
        """Update the sensors and requests available."""
        if self.full_inspection:
            self.ioloop.add_callback(self.inspect)
        else:
            # TODO(MS): Look inside msg and update only what is required.
            self.ioloop.add_callback(self.inspect)

    @tornado.gen.coroutine
    def inspect(self):
        yield self.inspect_requests()
        yield self.inspect_sensors()

    @tornado.gen.coroutine
    def connect(self):
        # TODO(MS): Add a time out for the connection.
        # Start KATCP device.
        self.katcp_client.start()
        yield self.katcp_client.until_running()
        yield self.until_connected()
        if self.full_inspection:
            self.ioloop.add_callback(self.inspect)
        else:
            # set synced true.
            self._sync.value = 0
            self._sync.set()

    def until_connected(self):
        return self.katcp_client.until_protocol()

    def until_synced(self):
        return self._sync.until_set()

    @tornado.gen.coroutine
    def inspect_requests(self, name=None):
        """Inspect all or one requests on the device.

        Parameters
        ----------

        name: optional str
            Name of the sensor or None to get all requests.

        """
        yield self.until_connected()
        self._sync.set_bit(0)  # Set flag for request syncing.
        if name is None:
            msg = katcp.Message.request('help')
        else:
            msg = katcp.Message.request('help', name)
        reply, informs = yield self.katcp_client.future_request(msg)
        requests_old = set(self._requests_index.keys())
        for msg in informs:
            name = msg.arguments[0]
            sen = {'description': msg.arguments[1]}
            if name not in self._requests_index:
                self._requests_index[name] = sen
            else:
                self._requests_index[name].update(sen)

        requests_updated = set(self._requests_index.keys())
        requests_added = requests_updated.difference(requests_old)
        if name is None:
            requests_removed = requests_old.difference(requests_updated)
        elif name not in requests_updated:
            requests_removed = set([name])
        else:
            requests_removed = set()

        if requests_added and self._cb_register.get('request_added'):
            self.ioloop.add_callback(self._cb_register.get('request_added'),
                                     list(requests_added))
        if requests_removed and self._cb_register.get('request_removed'):
            self.ioloop.add_callback(self._cb_register.get('request_removed'),
                                     list(requests_removed))
        self._sync.clear_bit(0)  # Clear flag for request syncing.

    @tornado.gen.coroutine
    def inspect_sensors(self, name=None):
        """Inspect all or one sensor on the device.

        Parameters
        ----------

        name: optional str
            Name of the sensor or None to get all sensors.

        """
        yield self.until_connected()
        self._sync.set_bit(1)  # Set flag for sensor syncing.
        if name is None:
            msg = katcp.Message.request('sensor-list')
        else:
            msg = katcp.Message.request('sensor-list', name)
        reply, informs = yield self.katcp_client.future_request(msg)
        sensors_old = set(self._sensors_index.keys())
        for msg in informs:
            name = msg.arguments[0]
            sen = {'description': msg.arguments[1],
                   'unit': msg.arguments[2],
                   'sensor_type': msg.arguments[3]}
            if name not in self._sensors_index:
                self._sensors_index[name] = sen
            else:
                self._sensors_index[name].update(sen)

        sensors_updated = set(self._sensors_index.keys())
        sensors_added = sensors_updated.difference(sensors_old)
        if name is None:
            sensors_removed = sensors_old.difference(sensors_updated)
        elif name not in sensors_updated:
            sensors_removed = set([name])
        else:
            sensors_removed = set()

        if sensors_added and self._cb_register.get('sensor_added'):
            self.ioloop.add_callback(self._cb_register.get('sensor_added'),
                                     list(sensors_added))
        if sensors_removed and self._cb_register.get('sensor_removed'):
            self.ioloop.add_callback(self._cb_register.get('sensor_removed'),
                                     list(sensors_removed))
        self._sync.clear_bit(1)  # Clear the flag for sensor syncing.

    @tornado.gen.coroutine
    def future_check_sensor(self, name, update=None):
        """Check if the sensor exists.

        Used internally by get_sensor. This method is aware of synchronisation
        in progress and if inspection of the server is allowed.

        Parameters
        ----------
        name: str
            Name of the sensor to verify.
        update: bool
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

        name: String
            Name of the sensor.

        update: Optional Boolean
            True allow inspect client to inspect katcp server if the sensor
            is not known.

        Returns
        -------

        Sensor NameTuple or None if sensor could not be found.

        """
        obj = None
        exist = yield self.future_check_sensor(name, update)
        if exist:
            sensor_info = self._sensors_index[name]
            obj = sensor_info.get('obj')
            if obj is None:
                sensor_type = katcp.Sensor.parse_type(
                    sensor_info.get('sensor_type'))
                obj = katcp.Sensor(name=name,
                                   sensor_type=sensor_type,
                                   description=sensor_info.get('description'),
                                   units=sensor_info.get('units'))
                self._sensors_index[name]['obj'] = obj

        raise tornado.gen.Return(obj)

    @tornado.gen.coroutine
    def future_check_request(self, name, update=None):
        """Check if the request exists.

        Used internally by get_request. This method is aware of synchronisation
        in progress and if inspection of the server is allowed.

        Parameters
        ----------

        name: str
            Name of the request to verify.

        update: bool default to None.
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
        and update (if allowed) to get information.

        Parameters
        ----------

        name: String
            Name of the request.

        update: Optional Boolean
            True allow inspect client to inspect katcp server if the request
            is not known.

        Returns
        -------

        Request NameTuple or None if request could not be found.

        """
        obj = None
        exist = yield self.future_check_request(name, update)
        if exist:
            request_info = self._requests_index[name]
            obj = request_info.get('obj')
            if obj is None:
                obj = RequestType(name,
                                  request_info.get('description', ''))
                self._requests_index[name]['obj'] = obj

        raise tornado.gen.Return(obj)

    def close(self):
        if self.katcp_client.is_connected() or self.katcp_client.running():
            self.katcp_client.stop()
            self.katcp_client.join()

    def set_sensor_added_callback(self, callback):
        """Set the Callback to be called when a new sensor is added.

        Parameters
        ----------

        callback: function
            reference to the function/method to be called.

        """
        self._cb_register['sensor_added'] = callback

    def set_sensor_removed_callback(self, callback):
        """Set the Callback to be called when a new sensor is added.

        Parameters
        ----------

        callback: function
            reference to the function/method to be called.

        """
        self._cb_register['sensor_removed'] = callback

    def set_request_added_callback(self, callback):
        """Set the Callback to be called when a new sensor is added.

        Parameters
        ----------

        callback: function
            reference to the function/method to be called.

        """
        self._cb_register['request_added'] = callback

    def set_request_removed_callback(self, callback):
        """Set the Callback to be called when a new sensor is added.

        Parameters
        ----------

        callback: function
            reference to the function/method to be called.

        """
        self._cb_register['request_removed'] = callback

    def simple_request(self, request, *args, **kwargs):
        use_mid = kwargs.get('use_mid')
        timeout = kwargs.get('timeout')
        msg = katcp.Message.request(request, *args)
        return self.katcp_client.future_request(msg, timeout, use_mid)


class InspectClientBlocking(InspectClientAsync):

    def __init__(self, host, port, full_inspection=None):
        super(InspectClientBlocking, self).__init__(
            host, port, False, full_inspection)

    def connect(self):
        """Connect to the KATCP device."""
        self.katcp_client.start()
        self.katcp_client.wait_running()
        self.katcp_client.wait_protocol()
        self.ioloop = self.katcp_client.ioloop
        if self.full_inspection:
            self.ioloop.add_callback(self.inspect)
        else:
            # set synced true.
            self._sync.set()

    def get_sensor(self, name, update=True):
        """Get the sensor object.

        Check if we have information for this sensor, if not connect to server
        and update (if allowed) to get information.

        Parameters
        ----------

        name: String
            Name of the sensor.

        update: Optional Boolean
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
        timeout : float in seconds
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
        return self._sync.wait_with_ioloop(ioloop, timeout)

    def simple_request(self, request, *args, **kwargs):
        use_mid = kwargs.get('use_mid')
        timeout = kwargs.get('timeout')
        msg = katcp.Message.request(request, *args)
        return self.katcp_client.blocking_request(msg, timeout, use_mid)
#
