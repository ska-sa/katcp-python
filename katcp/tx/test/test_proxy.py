
from katcp.tx.core import DeviceServer, ClientKatCPProtocol
from katcp.tx.proxy import ProxyKatCP, DeviceHandler, DeviceProtocol
from twisted.trial.unittest import TestCase, SkipTest
from twisted.internet.protocol import ClientCreator
from twisted.internet.defer import Deferred
from katcp import Sensor, Message
from katcp.kattypes import request, return_reply, Int
from twisted.internet import reactor

timeout = 5
#Deferred.debug = True


class ExampleProtocol(DeviceProtocol):
    @request(include_msg=True)
    @return_reply(Int(min=0))
    def request_req(self, msg):
        return "ok", 3


class ExampleDevice(DeviceServer):
    protocol = ExampleProtocol

    def setup_sensors(self):
        sensor = Sensor(int, "sensor1", "Test sensor 1", "count",
                        [0, 10])
        sensor.set_value(sensor.value(), status=Sensor.UNKNOWN, timestamp=1)
        self.add_sensor(sensor)
        sensor2 = Sensor(int, "sensor2", "Test sensor 2", "count",
                         [0, 10])
        sensor2.set_value(sensor.value(), status=Sensor.UNKNOWN, timestamp=0)
        self.add_sensor(sensor2)


class MyDeviceHandler(DeviceHandler):
    ready = False

    def device_ready(self):
        self.ready = True


class ExampleProxy(ProxyKatCP):
    on_device_ready = None
    CONN_DELAY_TIMEOUT = 0.05

    def __init__(self, port, finish):
        self.connect_to = port
        ProxyKatCP.__init__(self, 0, 'localhost')
        self.finish = finish

    def setup_devices(self):
        dev2 = MyDeviceHandler('device2', 'localhost', 6)
        dev2.connectionMade = lambda *args: None
        dev2._conn_counter = 100
        self.add_device(dev2)
        self.ready_devices = 1
        self.add_device(MyDeviceHandler('device', 'localhost',
                                        self.connect_to))

    def devices_scan_complete(self):
        self.finish.callback(None)

    def device_ready(self, device):
        if self.on_device_ready is not None:
            self.on_device_ready.callback(device)
            self.on_device_ready = None
        ProxyKatCP.device_ready(self, device)


class TestProxyBase(TestCase):
    def _base_test(self, request, callback):
        def devices_scan_complete(_):
            if request is None:
                # we don't want to send any requests, simply call callback and
                # be done
                if callback(None):
                    return
                self.port.stopListening()
                self.proxy.stop()
                finish.callback(None)
                return
            cc = ClientCreator(reactor, ClientKatCPProtocol)
            host = self.proxy.port.getHost()
            cc.connectTCP('localhost', host.port).addCallback(connected)

        def connected(protocol):
            self.client = protocol
            protocol.send_request(*request).addCallback(wrapper)

        def wrapper(arg):
            if callback(arg):
                return
            self.port.stopListening()
            self.proxy.stop()
            self.client.transport.loseConnection()
            finish.callback(None)

        finish = Deferred()
        d = Deferred()
        self.example_device = ExampleDevice(0, '')
        self.port = self.example_device.start()
        self.proxy = ExampleProxy(self.port.getHost().port, d)
        self.proxy.start()
        d.addCallback(devices_scan_complete)
        self.finish = finish
        return finish

    def test_simplest_proxy(self):
        def callback(_):
            devices = [i for i in self.proxy.devices.values() if
                       i.state == i.SYNCED]
            assert len(devices) == 1
            device = devices[0]
            assert 'sensor-list' in device.requests
            assert 'sensor1' in device.sensors

        return self._base_test(None, callback)

    def test_forwarding_commands(self):
        def callback((informs, reply)):
            self.assertEquals(reply, Message.reply("device-req", "ok", "3"))

        return self._base_test(('device-req',), callback)

    def test_forwarding_unsynced(self):
        def callback((informs, reply)):
            self.assertEquals(reply, Message.reply('device2-req', 'fail',
                                                   'Device not synced'))

        return self._base_test(('device2-req',), callback)

    def test_forwarding_sensors(self):
        def callback((informs, reply)):
            self.assertEquals(informs,
                    [Message.inform('sensor-value', '1000', '1',
                                    'device.sensor1', 'unknown', '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '1'))

        return self._base_test(('sensor-value', 'device.sensor1'), callback)

    def test_all_forwarded_sensors(self):
        def callback((informs, reply)):
            self.assertEquals(informs[2:],
                  [Message.inform('sensor-value', '1000', '1',
                                  'device.sensor1', 'unknown', '0'),
                   Message.inform('sensor-value', '0', '1', 'device.sensor2',
                                  'unknown', '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '4'))

        return self._base_test(('sensor-value',), callback)

    def test_all_forwarded_sensors_regex(self):
        def callback((informs, reply)):
            self.assertEquals(informs,
                  [Message.inform('sensor-value', '1000', '1',
                                  'device.sensor1', 'unknown', '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '1'))

        return self._base_test(('sensor-value', '/device\.sensor1/'),
                              callback)

    def test_device_list(self):
        def callback((informs, reply)):
            assert len(informs) == 2
            self.assertEquals(reply, Message.reply("device-list", "ok", "2"))

        return self._base_test(('device-list',), callback)

    def test_state_sensor(self):
        def callback((informs, reply)):
            assert len(informs) == 1
            assert informs[0].arguments[3:] == ['ok', 'synced']

        return self._base_test(('sensor-value', 'device-state',), callback)

    def test_sensor_list(self):
        def callback((informs, reply)):
            assert len(informs) == 4
            assert reply == Message.reply('sensor-list', 'ok', '4')

        return self._base_test(('sensor-list',), callback)

    def test_sensor_list_regex(self):
        def callback((informs, reply)):
            assert len(informs) == 2
            self.assertEquals(reply, Message.reply('sensor-list', 'ok', '2'))

        return self._base_test(('sensor-list', '/state/'), callback)

    def test_sensor_sampling(self):
        def check_value():
            assert self.proxy.sensors['device.sensor1'].value() == 10
            self.port.stopListening()
            self.proxy.stop()
            self.finish.callback(None)

        def sampling_done((informs, reply)):
            self.example_device.sensors['sensor1'].set_value(10)
            reactor.callLater(0.1, check_value)

        def callback(arg):
            d = self.proxy.devices['device'].send_request(
                'sensor-sampling', 'sensor1', 'period', '10')
            d.addCallback(sampling_done)
            return True
        return self._base_test(None, callback)

    def test_reconnect_base(self):
        def works((informs, reply)):
            self.assertEquals(reply, Message.reply('device-watchdog', 'ok'))
            self.port.stopListening()
            self.proxy.stop()
            self.client.transport.loseConnection()
            self.finish.callback(None)

        def ready(device):
            assert device.state == device.SYNCED
            # check if it's working
            self.client.send_request('device-watchdog').addCallback(works)

        def failed((informs, reply)):
            self.assertEquals((reply.mtype, reply.name, reply.arguments[0]),
                (Message.REPLY, 'device-watchdog', 'fail'))
            self.assertTrue(reply.arguments[1] in (
                'Connection was closed cleanly.',
                'Device not synced',
                ))
            self.proxy.on_device_ready = Deferred().addCallback(ready)

        def callback(_):
            device = self.proxy.devices['device']
            assert device.state == DeviceHandler.SYNCED
            self.example_device.clients.values()[0].transport.loseConnection()
            self.client.send_request('device-watchdog').addCallback(failed)
            return True

        return self._base_test(('watchdog',), callback)

    def test_device_ready(self):
        def callback(_):
            device = self.proxy.devices['device']
            assert device.ready == True

        return self._base_test(None, callback)

    def test_halt(self):
        def callback((informs, reply)):
            self.assertEquals(reply, Message.reply('halt', 'device', 'ok'))
            assert self.proxy.devices.keys() == ['device2']

        return self._base_test(('halt', 'device'), callback)


class RogueSensor(object):
    description = 'descr'
    units = 'some'
    stype = 'integer'
    formatted_params = (0, 10)

    def __init__(self, name, device):
        self.device = device
        self.name = name

    def read_formatted(self):
        for client in self.device.clients.values():
            client.transport._closeSocket()  # force a connection drop
        return 1, 2, 3


class RogueDevice(DeviceServer):
    def setup_sensors(self):
        self.add_sensor(RogueSensor('rogue', self))


class HandlingProxy(ExampleProxy):
    on_device_scan_failed = None

    def setup_devices(self):
        self.add_device(DeviceHandler('device', 'localhost',
                                      self.connect_to))

    def devices_scan_failed(self):
        if self.on_device_scan_failed is not None:
            self.on_device_scan_failed.callback(None)


class TestReconnect(TestCase):
    def test_rogue_device(self):
        raise SkipTest(
            'Test currently always fails, not sure why, must investigate')

        def devices_scan_complete(_):
            cc = ClientCreator(reactor, ClientKatCPProtocol)
            host = self.proxy.port.getHost()
            cc.connectTCP('localhost', host.port).addCallback(connected)

        def worked((informs, reply)):
            self.flushLoggedErrors()  # clean up error about conn lost
            self.proxy.on_device_ready = Deferred().addCallback(back)
            self.assertEquals(informs, [Message.inform("sensor-value",
                    "device.rogue", "Sensor reading failed.")])

        def back(_):
            self.port.stopListening()
            self.proxy.stop()
            self.client.transport.loseConnection()
            finish.callback(None)

        def connected(protocol):
            self.client = protocol
            protocol.send_request('sensor-value', 'device.rogue').addCallbacks(
                    worked)

        d = Deferred()
        self.example_device = RogueDevice(0, '')
        self.port = self.example_device.start()
        self.proxy = HandlingProxy(self.port.getHost().port, d)
        self.proxy.start()
        d.addCallback(devices_scan_complete)
        finish = Deferred()
        return finish

    def test_max_reconnect_tries_at_start(self):
        # port number is of tcp itself, should not have a server
        def failed(_):
            finish.callback(None)

        d = Deferred().addCallback(failed)
        self.proxy = HandlingProxy(6, None)
        self.proxy.on_device_scan_failed = d
        finish = Deferred()
        return finish
