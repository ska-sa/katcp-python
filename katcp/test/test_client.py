"""Tests for client module."""

import unittest
import katcp
import time
import katcp.client
import logging
import threading
from katcp.testutils import TestLogHandler, \
    DeviceTestClient, CallbackTestClient, DeviceTestServer, \
    TestUtilMixin

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)


class TestDeviceClient(unittest.TestCase, TestUtilMixin):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = DeviceTestClient(host, port)
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
        self.client.request(katcp.Message.request("watchdog"))

        time.sleep(0.1)

        msgs = self.server.messages()
        self._assert_msgs_equal(msgs, [
            r"?watchdog",
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

class TestBlockingClient(unittest.TestCase):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = katcp.BlockingClient(host, port)
        self.client.start(timeout=0.1)

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
        assert reply.name == "watchdog"
        assert reply.arguments == ["ok"]
        assert informs == []

        reply, informs = self.client.blocking_request(
            katcp.Message.request("help"))
        assert reply.name == "help"
        assert reply.arguments == ["ok", "12"]
        assert len(informs) == int(reply.arguments[1])


class TestCallbackClient(unittest.TestCase, TestUtilMixin):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = CallbackTestClient(host, port)
        self.client.start(timeout=0.1)

    def tearDown(self):
        if self.client.running():
            self.client.stop()
            self.client.join()
        if self.server.running():
            self.server.stop()
            self.server.join()

    def test_callback_request(self):
        """Test callback request."""

        watchdog_replies = []

        def watchdog_reply(reply):
            self.assertEqual(reply.name, "watchdog")
            self.assertEqual(reply.arguments, ["ok"])
            watchdog_replies.append(reply)

        self.client.request(
            katcp.Message.request("watchdog"),
            reply_cb=watchdog_reply,
        )

        time.sleep(0.1)
        self.assertTrue(watchdog_replies)

        help_replies = []
        help_informs = []

        def help_reply(reply):
            self.assertEqual(reply.name, "help")
            self.assertEqual(reply.arguments, ["ok", "12"])
            self.assertEqual(len(help_informs), int(reply.arguments[1]))
            help_replies.append(reply)

        def help_inform(inform):
            self.assertEqual(inform.name, "help")
            self.assertEqual(len(inform.arguments), 2)
            help_informs.append(inform)

        self.client.request(
            katcp.Message.request("help"),
            reply_cb=help_reply,
            inform_cb=help_inform,
        )

        time.sleep(0.1)
        self.assertEqual(len(help_replies), 1)
        self.assertEqual(len(help_informs), 12)

    def test_no_callback(self):
        """Test request without callback."""

        self.client.request(
            katcp.Message.request("help")
        )

        time.sleep(0.1)
        msgs = self.client.messages()

        self._assert_msgs_like(msgs,
            [("#version ", "")] +
            [("#build-state ", "")] +
            [("#help ", "")]*12 +
            [("!help ok 12", "")]
        )

    def test_user_data(self):
        """Test callbacks with user data."""
        help_replies = []
        help_informs = []

        def help_reply(reply, x, y):
            self.assertEqual(reply.name, "help")
            self.assertEqual(x, 5)
            self.assertEqual(y, "foo")
            help_replies.append(reply)

        def help_inform(inform, x, y):
            self.assertEqual(inform.name, "help")
            self.assertEqual(x, 5)
            self.assertEqual(y, "foo")
            help_informs.append(inform)

        self.client.request(
            katcp.Message.request("help"),
            reply_cb=help_reply,
            inform_cb=help_inform,
            user_data=(5, "foo")
        )

        time.sleep(0.1)
        self.assertEqual(len(help_replies), 1)
        self.assertEqual(len(help_informs), 12)

    def test_twenty_thread_mayhem(self):
        """Test using callbacks from twenty threads simultaneously."""
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
            self.client.request(
                request,
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
            done.wait(1.0)
            self.assertEqual(len(replies), 1)
            if len(informs) != 12:
                print thread_id, len(informs)
                print [x.arguments[0] for x in informs]
            self.assertEqual(len(informs), 12)
