# test_client.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for client module.
   """

import unittest2 as unittest
import mock
import time
import logging
import threading
import katcp
from katcp.core import ProtocolFlags

from katcp.testutils import (TestLogHandler, DeviceTestServer, TestUtilMixin,
                             counting_callback)

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)

NO_HELP_MESSAGES = 15         # Number of requests on DeviceTestServer

def remove_version_connect(msgs):
    """Remove #version-connect messages from a list of messages"""
    return [msg for msg in msgs if msg.name != 'version-connect']

class TestDeviceClientServerDetection(unittest.TestCase, TestUtilMixin):
    def setUp(self):
        self.client = katcp.DeviceClient('localhost', 0)
        self.assertFalse(self.client._received_protocol_info.isSet())
        self.v4_build_state = katcp.Message.inform('build-state', 'blah-5.21a3')
        self.v4_version = katcp.Message.inform('version', '7.3')
        self.v5_version_connect_mid = katcp.Message.inform(
            'version-connect', 'katcp-protocol', '5.0-I')
        self.v5_version_connect_nomid = katcp.Message.inform(
            'version-connect', 'katcp-protocol', '5.0')

    def _check_v5_mid(self):
        self.assertTrue(self.client._received_protocol_info.isSet())
        pf = self.client.protocol_flags
        self.assertTrue(pf.supports(pf.MESSAGE_IDS))
        self.assertTrue(self.client._server_supports_ids)
        self.assertEqual(pf.major, 5)

    def test_valid_v5(self):
        self.client.handle_message(self.v5_version_connect_mid)
        self._check_v5_mid()

    def _check_v4(self):
        self.assertTrue(self.client._received_protocol_info.isSet())
        pf = self.client.protocol_flags
        self.assertFalse(pf.supports(pf.MESSAGE_IDS))
        self.assertFalse(self.client._server_supports_ids)
        self.assertEqual(pf.major, 4)

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
        self.assertEqual(self.client._logger.warn.call_count, 1)
        log_msg = self.client._logger.warn.call_args[0][0]
        self.assertIn('Protocol Version', log_msg)
        self._check_v4()
        self.client._logger.warn.reset_mock()
        # Version 4-identifying informs should not cause any warnings
        self.client.handle_message(self.v4_version)
        self.client.handle_message(self.v4_build_state)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._logger.warn.call_count, 0)
        self._check_v4()

    def test_inform_version_connect(self):
        # Test that the inform handler doesn't screw up with a non-katcp related
        # version-connect inform.
        self.client.handle_message(katcp.Message.inform(
            'version-connect', 'not-katcp', '5.71a3'))
        # Should not raise any errors, but should also not set the protocol
        # infor received flag.
        self.assertFalse(self.client._received_protocol_info.isSet())

    def test_preset_v5_then_v4(self):
        self.client.preset_protocol_flags(ProtocolFlags(
            5, 0, [ProtocolFlags.MESSAGE_IDS]))
        self._check_v5_mid()
        self.client._disconnect = mock.Mock()
        self.client._logger = mock.Mock()
        # Any Version 4-identifying informs should cause warnings
        self.client.handle_message(self.v4_build_state)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._logger.warn.call_count, 1)
        log_msg = self.client._logger.warn.call_args[0][0]
        self.assertIn('Protocol Version', log_msg)
        self._check_v5_mid()
        self.client._logger.warn.reset_mock()
        self.client.handle_message(self.v4_version)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._logger.warn.call_count, 1)
        log_msg = self.client._logger.warn.call_args[0][0]
        self.assertIn('Protocol Version', log_msg)
        self._check_v5_mid()
        # A version 5 version-connect message with different flags should also
        # result in a warning
        self.client._logger.warn.reset_mock()
        self.client.handle_message(self.v5_version_connect_nomid)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._logger.warn.call_count, 1)
        log_msg = self.client._logger.warn.call_args[0][0]
        self.assertIn('Protocol Version', log_msg)
        self._check_v5_mid()
        # An identical version 5 version-connect message should not result in
        # any warnings
        self.client._logger.warn.reset_mock()
        self.client.handle_message(self.v5_version_connect_mid)
        self.assertEqual(self.client._disconnect.call_count, 0)
        self.assertEqual(self.client._logger.warn.call_count, 0)


class TestDeviceClientIntegrated(unittest.TestCase, TestUtilMixin):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = katcp.DeviceClient(host, port)
        self.client.start(timeout=0.1)

    def tearDown(self):
        if self.client.running():
            self.client.stop()
            self.client.join()
        if self.server.running():
            self.server.stop()
            self.server.join()

    def test_request(self):
        """Test request method."""
        self.assertTrue(self.client.wait_protocol(1))
        self.client.send_request(katcp.Message.request("watchdog"))
        self.client.send_request(katcp.Message.request("watchdog", mid=55))
        self.client._server_supports_ids = False
        with self.assertRaises(katcp.core.KatcpVersionError):
            self.client.send_request(katcp.Message.request("watchdog", mid=56))

        time.sleep(0.1)

        msgs = self.server.messages()
        self._assert_msgs_equal(msgs, [
            r"?watchdog",
            r"?watchdog[55]",
        ])

    def test_send_message(self):
        """Test send_message method."""
        self.client.send_message(katcp.Message.inform("random-inform"))

        time.sleep(0.1)

        msgs = self.server.messages()
        self._assert_msgs_equal(msgs, [
            r"#random-inform",
        ])

    def test_stop_and_restart(self):
        """Test stopping and then restarting a client."""
        self.client.stop(timeout=0.1)
        # timeout needs to be longer than select sleep.
        self.client.join(timeout=1.5)
        self.assertEqual(self.client._thread, None)
        self.assertFalse(self.client._running.isSet())
        self.client.start(timeout=0.1)

    def test_is_connected(self):
        """Test is_connected method."""
        self.assertTrue(self.client.is_connected())
        self.server.stop(timeout=0.1)
        # timeout needs to be longer than select sleep.
        self.server.join(timeout=1.5)
        self.assertFalse(self.client.is_connected())

    def test_wait_connected(self):
        """Test wait_connected method."""
        start = time.time()
        self.assertTrue(self.client.wait_connected(1.0))
        self.assertTrue(time.time() - start < 1.0)
        self.server.stop(timeout=0.1)
        # timeout needs to be longer than select sleep.
        self.server.join(timeout=1.5)
        start = time.time()
        self.assertFalse(self.client.wait_connected(0.2))
        self.assertTrue(0.15 < time.time() - start < 0.25)

    def test_bad_socket(self):
        """Test what happens when select is called on a dead socket."""
        # wait for client to connect
        time.sleep(0.1)

        # close socket while the client isn't looking
        # then wait for the client to notice
        sock = self.client._sock
        sockname = sock.getpeername()
        sock.close()
        time.sleep(1.25)

        # check that client reconnected
        self.assertTrue(sock is not self.client._sock,
                        "Expected %r to not be %r" % (sock, self.client._sock))
        self.assertEqual(sockname, self.client._sock.getpeername())

    def test_daemon_value(self):
        """Test passing in a daemon value to client start method."""
        self.client.stop(timeout=0.1)
        # timeout needs to be longer than select sleep.
        self.client.join(timeout=1.5)

        self.client.start(timeout=0.1, daemon=True)
        self.assertTrue(self.client._thread.isDaemon())

    def test_excepthook(self):
        """Test passing in an excepthook to client start method."""
        exceptions = []
        except_event = threading.Event()

        def excepthook(etype, value, traceback):
            """Keep track of exceptions."""
            exceptions.append(etype)
            except_event.set()

        self.client.stop(timeout=0.1)
        # timeout needs to be longer than select sleep.
        self.client.join(timeout=1.5)

        self.client.start(timeout=0.1, excepthook=excepthook)
        # force exception by deleteing _running
        old_running = self.client._running
        try:
            del self.client._running
            except_event.wait(1.5)
            self.assertEqual(exceptions, [AttributeError])
        finally:
            self.client._running = old_running


class TestBlockingClient(unittest.TestCase):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = katcp.BlockingClient(host, port)
        self.client.start(timeout=0.1)
        self.assertTrue(self.client.wait_protocol(timeout=1))

    def tearDown(self):
        if self.client.running():
            self.client.stop()
            self.client.join()
        if self.server.running():
            self.server.stop()
            self.server.join()

    def test_blocking_request(self):
        """Test blocking_request."""
        reply, informs = self.client.blocking_request(
            katcp.Message.request("watchdog"))
        self.assertEqual(reply.name, "watchdog")
        self.assertEqual(reply.arguments, ["ok"])
        self.assertEqual(remove_version_connect(informs), [])

        reply, informs = self.client.blocking_request(
            katcp.Message.request("help"))
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
        def blocking_request(*args, **kwargs):
            try:
                return self.client.blocking_request(*args, **kwargs)
            except RuntimeError, e:
                if not e.args[0].startswith('Request '):
                    raise
 
        # By default message identifiers should be enabled, and should start
        # counting at 1
        blocking_request(katcp.Message.request('watchdog'), timeout=0)
        blocking_request(katcp.Message.request('watchdog'), timeout=0)
        blocking_request(katcp.Message.request('watchdog'), timeout=0)
        # Extract katcp.Message object .mid attributes from the mock calls to
        # send_message
        mids = [args[0].mid              # arg[0] should be the Message() object
                for args, kwargs in self.client.send_message.call_args_list]
        self.assertEqual(mids, ['1','2','3'])
        self.client.send_message.reset_mock()

        # Explicitly ask for no mid to be used
        blocking_request(
            katcp.Message.request('watchdog'), use_mid=False, timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

        # Ask for a specific mid to be used
        self.client.send_message.reset_mock()
        blocking_request(katcp.Message.request('watchdog', mid=42), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, '42')

        ## Check situation for a katcpv4 server
        self.client._server_supports_ids = False

        # Should fail if an mid is passed
        with self.assertRaises(katcp.core.KatcpVersionError):
            blocking_request(
                katcp.Message.request('watchdog', mid=42), timeout=0)

        # Should fail if an mid is requested
        with self.assertRaises(katcp.core.KatcpVersionError):
            blocking_request(
                katcp.Message.request('watchdog'), use_mid=True, timeout=0)

        # Should use no mid by default
        self.client.send_message.reset_mock()
        blocking_request(katcp.Message.request('watchdog'), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

    def test_timeout(self):
        """Test calling blocking_request with a timeout."""
        try:
            self.client.blocking_request(
                katcp.Message.request("slow-command", "0.5"),
                timeout=0.001)
        except RuntimeError, e:
            self.assertEqual(str(e), "Request slow-command timed out after"
                             " 0.001 seconds.")
        else:
            self.assertFalse("Expected timeout on request")


class TestCallbackClient(unittest.TestCase, TestUtilMixin):

    def setUp(self):
        self.addCleanup(self.stop_server_client)
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = katcp.CallbackClient(host, port)
        self.client.start(timeout=0.1)
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

        self.assertTrue(self.client.wait_protocol(0.2))
        self.client.callback_request(
            katcp.Message.request("watchdog"),
            reply_cb=watchdog_reply,
        )

        watchdog_replied.wait(0.2)
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
            katcp.Message.request("help"),
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
        self.client.send_message = mock.Mock()

        # By default message identifiers should be enabled, and should start
        # counting at 1
        self.client.callback_request(katcp.Message.request('watchdog'))
        self.client.callback_request(katcp.Message.request('watchdog'))
        self.client.callback_request(katcp.Message.request('watchdog'))
        # Extract katcp.Message object .mid attributes from the mock calls to
        # send_message
        mids = [args[0].mid              # arg[0] should be the Message() object
                for args, kwargs in self.client.send_message.call_args_list]
        self.assertEqual(mids, ['1','2','3'])
        self.client.send_message.reset_mock()

        # Explicitly ask for no mid to be used
        self.client.callback_request(
            katcp.Message.request('watchdog'), use_mid=False)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

        # Ask for a specific mid to be used
        self.client.send_message.reset_mock()
        self.client.callback_request(katcp.Message.request('watchdog', mid=42))
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, '42')

        ## Check situation for a katcpv4 server
        self.client._server_supports_ids = False

        # Should fail if an mid is passed
        with self.assertRaises(katcp.core.KatcpVersionError):
            self.client.callback_request(
                katcp.Message.request('watchdog', mid=42))

        # Should fail if an mid is requested
        with self.assertRaises(katcp.core.KatcpVersionError):
            self.client.callback_request(
                katcp.Message.request('watchdog'), use_mid=True)

        # Should use no mid by default
        self.client.send_message.reset_mock()
        self.client.callback_request(katcp.Message.request('watchdog'))
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
        self.client.callback_request(katcp.Message.request("help"))
        help_completed.wait(1)
        self.assertTrue(help_completed.isSet())

        self._assert_msgs_like(help_messages,
            [("#help[1] ", "")] * NO_HELP_MESSAGES +
            [("!help[1] ok %d" % NO_HELP_MESSAGES, "")])

    def test_no_timeout(self):
        self.client._request_timeout = None
        replies = []
        informs = []
        replied = threading.Event()
        def reply_handler(reply):
            replies.append(reply)
            replied.set()
        def inform_handler(reply):
            informs.append(reply)

        with mock.patch('katcp.client.threading.Timer') as MockTimer:
            self.client.callback_request(katcp.Message.request("help"),
                                reply_cb=reply_handler,
                                inform_cb=inform_handler)
        replied.wait(1)
        # With no timeout no Timer object should have been instantiated
        self.assertEqual(MockTimer.call_count, 0)
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(remove_version_connect(informs)), NO_HELP_MESSAGES)

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
            katcp.Message.request("slow-command", "0.1"),
            use_mid=use_mid,
            reply_cb=reply_cb,
            inform_cb=inform_cb,
            timeout=timeout,
        )

        reply_cb.assert_wait(1)
        self.assertEqual(len(replies), 1)
        self.assertEqual([msg.name for msg in replies], ["slow-command"])
        self.assertEqual([msg.arguments for msg in replies], [
                ["fail", "Timed out after %f seconds" % timeout]])
        self.assertEqual(len(remove_version_connect(informs)), 0)

        del replies[:]
        del informs[:]
        reply_cb.reset()

        # test next request succeeds
        self.client.callback_request(
            katcp.Message.request("slow-command", "0.05"),
            reply_cb=reply_cb,
            inform_cb=inform_cb,
        )

        reply_cb.assert_wait(1)
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
        self.client._handle_timeout('fake_msg_id')

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
            katcp.Message.request("help"),
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

        request = katcp.Message.request("help")

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
                print thread_id, len(informs)
                print [x.arguments[0] for x in informs]
            self.assertEqual(len(informs), NO_HELP_MESSAGES)

    def test_blocking_request(self):
        """Test the callback client's blocking request."""
        reply, informs = self.client.blocking_request(
            katcp.Message.request("help"),
        )

        self.assertEqual(reply.name, "help")
        self.assertEqual(reply.arguments, ["ok", "%d" % NO_HELP_MESSAGES])
        self.assertEqual(len(remove_version_connect(informs)), NO_HELP_MESSAGES)

        reply, informs = self.client.blocking_request(
            katcp.Message.request("slow-command", "0.5"),
            timeout=0.001)

        self.assertEqual(reply.name, "slow-command")
        self.assertEqual(reply.arguments[0], "fail")
        self.assertTrue(reply.arguments[1].startswith("Timed out after"))

    def test_blocking_request_mid(self):
        ## Test that the blocking client does the right thing with message
        ## identifiers

        # Wait for the client to detect the server protocol. Server should
        # support message identifiers
        self.assertTrue(self.client.wait_protocol(0.2))
        # Replace send_message so that we can check the message
        self.client.send_message = mock.Mock()

        # By default message identifiers should be enabled, and should start
        # counting at 1
        self.client.blocking_request(
            katcp.Message.request('watchdog'), timeout=0)
        self.client.blocking_request(
            katcp.Message.request('watchdog'), timeout=0)
        self.client.blocking_request(
            katcp.Message.request('watchdog'), timeout=0)
        # Extract katcp.Message object .mid attributes from the mock calls to
        # send_message
        mids = [args[0].mid              # arg[0] should be the Message() object
                for args, kwargs in self.client.send_message.call_args_list]
        self.assertEqual(mids, ['1','2','3'])
        self.client.send_message.reset_mock()

        # Explicitly ask for no mid to be used
        self.client.blocking_request(katcp.Message.request('watchdog'),
                                     use_mid=False, timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

        # Ask for a specific mid to be used
        self.client.send_message.reset_mock()
        self.client.blocking_request(katcp.Message.request(
            'watchdog', mid=42), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, '42')

        ## Check situation for a katcpv4 server
        self.client._server_supports_ids = False

        # Should fail if an mid is passed
        with self.assertRaises(katcp.core.KatcpVersionError):
            self.client.blocking_request(katcp.Message.request(
                'watchdog', mid=42), timeout=0)

        # Should fail if an mid is requested
        with self.assertRaises(katcp.core.KatcpVersionError):
            self.client.blocking_request(
                katcp.Message.request('watchdog'), use_mid=True, timeout=0)

        # Should use no mid by default
        self.client.send_message.reset_mock()
        self.client.blocking_request(katcp.Message.request(
            'watchdog'), timeout=0)
        mid = self.client.send_message.call_args[0][0].mid
        self.assertEqual(mid, None)

    def test_request_fail_on_raise(self):
        """Test that the callback is called even if send_message raises
           KatcpClientError."""
        def raise_error(msg):
            raise katcp.KatcpClientError("Error %s" % msg.name)
        self.client.send_message = raise_error

        replies = []

        def reply_cb(msg):
            replies.append(msg)

        self.client.callback_request(katcp.Message.request("foo"),
            reply_cb=reply_cb,
        )

        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].name, "foo")
        self.assertEqual(replies[0].arguments, ["fail", "Error foo"])

    def test_stop_join(self):
        # Set up a slow command to ensure that there is something in
        # the async queue
        with mock.patch('katcp.client.threading.Timer') as MockTimer:
            instance = MockTimer.return_value
            self.client.callback_request(
                    katcp.Message.request("slow-command", "10000"),
                    timeout=10000.1)
        # Exactly one instance of MockTimer should have been instantiated
        self.assertEqual(MockTimer.call_count, 1)
        self.client.stop(timeout=0.5)
        # Check that the timer's cancel() method has been called
        instance.cancel.assert_called_once_with()
        self.client.join(timeout=0.45)
        instance.join.assert_called_once_with(timeout=0.45)
        # It's OK not to have this in teardown since the client will
        # itself cancel the slow_command when it is stop()ed
        self.client.blocking_request(katcp.Message.request("cancel-slow-command"))
