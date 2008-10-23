"""Tests for the katcp module."""

import unittest
import katcp
import threading
import time

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


class DeviceTestSensor(katcp.Sensor):
    def __init__(self, sensor_type, description, units, params,
                 timestamp, status, value):
        super(DeviceTestSensor, self).__init__(
            sensor_type, description, units, params)
        self.__timestamp = timestamp
        self.__status = status
        self.__value = value
        self.__sampling_changes = []

    def read(self):
        return self.__timestamp, self.__status, self.__value

    def _apply_sampling_change(self, strategy, params):
        self.__sampling_changes.append((strategy, params))

    def get_changes(self):
        return self.__sampling_changes


class TestSensor(unittest.TestCase):
    def test_int_sensor(self):
        """Test integer sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.INTEGER, "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3
        )
        self.assertEqual(s.read_formatted(), ("12345", "nominal", "3"))

    def test_float_sensor(self):
        """Test float sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.FLOAT, "A float.", "power",
                [0.0, 5.0],
                timestamp=12345, status=katcp.Sensor.WARN, value=3.0
        )
        self.assertEqual(s.read_formatted(), ("12345", "warn", "3.000000e+00"))

    def test_boolean_sensor(self):
        """Test boolean sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.BOOLEAN, "A boolean.", "on/off",
                None,
                timestamp=12345, status=katcp.Sensor.UNKNOWN, value=True
        )
        self.assertEqual(s.read_formatted(), ("12345", "unknown", "1"))

    def test_discrete_sensor(self):
        """Test discrete sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.DISCRETE, "A discrete sensor.", "state",
                ["on", "off"],
                timestamp=12345, status=katcp.Sensor.ERROR, value="on"
        )
        self.assertEqual(s.read_formatted(), ("12345", "error", "on"))

    def test_lru_sensor(self):
        """Test LRU sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.LRU, "An LRU sensor.", "state",
                None,
                timestamp=12345, status=katcp.Sensor.FAILURE,
                value=katcp.Sensor.LRU_ERROR
        )
        self.assertEqual(s.read_formatted(), ("12345", "failure", "error"))

    def test_sampling(self):
        """Test getting and setting the sampling."""
        s = DeviceTestSensor(
                katcp.Sensor.INTEGER, "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3
        )
        s.set_sampling(katcp.Sensor.NONE)
        s.set_sampling(katcp.Sensor.PERIOD, 10)
        s.set_sampling(katcp.Sensor.EVENT)
        s.set_sampling(katcp.Sensor.DIFFERENTIAL, 2)
        self.assertRaises(ValueError, s.set_sampling, "bad foo", [])
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.NONE, "foo")
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.PERIOD)
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.PERIOD, 1.5)
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.PERIOD, -1)
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.EVENT, "foo")
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.DIFFERENTIAL)
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.DIFFERENTIAL, -1)
        self.assertRaises(ValueError, s.set_sampling, katcp.Sensor.DIFFERENTIAL, 1.5)

        s.set_sampling_formatted("none")
        s.set_sampling_formatted("period", "15")
        s.set_sampling_formatted("event")
        s.set_sampling_formatted("differential", "2")
        self.assertRaises(ValueError, s.set_sampling_formatted, "random")
        self.assertRaises(ValueError, s.set_sampling_formatted, "period", "foo")
        self.assertRaises(ValueError, s.set_sampling_formatted, "differential", "bar")


class DeviceTestClient(katcp.DeviceClient):
    def __init__(self, *args, **kwargs):
        super(DeviceTestClient, self).__init__(*args, **kwargs)
        self.__msgs = []

    def raw_send(self, chunk):
        """Send a raw chunk of data to the server."""
        self._sock.send(chunk)

    def inform(self, msg):
        self.__msgs.append(msg)

    def reply(self, msg):
        self.__msgs.append(msg)

    def messages(self):
        return self.__msgs


class DeviceTestServer(katcp.DeviceServer):
    def setup_sensors(self):
        self.restarted = False
        self.add_sensor("an.int", DeviceTestSensor(
            katcp.Sensor.INTEGER, "An Integer.", "count",
            [-5, 5],
            timestamp=12345, status=katcp.Sensor.NOMINAL, value=3
        ))

    def schedule_restart(self):
        self.restarted = True


class TestDeviceServer(unittest.TestCase):
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
            r"#build-state device_stub-0.1",
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
            (r"#build-state device_stub-0.1", ""),
            (r"#log error", "KatcpSyntaxError:\_Bad\_type\_character\_'b'.\\n"),
        ])

    def test_server_ignores_informs_and_replies(self):
        """Tests server ignores informs and replies."""
        self.client.raw_send("#some inform\n")
        self.client.raw_send("!some reply\n")

        time.sleep(0.1)

        msgs = self.client.messages()
        self._assert_msgs_like(msgs, [
            (r"#version device_stub-0.1", ""),
            (r"#build-state device_stub-0.1", ""),
            (r"#log error", "Unexpected\_reply\_message\_!some\_received\_by\_server."),
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
        self.client.request(katcp.Message.request("sensor-sampling", "an.int"))
        self.client.request(katcp.Message.request("sensor-sampling", "an.int",
                                                  "differential", "2"))

        time.sleep(0.1)

        self.server.log.trace("trace-msg")
        self.server.log.debug("debug-msg")
        self.server.log.info("info-msg")
        self.server.log.warn("warn-msg")
        self.server.log.error("error-msg")
        self.server.log.critical("critical-msg")

        time.sleep(0.1)

        msgs = self.client.messages()
        self.assertEqual(self.server.restarted, True)
        self._assert_msgs_like(msgs, [
            (r"#version device_stub-0.1", ""),
            (r"#build-state device_stub-0.1", ""),
            (r"!watchdog ok", ""),
            (r"!restart ok", ""),
            (r"!log-level ok off", ""),
            (r"!log-level ok trace", ""),
            (r"#help halt", ""),
            (r"#help help", ""),
            (r"#help log-level", ""),
            (r"#help restart", ""),
            (r"#help sensor-list", ""),
            (r"#help sensor-sampling", ""),
            (r"#help watchdog", ""),
            (r"!help ok 7", ""),
            (r"#help watchdog", ""),
            (r"!help ok 1", ""),
            (r"!help fail", ""),
            (r"#sensor-type an.int integer An\_Integer. count -5 5", ""),
            (r"#sensor-status 12345 1 an.int nominal 3", ""),
            (r"!sensor-list ok 1", ""),
            (r"#sensor-type an.int integer An\_Integer. count -5 5", ""),
            (r"#sensor-status 12345 1 an.int nominal 3", ""),
            (r"!sensor-list ok 1", ""),
            (r"!sensor-list fail", ""),
            (r"!sensor-sampling ok an.int none", ""),
            (r"!sensor-sampling ok an.int differential 2", ""),
            (r"#log trace", r"root trace-msg"),
            (r"#log debug", r"root debug-msg"),
            (r"#log info", r"root info-msg"),
            (r"#log warn", r"root warn-msg"),
            (r"#log error", r"root error-msg"),
            (r"#log critical", r"root critical-msg"),
        ])

    def test_halt_request(self):
        """Test halt request."""
        self.client.request(katcp.Message.request("halt"))
        self.server.join()
        time.sleep(0.1)

        msgs = self.client.messages()
        self._assert_msgs_equal(msgs, [
            r"#version device_stub-0.1",
            r"#build-state device_stub-0.1",
            r"!halt ok",
            r"#disconnect Device\_server\_shutting\_down.",
        ])

class TestDeviceClient(unittest.TestCase):
    pass
    #TODO: add some client tests
