
from katcp.txprotocol import (ClientKatCP, TxDeviceServer, TxDeviceProtocol,
                              run_client)
from katcp import Message, Sensor
from katcp.test.testserver import run_subprocess, PORT, IntSensor, FloatSensor
from twisted.trial.unittest import TestCase
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Factory
from twisted.internet.protocol import ClientCreator
from twisted.internet.base import DelayedCall
DelayedCall.debug = True

import time
import sys, os, re

timeout = 5

class TestKatCP(TestCase):
    """ A tesited test case, run with trial testing

    Also note - don't forget to open a log file:
    tail -F --max-unchanged-stats=0 _trial_temp/test.log
    
    """
    
    def test_server_infrastructure(self):
        def connected(protocol):
            protocol.send_request('halt')

        d, process = run_subprocess(connected, ClientKatCP)
        return d

    def test_version_check(self):
        class TestKatCP(ClientKatCP):
            def inform_build_state(self, args):
                ClientKatCP.inform_build_state(self, args)
                # check that version is already set
                assert self.version == 'device_stub-0.1'
                self.send_request('halt')

        d, process = run_subprocess(None, TestKatCP)
        return d

    def test_help(self):
        def received_help((msgs, reply_msg), protocol):
            assert len(msgs) == 9
            protocol.send_request('halt')
            
        def connected(protocol):
            d = protocol.send_request('help')
            d.addCallback(received_help, protocol)

        d, process = run_subprocess(connected, ClientKatCP)
        return d

    def test_callback_sensor_sampling(self):
        def check(protocol):
            self.assertEquals(len(protocol.status_updates), 30)
            protocol.send_request('halt')
        
        def connected(protocol):
            protocol.send_request('sensor-sampling', 'int_sensor', 'period', 10)
            reactor.callLater(0.3, check, protocol)

        d, process = run_subprocess(connected, TestClientKatCP)
        return d

class TestFactory(TxDeviceServer):
    def setup_sensors(self):
        sensor = Sensor(int, 'int_sensor', 'descr', 'unit',
                        params=[-10, 10])
        sensor._timestamp = 0
        self.add_sensor(sensor)
        sensor = Sensor(float, 'float_sensor', 'descr', 'unit',
                        params=[-3.5, 3.5])
        sensor._timestamp = 1
        self.add_sensor(sensor)

class TestClientKatCP(ClientKatCP):
    def __init__(self, *args, **kwds):
        ClientKatCP.__init__(self, *args, **kwds)
        self.status_updates = []

    def update_sensor_status(self, msg):
        self.status_updates.append(msg)

class TestTxDeviceServer(TestCase):
    def end_test(self, _):
        self.peer = None
        self.finish.callback(None)

    def base_test(self, req, callback, cls=TestFactory,
                  client_cls=TestClientKatCP):
        def wrapper(arg, protocol):
            res = callback(arg, protocol)
            if not res:
                protocol.send_request('halt').addCallback(self.end_test)
        
        def connected(protocol):
            self.client = protocol
            d = protocol.send_request(*req)
            d.addCallback(wrapper, protocol)

        self.factory = cls(0, '127.0.0.1')
        self.factory.start()
        cc = ClientCreator(reactor, client_cls)
        port = self.factory.port
        d = cc.connectTCP(port.getHost().host, port.getHost().port)
        d.addCallback(connected)
        self.finish = Deferred()
        return self.finish
    
    def test_help(self):
        # check how many we really want
        count = 0
        for i in dir(TxDeviceProtocol):
            if i.startswith('request_'):
                count += 1
        
        def help((informs, reply), protocol):
            self.assertEquals(len(informs), count)
            assert 'request' not in informs[0].arguments[0]
            self.assertEquals(reply, Message.reply('help', "ok", str(count)))

        return self.base_test(('help',), help)

    def test_unknown_request(self):
        def got_unknown((args, reply), protocol):
            assert len(args) == 0
            assert reply.arguments[0] == 'invalid'
            assert reply.arguments[1] == 'Unknown request.'

        return self.base_test(('unknown-request',), got_unknown)
        
    def test_run_basic_sensors(self):
        def sensor_value_replied((informs, reply), protocol):
            self.assertEquals(informs, [Message.inform('sensor-value', '0', '1',
                                                       'int_sensor', 'unknown',
                                                       '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '1'))

        return self.base_test(('sensor-value', 'int_sensor'),
                              sensor_value_replied)

    def test_unknown_sensor(self):
        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-value',
                                                   'fail',
                                                   'Unknown sensor name'))

        return self.base_test(('sensor-value', 'xxx'),
                              reply)

    def test_all_sensor_values(self):
        def reply((informs, reply), protocol):
            msg1 = Message.inform('sensor-value', '1000', '1', 'float_sensor',
                                  'unknown', '0')
            msg2 = Message.inform('sensor-value', '0', '1', 'int_sensor',
                                  'unknown', '0')
            self.assertEquals(informs, [msg1, msg2])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '2'))

        return self.base_test(('sensor-value',), reply)

    def test_sensor_list(self):
        def reply((informs, reply), protocol):
            msg1 = Message.inform('sensor-list', 'int_sensor', 'descr', 'unit',
                                  'integer', '-10', '10')
            msg2 = Message.inform('sensor-list', 'float_sensor', 'descr',
                                  'unit', 'float', '-3.5', '3.5')
            self.assertEquals(informs, [msg2, msg1])
            self.assertEquals(reply, Message.reply('sensor-list', 'ok', '2'))

        return self.base_test(('sensor-list',), reply)

    def test_sensor_sampling_no_sensor_name(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-sampling', 'fail',
                                                   'No sensor name given.'))

        return self.base_test(('sensor-sampling',), reply)

    def test_sensor_sampling_wrong_name(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-sampling', 'fail',
                                                   'Unknown sensor name.'))

        return self.base_test(('sensor-sampling', 'xxx'), reply)

    def test_sensor_sampling_wrong_strategy(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-sampling', 'fail',
                                                   'Unknown strategy name.'))

        return self.base_test(('sensor-sampling', 'int_sensor', 'xuz'), reply)

    def test_sensor_sampling_period(self):
        def called_later(protocol):
            # this is necessary to cleanly exit the process so twisted
            # won't complain about leftover delayed calls
            self.assertEquals(len(self.client.status_updates), 30)
            self.client.send_request('sensor-sampling', 'int_sensor',
                                     'none').addCallback(send_halt, protocol)

        def send_halt(_, protocol):
            protocol.send_request('halt').addCallback(self.end_test)
        
        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor', 'period',
                                                   '10'))
            reactor.callLater(0.3, called_later, protocol)
            return True
        
        return self.base_test(('sensor-sampling', 'int_sensor', 'period', '10'),
                              reply)

    def test_sensor_sampling_auto(self):
        def even_more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 2)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '5')])
            protocol.send_request('halt').addCallback(self.end_test)
        
        def more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '3')])
            self.factory.sensors['int_sensor'].set_value(5)
            self.factory.sensors['int_sensor']._timestamp = 0
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(even_more, protocol)

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor', 'auto'))
            self.assertEquals(len(self.client.status_updates), 0)
            
            self.factory.sensors['int_sensor'].set_value(3)
            self.factory.sensors['int_sensor']._timestamp = 0
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(more, protocol)
            return True
        
        return self.base_test(('sensor-sampling', 'int_sensor', 'auto'), reply)


    def test_sensor_sampling_event(self):
        def even_more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '3')])
            protocol.send_request('halt').addCallback(self.end_test)
        
        def more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '3')])
            self.factory.sensors['int_sensor'].set_value(3)
            self.factory.sensors['int_sensor']._timestamp = 0
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(even_more, protocol)

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor', 'event'))
            self.assertEquals(len(self.client.status_updates), 0)
            
            self.factory.sensors['int_sensor'].set_value(3)
            self.factory.sensors['int_sensor']._timestamp = 0
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(more, protocol)
            return True
        
        return self.base_test(('sensor-sampling', 'int_sensor', 'event'), reply)

    def test_sensor_sampling_differential(self):
        def first((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '2')])
            self.factory.sensors['int_sensor'].set_value(5)
            self.factory.sensors['int_sensor']._timestamp = 0
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(second, protocol)

        def second((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '5')])
            self.factory.sensors['int_sensor'].set_value(10)
            self.factory.sensors['int_sensor']._timestamp = 0
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(third, protocol)

        def third((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 2)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '10')])
            protocol.send_request('halt').addCallback(self.end_test)

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor',
                                                   'differential', '3'))
            self.assertEquals(len(self.client.status_updates), 0)
            
            self.factory.sensors['int_sensor'].set_value(2)
            self.factory.sensors['int_sensor']._timestamp = 0
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(first, protocol)
            return True
        
        return self.base_test(('sensor-sampling', 'int_sensor',
                               'differential', '3'), reply)

    def test_raising_traceback(self):
        class FaultyProtocol(TxDeviceProtocol):
            def request_foobar(self, msg):
                raise KeyError
        
        class FaultyFactory(TestFactory):
            protocol = FaultyProtocol

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            assert 'Traceback' in str(reply)

        return self.base_test(('foobar',), reply, cls=FaultyFactory)

class TestMisc(TestCase):
    def test_requests(self):
        from katcp.server import DeviceServer
        for name in dir(DeviceServer):
            if (name.startswith('request_') and
                callable(getattr(DeviceServer, name))):
                assert hasattr(TxDeviceProtocol, name)
    test_requests.skip = True
