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
        m = self.p.parse(r"?foo \\\ \0\n\r\e\t")
        self.assertEqual(m.arguments, ["\\ \0\n\r\x1b\t"])

        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"?foo \z")

        # test unescaped tab
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, "?foo \t")

    def test_syntax_errors(self):
        """Test generation of syntax errors."""
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r" ?foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"? foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"?1foo")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r">foo")

    def test_message_to_string(self):
        """Test message to string round trip with escapes."""
        for m_str in [
            "?bar",
            r"?foo \\\ \0\n\r\e\t",
        ]:
            self.assertEqual(m_str, str(self.p.parse(m_str)))

    def test_command_names(self):
        """Test a variety of command names."""
        m = self.p.parse("!baz-bar")
        self.assertEqual(m.name, "baz-bar")
        self.assertRaises(katcp.KatcpSyntaxError, self.p.parse, r"?-foo")

    def test_empty_params(self):
        """Test parsing messages with empty parameters."""
        m = self.p.parse("!foo ") # 1 empty parameter
        self.assertEqual(m.arguments, [""])
        m = self.p.parse("!foo  ") # 2 empty parameter
        self.assertEqual(m.arguments, ["", ""])
        #import pdb
        #pdb.set_trace()
        m = self.p.parse("!foo \  \  ") # space, space, empty
        self.assertEqual(m.arguments, [" ", " ", ""])


class DeviceTestClient(katcp.DeviceClient):
    def __init__(self, *args, **kwargs):
        super(DeviceTestClient, self).__init__(*args, **kwargs)
        self._msgs = []

    def raw_send(self, chunk):
        """Send a raw chunk of data to the server."""
        self._sock.send(chunk)

    def inform(self, msg):
        self._msgs.append(msg)

    def reply(self, msg):
        self._msgs.append(msg)

    def messages(self):
        return self._msgs

class TestSensor(katcp.Sensor):
    def __init__(self, sensor_type, description, units, params,
                 timestamp, status, value):
        super(TestSensor, self).__init__(sensor_type, description, units, params)
        self.__timestamp = timestamp
        self.__status = status
        self.__value = value

    def read(self):
        return self.__timestamp, self.__status, self.__value

    def _apply_sampling_change(self, strategy, params):
        pass

class DeviceTestServer(katcp.DeviceServer):
    def setup_sensors(self):
        self.restarted = False
        self.add_sensor("an.int", TestSensor(
            katcp.Sensor.INTEGER, "An Integer.", "count",
            [-5, 5],
            timestamp=12345, status=TestSensor.NOMINAL, value=3
        ))

    def schedule_restart(self):
        self.restarted = True

class TestDeviceServer(unittest.TestCase):

    def setUp(self):
        self.server = DeviceTestServer('', 0)
        self.server_thread = threading.Thread(target=self.server.run)
        self.server_thread.start()
        time.sleep(0.2)

        host, port = self.server._sock.getsockname()

        self.client = DeviceTestClient(host, port)
        self.client_thread = threading.Thread(target=self.client.run)
        self.client_thread.start()
        time.sleep(0.2)

    def tearDown(self):
        self.client.stop()
        self.client_thread.join()
        self.server.stop()
        self.server_thread.join()

    def test_simple_connect(self):
        """Test a simple server setup and teardown with client connect."""
        # basic send
        self.client.request(katcp.Message.request("foo"))

        # pipe-lined send
        self.client.raw_send("?bar-boom\r\n#baz\r")

        # broken up sends
        self.client.raw_send("?boo")
        self.client.raw_send("m arg1 arg2")
        self.client.raw_send("\n")

        time.sleep(0.2)
        msgs = self.client.messages()

        for msg, msg_str in zip(msgs, [
            r"#version device_stub-0.1",
            r"#build-state device_stub-0.1",
            r"!foo invalid Unknown\ request.",
            r"!bar-boom invalid Unknown\ request.",
            r"!baz invalid Unknown\ request.",
            r"!boom invalid Unknown\ request.",
        ]):
            self.assertEqual(str(msg), msg_str)

    def test_bad_requests(self):
        """Test request failure paths in device server."""
        pass
        # TODO: implement test

    def test_standard_requests(self):
        """Test standard request and replies."""
        self.client.request(katcp.Message.request("watchdog"))
        self.client.request(katcp.Message.request("restart"))
        self.client.request(katcp.Message.request("log-level"))
        self.client.request(katcp.Message.request("log-level trace"))
        self.client.request(katcp.Message.request("help"))
        self.client.request(katcp.Message.request("help watchdog"))
        self.client.request(katcp.Message.request("help unknown-request"))
        self.client.request(katcp.Message.request("sensor-list"))
        self.client.request(katcp.Message.request("sensor-list an.int"))
        self.client.request(katcp.Message.request("sensor-list an.unknown"))
        self.client.request(katcp.Message.request("sensor-sampling an.int"))
        self.client.request(katcp.Message.request("sensor-sampling an.int diff 2"))

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

        for msg, msg_test in zip(msgs, [
            r"#version device_stub-0.1",
            r"#build-state device_stub-0.1",
            r"!watchdog ok",
            r"!restart ok",
            r"!log-level ok off",
            r"!log-level ok trace",
            r"#help halt",
            r"#help help",
            r"#help log-level",
            r"#help restart",
            r"#help sensor-list",
            r"#help sensor-sampling",
            r"#help watchdog",
            r"!help ok 7",
            r"#help watchdog",
            r"!help ok 1",
            r"!help fail",
            r"#sensor-type an.int integer An\ Integer. count -5 5",
            r"#sensor-status 12345 1 an.int nominal 3",
            r"!sensor-list ok 1",
            r"#sensor-type an.int integer An\ Integer. count -5 5",
            r"#sensor-status 12345 1 an.int nominal 3",
            r"!sensor-list ok 1",
            r"!sensor-list fail",
            r"!sensor-sampling ok an.int none",
            r"!sensor-sampling ok an.int diff 2",
            (r"#log trace", r"root trace-msg"),
            (r"#log debug", r"root debug-msg"),
            (r"#log info", r"root info-msg"),
            (r"#log warn", r"root warn-msg"),
            (r"#log error", r"root error-msg"),
            (r"#log critical", r"root critical-msg"),
        ]):
            str_msg = str(msg)
            if type(msg_test) is tuple:
                msg_prefix, msg_suffix = msg_test
            else:
                msg_prefix, msg_suffix = msg_test, None

            if msg_prefix and not str_msg.startswith(msg_prefix):
                self.assertEqual(str_msg, msg_prefix,
                    msg="Message '%s' does not start with '%s'."
                    % (str_msg, msg_prefix)
                )

            if msg_suffix and not str_msg.endswith(msg_suffix):
                self.assertEqual(str_msg, msg_prefix,
                    msg="Message '%s' does not end with '%s'."
                    % (str_msg, msg_prefix)
                )

class TestDeviceClient(unittest.TestCase):
    pass
