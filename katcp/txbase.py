
from katcp.txprotocol import TxDeviceServer, ClientKatCP, TxDeviceProtocol
from twisted.internet.defer import Deferred
from twisted.internet import reactor
from twisted.internet.protocol import ClientFactory
from katcp import Message
from katcp.kattypes import request, return_reply, Int

class DeviceHandler(ClientKatCP):
    SYNCING, SYNCED, UNSYNCED = range(3)

    TYPE = 'full'
    
    def __init__(self, name, host, port):
        self.name = name
        self.host = host
        self.port = port
        ClientKatCP.__init__(self)
        self.requests = []
        self.sensors = []
        self.state = self.UNSYNCED
    
    def connectionMade(self):
        """ This is called after connection has been made. Introspect server
        about it's capabilities
        """
        def got_help((informs, reply)):
            for inform in informs:
                self.requests.append(inform.arguments[0])
            self.send_request('sensor-list').addCallback(got_sensor_list)

        def got_sensor_list((informs, reply)):
            self.state = self.SYNCED
            for inform in informs:
                self.sensors.append(inform.arguments[0])
            self.proxy.device_ready(self)

        self.state = self.SYNCING
        self.send_request('help').addCallback(got_help)

class DeviceServer(TxDeviceProtocol):
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
            If the name is not a pattern, list just the sensor with the given name.
            A pattern starts and ends with a slash ('/') and uses the Python re
            module's regular expression syntax. All sensors whose names contain the
            pattern are listed.  The default is to list all sensors.

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
            Additional sensor parameters (type dependent). For integer and float
            sensors the additional parameters are the minimum and maximum sensor
            value. For discrete sensors the additional parameters are the allowed
            values. For all other types no additional parameters are sent.

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
        raise NotImplementedError
        # handle non-regex cases
        if not msg.arguments or not (msg.arguments[0].startswith("/")
            and msg.arguments[0].endswith("/")):
            return TxDeviceServer.request_sensor_list(self, msg)

        # handle regex
        name_re = re.compile(msg.arguments[0][1:-1])
        sensors = dict([(name, sensor) for name, sensor in
            self._sensors.iteritems() if name_re.search(name)])

        for name, sensor in sorted(sensors.items(), key=lambda x: x[0]):
            self.send_message(Message.inform("sensor-list",
                name, sensor.description, sensor.units, sensor.stype,
                *sensor.formatted_params))

        return Message.reply(msg.name, "ok", len(sensors))

    def __getattr__(self, attr):
        def request_returned((informs, reply)):
            assert informs == [] # for now
            # we *could* in theory just change message name, but let's copy
            # just in case
            self.send_message(Message.reply(dev_name + "-" + req_name,
                                            *reply.arguments))
        
        def callback(msg):
            if device.state is device.UNSYNCED:
                return Message.reply(dev_name + "-" + req_name, "fail",
                                     "Device not synced")
            device.send_request(req_name,
                                *msg.arguments).addCallback(request_returned)
        
        if not attr.startswith('request_'):
            return object.__getattribute__(self, attr)
        lst = attr.split('_')
        if len(lst) < 3:
            return object.__getattribute__(self, attr)
        dev_name = lst[1]
        device = self.factory.devices.get(dev_name, None)
        if device is None:
            return object.__getattribute__(self, attr)
        req_name = "_".join(lst[2:])
        return callback

class ClientDeviceFactory(ClientFactory):
    """ A factory that does uses prebuilt device handler objects
    """
    def __init__(self, addr_mapping):
        self.addr_mapping = addr_mapping # shared mapping with ProxyKatCP

    def buildProtocol(self, addr):
        return self.addr_mapping[(addr.host, addr.port)]

class ProxyKatCP(TxDeviceServer):
    """ This is a proxy class that will listen on a given host and port
    providing info about underlaying clients if needed
    """
    protocol = DeviceServer
    
    def __init__(self, *args, **kwds):
        TxDeviceServer.__init__(self, *args, **kwds)
        self.addr_mapping = {}
        self.client_factory = ClientDeviceFactory(self.addr_mapping)
        self.ready_devices = 0
        self.devices = {}
        self.setup_devices()
    
    def device_ready(self, device):
        self.ready_devices += 1
        if self.ready_devices == len(self.devices):
            self.devices_scan_complete()

    def add_device(self, device):
        """ Add a single device to the list of devices that we have
        """
        def really_add_device(addr):
            device.host = addr
            reactor.connectTCP(device.host, device.port, self.client_factory)
            self.devices[device.name] = device
            self.addr_mapping[(device.host, device.port)] = device
            device.proxy = self

        reactor.resolve(device.host).addCallback(really_add_device)

    def devices_scan_complete(self, _):
        """ A callback called when devices are properly set up and read.
        Override if needed
        """
        pass

    def setup_devices(self):
        raise NotImplementedError("Override this to provide devices setup")

    def stop(self):
        for device in self.devices.values():
            if device.state != device.UNSYNCED:
                device.transport.loseConnection(None)
        self.port.stopListening()
