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
from katcp.testutils import (TestLogHandler, DeviceTestServer, TestUtilMixin,
                             counting_callback)

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)

NO_HELP_MESSAGES = 14         # Number of requests on DeviceTestServer

class TestDeviceClient(unittest.TestCase, TestUtilMixin):
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
        assert reply.arguments == ["ok", "%d" % NO_HELP_MESSAGES]
        assert len(informs) == int(reply.arguments[1])

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
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = katcp.CallbackClient(host, port)
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
            self.assertEqual(reply.arguments, ["ok", "%d" % NO_HELP_MESSAGES])
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

        time.sleep(0.2)
        self.assertEqual(len(help_replies), 1)
        self.assertEqual(len(help_informs), NO_HELP_MESSAGES)

    def test_no_callback(self):
        """Test request without callback."""

        help_messages = []

        def handle_help_message(client, msg):
            help_messages.append(msg)

        self.client._inform_handlers["help"] = handle_help_message
        self.client._reply_handlers["help"] = handle_help_message

        self.client.request(katcp.Message.request("help"))

        time.sleep(0.1)

        self._assert_msgs_like(help_messages,
            [("#help ", "")] * NO_HELP_MESSAGES +
            [("!help ok %d" % NO_HELP_MESSAGES, "")])

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
            self.client.request(katcp.Message.request("help"),
                                reply_cb=reply_handler,
                                inform_cb=inform_handler)
        replied.wait(0.1)
        # With no timeout no Timer object should have been instantiated
        self.assertEqual(MockTimer.call_count, 0)
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(informs), NO_HELP_MESSAGES)

    def test_timeout(self):
        """Test requests that timeout."""

        replies = []
        informs = []
        timeout = 0.001

        @counting_callback()
        def reply_cb(msg):
            replies.append(msg)

        def inform_cb(msg):
            informs.append(msg)

        self.client.request(
            katcp.Message.request("slow-command", "0.1"),
            reply_cb=reply_cb,
            inform_cb=inform_cb,
            timeout=timeout,
        )

        reply_cb.assert_wait(1)
        self.assertEqual(len(replies), 1)
        self.assertEqual([msg.name for msg in replies], ["slow-command"])
        self.assertEqual([msg.arguments for msg in replies], [
                ["fail", "Timed out after %f seconds" % timeout]])
        self.assertEqual(len(informs), 0)

        del replies[:]
        del informs[:]
        reply_cb.reset()

        # test next request succeeds
        self.client.request(
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
            user_data=(5, "foo"))

        time.sleep(0.1)
        self.assertEqual(len(help_replies), 1)
        self.assertEqual(len(help_informs), NO_HELP_MESSAGES)

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
            done.wait(2.0)
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].arguments[0], "ok")
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
        self.assertEqual(len(informs), NO_HELP_MESSAGES)

        reply, informs = self.client.blocking_request(
            katcp.Message.request("slow-command", "0.5"),
            timeout=0.001)

        self.assertEqual(reply.name, "slow-command")
        self.assertEqual(reply.arguments[0], "fail")
        self.assertTrue(reply.arguments[1].startswith("Timed out after"))

    def test_use_ids(self):
        """Test the callbak client's use of message ids."""
        self.client._use_ids = True

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
            self.assertEqual(reply.arguments, ["ok", "%d" % NO_HELP_MESSAGES])
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

        time.sleep(0.2)
        self.assertEqual(len(help_replies), 1)
        self.assertEqual(len(help_informs), NO_HELP_MESSAGES)

    def test_request_fail_on_raise(self):
        """Test that the callback is called even if send_message raises
           KatcpClientError."""
        def raise_error(msg):
            raise katcp.KatcpClientError("Error %s" % msg.name)
        self.client.send_message = raise_error

        replies = []

        def reply_cb(msg):
            replies.append(msg)

        self.client.request(katcp.Message.request("foo"),
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
            self.client.request(
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
