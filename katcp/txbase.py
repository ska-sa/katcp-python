
from katcp.txprotocol import TxDeviceServer, ClientKatCP, TxDeviceProtocol
from twisted.internet.defer import Deferred
from twisted.internet import reactor
from twisted.internet.protocol import ClientCreator
from katcp import Message

unsynced_device = object()

class DeviceHandler(ClientKatCP):
    def __init__(self):
        ClientKatCP.__init__(self)
        self.requests = []
        self.sensors = []
    
    def connectionMade(self):
        """ This is called after connection has been made. Introspect server
        about it's capabilities
        """
        def got_help((informs, reply)):
            for inform in informs:
                self.requests.append(inform.arguments[0])
            self.send_request('sensor-list').addCallback(got_sensor_list)

        def got_sensor_list((informs, reply)):
            for inform in informs:
                self.sensors.append(inform.arguments[0])
            self.proxy.device_ready(self)

        self.send_request('help').addCallback(got_help)

class DeviceServer(TxDeviceProtocol):
    def request_device_list(self):
        pass

    def __getattr__(self, attr):
        def request_returned((informs, reply)):
            assert informs == [] # for now
            # we *could* in theory just change message name, but let's copy
            # just in case
            self.send_message(Message.reply(dev_name + "-" + req_name,
                                            *reply.arguments))
        
        def callback(msg):
            if device is unsynced_device:
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

class ProxyKatCP(TxDeviceServer):
    """ This is a proxy class that will listen on a given host and port
    providing info about underlaying clients if needed
    """
    protocol = DeviceServer
    
    def __init__(self, *args, **kwds):
        TxDeviceServer.__init__(self, *args, **kwds)
        self.cc = ClientCreator(reactor, DeviceHandler)
        self.all_devices = 0
        self.ready_devices = 0
        self.devices = {}
        self.setup_devices()
    
    def device_ready(self, device):
        self.ready_devices += 1
        self.devices[device.name] = device
        if self.ready_devices == self.all_devices:
            self.devices_scan_complete()

    def add_device(self, name, host, port):
        """ Add a single device to the list of devices that we have
        """
        def callback(device_handler):
            device_handler.name = name
            device_handler.port = port
            device_handler.host = host
            device_handler.proxy = self

        self.cc.connectTCP(host, port).addCallback(callback)
        self.devices[name] = unsynced_device
        # device is there, but not yet created
        self.all_devices += 1

    def devices_scan_complete(self, _):
        """ A callback called when devices are properly set up and read.
        Override if needed
        """
        pass

    def setup_devices(self):
        raise NotImplementedError("Override this to provide devices setup")

    def stop(self):
        for device in self.devices.values():
            if device is not unsynced_device:
                device.transport.loseConnection(None)
        self.port.stopListening()
