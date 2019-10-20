# test_katcp.py
# -*- coding: utf-8 -*-
# vim:fileencoding=utf-8 ai ts=4 sts=4 et sw=4
# Copyright 2009 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

"""Tests for the katcp utilities module.
   """
from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import logging
import unittest

import future
import tornado

import katcp

from katcp import KatcpSyntaxError, KatcpTypeError
from katcp.core import (AsyncEvent, AsyncState, Message, Sensor,
                        hashable_identity, until_some)
from katcp.testutils import TestLogHandler

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)


class TestMessage(unittest.TestCase):
    def test_init_basic(self):
        msg = Message(Message.REQUEST,
                      'hello', ['world', b'binary\xff\x00', 123, 4.5, True, False])
        self.assertEqual(msg.mtype, Message.REQUEST)
        self.assertEqual(msg.name, 'hello')
        self.assertEqual(msg.arguments, [
            b'world', b'binary\xff\x00', b'123', b'4.5', b'1', b'0'])
        self.assertIsNone(msg.mid)

    def test_init_mid(self):
        msg = Message(Message.REPLY, 'hello', ['world'], mid=345)
        self.assertEqual(msg.mtype, Message.REPLY)
        self.assertEqual(msg.name, 'hello')
        self.assertEqual(msg.arguments, [b'world'])
        self.assertEqual(msg.mid, b'345')

    def test_init_bad_name(self):
        self.assertRaises(ValueError, Message, Message.REPLY,
                          'underscores_bad', 'world', mid=345)
        self.assertRaises(ValueError, Message, Message.REPLY,
                          '', 'world', mid=345)
        self.assertRaises(ValueError, Message, Message.REPLY,
                          '1numberfirst', 'world', mid=345)

    def test_reply_ok(self):
        """Test reply checking."""
        self.assertEqual(Message.reply("foo", "ok").reply_ok(), True)
        self.assertEqual(Message.reply("foo", "ok", 1).reply_ok(), True)
        self.assertEqual(Message.reply("foo", "fail").reply_ok(), False)
        self.assertEqual(Message.reply("foo", "fail", "ok").reply_ok(),
                         False)
        self.assertEqual(Message.request("foo", "ok").reply_ok(), False)

    def test_request(self):
        """Test request method."""
        self.assertEqual(bytes(Message.request("foo")), b"?foo")
        self.assertEqual(bytes(Message.request("foo", mid=123)),
                         b"?foo[123]")
        self.assertEqual(bytes(Message.request("foo", "a", "b", mid=123)),
                         b"?foo[123] a b")

    def test_request_attributes(self):
        """Test request message attributes."""
        msg = Message.request('hello', 'world')
        self.assertEqual(msg.mtype, Message.REQUEST)
        self.assertEqual(msg.name, 'hello')
        self.assertEqual(msg.arguments, [b'world'])
        self.assertIsNone(msg.mid)

    def test_reply(self):
        """Test reply method."""
        self.assertEqual(bytes(Message.reply("foo")), b"!foo")
        self.assertEqual(bytes(Message.reply("foo", mid=123)), b"!foo[123]")
        self.assertEqual(bytes(Message.reply("foo", "a", "b", mid=123)),
                         b"!foo[123] a b")

    def test_reply_attributes(self):
        """Test reply message attributes."""
        msg = Message.reply('hello', 'world', mid=None)
        self.assertEqual(msg.mtype, Message.REPLY)
        self.assertEqual(msg.name, 'hello')
        self.assertEqual(msg.arguments, [b'world'])
        self.assertIsNone(msg.mid)

    def test_inform(self):
        """Test inform method."""
        self.assertEqual(bytes(Message.inform("foo")), b"#foo")
        self.assertEqual(bytes(Message.inform("foo", mid=123)),
                         b"#foo[123]")
        self.assertEqual(bytes(Message.inform("foo", "a", "b", mid=123)),
                         b"#foo[123] a b")

    def test_inform_attributes(self):
        """Test inform message attributes."""
        msg = Message.inform('hello', 'world', mid=1)
        self.assertEqual(msg.mtype, Message.INFORM)
        self.assertEqual(msg.name, 'hello')
        self.assertEqual(msg.arguments, [b'world'])
        self.assertEqual(msg.mid, b'1')

    def test_reply_to_request(self):
        req = Message.request('hello', 'world', mid=1)
        reply = Message.reply_to_request(req, 'test')
        self.assertEqual(reply.mtype, Message.REPLY)
        self.assertEqual(reply.name, 'hello')
        self.assertEqual(reply.arguments, [b'test'])
        self.assertEqual(reply.mid, b'1')

    def test_equality(self):
        class AlwaysEqual(object):
            def __eq__(self, other):
                return True

        msg = Message.inform("foo", "a", "b")
        assert msg == Message.inform("foo", "a", "b")
        assert msg != Message.request("foo", "a", "b")
        assert msg != Message.inform("bar", "a", "b")
        assert msg != Message.inform("foo", "a", "b", "c")
        assert msg != Message.reply("foo", "a", "b")
        assert msg != 3
        assert msg == AlwaysEqual()

    def test_bytes(self):
        msg = Message.request(
            'hello', u'café', b'_bin ary\xff\x00\n\r\t\\\x1b', 123, 4.5, True, False, '')
        raw = bytes(msg)
        expected = (
            b'?hello caf\xc3\xa9 _bin\\_ary\xff\\0\\n\\r\\t\\\\\\e 123 4.5 1 0 \\@')
        self.assertEqual(raw, expected)

    def test_bytes_mid(self):
        msg = Message.reply('fail', 'on fire', mid=234)
        self.assertEqual(bytes(msg), b'!fail[234] on\\_fire')

    def test_str(self):
        msg = Message.reply('fail', 'on fire', mid=234)
        self.assertEqual(str(msg), '!fail[234] on\\_fire')

        # Expect slighty different results in case of invalid UTF-8 bytes for
        # PY2's native byte string compared to PY3's unicode
        msg = Message.reply('ok', b'invalid utf-8\xff\x00')
        if future.utils.PY2:
            self.assertEqual(str(msg), '!ok invalid\_utf-8\xff\\0')
        else:
            with self.assertRaises(UnicodeDecodeError):
                str(msg)

    def test_repr(self):
        msg = Message.reply(
            'ok', 'café', b'_bin ary\xff\x00\n\r\t\\\x1b', 123, 4.5, True, False, '',
            'z'*1100)
        # storing Message.arguments as byte string results in slightly different
        # reprs for PY2 compared to PY3.
        if future.utils.PY2:
            expected = ("<Message reply ok (caf\xc3\xa9, "
                        "_bin\\_ary\xff\\0\\n\\r\\t\\\\\\e, "
                        "123, 4.5, 1, 0, , "
                        "{}..."
                        ")>".format('z'*1000))
        else:
            expected = (r"<Message reply ok (b'caf\xc3\xa9', "
                        r"b'_bin\\_ary\xff\\0\\n\\r\\t\\\\\\e', "
                        r"b'123', b'4.5', b'1', b'0', b'', "
                        r"b'{}...'"
                        r")>".format('z'*1000))
        self.assertEqual(repr(msg), expected)

    def test_bad_utf8_unicode(self):
        # Not great to have a test limited to PY3, but the 'utf-8' encoder
        # doesn't complain about this string on PY2
        if future.utils.PY3:
            msg = Message(Message.REQUEST, 'hello', [u'bad\ud83d\ude04string'])
            self.assertEqual(msg.arguments, [b'bad??string'])

    def test_stringy_arguments_to_bytes(self):
        """Test non-simple types get correctly converted to bytes."""

        class Stringy(object):
            def __str__(self):
                return "string"

        req = Message.request('hello', Stringy())
        self.assertEqual(req.arguments, [b"string"])


class TestMessageParser(unittest.TestCase):
    def setUp(self):
        self.p = katcp.MessageParser()

    def test_simple_messages(self):
        """Test simple messages."""
        m = self.p.parse(b"?foo")
        self.assertEqual(m.mtype, m.REQUEST)
        self.assertEqual(m.name, "foo")
        self.assertEqual(m.arguments, [])

        m = self.p.parse(b"#bar 123 baz 1.000e-05")
        self.assertEqual(m.mtype, m.INFORM)
        self.assertEqual(m.name, "bar")
        self.assertEqual(m.arguments, [b"123", b"baz", b"1.000e-05"])

        m = self.p.parse(b"!baz a17 goo")
        self.assertEqual(m.mtype, m.REPLY)
        self.assertEqual(m.name, "baz")
        self.assertEqual(m.arguments, [b"a17", b"goo"])

    def test_escape_sequences(self):
        """Test escape sequences."""
        m = self.p.parse(br"?foo \\\_\0\n\r\e\t\@")
        self.assertEqual(m.arguments, [b"\\ \0\n\r\x1b\t"])

        self.assertRaises(KatcpSyntaxError, self.p.parse, br"?foo \z")

        # test unescaped null
        self.assertRaises(KatcpSyntaxError, self.p.parse, b"?foo \0")

    def test_syntax_errors(self):
        """Test generation of syntax errors."""
        self.assertRaises(KatcpSyntaxError, self.p.parse, br" ?foo")
        self.assertRaises(KatcpSyntaxError, self.p.parse, br"? foo")
        self.assertRaises(KatcpSyntaxError, self.p.parse, br"?1foo")
        self.assertRaises(KatcpSyntaxError, self.p.parse, br">foo")
        self.assertRaises(KatcpSyntaxError, self.p.parse, b"!foo \\")
        self.assertRaises(KatcpSyntaxError, self.p.parse, b"!test message\n")
        self.assertRaises(KatcpSyntaxError, self.p.parse, b"")
        # invalid escapes
        self.assertRaises(KatcpSyntaxError, self.p.parse, b'!ok \\q')
        self.assertRaises(KatcpSyntaxError, self.p.parse, b'!ok q\\ other')
        # unescaped
        self.assertRaises(KatcpSyntaxError, self.p.parse, b'!ok \x1b')

    def test_message_to_string(self):
        """Test message to string round trip with escapes."""
        for m_str in [
            b"?bar",
            br"?foo \\\_\0\n\r\e\t",
        ]:
            self.assertEqual(m_str, bytes(self.p.parse(m_str)))

    def test_command_names(self):
        """Test a variety of command names."""
        m = self.p.parse(b"!baz-bar")
        self.assertEqual(m.name, "baz-bar")
        self.assertRaises(KatcpSyntaxError, self.p.parse, br"?-foo")
        self.assertRaises(KatcpSyntaxError, self.p.parse, br"?bad_name")
        self.assertRaises(KatcpSyntaxError, self.p.parse, br"? empty")

    def test_empty_params(self):
        """Test parsing messages with empty parameters."""
        m = self.p.parse(b"!foo \@")  # 1 empty parameter
        self.assertEqual(m.arguments, [b""])
        m = self.p.parse(b"!foo \@ \@")  # 2 empty parameter
        self.assertEqual(m.arguments, [b"", b""])
        m = self.p.parse(b"!foo \_ \_ \@")  # space, space, empty
        self.assertEqual(m.arguments, [b" ", b" ", b""])

    def test_whitespace(self):
        """Test parsing of whitespace between parameters."""
        m = self.p.parse(b"!baz   \@   ")  # 1 empty parameter
        self.assertEqual(m.arguments, [b""])
        m = self.p.parse(b"!baz\t\@\t\@")  # 2 empty parameter
        self.assertEqual(m.arguments, [b"", b""])
        m = self.p.parse(b"!baz\t \t\_\t\t\t \_\t\@   \t")  # space, space, \@
        self.assertEqual(m.arguments, [b" ", b" ", b""])

    def test_formfeed(self):
        """Test that form feeds are not treated as whitespace."""
        m = self.p.parse(b"!baz \fa\fb\f")
        self.assertEqual(m.arguments, [b"\fa\fb\f"])

    def test_message_ids(self):
        """Test that messages with message ids are parsed as expected."""
        m = self.p.parse(b"?bar[123]")
        self.assertEqual(m.mtype, m.REQUEST)
        self.assertEqual(m.name, "bar")
        self.assertEqual(m.arguments, [])
        self.assertEqual(m.mid, b"123")

        m = self.p.parse(b"!baz[1234] a b c")
        self.assertEqual(m.mtype, m.REPLY)
        self.assertEqual(m.name, "baz")
        self.assertEqual(m.arguments, [b"a", b"b", b"c"])
        self.assertEqual(m.mid, b"1234")

        # TODO (AJ) update katcp-python to enforce limits in the KATCP spec
        # out of range
        #self.assertRaises(KatcpSyntaxError, self.p.parse, b"!ok[0]")
        #self.assertRaises(KatcpSyntaxError, self.p.parse, b"!ok[1000000000000]")

        # bad format
        self.assertRaises(KatcpSyntaxError, self.p.parse, b"!ok[10")
        self.assertRaises(KatcpSyntaxError, self.p.parse, b"!ok[a]")

    def test_message_argument_formatting(self):
        float_val = 2.35532342334233294e17
        m = Message.request(
            'req-name', 1, float_val, True, False, 'string')
        self.assertEqual(m.arguments,
                         [b'1', repr(float_val).encode('ascii'), b'1', b'0', b'string'])

    def test_binary_data_arguments(self):
        m = self.p.parse(b'?test message \\0\\n\\r\\t\\e\\_binary')
        self.assertEqual(m, Message.request('test', b'message', b'\0\n\r\t\x1b binary'))

    def test_unicode_message_handling(self):
        unicode_str = u'!baz[1] Kl\xc3\xbcf skr\xc3\xa4m'
        self.assertRaises(KatcpTypeError, self.p.parse, unicode_str)


class TestProtocolFlags(unittest.TestCase):
    def test_parse_version(self):
        PF = katcp.ProtocolFlags
        self.assertEqual(PF.parse_version(b"foo"), PF(None, None, set()))
        self.assertEqual(PF.parse_version(b"1.0"), PF(1, 0, set()))
        self.assertEqual(PF.parse_version(b"5.0-MI"),
                         PF(5, 0, set([PF.MULTI_CLIENT, PF.MESSAGE_IDS])))
        # check an unknown flag
        self.assertEqual(PF.parse_version(b"5.1-MIU"),
                         PF(5, 1, set([PF.MULTI_CLIENT, PF.MESSAGE_IDS, b"U"])))
        # Check request timeout hint flag
        self.assertEqual(PF.parse_version(b"5.1-MTI"),
                         PF(5, 1, set([PF.MULTI_CLIENT, PF.MESSAGE_IDS,
                                       PF.REQUEST_TIMEOUT_HINTS])))

    def test_str(self):
        PF = katcp.ProtocolFlags
        self.assertEqual(str(PF(1, 0, set())), "1.0")
        self.assertEqual(str(PF(5, 0, set([PF.MULTI_CLIENT, PF.MESSAGE_IDS]))),
                         "5.0-IM")
        self.assertEqual(str(PF(5, 0, set([PF.MULTI_CLIENT, PF.MESSAGE_IDS,
                                           b"U"]))),
                         "5.0-IMU")
        self.assertEqual(str(PF(5, 1, set([PF.MULTI_CLIENT, PF.MESSAGE_IDS,
                                           PF.REQUEST_TIMEOUT_HINTS]))),
                         "5.1-IMT")

    def test_incompatible_options(self):
        PF = katcp.ProtocolFlags
        # Katcp v4 and below don't support message ids
        with self.assertRaises(ValueError):
            PF(4, 0, [PF.MESSAGE_IDS])

        # Katcp v5 and below don't support (proposed) timeout hint flag
        with self.assertRaises(ValueError):
            PF(5, 0, [PF.REQUEST_TIMEOUT_HINTS])


class TestSensor(unittest.TestCase):

    def test_default_descriptions(self):
        s = Sensor(Sensor.INTEGER, 'a sens', params=[0, 10])
        self.assertEqual(s.description, "Integer sensor 'a sens' with no unit")
        s = Sensor(Sensor.FLOAT, 'fsens', None, 'microseconds', params=[0, 10])
        self.assertEqual(s.description,
                         "Float sensor 'fsens' in unit microseconds")

    def test_units_none(self):
        """Test units initialised to None results in empty string."""
        s = Sensor.integer("a sens", None, None, None)
        self.assertEqual(s.units, "")

    def test_int_sensor_from_byte_strings(self):
        """Test integer sensor initialised from byte strings."""
        s = Sensor.integer(b"an.int", b"An integer.", b"count", [-4, 3])
        # sensor attributes must be native strings
        self.assertEqual(s.name, "an.int")
        self.assertEqual(s.units, "count")
        self.assertEqual(s.description, "An integer.")
        self.assertEqual(s.stype, "integer")
        s.set(timestamp=12345, status=Sensor.NOMINAL, value=3)
        self.assertEqual(s.value(), 3)
        # after formatting, byte strings are required
        self.assertEqual(s.format_reading(s.read()), (b"12345.000000", b"nominal", b"3"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"nominal", b"3"))

    def test_int_sensor_from_native_strings(self):
        """Test integer sensor initialised from native strings."""
        s = Sensor.integer("an.int", "An integer.", "count", [-4, 3])
        # sensor attributes must be native strings
        self.assertEqual(s.name, "an.int")
        self.assertEqual(s.units, "count")
        self.assertEqual(s.description, "An integer.")
        self.assertEqual(s.stype, "integer")
        s.set(timestamp=12345, status=Sensor.NOMINAL, value=3)
        # test both read_formatted and format_reading
        self.assertEqual(s.format_reading(s.read()), (b"12345.000000", b"nominal", b"3"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"nominal", b"3"))
        self.assertEqual(s.parse_value(b"3"), 3)
        self.assertEqual(s.parse_value(b"4"), 4)
        self.assertEqual(s.parse_value(b"-10"), -10)
        self.assertRaises(ValueError, s.parse_value, b"asd")
        self.assertRaises(KatcpTypeError, s.parse_value, u"asd")
        self.assertRaises(KatcpTypeError, s.parse_value, u"3")

        s = Sensor(Sensor.INTEGER, "an.int", "An integer.", "count", [-20, 20])
        self.assertEqual(s.value(), 0)
        s = Sensor(Sensor.INTEGER, "an.int", "An integer.", "count", [2, 20])
        self.assertEqual(s.value(), 2)
        s = Sensor.integer("an.int", "An integer.", "count", [2, 20], default=5)
        self.assertEqual(s.value(), 5)
        self.assertEqual(s.status(), Sensor.UNKNOWN)
        s = Sensor.integer("an.int", "An integer.", "count", [2, 20],
                           initial_status=Sensor.NOMINAL)
        self.assertEqual(s.status(), Sensor.NOMINAL)

    def test_float_sensor(self):
        """Test float sensor."""
        s = Sensor.float("a.float", "A float.", "power", [0.0, 5.0])
        self.assertEqual(s.stype, 'float')
        s.set(timestamp=12345, status=Sensor.WARN, value=3.0)
        # test both read_formatted and format_reading
        self.assertEqual(s.format_reading(s.read()), (b"12345.000000", b"warn", b"3.0"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"warn", b"3.0"))
        self.assertEqual(s.parse_value(b"3.0"), 3.0)
        self.assertEqual(s.parse_value(b"10"), 10.0)
        self.assertEqual(s.parse_value(b"-10"), -10.0)
        self.assertRaises(ValueError, s.parse_value, b"asd")

        s = Sensor(Sensor.FLOAT, "a.float", "A float.", "", [-20.0, 20.0])
        self.assertEqual(s.value(), 0.0)
        s = Sensor(Sensor.FLOAT, "a.float", "A float.", "", [2.0, 20.0])
        self.assertEqual(s.value(), 2.0)
        s = Sensor.float("a.float", "A float.", "", [2.0, 20.0], default=5.0)
        self.assertEqual(s.value(), 5.0)
        self.assertEqual(s.status(), Sensor.UNKNOWN)
        s = Sensor.float("a.float", "A float.", "", [2.0, 20.0],
                         initial_status=Sensor.WARN)
        self.assertEqual(s.status(), Sensor.WARN)

        # TODO (AJ) Test for non-ASCII fields
        # with self.assertRaises(KatcpTypeError):
        #     s = Sensor.float("a.temp", "Non-ASCII unit not allowed", "°C", default=22.0)

    def test_boolean_sensor(self):
        """Test boolean sensor."""
        s = Sensor.boolean("a.boolean", "A boolean.", "on/off", None)
        self.assertEqual(s.stype, 'boolean')
        s.set(timestamp=12345, status=Sensor.UNKNOWN, value=True)
        # test both read_formatted and format_reading
        self.assertEqual(s.format_reading(s.read()), (b"12345.000000", b"unknown", b"1"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"unknown", b"1"))
        self.assertEqual(s.parse_value(b"1"), True)
        self.assertEqual(s.parse_value(b"0"), False)
        self.assertRaises(ValueError, s.parse_value, b"asd")
        s = Sensor.boolean("a.boolean", "A boolean.", "on/off", default=True)
        self.assertEqual(s._value, True)
        s = Sensor.boolean("a.boolean", "A boolean.", "on/off", default=False)
        self.assertEqual(s._value, False)
        s = Sensor.boolean("a.boolean", "A boolean.", "on/off",
                           initial_status=Sensor.ERROR)
        self.assertEqual(s.status(), Sensor.ERROR)

    def test_discrete_sensor_from_byte_strings(self):
        """Test discrete sensor initialised from byte strings."""
        s = Sensor.discrete(
            b"a.discrete", b"A discrete sensor.", b"state", [b"on", b"off"])
        # sensor attributes must be native strings
        self.assertEqual(s.name, "a.discrete")
        self.assertEqual(s.units, "state")
        self.assertEqual(s.description, "A discrete sensor.")
        self.assertEqual(s.stype, "discrete")
        s.set(timestamp=12345, status=Sensor.ERROR, value="on")
        self.assertEqual(s.value(), "on")
        # after formatting, byte strings are required
        self.assertEqual(s.format_reading(s.read()), (b"12345.000000", b"error", b"on"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"error", b"on"))

    def test_discrete_sensor_from_native_strings(self):
        """Test discrete sensor initialised from native strings."""
        s = Sensor.discrete(
            "a.discrete", "A discrete sensor.", "state", ["on", "off"])
        self.assertEqual(s.stype, 'discrete')
        s.set(timestamp=12345, status=Sensor.ERROR, value="on")
        # test both read_formatted and format_reading
        self.assertEqual(s.format_reading(s.read()), (b"12345.000000", b"error", b"on"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"error", b"on"))
        self.assertEqual(s.parse_value(b"on"), "on")
        self.assertRaises(ValueError, s.parse_value, b"fish")
        s = Sensor.discrete("a.discrete", "A discrete sensor.", "state",
                            ["on", "off"], default='on')
        self.assertEqual(s._value, 'on')
        s = Sensor.discrete("a.discrete", "A discrete sensor.", "state",
                            ["on", "off"], default='off')
        self.assertEqual(s._value, 'off')
        s = Sensor.discrete("a.discrete", "A discrete sensor.", "state",
                            ["on", "off"], initial_status=Sensor.UNREACHABLE)
        self.assertEqual(s.status(), Sensor.UNREACHABLE)

    def test_lru_sensor(self):
        """Test LRU sensor."""
        s = Sensor.lru("an.lru", "An LRU sensor.", "state", None)
        self.assertEqual(s.stype, 'lru')
        s.set(timestamp=12345, status=Sensor.FAILURE, value=Sensor.LRU_ERROR)
        # test both read_formatted and format_reading
        self.assertEqual(
            s.format_reading(s.read()), (b"12345.000000", b"failure", b"error"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"failure", b"error"))
        self.assertEqual(s.parse_value(b"nominal"), Sensor.LRU_NOMINAL)
        self.assertRaises(ValueError, s.parse_value, b"fish")
        s = Sensor.lru(
            "an.lru", "An LRU sensor.", "state", default=Sensor.LRU_ERROR)
        self.assertEqual(s._value, Sensor.LRU_ERROR)
        s = Sensor.lru(
            "an.lru", "An LRU sensor.", "state", default=Sensor.LRU_NOMINAL)
        self.assertEqual(s._value, Sensor.LRU_NOMINAL)
        s = Sensor.lru(
            "an.lru", "An LRU sensor.", "state", initial_status=Sensor.FAILURE)
        self.assertEqual(s.status(), Sensor.FAILURE)

    def test_string_sensor(self):
        """Test string sensor."""
        s = Sensor.string("a.string", "A string sensor.", "filename", None)
        self.assertEqual(s.stype, 'string')
        s.set(timestamp=12345, status=Sensor.NOMINAL, value="zwoop")
        self.assertEqual(s._value, "zwoop")
        # test both read_formatted and format_reading
        self.assertEqual(
            s.format_reading(s.read()), (b"12345.000000", b"nominal", b"zwoop"))
        self.assertEqual(s.read_formatted(), (b"12345.000000", b"nominal", b"zwoop"))
        # test parsing byte string returns native string
        self.assertEqual(s.parse_value(b"bar foo"), "bar foo")
        # test default value
        s = Sensor.string(
            "a.string", "A string sensor.", "filename", default='baz')
        self.assertEqual(s._value, 'baz')
        # test initial status
        s = Sensor.string("a.string", "A string sensor.", "filename",
                          initial_status=Sensor.WARN)
        self.assertEqual(s.status(), Sensor.WARN)

        # Test using non-string types from set_value are "stringified"
        s = Sensor.string("a.string", "Value from list.")
        s.set_value([1, 2, 3])
        self.assertEqual(s.value(), [1, 2, 3])
        _, _, formatted_value = s.read_formatted()
        self.assertEqual(formatted_value, b'[1, 2, 3]')
        # But this is "one-way", if set from formatted byte string, the
        # "original" type is not inferred
        s.set_formatted(b'1.23', b'nominal', b'[4, 5, 6]')
        self.assertEqual(s.value(), '[4, 5, 6]')

        # Test Python version-specific limitations
        if future.utils.PY2:
            test_cases = [
                # name,       input,       raw,           from raw
                ('non-ascii', b"\x00\xff", b"\x00\xff",   b"\x00\xff"),
                ('utf-8',     u"räm",      b"r\xc3\xa4m", b"r\xc3\xa4m"),
            ]
        else:
            test_cases = [
                # name,       input,       raw,                from raw
                ('non-ascii', b"\x00\xff", b"\x00\xff",        UnicodeDecodeError),
                ('utf-8',     u"räm",      b"r\xc3\xa4m",      u"räm"),
                ('invalid',   u"\ud83d",   UnicodeEncodeError, None),
            ]
        for name, input_, raw, from_raw in test_cases:
            # set from input, then try to read back raw
            s.set_value(input_)
            if type(raw) is type and issubclass(raw, Exception):
                with self.assertRaises(raw):
                    s.read_formatted()
                continue  # cannot test setting from raw, so move on
            else:
                _, _, formatted_value = s.read_formatted()
                self.assertEqual(raw, formatted_value)

            # set raw, and try to read back value
            s.set_value(None)
            if type(from_raw) is type and issubclass(from_raw, Exception):
                with self.assertRaises(from_raw):
                    s.set_formatted(b'1.23', b'nominal', raw)
            else:
                s.set_formatted(b'1.23', b'nominal', raw)
                self.assertEqual(s.value(), from_raw)

    def test_timestamp_sensor(self):
        """Test timestamp sensor."""
        s = Sensor.timestamp("a.timestamp", "A timestamp sensor.", "", None)
        self.assertEqual(s.stype, 'timestamp')
        s.set(timestamp=12345, status=Sensor.NOMINAL, value=1001.9)
        # test both read_formatted and format_reading
        self.assertEqual(s.format_reading(s.read()),
                         (b"12345.000000", b"nominal", b"1001.900000"))
        self.assertEqual(s.read_formatted(),
                         (b"12345.000000", b"nominal", b"1001.900000"))
        # Test with katcp v4 parsing formatting
        self.assertEqual(s.format_reading(s.read(), major=4),
                         (b"12345000", b"nominal", b"1001900"))
        self.assertEqual(s.read_formatted(major=4),
                         (b"12345000", b"nominal", b"1001900"))
        self.assertAlmostEqual(s.parse_value(b"1002.100"), 1002.1)
        self.assertRaises(ValueError, s.parse_value, b"bicycle")
        s = Sensor.timestamp(
            "a.timestamp", "A timestamp sensor.", "", default=123)
        self.assertEqual(s._value, 123)

        s.set_formatted(b'12346.1', b'nominal', b'12246.1')
        self.assertEqual(s.read(), (12346.1, Sensor.NOMINAL, 12246.1))

        # Test with katcp v4 parsing
        s.set_formatted(b'12347100', b'nominal', b'12247100', major=4)
        self.assertEqual(s.read(), (12347.1, Sensor.NOMINAL, 12247.1))

        s = Sensor.timestamp("a.timestamp", "A timestamp sensor.", "",
                             initial_status=Sensor.NOMINAL)
        self.assertEqual(s.status(), Sensor.NOMINAL)

    def test_address_sensor(self):
        """Test address sensor."""
        s = Sensor.address("a.address", "An address sensor.", "", None)
        self.assertEqual(s.stype, 'address')
        s.set(timestamp=12345, status=Sensor.NOMINAL, value=("127.0.0.1", 80))
        # test both read_formatted and format_reading
        self.assertEqual(s.format_reading(s.read()),
                         (b"12345.000000", b"nominal", b"127.0.0.1:80"))
        self.assertEqual(s.read_formatted(),
                         (b"12345.000000", b"nominal", b"127.0.0.1:80"))
        self.assertEqual(s.parse_value(b"[::1]:80"), ("::1", 80))
        self.assertRaises(ValueError, s.parse_value, b"[::1]:foo")
        s = Sensor.address("a.address", "An address sensor.", "",
                           default=('192.168.101.1', 81))
        self.assertEqual(s._value, ('192.168.101.1', 81))
        s = Sensor.address("a.address", "An address sensor.", "",
                           initial_status=Sensor.NOMINAL)
        self.assertEqual(s.status(), Sensor.NOMINAL)

    def test_set_and_get_value(self):
        """Test getting and setting a sensor value."""
        s = Sensor.integer("an.int", "An integer.", "count", [-4, 3])
        s.set(timestamp=12345, status=Sensor.NOMINAL, value=3)

        self.assertEqual(s.value(), 3)

        s.set_value(2)
        self.assertEqual(s.value(), 2)

        s.set_value(3, timestamp=12345)
        self.assertEqual(s.read(), (12345, Sensor.NOMINAL, 3))

        s.set_value(5, timestamp=12345)
        self.assertEqual(s.read(), (12345, Sensor.NOMINAL, 5))

        s.set_formatted(b'12346.1', b'nominal', b'-2')
        self.assertEqual(s.read(), (12346.1, Sensor.NOMINAL, -2))

        # Test setting with katcp v4 parsing
        s.set_formatted(b'12347100', b'warn', b'-3', major=4)
        self.assertEqual(s.read(), (12347.1, Sensor.WARN, -3))

    def test_statuses(self):
        # Test that the status constants are all good
        valid_statuses = set(['unknown', 'nominal', 'warn', 'error',
                              'failure', 'unreachable', 'inactive'])
        status_vals_set = set()
        status_vals_dict = {}
        for st in valid_statuses:
            st_byte_str = st.encode('ascii')
            # Check that a capitalised attribute exists for each status
            st_val = getattr(Sensor, st.upper(), 'OOPS')
            self.assertNotEqual(st_val, 'OOPS')
            # Check that the status to name lookup dict is correct
            self.assertEqual(Sensor.STATUSES[st_val], st)
            self.assertEqual(Sensor.STATUSES_RAW[st_val], st_byte_str)
            # Check that the name to value lookup dict is correct
            self.assertEqual(Sensor.STATUS_NAMES[st], st_val)
            self.assertEqual(Sensor.STATUS_NAMES_RAW[st_byte_str], st_val)
            status_vals_set.add(st_val)
            status_vals_dict[st] = st_val

        # Check that the status values are all unique
        self.assertEqual(len(status_vals_set), len(valid_statuses))
        # Check that there are not extra entries in the forward/backward name
        # lookup dicts
        self.assertEqual(len(Sensor.STATUSES), len(valid_statuses))
        self.assertEqual(len(Sensor.STATUS_NAMES), len(valid_statuses))


class TestAsyncState(tornado.testing.AsyncTestCase):

    def setUp(self):
        super(TestAsyncState, self).setUp()
        self._valid_states = ['on', 'off', 'floating']
        self._state = AsyncState(self._valid_states, 'off', self.io_loop)

    def test_init(self):
        self.assertEqual(self._state.state, 'off')
        self.assertEqual(sorted(self._state.valid_states),
                         sorted(self._valid_states))

    @tornado.testing.gen_test
    def test_timeout_of_until_state(self):
        @tornado.gen.coroutine
        def set_state_on():
            self._state.set_state('on')
        # Test for timing out
        with self.assertRaises(tornado.gen.TimeoutError):
            yield self._state.until_state('on', timeout=0.1)
        # Test for NOT timing out
        self.io_loop.add_callback(set_state_on)
        yield self._state.until_state('on', timeout=0.1)

    @tornado.testing.gen_test
    def test_timeout_of_until_state_in(self):
        @tornado.gen.coroutine
        def set_state_floating():
            self._state.set_state('floating')
        # Test for timing out
        with self.assertRaises(tornado.gen.TimeoutError):
            yield self._state.until_state_in('on', 'floating', timeout=0.1)

        # Test for NOT timing out
        self.io_loop.add_callback(set_state_floating)
        yield self._state.until_state_in('on', 'floating', timeout=0.1)


class TestAsyncEvent(tornado.testing.AsyncTestCase):

    def setUp(self):
        super(TestAsyncEvent, self).setUp()
        self._event = AsyncEvent(self.io_loop)

    @tornado.testing.gen_test
    def test_timeout_of_until_set(self):
        @tornado.gen.coroutine
        def call_set():
            self._event.set()
        # Test for timing out
        with self.assertRaises(tornado.gen.TimeoutError):
            yield self._event.until_set(timeout=0.1)
        # Test for NOT timing out
        self.io_loop.add_callback(call_set)
        yield self._event.until_set(timeout=0.1)


class TestUntilSome(tornado.testing.AsyncTestCase):

    @tornado.testing.gen_test
    def test_until_some_args(self):
        results = yield until_some()
        self.assertEqual(results, [], 'Expected empty list for until_some()')
        f1 = tornado.concurrent.Future()
        f2 = tornado.concurrent.Future()
        f3 = tornado.concurrent.Future()
        # Test for timing out
        with self.assertRaises(tornado.gen.TimeoutError):
            yield until_some(f1, f2, f3, timeout=0.05)
        # Test for NOT timing out
        f3.set_result(84)
        f1.set_result(24)
        f2.set_result(42)
        results = yield until_some(f1, f2, f3, timeout=0.1)
        self.assertEqual(sorted(results), [(0, 24), (1, 42), (2, 84)],
                         'Results differ for until_some (3 arg futures)')

    @tornado.testing.gen_test
    def test_until_some_kwargs(self):
        f1 = tornado.concurrent.Future()
        f2 = tornado.concurrent.Future()
        f3 = tornado.concurrent.Future()
        futures = {'f1': f1, 'f2': f2, 'f3': f3}
        f1.set_result(24)
        with self.assertRaises(tornado.gen.TimeoutError):
            yield until_some(done_at_least=2, timeout=0.05, **futures)
        f2.set_result(42)
        results = yield until_some(done_at_least=1, timeout=0.1, **futures)
        # both f1 and f2 are ready, but kwargs dict makes order unpredictable
        self.assertEqual(len(results), 1)
        options = {'f1': 24, 'f2': 42}
        self.assertDictContainsSubset(dict(results), options,
                                      'Results differ for until_some (1 kwarg future)')
        f3.set_result(84)
        results = yield until_some(done_at_least=2, timeout=0.1, **futures)
        # similar to above, any 2 of the 3 kwarg futures could be returned
        self.assertEqual(len(results), 2)
        options = {'f1': 24, 'f2': 42, 'f3': 84}
        self.assertDictContainsSubset(dict(results), options,
                                      'Results differ for until_some (2 kwarg futures)')


class TestHashableIdentity(unittest.TestCase):

    def test_hashable_identity(self):

        class MyObject(object):
            def my_method(self):
                pass

        def my_func1():
            pass

        def my_func2():
            pass

        obj1 = MyObject()
        obj2 = MyObject()

        hash1 = hashable_identity(obj1)
        hash1_again = hashable_identity(obj1)
        hash2 = hashable_identity(obj2)
        self.assertEqual(hash1, hash1_again)
        self.assertNotEqual(hash1, hash2)

        hash1 = hashable_identity(obj1.my_method)
        hash1_again = hashable_identity(obj1.my_method)
        hash2 = hashable_identity(obj2.my_method)
        self.assertEqual(hash1, hash1_again)
        self.assertNotEqual(hash1, hash2)

        hash1 = hashable_identity(my_func1)
        hash1_again = hashable_identity(my_func1)
        hash2 = hashable_identity(my_func2)
        self.assertEqual(hash1, hash1_again)
        self.assertNotEqual(hash1, hash2)

        hash1 = hashable_identity('baz')
        hash1_again = hashable_identity('baz')
        hash2 = hashable_identity('foo')
        self.assertEqual(hash1, hash1_again)
        self.assertNotEqual(hash1, hash2)
