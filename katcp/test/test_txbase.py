
from katcp.txprotocol import TxDeviceServer, ClientKatCP
from katcp.txbase import (ProxyKatCP, DeviceHandler, TxDeviceProtocol,
                          )
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
        sensor = Sensor(int, "dev1.sensor1", "Test sensor 1", "count",
                        [0,10])
        sensor._timestamp = 1
        self.add_sensor(sensor)
        sensor2 = Sensor(int, "dev1.sensor2", "Test sensor 2", "count",
                         [0, 10])
        sensor2._timestamp = 0
        self.add_sensor(sensor2)

class ExampleProxy(ProxyKatCP):
    def __init__(self, port, finish):
        self.connect_to = port
        ProxyKatCP.__init__(self, 0, 'localhost')
        self.finish = finish
    
    def setup_devices(self):
        dev2 = DeviceHandler('device2', 'localhost', 1221)
        dev2.connectionMade = lambda *args: None
        self.add_device(dev2)
        self.ready_devices = 1
        self.add_device(DeviceHandler('device', 'localhost', self.connect_to))

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
            devices = [i for i in self.proxy.devices.values() if
                       i.state == i.SYNCED]
            assert len(devices) == 1
            device = devices[0]
            assert 'sensor_list' in device.requests
            assert 'dev1.sensor1' in device.sensors

        return self.base_test(None, callback)

    def test_forwarding_commands(self):
        def callback((informs, reply)):
            self.assertEquals(reply, Message.reply("device-req", "ok", "3"))
        
        return self.base_test(('device-req',), callback)

    def test_forwarding_unsynced(self):
        def callback((informs, reply)):
            self.assertEquals(reply, Message.reply('device2-req', 'fail',
                                                   'Device not synced'))

        return self.base_test(('device2-req',), callback)

    def test_forwarding_sensors(self):
        def callback((informs, reply)):
            self.assertEquals(informs,
                    [Message.inform('sensor-value', '1000', '1', 'dev1.sensor1',
                                    'unknown', '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '1'))
                                                       
        return self.base_test(('sensor-value', 'dev1.sensor1'), callback)

    def test_all_forwarded_sensors(self):
        def callback((informs, reply)):
            self.assertEquals(informs[2:],
                  [Message.inform('sensor-value', '1000', '1', 'dev1.sensor1',
                                  'unknown', '0'),
                   Message.inform('sensor-value', '0', '1', 'dev1.sensor2',
                                  'unknown', '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '4'))

        return self.base_test(('sensor-value',), callback)

    def test_all_forwarded_sensors_regex(self):
        def callback((informs, reply)):
            self.assertEquals(informs,
                  [Message.inform('sensor-value', '1000', '1', 'dev1.sensor1',
                                  'unknown', '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '1'))

        return self.base_test(('sensor-value', '/dev.\.sensor1/'),
                              callback)

    def test_device_list(self):
        def callback((informs, reply)):
            assert len(informs) == 2
            self.assertEquals(reply, Message.reply("device-list", "ok", "2"))
        
        return self.base_test(('device-list',), callback)

    def test_state_sensor(self):
        def callback((informs, reply)):
            assert len(informs) == 1
            assert informs[0].arguments[3:] == ['ok', 'synced']
        
        return self.base_test(('sensor-value', 'device-state',), callback)

    def test_sensor_list(self):
        def callback((informs, reply)):
            xxx
        
        return self.base_test(('sensor-list'), callback)
    test_sensor_list.skip = True
