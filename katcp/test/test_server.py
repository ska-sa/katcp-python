# test_server.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the server module.
   """

import unittest
import katcp
import time
import logging
import threading
from katcp.testutils import TestLogHandler, \
    BlockingTestClient, DeviceTestServer, TestUtilMixin

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)

NO_HELP_MESSAGES = 14         # Number of requests on DeviceTestServer

class TestDeviceServer(unittest.TestCase, TestUtilMixin):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        self.server.start(timeout=0.1)

        host, port = self.server._sock.getsockname()

        self.client = BlockingTestClient(self, host, port)
        self.client.start(timeout=0.1)

    def tearDown(self):
        if self.client.running():
            self.client.stop()
            self.client.join()
        if self.server.running():
            self.server.stop()
            self.server.join()

    def test_simple_connect(self):
        """Test a simple server setup and teardown with client connect."""
        get_msgs = self.client.message_recorder(
                blacklist=("version", "build-state"), replies=True)
        # basic send
        self.client.request(katcp.Message.request("foo"))

        # pipe-lined send
        self.client.raw_send("?bar-boom\r\n?baz\r")

        # broken up sends
        self.client.raw_send("?boo")
        self.client.raw_send("m arg1 arg2")
        self.client.raw_send("\n")

        time.sleep(0.1)

        self._assert_msgs_equal(get_msgs(), [
            r"!foo invalid Unknown\_request.",
            r"!bar-boom invalid Unknown\_request.",
            r"!baz invalid Unknown\_request.",
            r"!boom invalid Unknown\_request.",
        ])

    def test_bad_requests(self):
        """Test request failure paths in device server."""
        get_msgs = self.client.message_recorder(
                blacklist=("version", "build-state"), replies=True)
        self.client.raw_send("bad msg\n")
        # wait for reply
        self.client.blocking_request(katcp.Message.request("watchdog"))

        self._assert_msgs_like(get_msgs(), [
            (r"#log error", "KatcpSyntaxError:"
                            "\_Bad\_type\_character\_'b'.\\n"),
            (r"!watchdog ok", ""),
        ])

    def test_server_ignores_informs_and_replies(self):
        """Test server ignores informs and replies."""
        get_msgs = self.client.message_recorder(
                blacklist=("version", "build-state"), replies=True)
        self.client.raw_send("#some inform\n")
        self.client.raw_send("!some reply\n")

        time.sleep(0.1)

        self.assertFalse(get_msgs())

    def test_standard_requests(self):
        """Test standard request and replies."""
        get_msgs = self.client.message_recorder(
                blacklist=("version", "build-state"), replies=True)
        self.client.request(katcp.Message.request("watchdog"))
        self.client.request(katcp.Message.request("restart"))
        self.client.request(katcp.Message.request("log-level"))
        self.client.request(katcp.Message.request("log-level", "trace"))
        self.client.request(katcp.Message.request("log-level", "unknown"))
        self.client.request(katcp.Message.request("help"))
        self.client.request(katcp.Message.request("help", "watchdog"))
        self.client.request(katcp.Message.request("help", "unknown-request"))
        self.client.request(katcp.Message.request("client-list"))
        self.client.request(katcp.Message.request("sensor-list"))
        self.client.request(katcp.Message.request("sensor-list", "an.int"))
        self.client.request(katcp.Message.request("sensor-list", "an.unknown"))
        self.client.request(katcp.Message.request("sensor-value"))
        self.client.request(katcp.Message.request("sensor-value", "an.int"))
        self.client.request(katcp.Message.request("sensor-value",
                                                  "an.unknown"))
        self.client.request(katcp.Message.request("sensor-sampling", "an.int"))
        self.client.request(katcp.Message.request("sensor-sampling", "an.int",
                                                  "differential", "2"))
        self.client.request(katcp.Message.request("sensor-sampling"))
        self.client.request(katcp.Message.request("sensor-sampling",
                                                  "an.unknown", "auto"))
        self.client.blocking_request(katcp.Message.request("sensor-sampling",
                                                           "an.int",
                                                           "unknown"))

        time.sleep(0.1)

        self.server.log.trace("trace-msg")
        self.server.log.debug("debug-msg")
        self.server.log.info("info-msg")
        self.server.log.warn("warn-msg")
        self.server.log.error("error-msg")
        self.server.log.fatal("fatal-msg")

        time.sleep(0.1)

        self.assertEqual(self.server.restart_queue.get_nowait(), self.server)
        self._assert_msgs_like(get_msgs(), [
            (r"!watchdog ok", ""),
            (r"!restart ok", ""),
            (r"!log-level ok warn", ""),
            (r"!log-level ok trace", ""),
            (r"!log-level fail Unknown\_logging\_level\_name\_'unknown'", ""),
            (r"#help cancel-slow-command Cancel\_slow\_command\_request,\_"
             "resulting\_in\_it\_replying\_immedietely", ""),
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
            (r"#help sensor-value", ""),
            (r"#help slow-command", ""),
            (r"#help watchdog", ""),
            (r"!help ok %d" % NO_HELP_MESSAGES, ""),
            (r"#help watchdog", ""),
            (r"!help ok 1", ""),
            (r"!help fail", ""),
            (r"#client-list", ""),
            (r"!client-list ok 1", ""),
            (r"#sensor-list an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list ok 1", ""),
            (r"#sensor-list an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list ok 1", ""),
            (r"!sensor-list fail", ""),
            (r"#sensor-value 12345000 1 an.int nominal 3", ""),
            (r"!sensor-value ok 1", ""),
            (r"#sensor-value 12345000 1 an.int nominal 3", ""),
            (r"!sensor-value ok 1", ""),
            (r"!sensor-value fail", ""),
            (r"!sensor-sampling ok an.int none", ""),
            (r"#sensor-status 12345000 1 an.int nominal 3", ""),
            (r"!sensor-sampling ok an.int differential 2", ""),
            (r"!sensor-sampling fail No\_sensor\_name\_given.", ""),
            (r"!sensor-sampling fail Unknown\_sensor\_name.", ""),
            (r"!sensor-sampling fail Unknown\_strategy\_name.", ""),
            (r"#log trace", r"root trace-msg"),
            (r"#log debug", r"root debug-msg"),
            (r"#log info", r"root info-msg"),
            (r"#log warn", r"root warn-msg"),
            (r"#log error", r"root error-msg"),
            (r"#log fatal", r"root fatal-msg"),
        ])

    def test_standard_requests_with_ids(self):
        """Test standard request and replies with message ids."""
        get_msgs = self.client.message_recorder(
                blacklist=("version", "build-state"), replies=True)

        current_id = [0]

        def mid():
            current_id[0] += 1
            return current_id[0]

        self.client.request(katcp.Message.request("watchdog", mid=mid()))
        self.client.request(katcp.Message.request("restart", mid=mid()))
        self.client.request(katcp.Message.request("log-level", mid=mid()))
        self.client.request(katcp.Message.request("log-level", "trace",
                                                  mid=mid()))
        self.client.request(katcp.Message.request("log-level", "unknown",
                                                  mid=mid()))
        self.client.request(katcp.Message.request("help", mid=mid()))
        self.client.request(katcp.Message.request("help", "watchdog",
                                                  mid=mid()))
        self.client.request(katcp.Message.request("help", "unknown-request",
                                                  mid=mid()))
        self.client.request(katcp.Message.request("client-list", mid=mid()))
        self.client.request(katcp.Message.request("sensor-list", mid=mid()))
        self.client.request(katcp.Message.request("sensor-list", "an.int",
                                                  mid=mid()))
        self.client.request(katcp.Message.request("sensor-list", "an.unknown",
                                                  mid=mid()))
        self.client.request(katcp.Message.request("sensor-value", mid=mid()))
        self.client.request(katcp.Message.request("sensor-value", "an.int",
                                                  mid=mid()))
        self.client.request(katcp.Message.request("sensor-value", "an.unknown",
                                                  mid=mid()))
        self.client.blocking_request(katcp.Message.request("sensor-sampling",
                                                           "an.int",
                                                           mid=mid()))
        self.client.blocking_request(katcp.Message.request("sensor-sampling",
                                                           "an.int",
                                                           "differential", "2",
                                                           mid=mid()))
        self.client.blocking_request(katcp.Message.request("sensor-sampling",
                                                           mid=mid()))
        self.client.blocking_request(katcp.Message.request("sensor-sampling",
                                                           "an.unknown",
                                                           "auto", mid=mid()))
        self.client.blocking_request(katcp.Message.request("sensor-sampling",
                                                           "an.int", "unknown",
                                                           mid=mid()))

        self.server.log.trace("trace-msg")
        self.server.log.debug("debug-msg")
        self.server.log.info("info-msg")
        self.server.log.warn("warn-msg")
        self.server.log.error("error-msg")
        self.server.log.fatal("fatal-msg")

        time.sleep(0.1)

        self.assertEqual(self.server.restart_queue.get_nowait(), self.server)
        msgs = get_msgs()
        self._assert_msgs_like(msgs, [
            (r"!watchdog[1] ok", ""),
            (r"!restart[2] ok", ""),
            (r"!log-level[3] ok warn", ""),
            (r"!log-level[4] ok trace", ""),
            (r"!log-level[5] fail Unknown\_logging\_level\_name\_'unknown'",
             ""),
            (r"#help[6] cancel-slow-command Cancel\_slow\_command\_request,\_"
             "resulting\_in\_it\_replying\_immedietely", ""),
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
            (r"#help[6] sensor-value", ""),
            (r"#help[6] slow-command", ""),
            (r"#help[6] watchdog", ""),
            (r"!help[6] ok %d" % NO_HELP_MESSAGES, ""),
            (r"#help[7] watchdog", ""),
            (r"!help[7] ok 1", ""),
            (r"!help[8] fail", ""),
            (r"#client-list[9]", ""),
            (r"!client-list[9] ok 1", ""),
            (r"#sensor-list[10] an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list[10] ok 1", ""),
            (r"#sensor-list[11] an.int An\_Integer. count integer -5 5", ""),
            (r"!sensor-list[11] ok 1", ""),
            (r"!sensor-list[12] fail", ""),
            (r"#sensor-value[13] 12345000 1 an.int nominal 3", ""),
            (r"!sensor-value[13] ok 1", ""),
            (r"#sensor-value[14] 12345000 1 an.int nominal 3", ""),
            (r"!sensor-value[14] ok 1", ""),
            (r"!sensor-value[15] fail", ""),
            (r"!sensor-sampling[16] ok an.int none", ""),
            (r"#sensor-status 12345000 1 an.int nominal 3", ""),
            (r"!sensor-sampling[17] ok an.int differential 2", ""),
            (r"!sensor-sampling[18] fail No\_sensor\_name\_given.", ""),
            (r"!sensor-sampling[19] fail Unknown\_sensor\_name.", ""),
            (r"!sensor-sampling[20] fail Unknown\_strategy\_name.", ""),
            (r"#log trace", r"root trace-msg"),
            (r"#log debug", r"root debug-msg"),
            (r"#log info", r"root info-msg"),
            (r"#log warn", r"root warn-msg"),
            (r"#log error", r"root error-msg"),
            (r"#log fatal", r"root fatal-msg"),
        ])

    def test_sensor_list_regex(self):
        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-list", "/a.*/"))
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-list an.int An\_Integer. count integer -5 5",
            r"!sensor-list ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-list", "//"))
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-list an.int An\_Integer. count integer -5 5",
            r"!sensor-list ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-list", "/^int/"))
        self._assert_msgs_equal(informs + [reply], [
            r"!sensor-list ok 0",
        ])

    def test_sensor_value_regex(self):
        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-value", "/a.*/"))
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-value 12345000 1 an.int nominal 3",
            r"!sensor-value ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-value", "//"))
        self._assert_msgs_equal(informs + [reply], [
            r"#sensor-value 12345000 1 an.int nominal 3",
            r"!sensor-value ok 1",
        ])

        reply, informs = self.client.blocking_request(katcp.Message.request(
                "sensor-value", "/^int/"))
        self._assert_msgs_equal(informs + [reply], [
            r"!sensor-value ok 0",
        ])

    def test_halt_request(self):
        """Test halt request."""
        get_msgs = self.client.message_recorder(
                blacklist=("version", "build-state"), replies=True)
        self.client.request(katcp.Message.request("halt"))
        # hack to hide re-connect exception
        self.client.connect = lambda: None
        self.server.join()
        time.sleep(0.1)

        self._assert_msgs_equal(get_msgs(), [
            r"!halt ok",
            r"#disconnect Device\_server\_shutting\_down.",
        ])

    def test_bad_handlers(self):
        """Test that bad request and inform handlers are picked up."""
        try:

            class BadServer(katcp.DeviceServer):
                def request_baz(self, sock, msg):
                    pass

        except AssertionError:
            pass
        else:
            self.fail("Server metaclass accepted missing request_ docstring.")

        try:

            class BadServer(katcp.DeviceServer):
                def inform_baz(self, sock, msg):
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
                blacklist=("version", "build-state"), replies=True)

        self.client.request(katcp.Message.request("raise-exception"))
        self.client.request(katcp.Message.request("raise-fail"))

        time.sleep(0.1)

        self._assert_msgs_like(get_msgs(), [
            (r"!raise-exception fail Traceback", ""),
            (r"!raise-fail fail There\_was\_a\_problem\_with\_your\_request.",
             ""),
        ])

    def test_stop_and_restart(self):
        """Test stopping and restarting the device server."""
        self.server.stop(timeout=0.1)
        self.server.join(timeout=1.0)
        self.assertEqual(self.server._thread, None)
        self.assertFalse(self.server._running.isSet())
        self.server.start(timeout=1.0)

    def test_bad_client_socket(self):
        """Test what happens when select is called on a dead client socket."""
        # wait for client to arrive
        time.sleep(0.1)

        # close socket while the server isn't looking
        # then wait for the server to notice
        sock = self.server._socks[0]
        sock.close()
        time.sleep(0.75)

        # check that client was removed
        self.assertTrue(sock not in self.server._socks,
                        "Expected %r to not be in %r" %
                        (sock, self.server._socks))

    def test_bad_server_socket(self):
        """Test what happens when select is called on a dead server socket."""
        # wait for client to arrive
        time.sleep(0.1)

        # close socket while the server isn't looking
        # then wait for the server to notice
        sock = self.server._sock
        sockname = sock.getsockname()
        sock.close()
        time.sleep(0.75)

        # check that server restarted
        self.assertTrue(sock is not self.server._sock,
                        "Expected %r to not be %r" % (sock, self.server._sock))
        self.assertEqual(sockname, self.server._sock.getsockname())

    def test_daemon_value(self):
        """Test passing in a daemon value to server start method."""
        self.server.stop(timeout=0.1)
        self.server.join(timeout=1.0)

        self.server.start(timeout=0.1, daemon=True)
        self.assertTrue(self.server._thread.isDaemon())

    def test_excepthook(self):
        """Test passing in an excepthook to server start method."""
        exceptions = []
        except_event = threading.Event()

        def excepthook(etype, value, traceback):
            """Keep track of exceptions."""
            exceptions.append(etype)
            except_event.set()

        self.server.stop(timeout=0.1)
        self.server.join(timeout=1.5)

        self.server.start(timeout=0.1, excepthook=excepthook)
        # force exception by deleteing _running
        old_running = self.server._running
        try:
            del self.server._running
            except_event.wait(1.5)
            self.assertEqual(exceptions, [AttributeError])
        finally:
            self.server._running = old_running

        # close socket -- server didn't shut down correctly
        self.server._sock.close()
        self.server.stop(timeout=0.1)
        self.server.join(timeout=1.5)

        except_event.clear()
        del exceptions[:]
        self.server.start(timeout=0.1, excepthook=excepthook)
        # force exception in sample reactor and check that it makes
        # it back up
        reactor = self.server._reactor
        old_stop = reactor._stopEvent
        try:
            del reactor._stopEvent
            reactor._wakeEvent.set()
            except_event.wait(0.1)
            self.assertEqual(exceptions, [AttributeError])
        finally:
            reactor._stopEvent = old_stop

        # close socket -- server didn't shut down correctly
        self.server._sock.close()

    def test_sampling(self):
        """Test sensor sampling."""
        get_msgs = self.client.message_recorder(
                blacklist=("version", "build-state"), replies=True)
        self.client.request(katcp.Message.request("sensor-sampling", "an.int",
                                                  "period", 100))
        time.sleep(1.0)
        self.client.request(katcp.Message.request("sensor-sampling", "an.int",
                                                  "none"))
        time.sleep(0.5)

        msgs = get_msgs()
        updates = [x for x in msgs if x.name == "sensor-status"]
        others = [x for x in msgs if x.name != "sensor-status"]
        self.assertTrue(abs(len(updates) - 12) < 2,
                        "Expected 12 informs, saw %d." % len(updates))

        self._assert_msgs_equal(others, [
            r"!sensor-sampling ok an.int period 100",
            r"!sensor-sampling ok an.int none",
        ])

        self.assertEqual(updates[0].arguments[1:],
                         ["1", "an.int", "nominal", "3"])

    def test_add_remove_sensors(self):
        """Test adding and removing sensors from a running device."""
        an_int = self.server._sensors["an.int"]
        self.server.remove_sensor(an_int)
        self.server.add_sensor(an_int)
        self.test_sampling()
