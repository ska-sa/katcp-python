
from katcp.txprotocol import TxDeviceServer, ClientKatCP
from katcp.txbase import ProxyKatCP, DeviceHandler, TxDeviceProtocol
from twisted.trial.unittest import TestCase
from twisted.internet.protocol import ClientCreator
from twisted.internet.defer import Deferred
from katcp import Sensor, Message
from katcp.kattypes import request, return_reply, Int
from twisted.internet import reactor

timeout = 5
#Deferred.debug = True

class ExampleProtocol(TxDeviceProtocol):
    @request(include_msg=True)
    @return_reply(Int(min=0))
    def request_req(self, msg):
        return "ok", 3


class ExampleDevice(TxDeviceServer):
    protocol = ExampleProtocol
    
    def setup_sensors(self):
        self.add_sensor(Sensor(int, "sensor1", "Test sensor 1", "count",
                               [0,10]))

class ExampleProxy(ProxyKatCP):
    def __init__(self, port, finish):
        self.connect_to = port
        ProxyKatCP.__init__(self, 0, 'localhost')
        self.finish = finish
    
    def setup_devices(self):
        self.add_device('device', 'localhost', self.connect_to)

    def devices_scan_complete(self):
        self.finish.callback(None)

class TestTxProxyBase(TestCase):
    def base_test(self, request, callback):
        def devices_scan_complete(_):
            if request is None:
                # we don't want to send any requests, simply call callback and
                # be done
                callback(None)
                port.stopListening()
                self.proxy.stop()
                finish.callback(None)
                return
            cc = ClientCreator(reactor, ClientKatCP)
            host = self.proxy.port.getHost()
            cc.connectTCP('localhost', host.port).addCallback(connected)

        def connected(protocol):
            self.client = protocol
            protocol.send_request(*request).addCallback(wrapper)

        def wrapper(arg):
            callback(arg)
            port.stopListening()
            self.proxy.stop()
            self.client.transport.loseConnection()
            finish.callback(None)

        finish = Deferred()
        d = Deferred()
        port = ExampleDevice(0, '').start()
        self.proxy = ExampleProxy(port.getHost().port, d)
        self.proxy.start()
        d.addCallback(devices_scan_complete)
        return finish
    
    def test_simplest_proxy(self):
        def callback(_):
            assert len(self.proxy.devices) == 1
            device = self.proxy.devices.values()[0]
            assert 'sensor_list' in device.requests
            assert 'sensor1' in device.sensors

        return self.base_test(None, callback)

    def test_forwarding_commands(self):
        def callback((informs, reply)):
            self.assertEquals(reply, Message.reply("device-req", "ok", "3"))
        
        return self.base_test(('device-req',), callback)
