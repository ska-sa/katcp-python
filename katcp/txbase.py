
from katcp.txprotocol import TxDeviceServer, ClientKatCP
from twisted.internet.defer import Deferred
from twisted.internet import reactor
from twisted.internet.protocol import ClientCreator

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

class ProxyKatCP(TxDeviceServer):
    """ This is a proxy class that will listen on a given host and port
    providing info about underlaying clients if needed
    """
    def __init__(self, *args, **kwds):
        TxDeviceServer.__init__(self, *args, **kwds)
        self.cc = ClientCreator(reactor, DeviceHandler)
        self.all_devices = 0
        self.ready_devices = 0
        self.setup_devices()
        self.devices = []
    
    def start(self):
        TxDeviceServer.start(self)

    def device_ready(self, device):
        self.ready_devices += 1
        self.devices.append(device)
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
        self.all_devices += 1

    def devices_scan_complete(self, _):
        """ A callback called when devices are properly set up and read.
        Override if needed
        """
        pass

    def setup_devices(self):
        raise NotImplementedError("Override this to provide devices setup")

    def stop(self):
        for device in self.devices:
            device.transport.loseConnection(None)
        self.port.stopListening()
