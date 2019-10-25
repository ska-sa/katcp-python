# test_kattypes.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

"""Tests for the kattypes module.
   """

from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import unittest

from builtins import object

import future
import mock

from katcp import AsyncReply, FailReply, Message
from katcp.core import DEFAULT_KATCP_MAJOR
from katcp.kattypes import (Address, Bool, Discrete, DiscreteMulti, Float, Int,
                            Lru, Regex, Str, StrictTimestamp, Struct,
                            Timestamp, TimestampOrNow, inform, pack_types,
                            request, return_reply, send_reply, unpack_types)

MS_TO_SEC_FAC = 1/1000.
SEC_TO_MS_FAC = 1000


class TestType(unittest.TestCase):
    def setUp(self):
        self._pack = []
        self._unpack = []

    def test_pack(self):
        for t, value, result in self._pack:
            if type(result) is type and issubclass(result, Exception):
                self.assertRaises(result, t.pack, value)
            else:
                packed = t.pack(value)
                self.assertEqual(packed, result)
                self.assertEqual(type(packed), type(result))

    def test_unpack(self):
        for t, value, result in self._unpack:
            if type(result) is type and issubclass(result, Exception):
                self.assertRaises(result, t.unpack, value)
            else:
                unpacked = t.unpack(value)
                self.assertEqual(unpacked, result)
                self.assertEqual(type(unpacked), type(result))



class TestInt(TestType):

    def setUp(self):
        basic = Int()
        default = Int(default=11)
        optional = Int(optional=True)
        default_optional = Int(default=11, optional=True)
        minmax = Int(min=5, max=6)
        big_minmax = Int(min=-2**64, max=2**64)

        self._pack = [
            (basic, 5, b"5"),
            (basic, -5, b"-5"),
            (basic, "a", TypeError),
            (basic, None, ValueError),
            (minmax, 5, b"5"),
            (minmax, 6, b"6"),
            (minmax, 4, ValueError),
            (minmax, 7, ValueError),
            (big_minmax, 2**64, b"18446744073709551616"),
            (big_minmax, -2**64, b"-18446744073709551616"),
            (big_minmax, 2**64 + 1, ValueError),
            (big_minmax, -2**64 - 1, ValueError),
            (default, None, b"11"),
            (default_optional, None, b"11"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"5", 5),
            (basic, b"-5", -5),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (minmax, b"5", 5),
            (minmax, b"6", 6),
            (minmax, b"4", ValueError),
            (minmax, b"7", ValueError),
            (default, None, 11),
            (default_optional, None, 11),
            (optional, None, None),
        ]


class TestFloat(TestType):

    def setUp(self):
        basic = Float()
        default = Float(default=11.0)
        optional = Float(optional=True)
        default_optional = Float(default=11.0, optional=True)
        minmax = Float(min=5.0, max=6.0)
        big_minmax = Float(min=-2**64, max=2**64)  # allow integer limits

        self._pack = [
            (basic, 5.0, b"5.0"),
            (basic, -5.0, b"-5.0"),
            (basic, 5.5, b"5.5"),
            (basic, -5.5, b"-5.5"),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (minmax, 5.0, b"5.0"),
            (minmax, 6.0, b"6.0"),
            (minmax, 4.5, ValueError),
            (minmax, 6.5, ValueError),
            (big_minmax, 2**64, b"1.8446744073709552e+19"),
            (big_minmax, -2**64, b"-1.8446744073709552e+19"),
            (big_minmax, 2**64 + 1, ValueError),
            (big_minmax, -2**64 - 1, ValueError),
            (default, None, b"11.0"),
            (default_optional, None, b"11.0"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"5", 5.0),
            (basic, b"5.0", 5.0),
            (basic, b"-5", -5.0),
            (basic, b"-5.0", -5.0),
            (basic, b"5.5", 5.5),
            (basic, b"-5.5", -5.5),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (minmax, b"5", 5.0),
            (minmax, b"6", 6.0),
            (minmax, b"4.5", ValueError),
            (minmax, b"6.5", ValueError),
            (default, None, 11.0),
            (default_optional, None, 11.0),
            (optional, None, None),
        ]


class TestBool(TestType):

    def setUp(self):
        basic = Bool()
        default = Bool(default=True)
        optional = Bool(optional=True)
        default_optional = Bool(default=True, optional=True)

        self._pack = [
            (basic, True, b"1"),
            (basic, False, b"0"),
            (basic, 1, b"1"),
            (basic, 0, b"0"),
            (basic, "a", b"1"),
            (basic, None, ValueError),
            (default, None, b"1"),
            (default_optional, None, b"1"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"1", True),
            (basic, b"0", False),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (default, None, True),
            (default_optional, None, True),
            (optional, None, None),
        ]


class TestDiscrete(TestType):

    def setUp(self):
        basic = Discrete(("VAL1", "VAL2"))
        default = Discrete(("VAL1", "VAL2"), default="VAL1")
        optional = Discrete(("VAL1", "VAL2"), optional=True)
        default_optional = Discrete(("VAL1", "VAL2"), default="VAL1",
                                    optional=True)
        case_insensitive = Discrete(("val1", "VAL2"), case_insensitive=True)

        self._pack = [
            (basic, "VAL1", b"VAL1"),
            (basic, "VAL2", b"VAL2"),
            (basic, "a", ValueError),
            (basic, "val1", ValueError),
            (basic, None, ValueError),
            (default, None, b"VAL1"),
            (default_optional, None, b"VAL1"),
            (optional, None, ValueError),
            (case_insensitive, "VAL1", b"VAL1"),
            (case_insensitive, "vAl2", b"vAl2"),
            (case_insensitive, "a", ValueError),
        ]

        self._unpack = [
            (basic, b"VAL1", "VAL1"),
            (basic, b"VAL2", "VAL2"),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (default, None, "VAL1"),
            (default_optional, None, "VAL1"),
            (optional, None, None),
            (case_insensitive, b"val1", "val1"),
            (case_insensitive, b"vAl2", "vAl2"),
            (case_insensitive, b"a", ValueError),
        ]


class TestLru(TestType):

    def setUp(self):
        basic = Lru()
        default = Lru(default=Lru.LRU_NOMINAL)
        optional = Lru(optional=True)
        default_optional = Lru(default=Lru.LRU_NOMINAL, optional=True)

        self._pack = [
            (basic, Lru.LRU_NOMINAL, b"nominal"),
            (basic, Lru.LRU_ERROR, b"error"),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, b"nominal"),
            (default_optional, None, b"nominal"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"nominal", Lru.LRU_NOMINAL),
            (basic, b"error", Lru.LRU_ERROR),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (default, None, Lru.LRU_NOMINAL),
            (default_optional, None, Lru.LRU_NOMINAL),
            (optional, None, None),
        ]


class TestAddress(TestType):

    def setUp(self):
        basic = Address()
        default = Address(default=("127.0.0.1", None))
        optional = Address(optional=True)
        default_optional = Address(default=("127.0.0.1", None), optional=True)

        self._pack = [
            (basic, ("127.0.0.1", None), b"127.0.0.1"),
            (basic, ("127.0.0.1", 80), b"127.0.0.1:80"),
            (basic, ("0:0:0:0:0:0:0:1", None), b"[0:0:0:0:0:0:0:1]"),
            (basic, ("::1", None), b"[::1]"),
            (basic, ("::FFFF:204.152.189.116", None),
             b"[::FFFF:204.152.189.116]"),
            (basic, ("::1", 80), b"[::1]:80"),
            (basic, "127.0.0.1", ValueError),  # value not a tuple
            (default, None, b"127.0.0.1"),
            (default_optional, None, b"127.0.0.1"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"127.0.0.1", ("127.0.0.1", None)),
            (basic, b"127.0.0.1:80", ("127.0.0.1", 80)),
            (basic, b"[0:0:0:0:0:0:0:1]", ("0:0:0:0:0:0:0:1", None)),
            (basic, b"[::1]", ("::1", None)),
            (basic, b"[::FFFF:204.152.189.116]", ("::FFFF:204.152.189.116",
                                                 None)),
            (basic, b"[::1]:80", ("::1", 80)),
            (basic, b"127.0.0.1:foo", ValueError),
            (basic, None, ValueError),
            (default, None, ("127.0.0.1", None)),
            (default_optional, None, ("127.0.0.1", None)),
            (optional, None, None),
        ]


class TestTimestamp(TestType):

    def setUp(self):
        basic = Timestamp()
        default = Timestamp(default=1235475793.0324881)
        optional = Timestamp(optional=True)
        default_optional = Timestamp(default=1235475793.0324881, optional=True)

        self._pack = [
            (basic, 1235475381.6966901, b"1235475381.696690"),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, b"1235475793.032488"),
            (default_optional, None, b"1235475793.032488"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"1235475381.696", 1235475381.6960001),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (default, None, 1235475793.0324881),
            (default_optional, None, 1235475793.0324881),
            (optional, None, None),
        ]


class TestStrictTimestamp(TestType):

    def setUp(self):
        basic = StrictTimestamp()
        default = StrictTimestamp(default=1235475793.03249)
        optional = StrictTimestamp(optional=True)
        default_optional = StrictTimestamp(default=1235475793.03249,
                                           optional=True)

        self._pack = [
            (basic, 1235475381.69669, b"1235475381.69669"),
            (basic, -0.1, ValueError),
            (basic, -1.0, ValueError),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, b"1235475793.03249"),
            (default_optional, None, b"1235475793.03249"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"1235475381.696", 1235475381.6960001),
            (basic, b"-0.1", ValueError),
            (basic, b"-1.0", ValueError),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (default, None, 1235475793.03249),
            (default_optional, None, 1235475793.03249),
            (optional, None, None),
        ]


class TestTimestampOrNow(TestType):

    def setUp(self):
        basic = TimestampOrNow()
        default = TimestampOrNow(default=1235475793.0324881)
        optional = TimestampOrNow(optional=True)
        default_optional = TimestampOrNow(default=1235475793.0324881,
                                          optional=True)
        default_now = TimestampOrNow(default=TimestampOrNow.NOW)

        self._pack = [
            (basic, 1235475381.6966901, b"1235475381.696690"),
            (basic, "a", ValueError),
            (basic, TimestampOrNow.NOW, b"now"),
            (basic, None, ValueError),
            (default, None, b"1235475793.032488"),
            (default, TimestampOrNow.NOW, b"now"),
            (default_optional, None, b"1235475793.032488"),
            (optional, None, ValueError),
            (default_now, None, b"now"),
        ]

        self._unpack = [
            (basic, b"1235475381.696", 1235475381.6960001),
            (basic, b"a", ValueError),
            (basic, b"now", TimestampOrNow.NOW),
            (basic, None, ValueError),
            (default, None, 1235475793.0324881),
            (default, b"now", TimestampOrNow.NOW),
            (default_optional, None, 1235475793.0324881),
            (optional, None, None),
            (default_now, None, TimestampOrNow.NOW),
        ]


class TestStr(TestType):

    def setUp(self):
        basic = Str()
        default = Str(default="something")
        optional = Str(optional=True)
        default_optional = Str(default="something", optional=True)

        # For packing, the input can be any type except NoneType.
        self._pack = [
            (basic, "adsasdasd", b"adsasdasd"),
            (basic, b"adsasdasd", b"adsasdasd"),
            (basic, u"adsasdasd", b"adsasdasd"),
            (basic, u"skräm", b"skr\xc3\xa4m"),
            (basic, [1, 2.0, 'three', False], b"[1, 2.0, 'three', False]"),
            (basic, None, ValueError),
            (default, None, b"something"),
            (default_optional, None, b"something"),
            (optional, None, ValueError),
        ]

        # For unpacking, the input is assumed to be a byte string, and
        # the output should be a native string (bytes on PY2, unicode on PY3).
        # On PY2, as input is assumed to be bytes already, nothing is done,
        # nor is the type checked.
        if future.utils.PY2:
            bytes_unpacked = "adsasdasd"
            unicode_unpacked = u"adsasdasd"
            utf8_unpacked = "skr\xc3\xa4m"
            list_str_unpacked = "[1, 2.0, 'three', False]"
        else:
            bytes_unpacked = "adsasdasd"
            unicode_unpacked = AttributeError
            utf8_unpacked = "skräm"
            list_str_unpacked = "[1, 2.0, 'three', False]"

        self._unpack = [
            (basic, b"adsasdasd", bytes_unpacked),
            (basic, u"adsasdasd", unicode_unpacked),
            (basic, b"skr\xc3\xa4m", utf8_unpacked),
            (basic, b"[1, 2.0, 'three', False]", list_str_unpacked),
            (basic, None, ValueError),
            (default, None, "something"),
            (default_optional, None, "something"),
            (optional, None, None),
        ]


class TestStruct(TestType):

    def setUp(self):
        basic = Struct(">isf")
        default = Struct(">isf", default=(1, b"f", 3.4))
        optional = Struct(">isf", optional=True)
        default_optional = Struct(">isf", default=(1, b"f", 3.4), optional=True)

        self._pack = [
            (basic, (5, b"s", 2.5), b"\x00\x00\x00\x05s@ \x00\x00"),
            (basic, (b"s", 5, 2.5), ValueError),
            (basic, (5, b"s"), ValueError),
            (basic, None, ValueError),
            (default, None, b"\x00\x00\x00\x01f@Y\x99\x9a"),
            (default_optional, None, b"\x00\x00\x00\x01f@Y\x99\x9a"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"\x00\x00\x00\x05s@ \x00\x00", (5, b"s", 2.5)),
            (basic, b"asdfgasdfas", ValueError),
            (basic, None, ValueError),
            (default, None, (1, b"f", 3.4)),
            (default_optional, None, (1, b"f", 3.4)),
            (optional, None, None),
        ]


class TestRegex(TestType):

    def setUp(self):
        basic = Regex("\d\d:\d\d:\d\d")
        default = Regex("\d\d:\d\d:\d\d", default="00:00:00")
        optional = Regex("\d\d:\d\d:\d\d", optional=True)
        default_optional = Regex("\d\d:\d\d:\d\d", default="00:00:00",
                                 optional=True)

        self._pack = [
            (basic, "12:34:56", b"12:34:56"),
            (basic, "sdfasdfsadf", ValueError),
            (basic, None, ValueError),
            (default, None, b"00:00:00"),
            (default_optional, None, b"00:00:00"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, b"12:34:56", "12:34:56"),
            (basic, b"sdfasdfsadf", ValueError),
            (basic, None, ValueError),
            (default, None, "00:00:00"),
            (default_optional, None, "00:00:00"),
            (optional, None, None),
        ]


class TestDiscreteMulti(TestType):

    def setUp(self):
        basic = DiscreteMulti(("VAL1", "VAL2"))
        default = DiscreteMulti(("VAL1", "VAL2"), default=["VAL1"])
        optional = DiscreteMulti(("VAL1", "VAL2"), optional=True)
        default_optional = DiscreteMulti(("VAL1", "VAL2"), default=["VAL1"],
                                         optional=True)
        case_insensitive = DiscreteMulti(("val1", "VAL2"),
                                         case_insensitive=True)

        self._pack = [
            (basic, ["VAL1"], b"VAL1"),
            (basic, ["VAL2"], b"VAL2"),
            (basic, ["VAL1", "VAL2"], b"VAL1,VAL2"),
            (basic, "a", ValueError),
            (basic, "VAL1", ValueError),
            (basic, ["aaa"], ValueError),
            (basic, ["val1"], ValueError),
            (basic, ["VAL1", "val2"], ValueError),
            (basic, ["VAL1", "aaa"], ValueError),
            (basic, None, ValueError),
            (default, None, b"VAL1"),
            (default_optional, None, b"VAL1"),
            (optional, None, ValueError),
            (case_insensitive, ["VAL1"], b"VAL1"),
            (case_insensitive, ["vAl2"], b"vAl2"),
            (case_insensitive, ["VAL1", "val2"], b"VAL1,val2"),
            (case_insensitive, ["aaa"], ValueError),
        ]

        self._unpack = [
            (basic, b"VAL1", ["VAL1"]),
            (basic, b"VAL2", ["VAL2"]),
            (basic, b"VAL1,VAL2", ["VAL1", "VAL2"]),
            (basic, b"all", ["VAL1", "VAL2"]),
            (basic, b"VAL1,aaa", ValueError),
            (basic, b"VAL1,val2", ValueError),
            (basic, b"a", ValueError),
            (basic, None, ValueError),
            (default, None, ["VAL1"]),
            (default_optional, None, ["VAL1"]),
            (optional, None, None),
            (case_insensitive, b"val1", ["val1"]),
            (case_insensitive, b"vAl2", ["vAl2"]),
            (case_insensitive, b"VAL1,val2", ["VAL1", "val2"]),
            (case_insensitive, b"VAL1,aaa", ValueError),
            (case_insensitive, b"a", ValueError),
        ]


class TestDevice(object):
    def __init__(self):
        self.sent_messages = []

    @request(Int(min=1, max=10), Discrete(("on", "off")), Bool())
    @return_reply(Int(min=1, max=10), Discrete(("on", "off")), Bool())
    def request_one(self, req, i, d, b):
        if i == 3:
            return ("fail", "I failed!")
        if i == 5:
            return ("bananas", "This should never be sent")
        if i == 6:
            return ("ok", i, d, b, "extra parameter")
        if i == 9:
            self.finish_request_one(req, i, d, b)
            raise AsyncReply()
        return ("ok", i, d, b)

    @send_reply(Int(min=1, max=10), Discrete(("on", "off")), Bool())
    def finish_request_one(self, req, i, d, b):
        return (req, "ok", i, d, b)

    def reply(self, req, msg, orig_msg):
        self.sent_messages.append([req, msg])

    @request(Int(min=1, max=3, default=2),
             Discrete(("on", "off"), default="off"), Bool(default=True))
    @return_reply(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def request_two(self, req, i, d, b):
        return ("ok", i, d, b)

    @return_reply(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    @request(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def request_three(self, req, i, d, b):
        return ("ok", i, d, b)

    @return_reply()
    @request()
    def request_four(self, req):
        return ["ok"]

    @inform(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def inform_one(self, i, d, b):
        pass

    @request(Timestamp(), Timestamp(optional=True), major=4)
    @return_reply(Timestamp(), Timestamp(default=321), major=4)
    def request_katcpv4_time(self, req, timestamp1, timestamp2):
        self.katcpv4_time1 = timestamp1
        self.katcpv4_time2 = timestamp2
        if timestamp2:
            return ('ok', timestamp1, timestamp2)
        else:
            return ('ok', timestamp1)

    @request(Timestamp(multiple=True), major=4)
    @return_reply(Timestamp(multiple=True), major=4)
    def request_katcpv4_time_multi(self, req, *timestamps):
        self.katcpv4_time_multi = timestamps
        return ('ok',) + timestamps

    @return_reply(Int(), Str())
    @request(Int(), include_msg=True)
    def request_eight(self, req, msg, i):
        return ("ok", i, msg.name)

    @request(Int(), Float(multiple=True))
    @return_reply(Int(), Float(multiple=True))
    def request_int_multifloat(self, req, i, *floats):
        return ('ok', i) + floats

class TestDecorator(unittest.TestCase):
    def setUp(self):
        self.device = TestDevice()

    def test_request_multi(self):
        with self.assertRaises(TypeError) as ex:
            request(Bool(multiple=True), Int())
        self.assertEqual(
            str(ex.exception),
            'Only the last parameter type can accept multiple arguments.')

    def test_return_reply_multi(self):
        with self.assertRaises(TypeError) as ex:
            return_reply(Bool(multiple=True), Int())
        self.assertEqual(
            str(ex.exception),
            'Only the last parameter type can accept multiple arguments.')

    def test_katcpv4(self):
        ts = 12345678                     # In milliseconds
        req = mock.Mock()
        ret_msg = self.device.request_katcpv4_time(req, Message.request(
            'katcpv4-time', str(ts)))
        self.assertTrue(ret_msg.reply_ok())
        self.assertAlmostEqual(float(ret_msg.arguments[1]), ts)
        # Test decorator default value
        self.assertAlmostEqual(float(ret_msg.arguments[2]), 321*SEC_TO_MS_FAC)
        self.assertAlmostEqual(self.device.katcpv4_time1*SEC_TO_MS_FAC, ts)
        self.assertEqual(self.device.katcpv4_time2, None)
        ts1 = 1234
        ts2 = 2345
        ret_msg = self.device.request_katcpv4_time(req, Message.request(
            'katcpv4-time', str(ts1), str(ts2)))
        self.assertTrue(ret_msg.reply_ok())
        self.assertAlmostEqual(float(ret_msg.arguments[1]), ts1)
        self.assertAlmostEqual(float(ret_msg.arguments[2]), ts2)
        self.assertAlmostEqual(self.device.katcpv4_time1*SEC_TO_MS_FAC, ts1)
        self.assertAlmostEqual(self.device.katcpv4_time2*SEC_TO_MS_FAC, ts2)

    def test_katcpv4_multi(self):
        tss = (1234, 5678, 9012)                     # In milliseconds
        req = mock.Mock()
        ret_msg = self.device.request_katcpv4_time_multi(req, Message.request(
            'katcpv4-time-multi', *(str(ts) for ts in tss) ))
        for i, ts in enumerate(tss):
            self.assertAlmostEqual(float(ret_msg.arguments[i+1]), ts)
            self.assertAlmostEqual(self.device.katcpv4_time_multi[i],
                                   ts*MS_TO_SEC_FAC)

    def test_request_one(self):
        """Test request with no defaults."""
        req = mock.Mock()
        req.msg.name = 'one'
        self.assertEqual(str(self.device.request_one(req, Message.request(
                        "one", "2", "on", "0"))), "!one ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_one, req,
                          Message.request("one", "14", "on", "0"))
        self.assertRaises(FailReply, self.device.request_one, req,
                          Message.request("one", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_one, req,
                          Message.request("one", "2", "on", "3"))
        self.assertRaises(FailReply, self.device.request_one, req,
                          Message.request("one", "2", "on", "0", "3"))

        self.assertRaises(FailReply, self.device.request_one, req,
                          Message.request("one", "2", "on"))

        self.assertEqual(str(self.device.request_one(req, Message.request(
                        "one", "3", "on", "0"))), "!one fail I\\_failed!")
        self.assertRaises(ValueError, self.device.request_one, req,
                          Message.request("one", "5", "on", "0"))
        self.assertRaises(ValueError, self.device.request_one, req,
                          Message.request("one", "6", "on", "0"))

        req.reset_mock()
        self.assertRaises(AsyncReply, self.device.request_one, req,
                          Message.request("one", "9", "on", "0"))
        self.assertEqual(req.reply_with_message.call_count, 1)
        req.reply_with_message.assert_called_once_with(Message.reply(
            'one', 'ok', '9', 'on', '0'))

    def test_request_two(self):
        """Test request with defaults."""
        req = ""
        self.assertEqual(str(self.device.request_two(req, Message.request(
                        "two", "2", "on", "0"))), "!two ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_two, req,
                          Message.request("two", "4", "on", "0"))
        self.assertRaises(FailReply, self.device.request_two, req,
                          Message.request("two", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_two, req,
                          Message.request("two", "2", "on", "3"))

        self.assertEqual(str(self.device.request_two(req, Message.request(
                        "two", "2", "on"))), "!two ok 2 on 1")
        self.assertEqual(str(self.device.request_two(req, Message.request(
                        "two", "2"))), "!two ok 2 off 1")
        self.assertEqual(str(self.device.request_two(req, Message.request(
                        "two"))), "!two ok 2 off 1")

    def test_request_three(self):
        """Test request with no defaults and decorators in reverse order."""
        req = ""
        self.assertEqual(str(self.device.request_three(req, Message.request(
                        "three", "2", "on", "0"))), "!three ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_three, req,
                          Message.request("three", "4", "on", "0"))
        self.assertRaises(FailReply, self.device.request_three, req,
                          Message.request("three", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_three, req,
                          Message.request("three", "2", "on", "3"))

        self.assertRaises(FailReply, self.device.request_three, req,
                          Message.request("three", "2", "on"))

    def test_request_four(self):
        """Test request with no defaults and no parameters or return values"""
        req = ""
        self.assertEqual(str(self.device.request_four(req, Message.request(
                        "four"))), "!four ok")

    def test_inform_one(self):
        """Test inform with no defaults."""
        req = ""
        self.assertEqual(self.device.inform_one(Message.inform(
                         "one", "2", "on", "0")), None)

    def test_request_eight(self):
        """Test server request with a message argument."""
        req = ""
        self.assertEqual(str(self.device.request_eight(req, Message.request(
                         "eight", "8"))), "!eight ok 8 eight")

    def test_request_int_multifloat(self):
        req = self.device.request_int_multifloat
        desired_i, desired_floats = (7, (1.2, 999, 71.43))
        self.assertEqual(str(req('req', Message.request(
            'int-multifloat', desired_i, *desired_floats))),
                         '!int-multifloat ok 7 1.2 999.0 71.43')
        with self.assertRaises(FailReply) as ex:
            req('req', Message.request('int-multifloat', desired_i, 1.2, 'abc'))
        # storing Message.arguments as byte string results in slightly different
        # reprs for PY2 compared to PY3.
        if future.utils.PY2:
            expected = "Error in parameter 3 (): Could not parse value 'abc' as float."
        else:
            expected = "Error in parameter 3 (): Could not parse value 'b'abc'' as float."
        self.assertEqual(str(ex.exception), expected)


class TestPackTypes(unittest.TestCase):

    def check_packing(self, types, args, expected):
        list_packed = pack_types(list(types), list(args), DEFAULT_KATCP_MAJOR)
        tuple_packed = pack_types(tuple(types), tuple(args), DEFAULT_KATCP_MAJOR)
        self.assertEqual(list_packed, expected)
        self.assertEqual(tuple_packed, expected)

    def test_pack_types_empty(self):
        expected = []
        self.check_packing([], [], expected)

    def test_pack_types_single(self):
        expected = [b'one']
        self.check_packing([Str()], ['one'], expected)

    def test_pack_types_many_without_multiple(self):
        expected = [b'one', b'2']
        self.check_packing([Str(), Int()], ['one', 2], expected)

    def test_pack_types_many_with_multiple(self):
        expected = [b'one', b'2', b'3']
        self.check_packing([Str(), Int(multiple=True)], ['one', 2, 3], expected)

    def test_pack_types_more_types_than_args(self):
        expected = [b'one', b'2', b'1', b'four']
        self.check_packing(
            [Str(), Int(), Bool(default=True), Str(default='four')],
            ['one', 2],
            expected)

    def test_pack_types_more_args_than_types_fails(self):
        with self.assertRaises(ValueError):
            self.check_packing([], ['one'], [])


class TestUnpackTypes(unittest.TestCase):

    def check_unpacking(self, types, args, expected):
        arg_names = ['arg'] * len(args)  # names don't matter
        list_unpacked = unpack_types(
            list(types), list(args), arg_names, DEFAULT_KATCP_MAJOR)
        tuple_unpacked = unpack_types(
            tuple(types), tuple(args), tuple(arg_names), DEFAULT_KATCP_MAJOR)
        self.assertEqual(list_unpacked, expected)
        self.assertEqual(tuple_unpacked, expected)

    def test_unpack_types_empty(self):
        expected = []
        self.check_unpacking([], [], expected)

    def test_unpack_types_single(self):
        expected = ['one']
        self.check_unpacking([Str()], [b'one'], expected)

    def test_unpack_types_many_without_multiple(self):
        expected = ['one', 2]
        self.check_unpacking([Str(), Int()], [b'one', b'2'], expected)

    def test_unpack_types_many_with_multiple(self):
        expected = ['one', 2, 3]
        self.check_unpacking([Str(), Int(multiple=True)], [b'one', b'2', b'3'], expected)

    def test_unpack_types_more_types_than_args(self):
        expected = ['one', 2, True, None]
        self.check_unpacking(
            [Str(), Int(), Bool(default=True), Str(optional=True)],
            [b'one', b'2'],
            expected)

    def test_unpack_types_more_args_than_types_fails(self):
        with self.assertRaises(FailReply):
            self.check_unpacking([], ['one'], [])
