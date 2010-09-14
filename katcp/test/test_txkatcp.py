
from katcp.txprotocol import (ClientKatCP, ServerKatCP, ProxyKatCP,
                              ServerFactory, run_client)
from katcp import Message, Sensor
from katcp.test.testserver import run_subprocess, PORT
from twisted.trial.unittest import TestCase
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Factory
from twisted.internet.protocol import ClientCreator

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

    def test_server_introspection(self):
        def connected(protocol):
            assert len(protocol.sensors) == 2
            protocol.send_request('halt')
        
        d, process = run_subprocess(connected, ProxyKatCP)
        return d

class ServerProtocol(ServerKatCP):
    def request_help(self, msg):
        self.send_message(Message(Message.REPLY,
                                  msg.name,
                                  ["ok"]))

class TestFactory(ServerFactory):
    protocol = ServerKatCP

    def setup_sensors(self):
        sensor = Sensor(int, 'int_sensor', 'descr', 'unit',
                               params=[-10, 10])
        sensor._timestamp = 0
        self.add_sensor(sensor)
        sensor = Sensor(float, 'float_sensor', 'descr', 'unit',
                               params=[-3.5, 3.5])
        sensor._timestamp = 1
        self.add_sensor(sensor)

class TestKatCPServer(TestCase):
    def base_test(self, req, callback):
        def end_test(_):
            peer.stopListening()
            finish.callback(None)

        def wrapper(arg, protocol):
            callback(arg, protocol)
            protocol.send_request('halt').addCallback(end_test)
        
        def connected(protocol):
            d = protocol.send_request(*req)
            d.addCallback(wrapper, protocol)

        f = TestFactory()
        peer = reactor.listenTCP(0, f, interface='127.0.0.1')
        cc = ClientCreator(reactor, ClientKatCP)
        d = cc.connectTCP(peer.getHost().host, peer.getHost().port)
        d.addCallback(connected)
        finish = Deferred()
        return finish
    
    def test_help(self):
        def help((args, reply), protocol):
            self.assertEquals(reply, Message.reply('help', "ok", '0'))

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
