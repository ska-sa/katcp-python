
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
    def test_simplest_proxy(self):
        def stop_example_device(_):
            assert len(proxy.devices) == 1
            port.stopListening()
        
        d = Deferred()
        port = ExampleDevice(0, '').start()
        proxy = ExampleProxy(port.getHost().port, d)
        proxy.start()
        d.addCallback(stop_example_device)
        return d
