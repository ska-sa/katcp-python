
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

class TestKatCPServer(TestCase):
    def test_simple_server(self):
        def halt_replied(self):
            port.stopListening() # XXX handle it in a better way somehow
            finish.callback(None)
        
        def help((args, reply), protocol):
            assert reply.arguments == ["ok"]
            d = protocol.send_request('halt')
            d.addCallback(halt_replied)
        
        def connected(protocol):
            d = protocol.send_request('help')
            d.addCallback(help, protocol)
        
        f = Factory()
        f.protocol = ServerProtocol
        port = reactor.listenTCP(0, f, interface='127.0.0.1')
        cc = ClientCreator(reactor, ClientKatCP)
        d = cc.connectTCP(port.getHost().host, port.getHost().port)
        d.addCallback(connected)
        finish = Deferred()
        return finish

    def test_server_sensors(self):
        def halt_replied(self):
            peer.stopListening()
            finish.callback(None)
        
        def connected(protocol):
            assert len(protocol.sensors) == 1
            d = protocol.send_request('halt')
            d.addCallback(halt_replied)
            
        class TestFactory(ServerFactory):
            def setup_sensors(self):
                self.add_sensor(Sensor(int, 'int_sensor', 'descr', 'unit',
                                       params=[-10, 10]))
        
        f = TestFactory()
        f.protocol = ServerKatCP
        peer = reactor.listenTCP(0, f, interface='127.0.0.1')
        run_client(('localhost', peer.getHost().port), ProxyKatCP, connected)
        finish = Deferred()
        return finish
        
