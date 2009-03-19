"""Tests for the katcp module."""

import unittest
import katcp
import time
import katcp.sampling
import logging

class TestLogHandler(logging.Handler):
    """A logger for KATCP tests."""

    def __init__(self):
        """Create a TestLogHandler."""
        logging.Handler.__init__(self)
        self._records = []

    def emit(self, record):
        """Handle the arrival of a log message."""
        self._records.append(record)

    def clear(self):
        """Clear the list of remembered logs."""
        self._records = []

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)


class TestMessageParser(unittest.TestCase):
    def setUp(self):
        self.p = katcp.MessageParser()

    def test_simple_messages(self):
        """Test simple messages."""
        m = self.p.parse("?foo")
        self.assertEqual(m.mtype, m.REQUEST)
        self.assertEqual(m.name, "foo")
        self.assertEqual(m.arguments, [])

        m = self.p.parse("#bar 123 baz 1.000e-05")
        self.assertEqual(m.mtype, m.INFORM)
        self.assertEqual(m.name, "bar")
        self.assertEqual(m.arguments, ["123", "baz", "1.000e-05"])

        m = self.p.parse("!baz a17 goo")
        self.assertEqual(m.mtype, m.REPLY)
        self.assertEqual(m.name, "baz")
        self.assertEqual(m.arguments, ["a17", "goo"])

    def test_escape_sequences(self):
        """Test escape sequences."""
        m = self.p.parse(r"?foo \\\_\0\n\r\e\t\@")
        self.assertEqual(m.arguments, ["\\ \0\n\r\x1b\t"])

        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"?foo \z")

        # test unescaped null
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, "?foo \0")

    def test_syntax_errors(self):
        """Test generation of syntax errors."""
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r" ?foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"? foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"?1foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r">foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, "!foo \\")

    def test_message_to_string(self):
        """Test message to string round trip with escapes."""
        for m_str in [
            "?bar",
            r"?foo \\\_\0\n\r\e\t",
        ]:
            self.assertEqual(m_str, str(self.p.parse(m_str)))

    def test_command_names(self):
        """Test a variety of command names."""
        m = self.p.parse("!baz-bar")
        self.assertEqual(m.name, "baz-bar")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"?-foo")

    def test_empty_params(self):
        """Test parsing messages with empty parameters."""
        m = self.p.parse("!foo \@") # 1 empty parameter
        self.assertEqual(m.arguments, [""])
        m = self.p.parse("!foo \@ \@") # 2 empty parameter
        self.assertEqual(m.arguments, ["", ""])
        m = self.p.parse("!foo \_ \_ \@") # space, space, empty
        self.assertEqual(m.arguments, [" ", " ", ""])

    def test_whitespace(self):
        """Test parsing of whitespace between parameters."""
        m = self.p.parse("!baz   \@   ") # 1 empty parameter
        self.assertEqual(m.arguments, [""])
        m = self.p.parse("!baz\t\@\t\@") # 2 empty parameter
        self.assertEqual(m.arguments, ["", ""])
        m = self.p.parse("!baz\t \t\_\t\t\t \_\t\@   \t") # space, space, empty
        self.assertEqual(m.arguments, [" ", " ", ""])

    def test_formfeed(self):
        """Test that form feeds are not treated as whitespace."""
        m = self.p.parse("!baz \fa\fb\f")
        self.assertEqual(m.arguments, ["\fa\fb\f"])

class DeviceTestSensor(katcp.Sensor):
    def __init__(self, sensor_type, name, description, units, params,
                 timestamp, status, value):
        super(DeviceTestSensor, self).__init__(
            sensor_type, name, description, units, params)
        self.set(timestamp, status, value)
        self.__sampling_changes = []

    def _apply_sampling_change(self, strategy, params):
        self.__sampling_changes.append((strategy, params))

    def get_changes(self):
        return self.__sampling_changes


class TestSensor(unittest.TestCase):
    def test_int_sensor(self):
        """Test integer sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3
        )
        self.assertEqual(s.read_formatted(), ("12345000", "nominal", "3"))
        self.assertEquals(s.parse_value("3"), 3)
        self.assertRaises(ValueError, s.parse_value, "4")
        self.assertRaises(ValueError, s.parse_value, "-10")
        self.assertRaises(ValueError, s.parse_value, "asd")

    def test_float_sensor(self):
        """Test float sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.FLOAT, "a.float", "A float.", "power",
                [0.0, 5.0],
                timestamp=12345, status=katcp.Sensor.WARN, value=3.0
        )
        self.assertEqual(s.read_formatted(), ("12345000", "warn", "3.000000e+00"))
        self.assertEquals(s.parse_value("3"), 3.0)
        self.assertRaises(ValueError, s.parse_value, "10")
        self.assertRaises(ValueError, s.parse_value, "-10")
        self.assertRaises(ValueError, s.parse_value, "asd")

    def test_boolean_sensor(self):
        """Test boolean sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.BOOLEAN, "a.boolean", "A boolean.", "on/off",
                None,
                timestamp=12345, status=katcp.Sensor.UNKNOWN, value=True
        )
        self.assertEqual(s.read_formatted(), ("12345000", "unknown", "1"))
        self.assertEquals(s.parse_value("1"), True)
        self.assertEquals(s.parse_value("0"), False)
        self.assertRaises(ValueError, s.parse_value, "asd")

    def test_discrete_sensor(self):
        """Test discrete sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.DISCRETE, "a.discrete", "A discrete sensor.", "state",
                ["on", "off"],
                timestamp=12345, status=katcp.Sensor.ERROR, value="on"
        )
        self.assertEqual(s.read_formatted(), ("12345000", "error", "on"))
        self.assertEquals(s.parse_value("on"), "on")
        self.assertRaises(ValueError, s.parse_value, "fish")

    def test_lru_sensor(self):
        """Test LRU sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.LRU, "an.lru", "An LRU sensor.", "state",
                None,
                timestamp=12345, status=katcp.Sensor.FAILURE,
                value=katcp.Sensor.LRU_ERROR
        )
        self.assertEqual(s.read_formatted(), ("12345000", "failure", "error"))
        self.assertEquals(s.parse_value("nominal"), katcp.Sensor.LRU_NOMINAL)
        self.assertRaises(ValueError, s.parse_value, "fish")

    def test_string_sensor(self):
        """Test string sensor."""
        s = DeviceTestSensor(
            katcp.Sensor.STRING, "a.string", "A string sensor.", "filename",
            None,
            timestamp=12345, status=katcp.Sensor.NOMINAL, value="zwoop"
        )
        self.assertEqual(s.read_formatted(), ("12345000", "nominal", "zwoop"))
        self.assertEquals(s.parse_value("bar foo"), "bar foo")

    def test_timestamp_sensor(self):
        """Test timestamp sensor."""
        s = DeviceTestSensor(
            katcp.Sensor.TIMESTAMP, "a.timestamp", "A timestamp sensor.", "ms",
            None,
            timestamp=12345, status=katcp.Sensor.NOMINAL, value=1001.9
        )
        self.assertEqual(s.read_formatted(), ("12345000", "nominal", "1001900"))
        self.assertAlmostEqual(s.parse_value("1002100"), 1002.1)
        self.assertRaises(ValueError, s.parse_value, "bicycle")

    def test_sampling(self):
        """Test getting and setting the sampling."""
        s = DeviceTestSensor(
                katcp.Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3
        )
        katcp.sampling.SampleNone(None, s)
        katcp.sampling.SampleAuto(None, s)
        katcp.sampling.SamplePeriod(None, s, 10)
        katcp.sampling.SampleEvent(None, s)
        katcp.sampling.SampleDifferential(None, s, 2)
        self.assertRaises(ValueError, katcp.sampling.SampleNone, None, s, "foo")
        self.assertRaises(ValueError, katcp.sampling.SampleAuto, None, s, "bar")
        self.assertRaises(ValueError, katcp.sampling.SamplePeriod, None, s)
        self.assertRaises(ValueError, katcp.sampling.SamplePeriod, None, s, "1.5")
        self.assertRaises(ValueError, katcp.sampling.SamplePeriod, None, s, "-1")
        self.assertRaises(ValueError, katcp.sampling.SampleEvent, None, s, "foo")
        self.assertRaises(ValueError, katcp.sampling.SampleDifferential, None, s)
        self.assertRaises(ValueError, katcp.sampling.SampleDifferential, None, s, "-1")
        self.assertRaises(ValueError, katcp.sampling.SampleDifferential, None, s, "1.5")

        katcp.sampling.SampleStrategy.get_strategy("none", None, s)
        katcp.sampling.SampleStrategy.get_strategy("auto", None, s)
        katcp.sampling.SampleStrategy.get_strategy("period", None, s, "15")
        katcp.sampling.SampleStrategy.get_strategy("event", None, s)
        katcp.sampling.SampleStrategy.get_strategy("differential", None, s, "2")
        self.assertRaises(ValueError, katcp.sampling.SampleStrategy.get_strategy, "random", None, s)
        self.assertRaises(ValueError, katcp.sampling.SampleStrategy.get_strategy, "period", None, s, "foo")
        self.assertRaises(ValueError, katcp.sampling.SampleStrategy.get_strategy, "differential", None, s, "bar")

class DeviceTestClient(katcp.DeviceClient):
    def __init__(self, *args, **kwargs):
        super(DeviceTestClient, self).__init__(*args, **kwargs)
        self.__msgs = []

    def raw_send(self, chunk):
        """Send a raw chunk of data to the server."""
        self._sock.send(chunk)

    def inform_version(self, msg):
        """handle version inform message"""
        self.__msgs.append(msg)

    def inform_build_state(self, msg):
        """handle build state inform message"""
        self.__msgs.append(msg)

    def inform_log(self, msg):
        """handle log inform message"""
        self.__msgs.append(msg)

    def inform_disconnect(self, msg):
        """handle disconnect inform message"""
        self.__msgs.append(msg)

    def reply_halt(self, msg):
        """handle halt reply message"""
        self.__msgs.append(msg)

    def unhandled_reply(self, msg):
        """Fallback method for reply messages without a registered handler"""
        self.__msgs.append(msg)

    def unhandled_inform(self, msg):
        """Fallback method for inform messages without a registered handler"""
        self.__msgs.append(msg)

    def messages(self):
        return self.__msgs


class DeviceTestServer(katcp.DeviceServer):
    def __init__(self, *args, **kwargs):
        super(DeviceTestServer, self).__init__(*args, **kwargs)
        self.__msgs = []

    def setup_sensors(self):
        self.restarted = False
        self.add_sensor(DeviceTestSensor(
            katcp.Sensor.INTEGER, "an.int", "An Integer.", "count",
            [-5, 5],
            timestamp=12345, status=katcp.Sensor.NOMINAL, value=3
        ))

    def schedule_restart(self):
        self.restarted = True

    def request_new_command(self, sock, msg):
        """A new command."""
        return katcp.Message.reply(msg.name, "ok", "param1", "param2")

    def request_raise_exception(self, sock, msg):
        """A handler which raises an exception."""
        raise Exception("An exception occurred!")

    def request_raise_fail(self, sock, msg):
        """A handler which raises a FailReply."""
        raise katcp.FailReply("There was a problem with your request.")

    def handle_message(self, sock, msg):
        self.__msgs.append(msg)
        super(DeviceTestServer, self).handle_message(sock, msg)

    def messages(self):
        return self.__msgs


class TestUtilMixin(object):
    """Mixin class implementing test helper methods for making
       assertions about lists of KATCP messages.
       """

    def _assert_msgs_length(self, actual_msgs, expected_number):
        """Assert that the number of messages is that expected."""
        num_msgs = len(actual_msgs)
        if num_msgs < expected_number:
            self.assertEqual(num_msgs, expected_number,
                             "Too few messages received.")
        elif num_msgs > expected_number:
            self.assertEqual(num_msgs, expected_number,
                             "Too many messages received.")

    def _assert_msgs_equal(self, actual_msgs, expected_msgs):
        """Assert that the actual and expected messages are equal.

           actual_msgs: list of message objects received
           expected_msgs: expected message strings
           """
        for msg, msg_str in zip(actual_msgs, expected_msgs):
            self.assertEqual(str(msg), msg_str)
        self._assert_msgs_length(actual_msgs, len(expected_msgs))

    def _assert_msgs_like(self, actual_msgs, expected):
        """Assert that the actual messages start and end with
           the expected strings.

           actual_msgs: list of message objects received
           expected_msgs: tuples of (expected_prefix, expected_suffix)
           """
        for msg, (prefix, suffix) in zip(actual_msgs, expected):
            str_msg = str(msg)

            if prefix and not str_msg.startswith(prefix):
                self.assertEqual(str_msg, prefix,
                    msg="Message '%s' does not start with '%s'."
                    % (str_msg, prefix)
                )

            if suffix and not str_msg.endswith(suffix):
                self.assertEqual(str_msg, suffix,
                    msg="Message '%s' does not end with '%s'."
                    % (str_msg, suffix)
                )
        self._assert_msgs_length(actual_msgs, len(expected))


class TestDeviceServer(unittest.TestCase, TestUtilMixin):
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

    def test_simple_connect(self):
        """Test a simple server setup and teardown with client connect."""
        # basic send
        self.client.request(katcp.Message.request("foo"))

        # pipe-lined send
        self.client.raw_send("?bar-boom\r\n?baz\r")

        # broken up sends
        self.client.raw_send("?boo")
        self.client.raw_send("m arg1 arg2")
        self.client.raw_send("\n")

        time.sleep(0.1)

        msgs = self.client.messages()
        self._assert_msgs_equal(msgs, [
            r"#version device_stub-0.1",
            r"#build-state name-0.1",
            r"!foo invalid Unknown\_request.",
            r"!bar-boom invalid Unknown\_request.",
            r"!baz invalid Unknown\_request.",
            r"!boom invalid Unknown\_request.",
        ])

    def test_bad_requests(self):
        """Test request failure paths in device server."""
        self.client.raw_send("bad msg\n")

        time.sleep(0.1)

        msgs = self.client.messages()
        self._assert_msgs_like(msgs, [
            (r"#version device_stub-0.1", ""),
            (r"#build-state name-0.1", ""),
            (r"#log error", "KatcpSyntaxError:\_Bad\_type\_character\_'b'.\\n"),
        ])

    def test_server_ignores_informs_and_replies(self):
        """Test server ignores informs and replies."""
        self.client.raw_send("#some inform\n")
        self.client.raw_send("!some reply\n")

        time.sleep(0.1)

        msgs = self.client.messages()
        self._assert_msgs_like(msgs, [
            (r"#version device_stub-0.1", ""),
            (r"#build-state name-0.1", ""),
        ])

    def test_standard_requests(self):
        """Test standard request and replies."""
        self.client.request(katcp.Message.request("watchdog"))
        self.client.request(katcp.Message.request("restart"))
        self.client.request(katcp.Message.request("log-level"))
        self.client.request(katcp.Message.request("log-level", "trace"))
        self.client.request(katcp.Message.request("help"))
        self.client.request(katcp.Message.request("help", "watchdog"))
        self.client.request(katcp.Message.request("help", "unknown-request"))
        self.client.request(katcp.Message.request("sensor-list"))
        self.client.request(katcp.Message.request("sensor-list", "an.int"))
        self.client.request(katcp.Message.request("sensor-list", "an.unknown"))
        self.client.request(katcp.Message.request("sensor-value"))
        self.client.request(katcp.Message.request("sensor-value", "an.int"))
        self.client.request(katcp.Message.request("sensor-value", "an.unknown"))
        self.client.request(katcp.Message.request("sensor-sampling", "an.int"))
        self.client.request(katcp.Message.request("sensor-sampling", "an.int",
                                                  "differential", "2"))

        time.sleep(0.1)

        self.server.log.trace("trace-msg")
        self.server.log.debug("debug-msg")
        self.server.log.info("info-msg")
        self.server.log.warn("warn-msg")
        self.server.log.error("error-msg")
        self.server.log.fatal("fatal-msg")

        time.sleep(0.1)

        msgs = self.client.messages()
        self.assertEqual(self.server.restarted, True)
        self._assert_msgs_like(msgs, [
            (r"#version device_stub-0.1", ""),
            (r"#build-state name-0.1", ""),
            (r"!watchdog ok", ""),
            (r"!restart ok", ""),
            (r"!log-level ok warn", ""),
            (r"!log-level ok trace", ""),
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
            (r"#help watchdog", ""),
            (r"!help ok 12", ""),
            (r"#help watchdog", ""),
            (r"!help ok 1", ""),
            (r"!help fail", ""),
            (r"#sensor-list an.int integer An\_Integer. count -5 5", ""),
            (r"!sensor-list ok 1", ""),
            (r"#sensor-list an.int integer An\_Integer. count -5 5", ""),
            (r"!sensor-list ok 1", ""),
            (r"!sensor-list fail", ""),
            (r"!sensor-value fail", ""),
            (r"!sensor-value ok 12345000 1 an.int nominal 3", ""),
            (r"!sensor-value fail", ""),
            (r"!sensor-sampling ok an.int none", ""),
            (r"!sensor-sampling ok an.int differential 2", ""),
            (r"#log trace", r"root trace-msg"),
            (r"#log debug", r"root debug-msg"),
            (r"#log info", r"root info-msg"),
            (r"#log warn", r"root warn-msg"),
            (r"#log error", r"root error-msg"),
            (r"#log fatal", r"root fatal-msg"),
        ])

    def test_halt_request(self):
        """Test halt request."""
        self.client.request(katcp.Message.request("halt"))
        # hack to hide re-connect exception
        self.client.connect = lambda: None
        self.server.join()
        time.sleep(0.1)

        msgs = self.client.messages()
        self._assert_msgs_equal(msgs, [
            r"#version device_stub-0.1",
            r"#build-state name-0.1",
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

        self.client.request(katcp.Message.request("raise-exception"))
        self.client.request(katcp.Message.request("raise-fail"))

        time.sleep(0.1)

        msgs = self.client.messages()
        self._assert_msgs_like(msgs, [
            (r"#version device_stub-0.1", ""),
            (r"#build-state name-0.1", ""),
            (r"!raise-exception fail Traceback", ""),
            (r"!raise-fail fail There\_was\_a\_problem\_with\_your\_request.", ""),
        ])

    # TODO: add test for inform handlers
    # TODO: update inform pass test


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
