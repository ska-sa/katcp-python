
from katcp.tx.core import (ClientKatCPProtocol, DeviceServer, DeviceProtocol,
                           KatCPClientFactory)
from katcp import Message, Sensor
from katcp.tx.test.testserver import run_subprocess
from twisted.trial.unittest import TestCase
from twisted.internet import reactor
from twisted.internet.defer import Deferred, DeferredList
from twisted.internet.protocol import ClientCreator

from katcp.core import FailReply

#DelayedCall.debug = True
#Deferred.debug = True

timeout = 5


class TestKatCP(TestCase):
    """ A tesited test case, run with trial testing

    Also note - don't forget to open a log file:
    tail -F --max-unchanged-stats=0 _trial_temp/test.log

    """

    def test_server_infrastructure(self):
        def connected(protocol):
            protocol.send_request('halt')

        d, process = run_subprocess(connected, ClientKatCPProtocol)
        return d

    def test_version_check(self):
        class TestKatCP(ClientKatCPProtocol):
            def inform_build_state(self, args):
                ClientKatCPProtocol.inform_build_state(self, args)
                # check that version is already set
                assert self.version == 'device_stub-0.1'
                self.send_request('halt')

        d, process = run_subprocess(None, TestKatCP)
        return d

    def test_help(self):
        def received_help((msgs, reply_msg), protocol):
            assert len(msgs) == 9
            requests = set(msg.arguments[0] for msg in msgs)
            assert 'help' in requests
            assert 'sensor-list' in requests
            protocol.send_request('halt')

        def connected(protocol):
            d = protocol.send_request('help')
            d.addCallback(received_help, protocol)

        d, process = run_subprocess(connected, ClientKatCPProtocol)
        return d

    def test_callback_sensor_sampling(self):
        def check(protocol):
            self.assertTrue(len(protocol.status_updates) in (30, 31))
            protocol.send_request('halt')

        def connected(protocol):
            protocol.send_request('sensor-sampling', 'int_sensor',
                                  'period', 10)
            reactor.callLater(0.3, check, protocol)

        d, process = run_subprocess(connected, TestClientKatCP)
        return d


class TestProtocol(DeviceProtocol):
    notify_con_lost = None

    def connectionLost(self, _):
        DeviceProtocol.connectionLost(self, _)
        if self.notify_con_lost:
            self.notify_con_lost()


class TestFactory(DeviceServer):
    protocol = TestProtocol

    def setup_sensors(self):
        sensor = Sensor(int, 'int_sensor', 'descr', 'unit',
                        params=[-10, 10])
        sensor.set_value(sensor.value(), status=Sensor.UNKNOWN, timestamp=0)
        self.add_sensor(sensor)
        sensor = Sensor(float, 'float_sensor', 'descr', 'unit',
                        params=[-3.5, 3.5])
        sensor.set_value(sensor.value(), status=Sensor.UNKNOWN, timestamp=1)
        self.add_sensor(sensor)


class TestClientKatCP(ClientKatCPProtocol):
    def __init__(self, *args, **kwds):
        ClientKatCPProtocol.__init__(self, *args, **kwds)
        self.status_updates = []

    def inform_sensor_status(self, msg):
        self.status_updates.append(msg)


class TestDeviceServer(TestCase):
    def _end_test(self, _):
        self.peer = None
        self.finish.callback(None)

    def _base_test(self, req, callback, cls=TestFactory,
                  client_cls=TestClientKatCP):
        def wrapper(arg, protocol):
            res = callback(arg, protocol)
            if not res:
                protocol.send_request('halt').addCallback(self._end_test)

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
        for i in dir(DeviceProtocol):
            if i.startswith('request_'):
                count += 1

        def help((informs, reply), protocol):
            self.assertEquals(len(informs), count)
            assert 'request' not in informs[0].arguments[0]
            requests = set(m.arguments[0] for m in informs)
            assert 'help' in requests
            assert 'sensor-list' in requests
            self.assertEquals(reply, Message.reply('help', "ok", str(count)))

        return self._base_test(('help',), help)

    def test_help_arg(self):
        def reply((informs, reply), protocol):
            self.assertEquals(len(informs), 1)
            expected = DeviceProtocol.request_sensor_list.__doc__.strip()
            assert informs[0].arguments[1] == expected
            self.assertEquals(reply, Message.reply('help', 'ok', '1'))

        return self._base_test(('help', 'sensor-list'), reply)

    def test_help_arg_no_meth(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('help', 'fail',
                                                   'Unknown request method.'))

        return self._base_test(('help', 'xxxxxxxxxxxxx'), reply)

    def test_version_and_build_state(self):
        def callback((informs, reply), protocol):
            assert protocol.version == 'device_stub-0.1'
            assert protocol.build_state == 'name-0.1'

        return self._base_test('watchdog', callback)

    def test_unknown_request(self):
        def got_unknown((args, reply), protocol):
            assert len(args) == 0
            assert reply.arguments[0] == 'invalid'
            assert reply.arguments[1] == 'Unknown request.'

        return self._base_test(('unknown-request',), got_unknown)

    def test_run_basic_sensors(self):
        def sensor_value_replied((informs, reply), protocol):
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'unknown', '0')])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '1'))

        return self._base_test(('sensor-value', 'int_sensor'),
                              sensor_value_replied)

    def test_unknown_sensor(self):
        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-value',
                                                   'fail',
                                                   'Unknown sensor name.'))

        return self._base_test(('sensor-value', 'xxx'),
                              reply)

    def test_all_sensor_values(self):
        def reply((informs, reply), protocol):
            msg1 = Message.inform('sensor-value', '1000', '1', 'float_sensor',
                                  'unknown', '0')
            msg2 = Message.inform('sensor-value', '0', '1', 'int_sensor',
                                  'unknown', '0')
            self.assertEquals(informs, [msg1, msg2])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '2'))

        return self._base_test(('sensor-value',), reply)

    def test_sensor_value_regex(self):
        def reply((informs, reply), protocol):
            msg1 = Message.inform('sensor-value', '1000', '1', 'float_sensor',
                                  'unknown', '0')
            msg2 = Message.inform('sensor-value', '0', '1', 'int_sensor',
                                  'unknown', '0')
            self.assertEquals(informs, [msg1, msg2])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '2'))

        return self._base_test(('sensor-value', '/_sensor/'), reply)

    def test_sensor_value_regex_no_match(self):
        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-value', 'ok', '0'))

        return self._base_test(('sensor-value', '/^noname/'), reply)

    def test_sensor_list(self):
        def reply((informs, reply), protocol):
            msg1 = Message.inform('sensor-list', 'float_sensor', 'descr',
                                  'unit', 'float', '-3.5', '3.5')
            msg2 = Message.inform('sensor-list', 'int_sensor', 'descr', 'unit',
                                  'integer', '-10', '10')
            self.assertEquals(informs, [msg1, msg2])
            self.assertEquals(reply, Message.reply('sensor-list', 'ok', '2'))

        return self._base_test(('sensor-list',), reply)

    def test_sensor_list_unknown_sensor(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-list', 'fail',
                                                   'Unknown sensor name.'))

        return self._base_test(('sensor-list', 'dummy'), reply)

    def test_sensor_list_arg(self):
        def reply((informs, reply), protocol):
            msg = Message.inform('sensor-list', 'int_sensor', 'descr', 'unit',
                                 'integer', '-10', '10')
            self.assertEquals(informs, [msg])
            self.assertEquals(reply, Message.reply('sensor-list', 'ok', '1'))

        return self._base_test(('sensor-list', 'int_sensor'), reply)

    def test_sensor_list_regex(self):
        def reply((informs, reply), protocol):
            msg1 = Message.inform('sensor-list', 'float_sensor', 'descr',
                                  'unit', 'float', '-3.5', '3.5')
            msg2 = Message.inform('sensor-list', 'int_sensor', 'descr', 'unit',
                                  'integer', '-10', '10')
            self.assertEquals(informs, [msg1, msg2])
            self.assertEquals(reply, Message.reply('sensor-list', 'ok', '2'))

        return self._base_test(('sensor-list', '/_sensor/'), reply)

    def test_sensor_list_regex_no_match(self):
        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-list', 'ok', '0'))

        return self._base_test(('sensor-list', '/^noname/'), reply)

    def test_sensor_sampling_no_sensor_name(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-sampling', 'fail',
                                                   'No sensor name given.'))

        return self._base_test(('sensor-sampling',), reply)

    def test_sensor_sampling_wrong_name(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-sampling', 'fail',
                                                   'Unknown sensor name.'))

        return self._base_test(('sensor-sampling', 'xxx'), reply)

    def test_sensor_sampling_wrong_strategy(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-sampling', 'fail',
                                                   'Unknown strategy name.'))

        return self._base_test(('sensor-sampling', 'int_sensor', 'xuz'), reply)

    def test_sensor_sampling_no_argument(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('sensor-sampling',
                                                   'ok', 'int_sensor', 'none'))

        return self._base_test(('sensor-sampling', 'int_sensor'), reply)

    def test_sensor_sampling_period(self):
        def called_later(protocol):
            assert 20 <= len(self.client.status_updates) <= 30
            # eh, judge somehow how many it can get in exactly that period
            self.client.send_request('sensor-sampling', 'int_sensor',
                                     'none').addCallback(send_halt, protocol)
            # this is necessary to cleanly exit the process so twisted
            # won't complain about leftover delayed calls

        def send_halt(_, protocol):
            protocol.send_request('halt').addCallback(self._end_test)

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor', 'period',
                                                   '10'))
            reactor.callLater(0.3, called_later, protocol)
            return True

        return self._base_test(('sensor-sampling', 'int_sensor',
                                'period', '10'), reply)

    def test_sensor_sampling_auto(self):
        def even_more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 2)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '5')])
            protocol.send_request('halt').addCallback(self._end_test)

        def more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '3')])
            self.factory.sensors['int_sensor'].set(0, Sensor.NOMINAL, 5)
            protocol.send_request('sensor-value', 'int_sensor').addCallback(
                    even_more, protocol)

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor', 'auto'))
            self.assertEquals(len(self.client.status_updates), 0)

            self.factory.sensors['int_sensor'].set(0, Sensor.NOMINAL, 3)
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(more, protocol)
            return True

        return self._base_test(('sensor-sampling', 'int_sensor', 'auto'),
                               reply)

    def test_sensor_sampling_event(self):
        def even_more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '3')])
            protocol.send_request('halt').addCallback(self._end_test)

        def more((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '3')])
            self.factory.sensors['int_sensor'].set_value(3, timestamp=0)
            protocol.send_request('sensor-value', 'int_sensor').addCallback(
                    even_more, protocol)

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor', 'event'))
            self.assertEquals(len(self.client.status_updates), 0)

            self.factory.sensors['int_sensor'].set_value(3, timestamp=0)
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(more, protocol)
            return True

        return self._base_test(('sensor-sampling', 'int_sensor', 'event'),
                               reply)

    def test_sensor_sampling_differential(self):
        def first((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '2')])
            self.factory.sensors['int_sensor'].set_value(5, timestamp=0)
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(second, protocol)

        def second((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 1)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '5')])
            self.factory.sensors['int_sensor'].set_value(10, timestamp=0)
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(third, protocol)

        def third((informs, reply), protocol):
            self.assertEquals(len(self.client.status_updates), 2)
            self.assertEquals(informs, [Message.inform('sensor-value', '0',
                                                       '1', 'int_sensor',
                                                       'nominal', '10')])
            protocol.send_request('halt').addCallback(self._end_test)

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply, Message.reply('sensor-sampling', 'ok',
                                                   'int_sensor',
                                                   'differential', '3'))
            self.assertEquals(len(self.client.status_updates), 0)

            self.factory.sensors['int_sensor'].set_value(2, timestamp=0)
            protocol.send_request('sensor-value',
                                  'int_sensor').addCallback(first, protocol)
            return True

        return self._base_test(('sensor-sampling', 'int_sensor',
                               'differential', '3'), reply)

    def test_raising_traceback(self):
        class FaultyProtocol(DeviceProtocol):
            def request_foobar(self, msg):
                raise KeyError

        class FaultyFactory(TestFactory):
            protocol = FaultyProtocol

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            assert 'Traceback' in str(reply)
            self.flushLoggedErrors()  # clean up errors so they're not reported
            # as test failures

        return self._base_test(('foobar',), reply, cls=FaultyFactory)

    def test_watchdog(self):
        def reply((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('watchdog', 'ok'))

        return self._base_test(('watchdog',), reply)

    def test_fail(self):
        class FaultyProtocol(DeviceProtocol):
            def request_foo_bar(self, msg):
                raise FailReply("failed")

        class FaultyFactory(TestFactory):
            protocol = FaultyProtocol

        def reply((informs, reply), protocol):
            self.assertEquals(informs, [])
            self.assertEquals(reply,
                              Message.reply("foo-bar", "fail", "failed"))

        return self._base_test(('foo-bar',), reply, cls=FaultyFactory)

    def test_client_list(self):
        def got_client_list(values, protocols):
            for success, (informs, reply) in values:
                assert success
                assert len(informs) == 2
                self.assertEquals(reply, Message.reply('client-list', 'ok',
                                                       '2'))
            # disconnect one and check it deregisters, with notification
            # when tcp reaches the other end
            for v in self.factory.clients.values():
                v.notify_con_lost = lambda: send_client_list(protocols[1])
            protocols[0].transport.loseConnection()

        def send_client_list(protocol):
            for v in self.factory.clients.values():
                v.notify_con_lost = None
            protocol.send_request('client-list').addCallback(client_list2,
                                                                 protocol)

        def client_list2((informs, reply), protocol):
            assert len(informs) == 1
            self.assertEquals(reply, Message.reply('client-list', 'ok', '1'))
            self.factory.stop()
            finish.callback(None)

        def connected(values):
            l = []
            protocols = []
            for success, value in values:
                assert success
                l.append(value.send_request('client-list'))
                protocols.append(value)
            DeferredList(l).addCallback(got_client_list,
                                        protocols)

        self.factory = TestFactory(0, '127.0.0.1')
        self.factory.start()
        cc = ClientCreator(reactor, TestClientKatCP)
        port = self.factory.port
        d = cc.connectTCP(port.getHost().host, port.getHost().port)
        d2 = cc.connectTCP(port.getHost().host, port.getHost().port)
        DeferredList([d, d2]).addCallback(connected)
        finish = Deferred()
        return finish

    def test_log_basic(self):
        class TestProtocol(ClientKatCPProtocol):
            def inform_log(self, msg):
                got_log.callback(msg)

        def log_received(msg):
            self.assertEquals(msg, Message.inform("log", "warn", "0", "root",
                                                  "a warning"))
            self.factory.stop()
            finish.callback(None)

        def connected(protocol):
            self.factory.log.warn('a warning', timestamp=0)

        self.factory = TestFactory(0, '127.0.0.1')
        self.factory.start()
        cc = ClientCreator(reactor, TestProtocol)
        port = self.factory.port
        cc.connectTCP(port.getHost().host, port.getHost().port).addCallback(
            connected)
        finish = Deferred()
        got_log = Deferred()
        got_log.addCallback(log_received)
        return finish

    def test_disconnect_errbacks(self):
        def failed(failure):
            #assert failure.type is ConnectionDone
            self.factory.stop()
            self.finish.callback(None)

        def callback((informs, reply), protocol):
            self.factory.clients.values()[0].transport.loseConnection()
            protocol.send_request('watchdog').addErrback(failed)
            return True

        return self._base_test(('watchdog',), callback)

    def test_log_level(self):
        class TestProtocol(ClientKatCPProtocol):
            def __init__(self, *args, **kwds):
                ClientKatCPProtocol.__init__(self, *args, **kwds)
                self.msgs = []

            def inform_log(self, msg):
                self.msgs.append(msg)

        def log_level1((informs, reply), protocol):
            self.assertEquals(reply, Message.reply('log-level', 'ok', 'warn'))
            self.factory.log.debug('blah', timestamp=0)
            protocol.send_request('log-level', 'debug').addCallback(log_level2,
                                                                    protocol)
            return True

        def log_level2((informs, reply), protocol):
            self.assertEquals(protocol.msgs, [])
            self.factory.log.debug("foo", timestamp=0)
            protocol.send_request('log-level').addCallback(log_level3,
                                                           protocol)

        def log_level3((informs, reply), protocol):
            self.assertEquals(protocol.msgs, [Message.inform("log", "debug",
                                                             "0", "root",
                                                             "foo")])
            self.factory.stop()
            self.finish.callback(None)

        return self._base_test(('log-level',), log_level1,
                              client_cls=TestProtocol)


class TestClient(TestCase):
    def test_autoconnect(self):
        def f():
            self.f = TestFactory(12345, 'localhost')
            self.f.start()

        res = Deferred()

        class Protocol(ClientKatCPProtocol):
            def connectionMade(_):
                d = self.f.port.stopListening()
                client_fac.stopTrying()
                d.addCallback(lambda _: res.callback(None))

        class ClientFactory(KatCPClientFactory):
            protocol = Protocol

        client_fac = ClientFactory()
        reactor.connectTCP('localhost', 12345, client_fac)
        reactor.callLater(0.3, f)
        return res


class TestMisc(TestCase):
    def test_requests(self):
        from katcp.server import DeviceServer
        for name in dir(DeviceServer):
            if (name.startswith('request_') and
                callable(getattr(DeviceServer, name))):
                assert hasattr(DeviceProtocol, name)
