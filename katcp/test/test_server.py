# test_server.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the server module.
   """
from __future__ import division, print_function, absolute_import

import unittest2 as unittest
import sys
import socket
import errno
import time
import logging
import thread
import threading

import mock
import tornado.testing

import katcp

from concurrent.futures import Future
from collections import defaultdict
from functools import partial, wraps
from tornado import gen

from katcp.testutils import (
    AsyncDeviceTestServer,
    BlockingTestClient,
    ClientConnectionTest,
    DeviceTestServer,
    handle_mock_req,
    mock_req,
    TestLogHandler,
    TestUtilMixin,
    start_thread_with_cleanup,
    WaitingMock)
from katcp.core import FailReply
from katcp import (kattypes,
                   __version__)

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)
logger = logging.getLogger(__name__)

NO_HELP_MESSAGES = 16       # Number of requests on DeviceTestServer

class test_ClientConnection(unittest.TestCase):
    def test_init(self):
        # Test that the ClientConnection methods are correctly bound to the
        # server methods

        # Check standard async inform
        server = mock.Mock(spec=katcp.server.KATCPServer(mock.Mock(), '', 0))
        raw_socket = 'raw_socket'
        DUT = katcp.server.ClientConnection(server, raw_socket)
        inf_arg = katcp.Message.inform('infarg')
        DUT.inform(inf_arg)
        server.send_message.assert_called_once_with(raw_socket, inf_arg)

        # Check reply-inform
        server.send_message.reset_mock()
        mid = '5'
        rif_req = katcp.Message.request('rif', mid=mid)
        rif_inf = katcp.Message.inform('rif')
        # Double-check that the mid's don't match before the call
        self.assertNotEqual(rif_inf.mid, mid)
        DUT.reply_inform(rif_inf, rif_req)
        # Check correct message sent
        server.send_message.assert_called_once_with(raw_socket, rif_inf)
        # Check that mid is copied over
        self.assertEqual(rif_inf.mid, mid)

        # Check that an assert is raised the original request and inform names don't match
        server.send_message.reset_mock()
        rif_req = katcp.Message.request('riffy')
        with self.assertRaises(Exception):
            DUT.reply_inform(rif_inf, rif_req)
        self.assertFalse(server.send_message.called)

        # Check reply
        server.send_message.reset_mock()
        mid = '7'
        rep_req = katcp.Message.request('rep-req', mid=mid)
        rep_rep = katcp.Message.reply('rep-req')
        # Double-check that the mid's don't match before the call
        self.assertNotEqual(rep_rep.mid, mid)
        DUT.reply(rep_rep, rep_req)
        # Check correct message sent
        server.send_message.assert_called_once_with(raw_socket, rep_rep)
        # Check that the mid was copied over
        self.assertEqual(rep_rep.mid, mid)

        # Check that an assert is raised the original request and reply names don't match
        server.send_message.reset_mock()
        rep_rep = katcp.Message.request('reppy')
        with self.assertRaises(Exception):
            DUT.reply(rep_rep, rep_req)
        self.assertFalse(server.send_message.called)

        # Check that mass_inform works
        DUT.mass_inform(katcp.Message.inform('blahh'))
        server.mass_send_message.assert_called_once_with(katcp.Message.inform('blahh'))

class test_ClientRequestConnection(unittest.TestCase):
    def setUp(self):
        self.client_connection = mock.Mock()
        self.req_msg = katcp.Message.request(
            'test-request', 'parm1', 'parm2', mid=42)
        self.DUT = katcp.server.ClientRequestConnection(
            self.client_connection, self.req_msg)

    def test_inform(self):
        arguments = ('inf1', 'inf2')
        self.DUT.inform(*arguments)
        self.assertEqual(self.client_connection.inform.call_count, 1)
        (inf_msg,), kwargs = self.client_connection.inform.call_args
        self.assertSequenceEqual(inf_msg.arguments, arguments)
        self.assertEqual(inf_msg.name, 'test-request')
        self.assertEqual(inf_msg.mid, '42')
        self.assertEqual(inf_msg.mtype, katcp.Message.INFORM)

    def test_reply(self):
        arguments = ('inf1', 'inf2')
        self.DUT.reply(*arguments)
        self.assertEqual(self.client_connection.reply.call_count, 1)
        (rep_msg, req_msg), kwargs = self.client_connection.reply.call_args
        self.assertIs(req_msg, self.req_msg)
        self.assertSequenceEqual(rep_msg.arguments, arguments)
        self.assertEqual(rep_msg.name, 'test-request')
        self.assertEqual(rep_msg.mid, '42')
        self.assertEqual(rep_msg.mtype, katcp.Message.REPLY)
        # Test that we can't reply twice
        with self.assertRaises(RuntimeError):
            self.DUT.reply(*arguments)

    def test_reply_with_msg(self):
        rep_msg = katcp.Message.reply('test-request', 'inf1', 'inf2')
        self.DUT.reply_with_message(rep_msg.copy())
        self.assertEqual(self.client_connection.reply.call_count, 1)
        (actual_rep_msg, req_msg), kwargs = self.client_connection.reply.call_args
        self.assertIs(req_msg, self.req_msg)
        self.assertEqual(actual_rep_msg, rep_msg)
        # Test that we can't reply twice
        with self.assertRaises(RuntimeError):
            self.DUT.reply_with_message(rep_msg)

    def test_reply_message(self):
        arguments = ('inf1', 'inf2')
        rep_msg = self.DUT.make_reply(*arguments)
        self.assertSequenceEqual(rep_msg.arguments, arguments)
        self.assertEqual(rep_msg.name, 'test-request')
        self.assertEqual(rep_msg.mid, '42')
        self.assertEqual(rep_msg.mtype, katcp.Message.REPLY)

class TestDeviceServerV4(unittest.TestCase, TestUtilMixin):

    class DeviceTestServerV4(DeviceTestServer):
        ## Protocol versions and flags for a katcp v4 server
        PROTOCOL_INFO = katcp.ProtocolFlags(4, 0, set([
            katcp.ProtocolFlags.MULTI_CLIENT,
            ]))

    def setUp(self):
        self.server = self.DeviceTestServerV4('', 0)

    def test_log(self):
        self.server.mass_inform = mock.Mock()
        self.server.log.warn('A warning', timestamp=1234)
        self.assertEqual(self.server.mass_inform.call_count, 1)
        (msg, ), _ = self.server.mass_inform.call_args
        level, timestamp, name, log_message = msg.arguments
        self.assertEqual(msg.name, 'log')
        self.assertIs(msg.mid, None)
        # Timestamp should be in miliseconds
        self.assertEqual(timestamp, '1234000')
        self.assertIn('A warning', log_message)

    def test_on_client_connect(self):
        fake_sock = mock.Mock()
        conn = katcp.server.ClientConnection(self.server._server, fake_sock)
        mock_conn = mock.Mock(spec=conn)
        self.server.BUILD_INFO = ('buildy', 1, 2, 'g')
        self.server.VERSION_INFO = ('deviceapi', 5, 6)
        # Hack around ioloop thread asserts
        self.server._server.ioloop_thread_id = thread.get_ident()
        # Test call
        self.server.on_client_connect(mock_conn)
        # we are expecting 2 inform messages
        no_msgs = 2
        self.assertEqual(mock_conn.inform.call_count, no_msgs)
        # Get all the inform messages
        msgs = [str(call[0][0]) for call in mock_conn.inform.call_args_list]
        self._assert_msgs_equal(msgs, (
            r'#version deviceapi-5.6',
            r'#build-state buildy-1.2g') )

    def test_sensor_sampling(self):
        start_thread_with_cleanup(self, self.server)
        s = katcp.Sensor.boolean('a-sens')
        s.set(1234, katcp.Sensor.NOMINAL, True)
        self.server.add_sensor(s)
        self.server._send_message = WaitingMock()
        self.server.wait_running(timeout=1.)
        self.assertTrue(self.server.running())
        self.server._strategies = defaultdict(lambda : {})
        req = mock_req('sensor-sampling', 'a-sens', 'event')
        self.server.request_sensor_sampling(req, req.msg).result(timeout=1)
        inf = req.client_connection.inform
        inf.assert_wait_call_count(count=1)
        (inf_msg, ) = inf.call_args[0]
        self._assert_msgs_equal([inf_msg], (
            r'#sensor-status 1234000 1 a-sens nominal 1',))
        req = mock_req('sensor-sampling', 'a-sens', 'period', 1000)
        self.server.request_sensor_sampling(req, req.msg).result()
        client = req.client_connection
        strat = self.server._strategies[client][s]
        # Test that the periodic update period is converted to seconds
        self.assertEqual(strat._period, 1.)
        # test that parameters returned by the request matches v4 format.
        #
        # We need to pass in the same client_conn as used by the previous
        # request since strategies are bound to specific connections
        req = mock_req('sensor-sampling', 'a-sens', client_conn=client)
        reply = self.server.request_sensor_sampling(req, req.msg).result()
        self._assert_msgs_equal([reply],
                                ['!sensor-sampling ok a-sens period 1000'])
        # event-rate is not an allowed v4 strategy
        with self.assertRaises(FailReply):
            self.server.request_sensor_sampling(req, katcp.Message.request(
                'sensor-sampling', 'a-sens', 'event-rate', 1000, 2000)).result()
        # differential-rate is not an allowed v4 strategy
        with self.assertRaises(FailReply):
            self.server.request_sensor_sampling(req, katcp.Message.request(
             'sensor-sampling', 'a-sens', 'differential-rate', 1, 1000, 2000)).result()

    def test_sensor_value(self):
        s = katcp.Sensor.boolean('a-sens')
        s.set(1234, katcp.Sensor.NOMINAL, True)
        self.server.add_sensor(s)
        client_conn = ClientConnectionTest()
        self.server.handle_message(client_conn, katcp.Message.request(
            'sensor-value', 'a-sens'))
        self._assert_msgs_equal(client_conn.messages, [
            '#sensor-value 1234000 1 a-sens nominal 1',
            '!sensor-value ok 1'])

    def test_excluded_default_handlers(self):
        """
        Test that default handers from higher KATCP versions are not included

        """
        self.assertNotIn('request-timeout-hint', self.server._request_handlers)
        self.assertNotIn('version-list', self.server._request_handlers)


class TestDeviceServerV4Async(TestDeviceServerV4):

    class DeviceTestServerV4(DeviceTestServer):
        ## Protocol versions and flags for a katcp v4 server
        PROTOCOL_INFO = katcp.ProtocolFlags(4, 0, set([
            katcp.ProtocolFlags.MULTI_CLIENT,
            ]))

        def __init__(self, *args, **kwargs):
            super(TestDeviceServerV4Async.DeviceTestServerV4, self).__init__(
                *args, **kwargs)
            self.set_concurrency_options(thread_safe=False, handler_thread=False)


class TestVersionCompatibility(unittest.TestCase):
    def test_wrong_version(self):
        class DeviceTestServerWrong(DeviceTestServer):
            ## Protocol versions and flags for a katcp v4 server
            PROTOCOL_INFO = katcp.ProtocolFlags(3, 0, set([
                katcp.ProtocolFlags.MULTI_CLIENT,
                ]))

        # Only major versions 4 and 5 are supported
        with self.assertRaises(ValueError):
            DeviceTestServerWrong('', 0)
        DeviceTestServerWrong.PROTOCOL_INFO.major = 6
        with self.assertRaises(ValueError):
            DeviceTestServerWrong('', 0)

katcp_version = __version__

class test_DeviceServer(unittest.TestCase, TestUtilMixin):

    expected_connect_messages = (
            # Will have to be updated if the default KATCP protocol
            # spec version changes
            r'#version-connect katcp-protocol 5.0-IM',
            r'#version-connect katcp-library katcp-python-%s' % katcp_version,
            r'#version-connect katcp-device deviceapi-5.6 buildy-1.2g')

    def setUp(self):
        self.server = DeviceTestServer('', 0)

    def test_on_client_connect(self):
        fake_sock = mock.Mock()
        mock_conn = mock.Mock(
            spec=katcp.server.ClientConnection(self.server._server, fake_sock))
        self.server.BUILD_INFO = ('buildy', 1, 2, 'g')
        self.server.VERSION_INFO = ('deviceapi', 5, 6)
        # Hack around ioloop thread asserts
        self.server._server.ioloop_thread_id = thread.get_ident()
        # Test call
        self.server.on_client_connect(mock_conn)
        # we are expecting 3 inform messages
        no_msgs = 3
        self.assertEqual(mock_conn.inform.call_count, no_msgs)
        # Get all the messages sent to _send_message
        msgs = [str(call[0][0]) for call in mock_conn.inform.call_args_list]
        self._assert_msgs_equal(msgs, self.expected_connect_messages)

    def test_request_sensor_sampling_clear(self):
        self.server.clear_strategies = mock.Mock()
        start_thread_with_cleanup(self, self.server, start_timeout=1)
        client_connection = ClientConnectionTest()
        self.server.ioloop.make_current()
        tf = self.server.handle_message(
            client_connection, katcp.Message.request('sensor-sampling-clear'))
        # tf may be a tornado (not thread-safe) future, so we wrap it in a thread-safe
        # future object
        f = Future()
        self.server.ioloop.add_callback(gen.chain_future, tf, f)
        f.result(timeout=1)
        # Ensure that the tornado future has finished running its callbacks
        self.server.sync_with_ioloop()
        self._assert_msgs_equal(client_connection.messages, [
            '!sensor-sampling-clear ok'])
        self.server.clear_strategies.assert_called_once_with(client_connection)

    def test_has_sensor(self):
        self.assertFalse(self.server.has_sensor('blaah'))
        self.server.add_sensor(katcp.Sensor.boolean('blaah', 'blaah sens'))
        self.assertTrue(self.server.has_sensor('blaah'))

    def test_excluded_default_handlers(self):
        """
        Test that default handers from higher KATCP versions are not included

        """
        # TODO NM 2017-04-18 This should probably be removed if we do official
        # KATCP v5.1 release and make it the default server version
        self.assertNotIn('request-timeout-hint', self.server._request_handlers)


class test_DeviceServerAsync(test_DeviceServer):
    def setUp(self):
        super(test_DeviceServerAsync, self).setUp()
        self.server.set_concurrency_options(
            thread_safe=False, handler_thread=False)

class test_DeviceServer51(test_DeviceServer):
    """Proposed additional tests for Verion 5.1 server"""

    expected_connect_messages = (
        r'#version-connect katcp-protocol 5.1-IMT',
        r'#version-connect katcp-library katcp-python-%s' % katcp_version,
        r'#version-connect katcp-device deviceapi-5.6 buildy-1.2g')

    def setUp(self):
        class DeviceTestServer51(DeviceTestServer):
            PROTOCOL_INFO = katcp.ProtocolFlags(
            5, 1, [
                katcp.ProtocolFlags.MULTI_CLIENT,
                katcp.ProtocolFlags.MESSAGE_IDS,
                katcp.ProtocolFlags.REQUEST_TIMEOUT_HINTS])
        self.server = DeviceTestServer51('', 0)
        self.server.set_concurrency_options(
            thread_safe=False, handler_thread=False)

    def test_excluded_default_handlers(self):
        pass                  #  No excluded default handlers for v5.1 as of yet

    def test_request_timeout_hint(self):
        req = mock_req('request-timeout-hint')
        handle_mock_req(self.server, req)
        # Check that the default test server "slow command" request has the
        # expected timeout hint of 99 seconds
        self._assert_msgs_equal([req.reply_msg],
                                ['!request-timeout-hint ok 1'])
        self._assert_msgs_equal(req.inform_msgs,
                                ['#request-timeout-hint slow-command 99.0'])
        # Check that a request without a hint gives a timeout of 0 if explicitly
        # asked for
        req = mock_req('request-timeout-hint', 'help')
        handle_mock_req(self.server, req)
        self._assert_msgs_equal([req.reply_msg],
                                ['!request-timeout-hint ok 1'])
        self._assert_msgs_equal(req.inform_msgs,
                                ['#request-timeout-hint help 0.0'])

        # Now set hint on help command and check that it is reflected. Note, in
        # Python 3 the im_func would not be neccesary, but in Python 2 you
        # cannot add a new attribute to an instance method. For Python 2 we need
        # to make a copy of the original handler function using partial and then
        # mess with that copy.
        handler_original = self.server._request_handlers['help'].im_func
        handler_updated = wraps(handler_original)(partial(handler_original,
                                                          self.server))
        self.server._request_handlers[
            'help'] = kattypes.request_timeout_hint(25.5)(handler_updated)
        req = mock_req('request-timeout-hint', 'help')
        handle_mock_req(self.server, req)
        self._assert_msgs_equal([req.reply_msg],
                                ['!request-timeout-hint ok 1'])
        self._assert_msgs_equal(req.inform_msgs,
                                ['#request-timeout-hint help 25.5'])
        req = mock_req('request-timeout-hint')
        handle_mock_req(self.server, req)
        # Check that the default test server "slow command" request has the
        # expected timeout hint of 99 seconds
        self._assert_msgs_equal([req.reply_msg],
                                ['!request-timeout-hint ok 2'])
        self._assert_msgs_equal(req.inform_msgs,
                                ['#request-timeout-hint help 25.5',
                                 '#request-timeout-hint slow-command 99.0'])




class TestDeviceServerClientIntegrated(unittest.TestCase, TestUtilMixin):

    BLACKLIST = ("version-connect", "version", "build-state")

    def setUp(self):
        super(TestDeviceServerClientIntegrated, self).setUp()
        self._setup_server()
        host, port = self.server.bind_address
        self.server_addr = (host, port)

        self.client = BlockingTestClient(self, host, port)
        start_thread_with_cleanup(self, self.client, start_timeout=1)
        self.assertTrue(self.client.wait_protocol(timeout=1))


    def _setup_server(self):
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)

    def test_log(self):
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST,
                replies=True)

        def tst():
            with mock.patch('katcp.server.time.time') as m_time:
                m_time.return_value = 1234
                self.server.log.error('An error')
        self.server.ioloop.add_callback(tst)

        get_msgs.wait_number(1)
        self._assert_msgs_equal(
            get_msgs(), [r"#log error 1234.000000 root An\_error"])

    def test_simple_connect(self):
        """Test a simple server setup and teardown with client connect."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST,
                replies=True)
        # basic send
        self.client.request(katcp.Message.request("foo"), use_mid=False)

        # pipe-lined send
        self.client.raw_send("?bar-boom\r\n?baz\r")

        # broken up sends
        self.client.raw_send("?boo")
        self.client.raw_send("m arg1 arg2")
        self.client.raw_send("\n")

        self._assert_msgs_equal(get_msgs(min_number=4), [
            r"!foo invalid Unknown\_request.",
            r"!bar-boom invalid Unknown\_request.",
            r"!baz invalid Unknown\_request.",
            r"!boom invalid Unknown\_request.",
        ])

    def test_bad_requests(self):
        """Test request failure paths in device server."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST, replies=True)
        self.client.raw_send("bad msg\n")
        # wait for reply
        self.client.blocking_request(
            katcp.Message.request("watchdog"), use_mid=False)

        self._assert_msgs_like(get_msgs(), [
            (r"#log error", "KatcpSyntaxError:"
                            "\_Bad\_type\_character\_'b'.\\n"),
            (r"!watchdog ok", ""),
        ])

    def test_slow_client(self):
        # Test that server does not choke sending messages to slow clients

        # Set max server write buffer size smaller so that it gives up earlier to make the
        # test faster
        self.server._server.MAX_WRITE_BUFFER_SIZE = 16384

        self.client.wait_protocol(1)
        slow_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        slow_sock.connect(self.server_addr)
        slow_sock.settimeout(0.01)
        # Send a bunch of request to the server, but don't read anything from
        # the server
        try:
            slow_sock.sendall('?help\n'*1000000)
        except (socket.error, socket.timeout):
            pass

        t0 = time.time()
        # Request should not have taken a very long time.
        self.client.assert_request_succeeds('help', informs_count=NO_HELP_MESSAGES)
        self.assertTrue(time.time() - t0 < 1)


    def test_server_ignores_informs_and_replies(self):
        """Test server ignores informs and replies."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST, replies=True)
        self.client.raw_send("#some inform\n")
        self.client.raw_send("!some reply\n")

        time.sleep(0.1)

        self.assertFalse(get_msgs())

    def test_standard_requests(self):
        """Test standard request and replies."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST, replies=True)
        nomid_req = partial(self.client.blocking_request, use_mid=False)
        nomid_req(katcp.Message.request("watchdog"))
        nomid_req(katcp.Message.request("restart"))
        nomid_req(katcp.Message.request("log-level"))
        nomid_req(katcp.Message.request("log-level", "trace"))
        nomid_req(katcp.Message.request("log-level", "unknown"))
        nomid_req(katcp.Message.request("help"))
        nomid_req(katcp.Message.request("help", "watchdog"))
        nomid_req(katcp.Message.request("help", "unknown-request"))
        nomid_req(katcp.Message.request("client-list"))
        nomid_req(katcp.Message.request("version-list"))
        nomid_req(katcp.Message.request("sensor-list"))
        nomid_req(katcp.Message.request("sensor-list", "an.int"))
        nomid_req(katcp.Message.request("sensor-list", "an.unknown"))
        nomid_req(katcp.Message.request("sensor-value"))
        nomid_req(katcp.Message.request("sensor-value", "an.int"))
        nomid_req(katcp.Message.request("sensor-value",
                                                  "an.unknown"))
        nomid_req(katcp.Message.request("sensor-sampling", "an.int"))
        nomid_req(katcp.Message.request("sensor-sampling", "an.int",
                                                  "differential", "2"))
        nomid_req(katcp.Message.request("sensor-sampling", "an.int",
                                                  "event-rate", "2", "3"))
        nomid_req(katcp.Message.request("sensor-sampling"))
        nomid_req(katcp.Message.request("sensor-sampling",
                                                  "an.unknown", "auto"))
        nomid_req(katcp.Message.request("sensor-sampling", "an.int", "unknown"))

        def tst():
            self.server.log.trace("trace-msg")
            self.server.log.debug("debug-msg")
            self.server.log.info("info-msg")
            self.server.log.warn("warn-msg")
            self.server.log.error("error-msg")
            self.server.log.fatal("fatal-msg")
        self.server.ioloop.add_callback(tst)

        self.assertEqual(self.server.restart_queue.get_nowait(), self.server)
        expected_msgs = [
            (r"!watchdog ok", ""),
            (r"!restart ok", ""),
            (r"!log-level ok warn", ""),
            (r"!log-level ok trace", ""),
            (r"!log-level fail Unknown\_logging\_level\_name\_'unknown'", ""),
            (r"#help cancel-slow-command Cancel\_slow\_command\_request,\_"
             "resulting\_in\_it\_replying\_immediately", ""),
            (r"#help client-list", ""),
            (r"#help halt", ""),
            (r"#help help", ""),
            (r"#help log-level", ""),
            (r"#help new-command", ""),
            (r"#help raise-exception", ""),
            (r"#help raise-fail", ""),
            (r"#help restart", ""),
            (r"#help sensor-list", ""),
            (r"#help sensor-sampling", ""),
            (r"#help sensor-sampling-clear", ""),
            (r"#help sensor-value", ""),
            (r"#help slow-command", ""),
            (r"#help version-list", ""),
            (r"#help watchdog", ""),
            (r"!help ok %d" % NO_HELP_MESSAGES, ""),
            (r"#help watchdog", ""),
            (r"!help ok 1", ""),
            (r"!help fail", ""),
            (r"#client-list", ""),
            (r"!client-list ok 1", ""),
            (r"#version-list katcp-protocol", ""),
            (r"#version-list katcp-library", ""),
            (r"#version-list katcp-device", ""),
            (r"!version-list ok 3", ""),
            (r"#sensor-list an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list ok 1", ""),
            (r"#sensor-list an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list ok 1", ""),
            (r"!sensor-list fail", ""),
            (r"#sensor-value 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-value ok 1", ""),
            (r"#sensor-value 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-value ok 1", ""),
            (r"!sensor-value fail", ""),
            (r"!sensor-sampling ok an.int none", ""),
            (r"#sensor-status 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-sampling ok an.int differential 2", ""),
            (r"#sensor-status 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-sampling ok an.int event-rate 2 3", ""),
            (r"!sensor-sampling fail No\_sensor\_name\_given.", ""),
            (r"!sensor-sampling fail Unknown\_sensor\_name:\_an.unknown.", ""),
            (r"!sensor-sampling fail Unknown\_strategy\_name:\_unknown.", ""),
            (r"#log trace", r"root trace-msg"),
            (r"#log debug", r"root debug-msg"),
            (r"#log info", r"root info-msg"),
            (r"#log warn", r"root warn-msg"),
            (r"#log error", r"root error-msg"),
            (r"#log fatal", r"root fatal-msg"),
        ]
        self._assert_msgs_like(get_msgs(min_number=len(expected_msgs)),
                               expected_msgs)

    def test_standard_requests_with_ids(self):
        """Test standard request and replies with message ids."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST, replies=True)

        current_id = [0]

        def mid():
            current_id[0] += 1
            return str(current_id[0])

        def mid_req(*args):
            return katcp.Message.request(*args, mid=mid())


        self.client.request(mid_req("watchdog"))
        self.client._next_id = mid  # mock our mid generator for testing
        self.client.blocking_request(mid_req("restart"))
        self.client.request(mid_req("log-level"))
        self.client.request(mid_req("log-level", "trace"))
        self.client.request(mid_req("log-level", "unknown"))
        self.client.request(mid_req("help"))
        self.client.request(mid_req("help", "watchdog"))
        self.client.request(mid_req("help", "unknown-request"))
        self.client.request(mid_req("client-list"))
        self.client.request(mid_req("version-list"))
        self.client.request(mid_req("sensor-list"))
        self.client.request(mid_req("sensor-list", "an.int"))
        self.client.request(mid_req("sensor-list", "an.unknown"))
        self.client.request(mid_req("sensor-value"))
        self.client.request(mid_req("sensor-value", "an.int"))
        self.client.request(mid_req("sensor-value", "an.unknown"))
        self.client.blocking_request(mid_req("sensor-sampling", "an.int"))
        self.client.blocking_request(mid_req(
            "sensor-sampling", "an.int", "differential", "2"))
        self.client.blocking_request(mid_req(
            "sensor-sampling", "an.int", "event-rate", "2", "3")),
        self.client.blocking_request(mid_req("sensor-sampling"))
        self.client.blocking_request(mid_req(
            "sensor-sampling", "an.unknown", "auto"))
        self.client.blocking_request(mid_req(
            "sensor-sampling", "an.int", "unknown"))

        def tst():
            self.server.log.trace("trace-msg")
            self.server.log.debug("debug-msg")
            self.server.log.info("info-msg")
            self.server.log.warn("warn-msg")
            self.server.log.error("error-msg")
            self.server.log.fatal("fatal-msg")
        self.server.ioloop.add_callback(tst)

        expected_msgs = [
            (r"!watchdog[1] ok", ""),
            (r"!restart[2] ok", ""),
            (r"!log-level[3] ok warn", ""),
            (r"!log-level[4] ok trace", ""),
            (r"!log-level[5] fail Unknown\_logging\_level\_name\_'unknown'",
             ""),
            (r"#help[6] cancel-slow-command Cancel\_slow\_command\_request,\_"
             "resulting\_in\_it\_replying\_immediately", ""),
            (r"#help[6] client-list", ""),
            (r"#help[6] halt", ""),
            (r"#help[6] help", ""),
            (r"#help[6] log-level", ""),
            (r"#help[6] new-command", ""),
            (r"#help[6] raise-exception", ""),
            (r"#help[6] raise-fail", ""),
            (r"#help[6] restart", ""),
            (r"#help[6] sensor-list", ""),
            (r"#help[6] sensor-sampling", ""),
            (r"#help[6] sensor-sampling-clear", ""),
            (r"#help[6] sensor-value", ""),
            (r"#help[6] slow-command", ""),
            (r"#help[6] version-list", ""),
            (r"#help[6] watchdog", ""),
            (r"!help[6] ok %d" % NO_HELP_MESSAGES, ""),
            (r"#help[7] watchdog", ""),
            (r"!help[7] ok 1", ""),
            (r"!help[8] fail", ""),
            (r"#client-list[9]", ""),
            (r"!client-list[9] ok 1", ""),
            (r"#version-list[10] katcp-protocol", ""),
            (r"#version-list[10] katcp-library", ""),
            (r"#version-list[10] katcp-device", ""),
            (r"!version-list[10] ok 3", ""),
            (r"#sensor-list[11] an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list[11] ok 1", ""),
            (r"#sensor-list[12] an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list[12] ok 1", ""),
            (r"!sensor-list[13] fail", ""),
            (r"#sensor-value[14] 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-value[14] ok 1", ""),
            (r"#sensor-value[15] 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-value[15] ok 1", ""),
            (r"!sensor-value[16] fail", ""),
            (r"!sensor-sampling[17] ok an.int none", ""),
            (r"#sensor-status 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-sampling[18] ok an.int differential 2", ""),
            (r"#sensor-status 12345.000000 1 an.int nominal 3", ""),
            (r"!sensor-sampling[19] ok an.int event-rate 2 3", ""),
            (r"!sensor-sampling[20] fail No\_sensor\_name\_given.", ""),
            (r"!sensor-sampling[21] fail Unknown\_sensor\_name:\_an.unknown.", ""),
            (r"!sensor-sampling[22] fail Unknown\_strategy\_name:\_unknown.", ""),
            (r"#log trace", r"root trace-msg"),
            (r"#log debug", r"root debug-msg"),
            (r"#log info", r"root info-msg"),
            (r"#log warn", r"root warn-msg"),
            (r"#log error", r"root error-msg"),
            (r"#log fatal", r"root fatal-msg"),
        ]
        self.assertEqual(self.server.restart_queue.get_nowait(), self.server)
        self._assert_msgs_like(get_msgs(min_number=len(expected_msgs)), expected_msgs)

    def test_sensor_list_regex(self):
        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-list", "/a.*/"), use_mid=False)
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-list an.int An\_Integer. count integer -5 5",
            r"!sensor-list ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-list", "//"), use_mid=False)
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-list an.int An\_Integer. count integer -5 5",
            r"!sensor-list ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-list", "/^int/"), use_mid=False)
        self._assert_msgs_equal(informs + [reply], [
            r"!sensor-list ok 0",
        ])

    def test_sensor_value_regex(self):
        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-value", "/a.*/"), use_mid=False)
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-value 12345.000000 1 an.int nominal 3",
            r"!sensor-value ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-value", "//"), use_mid=False)
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-value 12345.000000 1 an.int nominal 3",
            r"!sensor-value ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-value", "/^int/"), use_mid=False)
        self._assert_msgs_equal(informs + [reply], [
            r"!sensor-value ok 0",
        ])

    def test_client_list(self):
        reply, informs = self.client.blocking_request(
            katcp.Message.request('client-list'), use_mid=False)
        self.assertEqual(str(reply), '!client-list ok 1')
        self.assertEqual(len(informs), 1)
        inform = str(informs[0])
        self.assertTrue(inform.startswith('#client-list 127.0.0.1:'))
        _, addr = inform.split()
        host, port = addr.split(':')
        port = int(port)
        self.assertEqual((host, port), self.client.sockname)

    def test_halt_request(self):
        """Test halt request."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST, replies=True)
        self.client.request(katcp.Message.request("halt"))
        # hack to hide re-connect exception
        self.client.connect = lambda: None
        self.server.join()

        self._assert_msgs_equal(get_msgs(min_number=2), [
            r"!halt[1] ok",
            r"#disconnect Device\_server\_shutting\_down.",
        ])

    def test_bad_handlers(self):
        """Test that bad request and inform handlers are picked up."""
        try:

            class BadServer(katcp.DeviceServer):
                def request_baz(self, req, msg):
                    pass

        except AssertionError:
            pass
        else:
            self.fail("Server metaclass accepted missing request_ docstring.")

        try:

            class BadServer(katcp.DeviceServer):
                def inform_baz(self, req, msg):
                    pass

        except AssertionError:
            pass
        else:
            self.fail("Server metaclass accepted missing inform_ docstring.")

        class SortOfOkayServer(katcp.DeviceServer):
            request_bar = 1
            inform_baz = 2

        assert("bar" not in SortOfOkayServer._request_handlers)
        assert("baz" not in SortOfOkayServer._inform_handlers)

    def test_handler_exceptions(self):
        """Test handling of failure replies and other exceptions."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST, replies=True)
        self.assertTrue(self.client.wait_protocol(timeout=1))

        self.client.request(katcp.Message.request("raise-exception"))
        self.client.request(katcp.Message.request("raise-fail"))

        time.sleep(0.1)

        self._assert_msgs_like(get_msgs(), [
            (r"!raise-exception[1] fail Traceback", ""),
            (r"!raise-fail[2] fail There\_was\_a\_problem\_with\_your\_request.",
             ""),
        ])

    def test_stop_and_restart(self):
        """Test stopping and restarting the device server."""
        # So we can wait for the client to disconnect
        self.client.notify_connected = WaitingMock()
        self.server.stop(timeout=0.1)
        self.server.join(timeout=1.0)
        self.assertFalse(self.server.running())
        # Wait for client to be disconnected
        self.client.notify_connected.assert_wait_call_count(1)
        self.assertFalse(self.client.is_connected())
        self.server.start(timeout=1.0)

    def test_bad_client_socket(self):
        """Test what happens when select is called on a dead client socket."""
        # close client stream while the server isn't looking then wait for the server to
        # notice

        stream, client_conn = self.server._server._connections.items()[0]
        # Wait for the client to disconnect
        self.client.notify_connected = WaitingMock()
        self.server.ioloop.add_callback(stream.close)
        # Wait for the client to be disconnected, and to connect again
        self.client.notify_connected.assert_wait_call_count(2)
        self.server.sync_with_ioloop()

        # check that client stream was removed from the KATCPServer
        self.assertTrue(stream not in self.server._server._connections,
                        "Expected %r to not be in %r" %
                        (stream, self.server._server._connections))
        # And check that the ClientConnection object was removed from the DeviceServer
        self.assertTrue(client_conn not in self.server._client_conns,
                        "Expected %r to not be in %r" %
                        (client_conn, self.server._client_conns))

    def test_sampling(self):
        """Test sensor sampling."""
        get_msgs = self.client.message_recorder(
                blacklist=self.BLACKLIST, replies=True)
        self.client.wait_protocol(timeout=1)
        self.client.request(katcp.Message.request(
            "sensor-sampling", "an.int", "period", 1/32.))

        # Wait for the request reply and for the sensor update messages to
        # arrive. We expect update one the moment the sensor-sampling request is
        # made, then four more over 4/32. of a second, resutling in 6
        # messages. Wait 0.75 of a period longer just to be sure we get everything.

        self.assertTrue(get_msgs.wait_number(6, timeout=4.75/32.))
        self.client.assert_request_succeeds("sensor-sampling", "an.int", "none")
        # Wait for reply to above request
        get_msgs.wait_number(7)
        msgs = get_msgs()
        updates = [x for x in msgs if x.name == "sensor-status"]
        others = [x for x in msgs if x.name != "sensor-status"]
        self.assertTrue(abs(len(updates) - 5) < 2,
                        "Expected 5 informs, saw %d." % len(updates))

        self._assert_msgs_equal(others, [
            r"!sensor-sampling[1] ok an.int period %s" % (1/32.),
            r"!sensor-sampling[2] ok an.int none",
        ])

        self.assertEqual(updates[0].arguments[1:],
                         ["1", "an.int", "nominal", "3"])

        ## Now clear the strategies on this sensor
        # There should only be on connection to the server, so it should be
        # the test client
        client_conn = list(self.server._client_conns)[0]
        self.server.ioloop.add_callback(self.server.clear_strategies, client_conn)
        self.server.sync_with_ioloop()
        self.client.assert_request_succeeds("sensor-sampling", "an.int",
                                            args_equal=["an.int", "none"])

        # Check that we did not accidentally clobber the strategy datastructure
        # in the proccess
        self.client.assert_request_succeeds(
            "sensor-sampling", "an.int", "period", 0.125)


    def test_add_remove_sensors(self):
        """Test adding and removing sensors from a running device."""
        an_int = self.server._sensors["an.int"]
        self.server.remove_sensor(an_int)
        # TODO remove_sensor test that checks that everything is indeed gone
        self.server.add_sensor(an_int)
        self.test_sampling()

    def test_async_request_handler(self):
        """
        Request handlers allowing other requests to be handled before replying

        """
        # We use a coroutine running on the client's ioloop for this test
        @gen.coroutine
        def do_async_request_test(threadsafe_future, tornado_future):
            try:
                slow_wait_time = 20
                # Kick off a slow, async request
                slow_f = self.client.future_request(katcp.Message.request(
                    'slow-command', slow_wait_time))
                t0 = time.time()
                # Do another normal request that should reply before the slow
                # request
                reply, _ = yield self.client.future_request(
                    katcp.Message.request('watchdog'))
                self.assertTrue(reply.reply_ok(), 'Normal request should succeed')
                # Normal request should reply quickly
                t1 = time.time()
                self.assertTrue(
                    t1 - t0 < 0.1*slow_wait_time,
                    'The normal request should reply almost immediately')
                # Slow request should still be outstanding
                self.assertFalse(
                    slow_f.done(),
                    'slow async request should not be complete yet')
                # Now cancel the slow command
                reply, _ = yield self.client.future_request(
                    katcp.Message.request('cancel-slow-command'))
                self.assertTrue(reply.reply_ok(),
                                '?cancel-slow-command should succeed')
                slow_reply, _ = yield slow_f
                self.assertTrue(
                    slow_reply.reply_ok(),
                    '?slow-command should succeed after being cancelled')
                t2 = time.time()
                self.assertTrue(
                    t2 - t1 < 0.1*slow_wait_time,
                    'Slow request should be cancelled almost immediately')
            except Exception as exc:
                tornado_future.set_exc_info(sys.exc_info())
                threadsafe_future.set_exception(exc)
            else:
                threadsafe_future.set_result(None)

        tornado_future = gen.Future()
        threadsafe_future = Future()
        self.client.ioloop.add_callback(
            do_async_request_test, threadsafe_future, tornado_future)
        try:
            threadsafe_future.result()
        except Exception:
            # Use the tornado future to get a usable traceback
            tornado_future.result()


class TestHandlerFiltering(unittest.TestCase):
    class DeviceWithEverything(katcp.DeviceServer):
        PROTOCOL_INFO = katcp.ProtocolFlags(
            5, 1, [
                katcp.ProtocolFlags.MULTI_CLIENT,
                katcp.ProtocolFlags.MESSAGE_IDS,
                katcp.ProtocolFlags.REQUEST_TIMEOUT_HINTS])

        def request_simple(self, req, msg):
            """A simple request"""

        @kattypes.return_reply()
        @kattypes.minimum_katcp_version(5, 1)
        def request_version_51(self, req, msg):
            """Request KATCP v5.1"""

        @kattypes.minimum_katcp_version(5, 0)
        def request_version_5(self, req, msg):
            """Request KATCP v5.0"""

        @kattypes.request()
        @kattypes.has_katcp_protocol_flags(
                [katcp.ProtocolFlags.MULTI_CLIENT,
                 katcp.ProtocolFlags.MESSAGE_IDS])
        def request_flags(self, req):
            """Request some flags"""

        @kattypes.has_katcp_protocol_flags(
                [katcp.ProtocolFlags.MULTI_CLIENT])
        def request_fewer_flags(self, req, msg):
            """Request with fewer flags"""

        @kattypes.request()
        @kattypes.return_reply()
        @kattypes.minimum_katcp_version(5, 1)
        @kattypes.has_katcp_protocol_flags(
            [katcp.ProtocolFlags.MULTI_CLIENT])
        def request_version_and_flags(self, req, msg):
            """Request with version and flag requirements"""

    all_expected_handlers = frozenset(['simple', 'version-51', 'version-5',
                                       'flags', 'fewer-flags',
                                       'version-and-flags'])

    def _test_handler_protocol_filters(self, DeviceCls, expected_handlers):
        """Test that handlers are filtered according to protocol requirements."""
        expected_handlers = set(expected_handlers)
        not_expected_handlers = self.all_expected_handlers - expected_handlers
        actual_device_handlers = set(DeviceCls._request_handlers.keys())

        self.assertEqual(actual_device_handlers & expected_handlers,
                         expected_handlers)
        self.assertEqual(set(), actual_device_handlers & not_expected_handlers)

    def test_handler_protocol_filters_all(self):
        """Test handler filtering where everything should be included"""
        self._test_handler_protocol_filters(self.DeviceWithEverything,
                                            self.all_expected_handlers)

    def test_handler_protocol_filters_five_one_with_nothing(self):
        """Test handler filtering where protocol flags exclude some"""

        class DeviceVersionFiveOneWithNothing(self.DeviceWithEverything):
            PROTOCOL_INFO = katcp.ProtocolFlags(5, 1, [])

        self._test_handler_protocol_filters(
            DeviceVersionFiveOneWithNothing,
            ['simple', 'version-5', 'version-51'])

    def test_handler_protocol_filters_five_one_with_multi(self):
        """Test handler filtering where protocol flags and version exclude some"""

        class DeviceVersionFiveOneWithMultiClient(self.DeviceWithEverything):
            PROTOCOL_INFO = katcp.ProtocolFlags(5, 1, [
                katcp.ProtocolFlags.MULTI_CLIENT])

        self._test_handler_protocol_filters(
            DeviceVersionFiveOneWithMultiClient,
            ['simple', 'version-5', 'version-51', 'fewer-flags',
            'version-and-flags'])

    def test_handler_protocol_filters_five(self):
        """Test handler filtering for a KATCP v5.0 device"""

        class DeviceVersionFive(self.DeviceWithEverything):
            PROTOCOL_INFO = katcp.ProtocolFlags(
                5, 0, [
                    katcp.ProtocolFlags.MULTI_CLIENT,
                    katcp.ProtocolFlags.MESSAGE_IDS])

        self._test_handler_protocol_filters(
            DeviceVersionFive,
            ['simple', 'version-5', 'flags', 'fewer-flags'])

    def test_handler_protocol_filters_four(self):
        """Test handler filtering for a KATCP v4.0 device"""

        class DeviceVersionFour(self.DeviceWithEverything):
            PROTOCOL_INFO = katcp.ProtocolFlags(
                4, 0, [katcp.ProtocolFlags.MULTI_CLIENT])

        self._test_handler_protocol_filters(
            DeviceVersionFour, ['simple', 'fewer-flags'])


class TestDeviceServerClientIntegratedAsync(
        tornado.testing.AsyncTestCase,
        TestDeviceServerClientIntegrated):

    def _setup_server(self):
        self.server = AsyncDeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)
