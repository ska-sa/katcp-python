
from katcp.txprotocol import TxDeviceServer, ClientKatCP
from katcp.txbase import ProxyKatCP, DeviceHandler
from twisted.trial.unittest import TestCase
from twisted.internet.protocol import ClientCreator
from twisted.internet.defer import Deferred
from katcp import Sensor

timeout = 5

class ExampleDevice(TxDeviceServer):
    def setup_sensors(self):
        self.add_sensor(Sensor(int, "sensor1", "Test sensor 1", "count",
                               [0,10]))

class ExampleProxy(ProxyKatCP):
    def __init__(self, port, finish):
        self.connect_to = port
        ProxyKatCP.__init__(self, 0, '')
        self.finish = finish
    
    def setup_devices(self):
        self.add_device('device', 'localhost', self.connect_to)

    def devices_scan_complete(self):
        self.stop()
        self.finish.callback(None)

class TestTxProxyBase(TestCase):
    def base_test(self, callback):
        def wrapper(*args):
            callback(*args)
            port.stopListening()
        
        d = Deferred()
        port = ExampleDevice(0, '').start()
        self.proxy = ExampleProxy(port.getHost().port, d)
        self.proxy.start()
        d.addCallback(wrapper)
        return d
    
    def test_simplest_proxy(self):
        def callback(_):
            assert len(self.proxy.devices) == 1
            device = self.proxy.devices.values()[0]
            assert 'sensor_list' in device.requests
            assert 'sensor1' in device.sensors

        return self.base_test(callback)

    def test_forwarding_commands(self):
        pass
