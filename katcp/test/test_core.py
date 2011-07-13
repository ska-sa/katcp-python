# test_katcp.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the katcp utilities module.
   """

import unittest
import logging
import katcp
from katcp.testutils import TestLogHandler, DeviceTestSensor

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)


class TestMessage(unittest.TestCase):
    def test_reply_ok(self):
        """Test reply checking."""
        self.assertEqual(katcp.Message.reply("foo", "ok").reply_ok(), True)
        self.assertEqual(katcp.Message.reply("foo", "ok", 1).reply_ok(), True)
        self.assertEqual(katcp.Message.reply("foo", "fail").reply_ok(), False)
        self.assertEqual(katcp.Message.reply("foo", "fail", "ok").reply_ok(),
                         False)
        self.assertEqual(katcp.Message.request("foo", "ok").reply_ok(), False)

    def test_request(self):
        """Test request method."""
        self.assertEqual(str(katcp.Message.request("foo")), "?foo")
        self.assertEqual(str(katcp.Message.request("foo", mid=123)),
                         "?foo[123]")
        self.assertEqual(str(katcp.Message.request("foo", "a", "b", mid=123)),
                         "?foo[123] a b")

    def test_reply(self):
        """Test reply method."""
        self.assertEqual(str(katcp.Message.reply("foo")), "!foo")
        self.assertEqual(str(katcp.Message.reply("foo", mid=123)), "!foo[123]")
        self.assertEqual(str(katcp.Message.reply("foo", "a", "b", mid=123)),
                         "!foo[123] a b")

    def test_inform(self):
        """Test inform method."""
        self.assertEqual(str(katcp.Message.inform("foo")), "#foo")
        self.assertEqual(str(katcp.Message.inform("foo", mid=123)),
                         "#foo[123]")
        self.assertEqual(str(katcp.Message.inform("foo", "a", "b", mid=123)),
                         "#foo[123] a b")

    def test_equality(self):
        class AlwaysEqual(object):
            def __eq__(self, other):
                return True

        msg = katcp.Message.inform("foo", "a", "b")
        assert msg == katcp.Message.inform("foo", "a", "b")
        assert msg != katcp.Message.request("foo", "a", "b")
        assert msg != katcp.Message.inform("bar", "a", "b")
        assert msg != katcp.Message.inform("foo", "a", "b", "c")
        assert msg != 3
        assert msg == AlwaysEqual()


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
        m = self.p.parse("!foo \@")  # 1 empty parameter
        self.assertEqual(m.arguments, [""])
        m = self.p.parse("!foo \@ \@")  # 2 empty parameter
        self.assertEqual(m.arguments, ["", ""])
        m = self.p.parse("!foo \_ \_ \@")  # space, space, empty
        self.assertEqual(m.arguments, [" ", " ", ""])

    def test_whitespace(self):
        """Test parsing of whitespace between parameters."""
        m = self.p.parse("!baz   \@   ")  # 1 empty parameter
        self.assertEqual(m.arguments, [""])
        m = self.p.parse("!baz\t\@\t\@")  # 2 empty parameter
        self.assertEqual(m.arguments, ["", ""])
        m = self.p.parse("!baz\t \t\_\t\t\t \_\t\@   \t")  # space, space, \@
        self.assertEqual(m.arguments, [" ", " ", ""])

    def test_formfeed(self):
        """Test that form feeds are not treated as whitespace."""
        m = self.p.parse("!baz \fa\fb\f")
        self.assertEqual(m.arguments, ["\fa\fb\f"])

    def test_message_ids(self):
        """Test that messages with message ids are parsed as expected."""
        m = self.p.parse("?bar[123]")
        self.assertEqual(m.mtype, m.REQUEST)
        self.assertEqual(m.name, "bar")
        self.assertEqual(m.arguments, [])
        self.assertEqual(m.mid, "123")

        m = self.p.parse("!baz[1234] a b c")
        self.assertEqual(m.mtype, m.REPLY)
        self.assertEqual(m.name, "baz")
        self.assertEqual(m.arguments, ["a", "b", "c"])
        self.assertEqual(m.mid, "1234")


class TestSensor(unittest.TestCase):
    def test_int_sensor(self):
        """Test integer sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3)
        self.assertEqual(s.read_formatted(), ("12345000", "nominal", "3"))
        self.assertEquals(s.parse_value("3"), 3)
        self.assertRaises(ValueError, s.parse_value, "4")
        self.assertRaises(ValueError, s.parse_value, "-10")
        self.assertRaises(ValueError, s.parse_value, "asd")

        s = katcp.Sensor(katcp.Sensor.INTEGER, "an.int", "An integer.",
                         "count", [-20, 20])
        self.assertEquals(s.value(), 0)
        s = katcp.Sensor(katcp.Sensor.INTEGER, "an.int", "An integer.",
                         "count", [2, 20])
        self.assertEquals(s.value(), 2)
        s = katcp.Sensor(katcp.Sensor.INTEGER, "an.int", "An integer.",
                         "count", [2, 20], default=5)
        self.assertEquals(s.value(), 5)

    def test_float_sensor(self):
        """Test float sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.FLOAT, "a.float", "A float.", "power",
                [0.0, 5.0],
                timestamp=12345, status=katcp.Sensor.WARN, value=3.0)
        self.assertEqual(s.read_formatted(), ("12345000", "warn", "3"))
        self.assertEquals(s.parse_value("3"), 3.0)
        self.assertRaises(ValueError, s.parse_value, "10")
        self.assertRaises(ValueError, s.parse_value, "-10")
        self.assertRaises(ValueError, s.parse_value, "asd")

        s = katcp.Sensor(katcp.Sensor.FLOAT, "a.float", "A float.", "",
                         [-20.0, 20.0])
        self.assertEquals(s.value(), 0.0)
        s = katcp.Sensor(katcp.Sensor.FLOAT, "a.float", "A float.", "",
                         [2.0, 20.0])
        self.assertEquals(s.value(), 2.0)
        s = katcp.Sensor(katcp.Sensor.FLOAT, "a.float", "A float.", "",
                         [2.0, 20.0], default=5.0)
        self.assertEquals(s.value(), 5.0)

    def test_boolean_sensor(self):
        """Test boolean sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.BOOLEAN, "a.boolean", "A boolean.", "on/off",
                None,
                timestamp=12345, status=katcp.Sensor.UNKNOWN, value=True)
        self.assertEqual(s.read_formatted(), ("12345000", "unknown", "1"))
        self.assertEquals(s.parse_value("1"), True)
        self.assertEquals(s.parse_value("0"), False)
        self.assertRaises(ValueError, s.parse_value, "asd")

    def test_discrete_sensor(self):
        """Test discrete sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.DISCRETE, "a.discrete", "A discrete sensor.",
                "state", ["on", "off"],
                timestamp=12345, status=katcp.Sensor.ERROR, value="on")
        self.assertEqual(s.read_formatted(), ("12345000", "error", "on"))
        self.assertEquals(s.parse_value("on"), "on")
        self.assertRaises(ValueError, s.parse_value, "fish")

    def test_lru_sensor(self):
        """Test LRU sensor."""
        s = DeviceTestSensor(
                katcp.Sensor.LRU, "an.lru", "An LRU sensor.", "state",
                None,
                timestamp=12345, status=katcp.Sensor.FAILURE,
                value=katcp.Sensor.LRU_ERROR)
        self.assertEqual(s.read_formatted(), ("12345000", "failure", "error"))
        self.assertEquals(s.parse_value("nominal"), katcp.Sensor.LRU_NOMINAL)
        self.assertRaises(ValueError, s.parse_value, "fish")

    def test_string_sensor(self):
        """Test string sensor."""
        s = DeviceTestSensor(
            katcp.Sensor.STRING, "a.string", "A string sensor.", "filename",
            None,
            timestamp=12345, status=katcp.Sensor.NOMINAL, value="zwoop")
        self.assertEqual(s.read_formatted(), ("12345000", "nominal", "zwoop"))
        self.assertEquals(s.parse_value("bar foo"), "bar foo")

    def test_timestamp_sensor(self):
        """Test timestamp sensor."""
        s = DeviceTestSensor(
            katcp.Sensor.TIMESTAMP, "a.timestamp", "A timestamp sensor.", "ms",
            None,
            timestamp=12345, status=katcp.Sensor.NOMINAL, value=1001.9)
        self.assertEqual(s.read_formatted(),
                         ("12345000", "nominal", "1001900"))
        self.assertAlmostEqual(s.parse_value("1002100"), 1002.1)
        self.assertRaises(ValueError, s.parse_value, "bicycle")

    def test_set_and_get_value(self):
        """Test getting and setting a sensor value."""
        s = DeviceTestSensor(
                katcp.Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3)

        self.assertEqual(s.value(), 3)

        s.set_value(2)
        self.assertEqual(s.value(), 2)

        s.set_value(3, timestamp=12345)
        self.assertEqual(s.read(), (12345, katcp.Sensor.NOMINAL, 3))

        self.assertRaises(ValueError, s.set_value, 5)
