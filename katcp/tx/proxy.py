
from katcp.tx.core import DeviceServer, ClientKatCPProtocol, DeviceProtocol
from twisted.internet.defer import DeferredList
from twisted.internet import reactor
from twisted.internet.protocol import ClientFactory
from twisted.python import log
from katcp import Message, AsyncReply, Sensor
from katcp.kattypes import request, return_reply, Int

import re
import time


def value_only_formatted(func):
    """ A decorator that changes a value-only read into read_formatted
    format (using time.time and 'ok')
    """
    def new_func(self):
        return time.time(), "ok", func(self)
    new_func.func_name = func.func_name
    return new_func


class ProxiedSensor(Sensor):
    """ A sensor which is a proxy for other sensor on the remote device.
    Returns a deferred on read
    """
    def __init__(self, name, description, units, stype, device, proxy,
                 *formatted_params):
        self.basename = name
        self.device = device
        stype = Sensor.parse_type(stype)
        params = Sensor.parse_params(stype, formatted_params)
        Sensor.__init__(self, stype, device.name + '.' + name, description,
                        units, params=params)

    def read_formatted(self):
        return self.device.send_request('sensor-value', self.basename)


class StateSensor(object):
    """ A device state sensor
    """
    description = 'connection state'
    stype = 'discrete'
    formatted_params = ('unsynced', 'syncing', 'synced')
    units = ''

    def __init__(self, name, device):
        self.device = device
        self.name = name

    @value_only_formatted
    def read_formatted(self):
        return DeviceHandler.STATE_NAMES[self.device.state]


class DeviceHandler(ClientKatCPProtocol):
    SYNCING, SYNCED, UNSYNCED = range(3)
    STATE_NAMES = ['syncing', 'synced', 'unsynced']

    TYPE = 'full'

    stopping = False

    _conn_counter = 0

    SensorClass = ProxiedSensor

    def __init__(self, name, host, port):
        self.name = name
        self.host = host
        self.port = port
        ClientKatCPProtocol.__init__(self)
        self.requests = []
        self.sensors = {}
        self.state = self.UNSYNCED

    def _got_help(self, (informs, reply)):
        for inform in informs:
            self.requests.append(inform.arguments[0])
        self.send_request('sensor-list').addCallback(self._got_sensor_list)

    def _got_sensor_list(self, (informs, reply)):
        self.state = self.SYNCED
        for inform in informs:
            name, description, units, stype = inform.arguments[:4]
            formatted_arguments = inform.arguments[4:]
            sensor = self.SensorClass(name, description,
                                      units, stype,
                                      self, self.proxy, *formatted_arguments)
            self.sensors[name] = sensor
            self.proxy.add_proxied_sensor(self, sensor)
        self.device_ready()
        self.proxy.device_ready(self)

    def device_ready(self):
        """ Another hook that can be overloaded if you want to execute
        code just after device has been synced
        """
        pass

    def connectionMade(self):
        """ This is called after connection has been made. Introspect server
        about it's capabilities
        """
        self.state = self.SYNCING
        self.send_request('help').addCallback(self._got_help)
        self._conn_counter = 0

    def add_proxy(self, proxy):
        self.proxy = proxy
        proxy.add_sensor(StateSensor(self.name + '-' + 'state', self))

    def schedule_resyncing(self):
        reactor.connectTCP(self.host, self.port, self.proxy.client_factory)

    def connectionLost(self, failure):
        self.state = self.UNSYNCED
        ClientKatCPProtocol.connectionLost(self, failure)
        if not self.stopping:
            reactor.callLater(self.proxy.CONN_DELAY_TIMEOUT,
                              self.schedule_resyncing)

    def stop(self):
        """ A hook for stopping requested device, if necessary, does nothing
        by default
        """
        pass

    def inform_sensor_status(self, msg):
        try:
            sensor = self.sensors[msg.arguments[2]]
            sensor.set_formatted(msg.arguments[0], msg.arguments[3],
                                 msg.arguments[4])
        except:
            log.err()


class ProxyProtocol(DeviceProtocol):
    @request(include_msg=True)
    @return_reply(Int(min=0))
    def request_device_list(self, reqmsg):
        """Return a list of devices aggregated by the proxy.

        Returns the list of devices a sequence of #device-list informs.

        Inform Arguments
        ----------------
        device : str
            Name of a device.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the list of devices succeeded.
        informs : int
            Number of #device-list informs sent.

        Examples
        --------
        ?device-list
        #device-list antenna
        #device-list enviro
        !device-list ok 2
        """
        for name in sorted(self.factory.devices):
            self.send_message(Message.inform("device-list", name,
                              self.factory.devices[name].TYPE))
        return "ok", len(self.factory.devices)

    def request_sensor_list(self, msg):
        """Request the list of sensors.

        The list of sensors is sent as a sequence of #sensor-list informs.

        Parameters
        ----------
        name : str or pattern, optional
            If the name is not a pattern, list just the sensor with the given
            name. A pattern starts and ends with a slash ('/') and uses the
            Python re module's regular expression syntax. All sensors whose
            names contain the pattern are listed.  The default is to list all
            sensors.

        Inform Arguments
        ----------------
        name : str
            The name of the sensor being described.
        description : str
            Description of the named sensor.
        units : str
            Units for the value of the named sensor.
        type : str
            Type of the named sensor.
        params : list of str, optional
            Additional sensor parameters (type dependent). For integer and
            float sensors the additional parameters are the minimum and maximum
            sensor value. For discrete sensors the additional parameters are
            the allowed values. For all other types no additional parameters
            are sent.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the sensor list succeeded.
        informs : int
            Number of #sensor-list inform messages sent.

        Examples
        --------
        ?sensor-list
        #sensor-list psu.voltage PSU\_voltage. V float 0.0 5.0
        #sensor-list cpu.status CPU\_status. \@ discrete on off error
        ...
        !sensor-list ok 5

        ?sensor-list /voltage/
        #sensor-list psu.voltage PSU\_voltage. V float 0.0 5.0
        #sensor-list cpu.voltage CPU\_voltage. V float 0.0 3.0
        !sensor-list ok 2

        ?sensor-list cpu.power.on
        #sensor-list cpu.power.on Whether\_CPU\_hase\_power. \@ boolean
        !sensor-list ok 1
        """
        # handle non-regex cases
        if not msg.arguments or not (msg.arguments[0].startswith("/")
            and msg.arguments[0].endswith("/")):
            return DeviceProtocol.request_sensor_list(self, msg)

        # handle regex
        name_re = re.compile(msg.arguments[0][1:-1])
        sensors = dict([(name, sensor) for name, sensor in
            self.factory.sensors.iteritems() if name_re.search(name)])

        for name, sensor in sorted(sensors.items(), key=lambda x: x[0]):
            self.send_message(Message.inform("sensor-list",
                name, sensor.description, sensor.units, sensor.stype,
                *sensor.formatted_params))

        return Message.reply(msg.name, "ok", len(sensors))

    def _send_all_sensors(self, filter=None):
        """ Sends all sensor values with given filter (None = all)
        """
        counter = [0]  # this has to be a list or an object, thanks to
        # python lexical scoping rules (we could not write count += 1
        # in a function)

        def device_ok((informs, reply), device):
            for inform in informs:
                inform.arguments[2] = device.name + '.' + \
                                      inform.arguments[2]
                if filter is None or re.match(filter, inform.arguments[2]):
                    self.send_message(inform)
                    counter[0] += 1

        def all_ok(_):
            self.send_message(Message.reply('sensor-value', 'ok',
                                            str(counter[0])))

        wait_for = []
        for device in self.factory.devices.itervalues():
            if device.state == device.SYNCED:
                d = device.send_request('sensor-value')
                d.addCallback(device_ok, device)
                wait_for.append(d)
            # otherwise we don't have the list of sensors, so we don't
            # send the message
        DeferredList(wait_for).addCallback(all_ok)
        for name, sensor in self.factory.sensors.iteritems():
            if not isinstance(sensor, ProxiedSensor):
                if filter is None or re.match(filter, name):
                    timestamp_ms, status, value = sensor.read_formatted()
                    counter[0] += 1
                    self.send_message(Message.inform('sensor-value',
                                                     timestamp_ms, "1",
                                                     name, status,
                                                     value))

    def request_sensor_value(self, msg):
        """Poll a sensor value or value(s).

        A list of sensor values as a sequence of #sensor-value informs.

        Parameters
        ----------
        name : str or pattern, optional
            If the name is not a pattern, list just the values of sensors with
            the given name.  A pattern starts and ends with a slash ('/') and
            uses the Python re module's regular expression syntax. The values
            of all sensors whose names contain the pattern are listed.  The
            default is to list the values of all sensors.

        Inform Arguments
        ----------------
        timestamp : float
            Timestamp of the sensor reading in milliseconds since the Unix
            epoch.
        count : {1}
            Number of sensors described in this #sensor-value inform. Will
            always be one. It exists to keep this inform compatible with
            #sensor-status.
        name : str
            Name of the sensor whose value is being reported.
        value : object
            Value of the named sensor. Type depends on the type of the sensor.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the list of values succeeded.
        informs : int
            Number of #sensor-value inform messages sent.

        Examples
        --------
        ?sensor-value
        #sensor-value 1244631611415.231 1 psu.voltage 4.5
        #sensor-value 1244631611415.200 1 cpu.status off
        ...
        !sensor-value ok 5

        ?sensor-value /voltage/
        #sensor-value 1244631611415.231 1 psu.voltage 4.5
        #sensor-value 1244631611415.100 1 cpu.voltage 4.5
        !sensor-value ok 2

        ?sensor-value cpu.power.on
        #sensor-value 1244631611415.231 1 cpu.power.on 0
        !sensor-value ok 1
        """
        if not msg.arguments:
            self._send_all_sensors()
            raise AsyncReply()
        name = msg.arguments[0]
        if len(name) >= 2 and name.startswith("/") and name.endswith("/"):
            # regex case
            self._send_all_sensors(name[1:-1])
            raise AsyncReply()
        else:
            return DeviceProtocol.request_sensor_value(self, msg)

    def request_halt(self, msg):
        """ drops connection to specified device
        """
        def got_halt((informs, reply)):
            self.send_message(Message.reply('halt', dev_name, 'ok'))

        if not msg.arguments:
            return DeviceProtocol.halt(self, msg)
        try:
            dev_name = msg.arguments[0]
            device = self.factory.devices[dev_name]
        except KeyError:
            return Message.reply('halt', 'fail',
                                 'Unknown device %s' % dev_name)
        else:
            self.factory.unregister_device(dev_name)
            device.send_request('halt').addCallback(got_halt)
            raise AsyncReply()

    def __getattr__(self, attr):
        # TODO: It would be cleaner if a new request method / callback
        # wasn't created for every proxied request.
        # TODO: These proxied methods should appear in the ?help for the proxy
        # but currently don't.

        def request_returned((informs, reply)):
            assert informs == []  # for now
            # we *could* in theory just change message name, but let's copy
            # just in case
            self.send_message(Message.reply(dev_name + "-" + req_name,
                                            *reply.arguments))

        def request_failed(failure):
            self.send_message(Message.reply(dev_name + '-' + req_name,
                                            "fail", failure.getErrorMessage()))

        def callback(msg):
            if device.state is device.UNSYNCED:
                return Message.reply(dev_name + "-" + req_name, "fail",
                                     "Device not synced")
            d = device.send_request(req_name, *msg.arguments)
            d.addCallbacks(request_returned, request_failed)
            raise AsyncReply()

        if not attr.startswith('request_'):
            return object.__getattribute__(self, attr)
        lst = attr.split('_', 2)
        if len(lst) < 3:
            return object.__getattribute__(self, attr)
        dev_name, req_name = lst[1], lst[2]
        device = self.factory.devices.get(dev_name, None)
        if device is None:
            return object.__getattribute__(self, attr)
        return callback


class ClientDeviceFactory(ClientFactory):
    """ A factory that does uses prebuilt device handler objects
    """
    def __init__(self, addr_mapping, max_reconnects, conn_delay_timeout,
                 proxy):
        self.addr_mapping = addr_mapping  # shared dict with proxy
        self.max_reconnects = max_reconnects
        self.conn_delay_timeout = conn_delay_timeout
        self.proxy = proxy

    def clientConnectionFailed(self, connector, reason):
        addr = connector.host, connector.port
        device = self.addr_mapping[addr]
        if device._conn_counter < self.max_reconnects:
            device._conn_counter += 1
            reactor.callLater(self.conn_delay_timeout,
                              device.schedule_resyncing)
        else:
            self.proxy.devices_scan_failed()

    def buildProtocol(self, addr):
        return self.addr_mapping[(addr.host, addr.port)]


class ProxyKatCP(DeviceServer):
    """ This is a proxy class that will listen on a given host and port
    providing info about underlaying clients if needed
    """
    protocol = ProxyProtocol

    MAX_RECONNECTS = 10
    CONN_DELAY_TIMEOUT = 1

    def __init__(self, *args, **kwds):
        DeviceServer.__init__(self, *args, **kwds)
        self.addr_mapping = {}
        self.client_factory = ClientDeviceFactory(self.addr_mapping,
                                                  self.MAX_RECONNECTS,
                                                  self.CONN_DELAY_TIMEOUT,
                                                  self)
        self.ready_devices = 0
        self.devices = {}
        self.setup_devices()
        self.scan_called = False

    def device_ready(self, device):
        self.ready_devices += 1
        if self.ready_devices == len(self.devices) and not self.scan_called:
            self.scan_called = True  # one shot only
            self.devices_scan_complete()

    def add_device(self, device):
        """ Add a single device to the list of devices that we have
        """
        def really_add_device(addr):
            device.host = addr
            reactor.connectTCP(device.host, device.port, self.client_factory)
            self.devices[device.name] = device
            self.addr_mapping[(device.host, device.port)] = device
            device.add_proxy(self)

        reactor.resolve(device.host).addCallback(really_add_device)

    def add_proxied_sensor(self, device, sensor):
        self.sensors[sensor.name] = sensor

    def devices_scan_complete(self):
        """ A callback called when devices are properly set up and read.
        Override if needed
        """
        pass

    def devices_scan_failed(self):
        """ A callback called when device setup failed to connect after
        repeated tries
        """
        pass

    def setup_devices(self):
        raise NotImplementedError("Override this to provide devices setup")

    def stop(self):
        for device in self.devices.values():
            if device.state != device.UNSYNCED:
                device.stopping = True
                device.stop()
                device.transport.loseConnection()
        DeviceServer.stop(self)

    def unregister_device(self, dev_name):
        device = self.devices[dev_name]
        device.stopping = True
        for name in self.sensors.keys():
            if (name.startswith(dev_name + '.') or
                name.startswith(dev_name + '-')):
                del self.sensors[name]
        del self.devices[dev_name]
