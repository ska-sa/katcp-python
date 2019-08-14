# test_client.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for client module."""

from __future__ import division, print_function, absolute_import

import unittest2 as unittest
import socket
import mock
import time
import logging
import threading
import tornado.testing

import katcp

from concurrent.futures import Future

from tornado import gen

from katcp.core import ProtocolFlags, Message

from katcp.testutils import (TestLogHandler, DeviceTestServer, TestUtilMixin,
                             counting_callback, start_thread_with_cleanup,
                             WaitingMock, TimewarpAsyncTestCase)

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)

logger = logging.getLogger(__name__)

# Number of requests on DeviceTestServer
NO_HELP_MESSAGES = len(DeviceTestServer._request_handlers)


def remove_version_connect(msgs):
    """Remove #version-connect messages from a list of messages"""
    return [msg for msg in msgs if msg.name != 'version-connect']


class TestDeviceClientServerDetection(unittest.TestCase, TestUtilMixin):
    def setUp(self):
        self.client = katcp.DeviceClient('localhost', 0)
        self.assertFalse(self.client._received_protocol_info.isSet())
        self.v4_build_state = Message.inform('build-state', 'blah-5.21a3')
        self.v4_version = Message.inform('version', '7.3')
        self.v5_version_connect_mid = Message.inform(
            'version-connect', 'katcp-protocol', '5.0-I')
        self.v5_version_connect_nomid = Message.inform(
            'version-connect', 'katcp-protocol', '5.0')

    def _check_v5_mid(self):
        self.assertTrue(self.client._received_protocol_info.isSet())
        pf = self.client.protocol_flags
        self.assertTrue(pf.supports(pf.MESSAGE_IDS))
        self.assertTrue(self.client._server_supports_ids)
        self.assertEqual(pf.major, 5)
        self.assertEqual(self.client.convert_seconds(1), 1)

    def test_valid_v5(self):
        self.client.handle_message(self.v5_version_connect_mid)
        self._check_v5_mid()

    def _check_v4(self):
        self.assertTrue(self.client._received_protocol_info.isSet())
        pf = self.client.protocol_flags
        self.assertFalse(pf.supports(pf.MESSAGE_IDS))
        self.assertFalse(self.client._server_supports_ids)
        self.assertEqual(pf.major, 4)
        self.assertEqual(self.client.convert_seconds(1), 1000)

    def test_valid_v4_version_first(self):
        self.client.handle_message(self.v4_version)
        self._check_v4()
        self.client.handle_message(self.v4_build_state)
        self._check_v4()

    def test_valid_v4_build_state_first(self):
        self.client.handle_message(self.v4_build_state)
        self._check_v4()
        self.client.handle_message(self.v4_version)
        self._check_v4()

    def test_inconsistent_v4_then_v5(self):
        self.client.handle_message(self.v4_build_state)
        self._check_v4()
        self.client._disconnect = mock.Mock()
        self.client._logger = mock.Mock()
        self.client.handle_message(self.v5_version_connect_mid)
        self.assertEqual(self.client._disconnect.call_count, 1)
        self.assertEqual(self.client._auto_reconnect, False)
        self.assertEqual(self.client._logger.error.call_count, 1)
        log_msg = self.client._logger.error.call_args[0][0]
        self.assertIn('Protocol Version Error', log_msg)

    def test_inconsistent_v5_then_v4(self):
        self.client.handle_message(self.v5_version_connect_mid)
        self._check_v5_mid()
        self.client._disconnect = mock.Mock()
        self.client._logger = mock.Mock()
        self.client.handle_message(self.v4_build_state)
        self.assertEqual(self.client._disconnect.call_count, 1)
        self.assertEqual(self.client._auto_reconnect, False)
        self.assertEqual(self.client._logger.error.call_count, 1)
        log_msg = self.client._logger.error.call_args[0][0]
        self.assertIn('Protocol Version', log_msg)

    def test_preset(self):
        self.client.preset_protocol_flags(ProtocolFlags(
            5, 0, [ProtocolFlags.MESSAGE_IDS]))
        self._check_v5_mid()
        self.client._received_protocol_info.clear()
        self.client.preset_protocol_flags(ProtocolFlags(4, 0, ''))
        self._check_v4()

    def test_preset_v4_then_v5(self):
        self.client.preset_protocol_flags(ProtocolFlags(4, 0, ''))
        self._check_v4()
        self.client._disconnect = mock.Mock()
        self.client._logger = mock.Mock()
        # A version 5 version-connect message should result in a warning
        self.client.handle_message(self.v5_version_connect_mid)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._auto_reconnect, True)
        self.assertEqual(self.client._logger.warn.call_count, 1)
        log_msg = self.client._logger.warn.call_args[0][0]
        self.assertIn('Protocol Version', log_msg)
        self._check_v4()
        self.client._logger.warn.reset_mock()
        # Version 4-identifying informs should not cause any warnings
        self.client.handle_message(self.v4_version)
        self.client.handle_message(self.v4_build_state)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._auto_reconnect, True)
        self.assertEqual(self.client._logger.warn.call_count, 0)
        self._check_v4()

    def test_inform_version_connect(self):
        # Test that the inform handler doesn't screw up with a non-katcp related
        # version-connect inform.
        self.client.handle_message(Message.inform(
            'version-connect', 'not-katcp', '5.71a3'))
        # Should not raise any errors, but should also not set the protocol
        # infor received flag.
        self.assertFalse(self.client._received_protocol_info.isSet())

    def test_preset_v5_then_v4(self):
        def check_warn_not_disconect():
            self.assertEqual(self.client._disconnect.call_count, 0)
            self.assertEqual(self.client._auto_reconnect, True)
            self.assertEqual(self.client._logger.warn.call_count, 1)
            log_msg = self.client._logger.warn.call_args[0][0]
            self.assertIn('Protocol Version', log_msg)
            self.client._logger.warn.reset_mock()

        self.client.preset_protocol_flags(ProtocolFlags(
            5, 0, [ProtocolFlags.MESSAGE_IDS]))
        self._check_v5_mid()
        self.client._disconnect = mock.Mock()
        self.client._logger = mock.Mock()
        # Any Version 4-identifying informs should cause warnings
        self.client.handle_message(self.v4_build_state)
        check_warn_not_disconect()
        self._check_v5_mid()
        self.client.handle_message(self.v4_version)
        check_warn_not_disconect()
        self._check_v5_mid()
        # A version 5 version-connect message with different flags should also
        # result in a warning
        self.client.handle_message(self.v5_version_connect_nomid)
        check_warn_not_disconect()
        self._check_v5_mid()
        # An identical version 5 version-connect message should not result in
        # any warnings
        self.client.handle_message(self.v5_version_connect_mid)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._logger.warn.call_count, 0)
        self.assertEqual(self.client._auto_reconnect, True)
        self._check_v5_mid()


class TestDeviceClientIntegrated(unittest.TestCase, TestUtilMixin):

    def setUp(self):
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)

        host, port = self.server.bind_address

        self.client = katcp.DeviceClient(host, port)
        self.client.enable_thread_safety()
        start_thread_with_cleanup(self, self.client, start_timeout=1)
        self.client.wait_connected(timeout=1)

    def test_versions(self):
        """Test that the versions parameter is populated."""
        preamble_done = self.client.handle_reply = WaitingMock()
        # Do a request and wait for it to be done so that we can be sure we received the
        # full connection-header before testing
        self.client.request(Message.request('watchdog'))
        preamble_done.assert_wait_call_count(1, timeout=1)
        versions = self.client.versions
        self.assertIn('katcp', ' '.join(versions['katcp-library']))
        self.assertIn('device', ' '.join(versions['katcp-device']))
        self.assertTrue(' '.join(versions['katcp-protocol']))

    def test_request(self):
        """Test request method."""
        self.assertTrue(self.client.wait_protocol(1))
        self.client.send_request(Message.request("watchdog"))
        self.client._server_supports_ids = False
        with self.assertRaises(katcp.core.KatcpVersionError):
            self.client.send_request(Message.request("watchdog", mid=56))
        self.client._server_supports_ids = True
        self.client.send_request(Message.request("watchdog", mid=55))

        msgs = self.server.until_messages(2).result(timeout=1)
        self._assert_msgs_equal(msgs, [
            r"?watchdog",
            r"?watchdog[55]",
        ])

    def test_send_message(self):
        """Test send_message method."""
        self.client.send_message(Message.inform("random-inform"))

        msgs = self.server.until_messages(1).result(timeout=1)
        self._assert_msgs_equal(msgs, [
            r"#random-inform",
        ])

    def test_stop_and_restart(self):
        """Test stopping and then restarting a client."""
        self.client.wait_running(timeout=1)
        before_threads = threading.enumerate()
        self.client.stop(timeout=1)
        self.client.join(timeout=1)
        # Test that we have fewer threads than before
        self.assertLess(len(threading.enumerate()), len(before_threads))
        self.assertFalse(self.client._running.isSet())
        self.client.start(timeout=1)
        self.client.wait_running(timeout=1)
        # Now we should have the original number of threads again
        self.assertEqual(len(threading.enumerate()), len(before_threads))

    def test_is_connected(self):
        """Test is_connected method."""
        self.assertTrue(self.client.is_connected())
        # Use client.notify_connected to synchronise to the disconnection
        disconnected = threading.Event()
        self.client.notify_connected = (
            lambda connected: disconnected.set() if not connected else None)
        self.server.stop(timeout=1.)
        # Restart server during cleanup to keep teardown happy
        self.addCleanup(self.server.start)
        self.server.join(timeout=1.)
        # Wait for the client to be disconnected
        disconnected.wait(1.5)
        self.assertFalse(self.client.is_connected())

    def test_wait_connected(self):
        """Test wait_connected method."""
        start = time.time()
        # Ensure that we are connected at the start of the test
        self.assertTrue(self.client.wait_connected(1.0))
        # Check that we did not wait more than the timeout.
        self.assertTrue(time.time() - start < 1.0)
        # Now we will cause the client to disconnect by stopping the server, and then
        # checking that wait_connected returns fails, and waits approximately the right
        # amount of time
        # Use client.notify_connected to synchronise to the disconnection
        disconnected = threading.Event()
        self.client.notify_connected = (
            lambda connected: disconnected.set() if not connected else None)
        self.server.stop(timeout=0.1)
        self.server.join(timeout=1)
        # Restart server during cleanup to keep teardown happy
        self.addCleanup(self.server.start)
        # Wait for the client to be disconnected
        disconnected.wait(1)
        # Now check that wait_connected returns false
        start = time.time()
        self.assertFalse(self.client.wait_connected(0.1))
        # And waited more or less the timeout.
        self.assertTrue(0.05 < time.time() - start <= 0.15)

    def test_bad_socket(self):
        """Test what happens when select is called on a dead socket."""
        # wait for client to connect
        self.client.wait_connected(timeout=1)
        f = Future()
        def notify_connected(connected):
            if connected:
                f.set_result(connected)
        self.client.notify_connected = notify_connected

        # close stream while the client isn't looking
        # then wait for the client to notice
        stream = self.client._stream
        sockname = stream.socket.getpeername()
        self.client.ioloop.add_callback(stream.close)
        f.result(timeout=1.25)

        # check that client reconnected
        self.assertTrue(stream is not self.client._stream,
                        "Expected %r to not be %r" % (stream, self.client._stream))
        self.assertEqual(sockname, self.client._stream.socket.getpeername())

class TestBlockingClient(unittest.TestCase):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)

        host, port = self.server.bind_address

        self.client = katcp.BlockingClient(host, port)
        start_thread_with_cleanup(self, self.client, start_timeout=1)
        self.assertTrue(self.client.wait_protocol(timeout=1))

    def test_blocking_request(self):
        """Test blocking_request."""
        reply, informs = self.client.blocking_request(
            Message.request("watchdog"))
        self.assertEqual(reply.name, "watchdog")
        self.assertEqual(reply.arguments, ["ok"])
        self.assertEqual(remove_version_connect(informs), [])

        reply, informs = self.client.blocking_request(
            Message.request("help"))
        self.assertEqual(reply.name, "help")
        self.assertEqual(reply.arguments, ["ok", "%d" % NO_HELP_MESSAGES])
        self.assertEqual(len(informs), int(reply.arguments[1]))

    def test_blocking_request_mid(self):
        ## Test that the blocking client does the right thing with message
        ## identifiers
        # Wait for the client to detect the server protocol. Server should
        # support message identifiers
        self.assertTrue(self.client.wait_protocol(0.2))
        # Replace send_message so that we can check the message
        self.client.send_message = mock.Mock()

        # blocking_request() raises RuntimeError on timeout, so make a wrapper
        # function to eat that

        # TODO (NM 2014-09-08): Can remove this once we've made all clients handle
        # blocking_request timeouts in the same way.
        def blocking_request(*args, **kwargs):
            try:
                return self.client.blocking_request(*args, **kwargs)
            except RuntimeError, e:
                if not e.args[0].startswith('Request '):
                    raise

        # By default message identifiers should be enabled, and should start
        # counting at 1
        blocking_request(Message.request('watchdog'), timeout=0)
        blocking_request(Message.request('watchdog'), timeout=0)
        blocking_request(Message.request('watchdog'), timeout=0)
        # Extract Message object .mid attributes from the mock calls to
        # send_message
        mids = [args[0].mid              # arg[0] should be the Message() object
                for args, kwargs in self.client.send_message.call_args_list]
        self.assertEqual(mids, ['1','2','3'])
        self.client.send_message.reset_mock()

        # Explicitly ask for no mid to be used
        blocking_request(
            Message.request('watchdog'), use_mid=False, timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

        # Ask for a specific mid to be used
        self.client.send_message.reset_mock()
        blocking_request(Message.request('watchdog', mid=42), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, '42')

        ## Check situation for a katcpv4 server
        self.client._server_supports_ids = False

        # Should fail if an mid is passed
        reply, informs =  blocking_request(
            Message.request('watchdog', mid=42), timeout=0)
        self.assertFalse(reply.reply_ok())

        # Should fail if an mid is requested
        reply, informs = blocking_request(
            Message.request('watchdog'), use_mid=True, timeout=0)
        self.assertFalse(reply.reply_ok())

        # Should use no mid by default
        self.client.send_message.reset_mock()
        blocking_request(Message.request('watchdog'), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

    def test_timeout(self):
        """Test calling blocking_request with a timeout."""
        reply, informs = self.client.blocking_request(
            Message.request("slow-command", "0.5"), timeout=0.001)
        self.assertFalse(reply.reply_ok())
        self.assertRegexpMatches(
            reply.arguments[1],
            r"Request slow-command timed out after 0\..* seconds.")

class TestCallbackClient(unittest.TestCase, TestUtilMixin):

    def setUp(self):
        self.addCleanup(self.stop_server_client)
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server.bind_address

        self.client = katcp.CallbackClient(host, port)
        self.client.start(timeout=1)
        self.assertTrue(self.client.wait_protocol(timeout=1))

    def stop_server_client(self):
        if hasattr(self, 'client') and self.client.running():
            self.client.stop()
            self.client.join()
        if hasattr(self, 'server') and self.server.running():
            self.server.stop()
            self.server.join()

    def test_callback_request(self):
        """Test callback request."""

        watchdog_replies = []
        watchdog_replied = threading.Event()

        def watchdog_reply(reply):
            self.assertEqual(reply.name, "watchdog")
            self.assertEqual(reply.arguments, ["ok"])
            watchdog_replies.append(reply)
            watchdog_replied.set()

        self.assertTrue(self.client.wait_protocol(0.5))
        self.client.callback_request(
            Message.request("watchdog"),
            reply_cb=watchdog_reply,
        )

        watchdog_replied.wait(0.5)
        self.assertTrue(watchdog_replies)

        help_replies = []
        help_informs = []
        help_replied = threading.Event()

        def help_reply(reply):
            self.assertEqual(reply.name, "help")
            self.assertEqual(reply.arguments, ["ok", "%d" % NO_HELP_MESSAGES])
            self.assertEqual(len(help_informs), int(reply.arguments[1]))
            help_replies.append(reply)
            help_replied.set()

        def help_inform(inform):
            self.assertEqual(inform.name, "help")
            self.assertEqual(len(inform.arguments), 2)
            help_informs.append(inform)


        self.client.callback_request(
            Message.request("help"),
            reply_cb=help_reply,
            inform_cb=help_inform,
        )

        help_replied.wait(1)
        self.assertTrue(help_replied.isSet())
        help_replied.clear()
        help_replied.wait(0.05)   # Check if (unwanted) late help replies arrive
        self.assertFalse(help_replied.isSet())
        self.assertEqual(len(help_replies), 1)
        self.assertEqual(len(help_informs), NO_HELP_MESSAGES)

    def test_callback_request_mid(self):
        ## Test that the client does the right thing with message identifiers

        # Wait for the client to detect the server protocol. Server should
        # support message identifiers
        self.assertTrue(self.client.wait_protocol(0.2))
        # Replace send_message so that we can check the message
        self.client.send_message = mock.Mock(wraps=self.client.send_message)

        # By default message identifiers should be enabled, and should start
        # counting at 1
        cb = counting_callback(number_of_calls=3)(lambda *x: x)
        self.client.callback_request(Message.request('watchdog'), reply_cb=cb)
        self.client.callback_request(Message.request('watchdog'), reply_cb=cb)
        self.client.callback_request(Message.request('watchdog'), reply_cb=cb)
        cb.assert_wait()
        # Extract Message object .mid attributes from the mock calls to
        # send_message
        mids = [args[0].mid              # args[0] should be the Message() object
                for args, kwargs in self.client.send_message.call_args_list]
        self.assertEqual(mids, ['1','2','3'])
        self.client.send_message.reset_mock()

        # Explicitly ask for no mid to be used
        cb = counting_callback(number_of_calls=1)(lambda *x: x)
        self.client.callback_request(
            Message.request('watchdog'), use_mid=False, reply_cb=cb)
        cb.assert_wait()
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

        # Ask for a specific mid to be used
        self.client.send_message.reset_mock()
        cb = counting_callback(number_of_calls=1)(lambda *x: x)
        self.client.callback_request(
            Message.request('watchdog', mid=42), reply_cb=cb)
        cb.assert_wait()
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, '42')

        ## Check situation for a katcpv4 server
        self.client._server_supports_ids = False

        # Should fail if an mid is passed
        reply = [None]
        @counting_callback()
        def cb(msg):
            reply[0] = msg
        self.client.callback_request(
            Message.request('watchdog', mid=42), reply_cb=cb)
        cb.assert_wait()
        self.assertFalse(reply[0].reply_ok())

        # Should fail if an mid is requested
        reply = [None]
        cb.reset()
        self.client.callback_request(
            Message.request('watchdog'), use_mid=True, reply_cb=cb)
        cb.assert_wait()
        self.assertFalse(reply[0].reply_ok())

        # Should use no mid by default
        self.client.send_message.reset_mock()
        cb = counting_callback(number_of_calls=1)(lambda *x: x)
        self.client.callback_request(Message.request('watchdog'), reply_cb=cb)
        cb.assert_wait(timeout=1)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

    def test_no_callback(self):
        """Test request without callback."""

        help_messages = []
        help_completed = threading.Event()

        def handle_help_message(client, msg):
            help_messages.append(msg)
            if msg.mtype == msg.REPLY:
                help_completed.set()

        self.client._inform_handlers["help"] = handle_help_message
        self.client._reply_handlers["help"] = handle_help_message
        # Set client._last_msg_id so we know that the ID is. Should be
        # _last_msg_id + 1
        self.client._last_msg_id = 0
        self.assertTrue(self.client.wait_protocol(0.2))
        self.client.callback_request(Message.request("help"))
        help_completed.wait(1)
        self.assertTrue(help_completed.isSet())

        self._assert_msgs_like(help_messages,
            [("#help[1] ", "")] * NO_HELP_MESSAGES +
            [("!help[1] ok %d" % NO_HELP_MESSAGES, "")])

    def test_timeout(self):
        self._test_timeout()

    def test_timeout_nomid(self, use_mid=False):
        self._test_timeout(use_mid=False)

    def _test_timeout(self, use_mid=None):
        """Test requests that timeout."""

        replies = []
        replied = threading.Event()
        informs = []
        timeout = 0.001

        @counting_callback()
        def reply_cb(msg):
            replies.append(msg)
            replied.set()

        def inform_cb(msg):
            informs.append(msg)

        self.assertTrue(self.client.wait_protocol(0.2))
        self.client.callback_request(
            Message.request("slow-command", "0.1"),
            use_mid=use_mid,
            reply_cb=reply_cb,
            inform_cb=inform_cb,
            timeout=timeout,
        )

        reply_cb.assert_wait(1)
        self.client.request(Message.request('cancel-slow-command'))
        msg = replies[0]
        self.assertEqual(msg.name, "slow-command")
        self.assertFalse(msg.reply_ok())
        self.assertRegexpMatches(msg.arguments[1],
                                 r"Request slow-command timed out after 0\..* seconds.")
        self.assertEqual(len(remove_version_connect(informs)), 0)
        self.assertEqual(len(replies), 1)

        del replies[:]
        del informs[:]
        reply_cb.reset()

        # test next request succeeds
        self.client.callback_request(
            Message.request("slow-command", "0.05"),
            reply_cb=reply_cb,
            inform_cb=inform_cb,
        )

        reply_cb.assert_wait()
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(informs), 0)
        self.assertEqual([msg.name for msg in replies + informs],
                         ["slow-command"] * len(replies + informs))
        self.assertEqual([msg.arguments for msg in replies], [["ok"]])

    def test_timeout_nocb(self):
        """Test requests that timeout with no callbacks."""
        # Included to test https://katfs.kat.ac.za/mantis/view.php?id=1722
        # Situation can occur during a race between the timeout handler and the
        # receipt of a reply -- the reply can arrive after the timeout timer has
        # expired but before the request has been popped off the stack with
        # client._pop_async_request(). The normal request handler then pops off
        # the request first, resulting in the timeout handler getting a bunch of
        # None's. It should handle this gracefully.

        # Running the handler with a fake msg_id should have the same result as
        # running it after a real request has already been popped. The expected
        # result is that no assertions are raised.

        # NM 2014-09-26: This is probably no longer an issue with the tornado-based client
        # implementatin, but leaving the test for good measure
        f = Future()
        @gen.coroutine
        def cb():
            self.client._handle_timeout('fake_msg_id', time.time())
        self.client.ioloop.add_callback(lambda : gen.chain_future(cb(), f))
        f.result(timeout=1)


    def test_user_data(self):
        """Test callbacks with user data."""
        help_replies = []
        help_informs = []
        done = threading.Event()

        def help_reply(reply, x, y):
            self.assertEqual(reply.name, "help")
            self.assertEqual(x, 5)
            self.assertEqual(y, "foo")
            help_replies.append(reply)
            done.set()

        def help_inform(inform, x, y):
            self.assertEqual(inform.name, "help")
            self.assertEqual(x, 5)
            self.assertEqual(y, "foo")
            help_informs.append(inform)

        self.client.callback_request(
            Message.request("help"),
            reply_cb=help_reply,
            inform_cb=help_inform,
            user_data=(5, "foo"))

        done.wait(1)
        # Wait a bit longer to see if spurious replies arrive
        time.sleep(0.01)
        self.assertEqual(len(help_replies), 1)
        self.assertEqual(len(remove_version_connect(help_informs)),
                         NO_HELP_MESSAGES)

    def test_fifty_thread_mayhem(self):
        """Test using callbacks from fifty threads simultaneously."""
        num_threads = 50
        # map from thread_id -> (replies, informs)
        results = {}
        # list of thread objects
        threads = []

        def reply_cb(reply, thread_id):
            results[thread_id][0].append(reply)
            results[thread_id][2].set()

        def inform_cb(inform, thread_id):
            results[thread_id][1].append(inform)

        def worker(thread_id, request):
            self.client.callback_request(
                request.copy(),
                reply_cb=reply_cb,
                inform_cb=inform_cb,
                user_data=(thread_id,),
            )

        request = Message.request("help")

        for thread_id in range(num_threads):
            results[thread_id] = ([], [], threading.Event())

        for thread_id in range(num_threads):
            thread = threading.Thread(target=worker, args=(thread_id, request))
            threads.append(thread)

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        for thread_id in range(num_threads):
            replies, informs, done = results[thread_id]
            done.wait(5.0)
            self.assertTrue(done.isSet())
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].arguments[0], "ok")
            informs = remove_version_connect(informs)
            if len(informs) != NO_HELP_MESSAGES:
                print(thread_id, len(informs))
                print([x.arguments[0] for x in informs])
            self.assertEqual(len(informs), NO_HELP_MESSAGES)

    def test_blocking_request(self):
        """Test the callback client's blocking request."""
        reply, informs = self.client.blocking_request(
            Message.request("help"),
        )

        self.assertEqual(reply.name, "help")
        self.assertEqual(reply.arguments, ["ok", "%d" % NO_HELP_MESSAGES])
        self.assertEqual(len(remove_version_connect(informs)), NO_HELP_MESSAGES)

        reply, informs = self.client.blocking_request(
            Message.request("slow-command", "0.5"),
            timeout=0.001)

        self.assertEqual(reply.name, "slow-command")
        self.assertEqual(reply.arguments[0], "fail")
        self.assertRegexpMatches(
            reply.arguments[1],
            r"Request slow-command timed out after 0\..* seconds.")

    def test_blocking_request_mid(self):
        ## Test that the blocking client does the right thing with message
        ## identifiers

        # Wait for the client to detect the server protocol. Server should
        # support message identifiers
        self.assertTrue(self.client.wait_protocol(1))
        # Replace send_message so that we can check the message
        self.client.send_message = mock.Mock()

        # By default message identifiers should be enabled, and should start
        # counting at 1
        self.client.blocking_request(Message.request('watchdog'), timeout=0)
        self.client.blocking_request(Message.request('watchdog'), timeout=0)
        self.client.blocking_request(Message.request('watchdog'), timeout=0)
        # Extract Message object .mid attributes from the mock calls to
        # send_message
        mids = [args[0].mid              # arg[0] should be the Message() object
                for args, kwargs in self.client.send_message.call_args_list]
        self.assertEqual(mids, ['1','2','3'])
        self.client.send_message.reset_mock()

        # Explicitly ask for no mid to be used
        self.client.blocking_request(Message.request('watchdog'),
                                     use_mid=False, timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

        # Ask for a specific mid to be used
        self.client.send_message.reset_mock()
        self.client.blocking_request(Message.request(
            'watchdog', mid=42), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, '42')

        ## Check situation for a katcpv4 server
        self.client._server_supports_ids = False

        # Should fail if an mid is passed
        reply, inform = self.client.blocking_request(Message.request(
                'watchdog', mid=42), timeout=0)
        self.assertFalse(reply.reply_ok())

        # Should fail if an mid is requested
        reply, inform = self.client.blocking_request(
            Message.request('watchdog'), use_mid=True, timeout=0)
        self.assertFalse(reply.reply_ok())

        # Should use no mid by default
        self.client.send_message.reset_mock()
        self.client.blocking_request(Message.request(
            'watchdog'), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

    def test_request_fail_on_raise(self):
        """Test that the callback is called even if send_message raises
           KatcpClientError."""
        def raise_error(msg, timeout=None):
            raise katcp.KatcpClientError("Error %s" % msg.name)
        self.client.send_message = raise_error

        replies = []

        @counting_callback()
        def reply_cb(msg):
            replies.append(msg)

        self.client.callback_request(Message.request("foo"),
            reply_cb=reply_cb,
        )

        reply_cb.assert_wait()
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].name, "foo")
        self.assertEqual(replies[0].arguments, ["fail", "Error foo"])

    def test_stop_cleanup(self):
        self.client.wait_protocol(timeout=1)
        mid = 56
        future_reply = Future()
        self.client.ioloop.add_callback(
            lambda : gen.chain_future(self.client.future_request(Message.request(
                'slow-command', 1, mid=mid)), future_reply))
        # Force a disconnect
        self.client.stop()
        reply, informs = future_reply.result(timeout=1)
        self.assertEqual(reply, Message.reply(
            'slow-command', 'fail', 'Client stopped before reply was received', mid=mid))

class test_AsyncClientIntegrated(tornado.testing.AsyncTestCase, TestUtilMixin):
    def setUp(self):
        super(test_AsyncClientIntegrated, self).setUp()
        self.server = DeviceTestServer('', 0)
        self.server.set_ioloop(self.io_loop)
        self.server.set_concurrency_options(thread_safe=False, handler_thread=False)
        self.server.start()

        host, port = self.server.bind_address
        self.client = katcp.CallbackClient(host, port)
        self.client.set_ioloop(self.io_loop)
        self.client.start()

    @tornado.testing.gen_test
    def test_timeout_of_until_connected(self):
        # Test for timing out
        with self.assertRaises(tornado.gen.TimeoutError):
            yield self.client.until_connected(timeout=0.0001)
        # Test for NOT timing out
        host, port = self.server.bind_address
        client2 = katcp.CallbackClient(host, port)
        client2.set_ioloop(self.io_loop)
        client2.start()
        yield client2.until_connected(timeout=0.5)
        self.assertTrue(client2.is_connected)

    @tornado.testing.gen_test
    def test_timeout_of_until_protocol(self):
        # Test for timing out
        with self.assertRaises(tornado.gen.TimeoutError):
            yield self.client.until_protocol(timeout=0.0001)
        # Test for NOT timing out
        host, port = self.server.bind_address
        client2 = katcp.CallbackClient(host, port)
        client2.set_ioloop(self.io_loop)
        client2.start()
        yield client2.until_protocol(timeout=0.5)

    @tornado.testing.gen_test
    def test_future_request_simple(self):
        yield self.client.until_connected()
        reply, informs = yield self.client.future_request(Message.request('watchdog'))
        self.assertEqual(len(informs), 0)
        self.assertEqual(reply.name, "watchdog")
        self.assertEqual(reply.arguments, ["ok"])

    @tornado.testing.gen_test
    def test_future_request_with_informs(self):
        yield self.client.until_connected()
        reply, informs = yield self.client.future_request(Message.request('help'))
        self.assertEqual(reply.name, "help")
        self.assertEqual(reply.arguments, ["ok", "%d" % NO_HELP_MESSAGES])
        self.assertEqual(len(informs), NO_HELP_MESSAGES)

    @tornado.testing.gen_test
    def test_disconnect_cleanup(self):
        yield self.client.until_protocol()
        mid = 55
        future_reply = self.client.future_request(Message.request(
            'slow-command', 1, mid=mid))
        # Force a disconnect
        self.client._disconnect()
        reply, informs = yield future_reply
        self.assertEqual(reply, Message.reply(
            'slow-command', 'fail', 'Connection closed before reply was received',
            mid=mid))

    @tornado.testing.gen_test
    def test_stop_cleanup(self):
        yield self.client.until_protocol()
        mid = 564
        future_reply = self.client.future_request(Message.request(
            'slow-command', 1, mid=mid))
        # Stop client
        self.client.stop()
        reply, informs = yield future_reply
        self.assertEqual(reply, Message.reply(
            'slow-command', 'fail', 'Client stopped before reply was received', mid=mid))

class test_AsyncClientIntegratedBase(TimewarpAsyncTestCase):
    def setUp(self):
        super(test_AsyncClientIntegratedBase, self).setUp()
        self.server = DeviceTestServer('localhost', 0)
        self.server.set_ioloop(self.io_loop)
        self.server.set_concurrency_options(thread_safe=False, handler_thread=False)
        self.server.start()

        host, port = self.server.bind_address
        logger.info('host, port: {}:{}'.format(host, port))
        self.client = katcp.CallbackClient(host, port)
        self.client.set_ioloop(self.io_loop)


class test_AsyncClientIntegrated(test_AsyncClientIntegratedBase):

    @tornado.testing.gen_test()
    def test_last_connect_time(self):
        self.server.stop()
        yield self.wake_ioloop()
        self.client.start()
        yield self.client.until_running()
        # Client is running, but server has stopped, so there should be no connection
        self.assertEqual(self.client.last_connect_time, None,
                         "last_connect_time should be 'None' before first connection")

        # Start up the server
        self.server.start()
        # and give the client time to connect
        yield self.client._waiting_to_retry.until_set()
        t0 = self.io_loop.time()
        self.set_ioloop_time(t0 + self.client.auto_reconnect_delay*1.1)
        # The connection should be made at the current ioloop time
        tc0 = self.io_loop.time()
        # Wait for the client to go through the motions of connecting
        yield self.client.until_connected()
        # Check that the correct connection time was stored
        self.assertEqual(self.client.last_connect_time, tc0)

        # Move time along and check that the last_connect_time is unchanged
        t1 = tc0 + 1.5
        self.set_ioloop_time(t1)
        yield self.wake_ioloop()
        # Connect time should not have changed since we did not reconnect
        self.assertEqual(self.client.last_connect_time, tc0)

        # Stop the server and check that last_connect_time is not affected
        self.server.stop()
        yield self.client._waiting_to_retry.until_set()
        self.assertFalse(self.client.is_connected())
        # Connect time should not have changed since we did not reconnect
        self.assertEqual(self.client.last_connect_time, tc0)

        # Restart the server and check that last_connect_time is updated
        self.server.start()
        t2 = t1 + 1.2
        self.set_ioloop_time(t2)
        yield self.client.until_connected()
        # Connect time should now be the current time
        self.assertEqual(self.client.last_connect_time, t2)


class test_AsyncClientTimeoutsIntegrated(test_AsyncClientIntegratedBase):
    def setUp(self):
        super(test_AsyncClientTimeoutsIntegrated, self).setUp()
        self.client.start()


    @tornado.testing.gen_test(timeout=10)
    # We are using time-warping, so the timeout should be longer than the fake-duration
    def test_future_request_default_timeout(self):
        # Test the default timeout of 5s
        yield self._test_timeout(5)

    @tornado.testing.gen_test()
    def test_future_request_change_default_timeout(self):
        self.client._request_timeout = 3
        yield self._test_timeout(3)

    @tornado.testing.gen_test()
    def test_future_request_request_timeout(self):
        yield self._test_timeout(1, set_request_timeout=True)

    @gen.coroutine
    def _test_timeout(self, timeout, set_request_timeout=False):
        request_timeout = timeout if set_request_timeout else None
        yield self.client.until_connected()
        t0 = self.io_loop.time()
        reply_future = self.client.future_request(Message.request('slow-command', timeout + 1),
                                                  timeout=request_timeout)
        # Warp to just before the timeout expires and check that the future is not yet
        # resolved
        self.set_ioloop_time(t0 + timeout*0.9999)
        yield self.wake_ioloop()
        self.assertFalse(reply_future.done())
        # Warp to just after the timeout expires, and check that it gives us a timeout
        # error reply
        self.set_ioloop_time(t0 + timeout*1.0001)
        yield self.wake_ioloop()
        self.assertTrue(reply_future.done())
        reply, informs = reply_future.result()
        self.assertFalse(reply.reply_ok())
        self.assertRegexpMatches(
            reply.arguments[1],
            r"Request slow-command timed out after .* seconds.")
