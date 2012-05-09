# test_kattypes.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the kattypes module.
   """

import unittest2 as unittest
from katcp import Message, FailReply, AsyncReply
from katcp.kattypes import request, inform, return_reply, send_reply,  \
                           Bool, Discrete, Float, Int, Lru, Timestamp, \
                           Str, Struct, Regex, DiscreteMulti, TimestampOrNow, \
                           StrictTimestamp


class TestType(unittest.TestCase):
    def setUp(self):
        self._pack = []
        self._unpack = []

    def test_pack(self):
        for t, value, result in self._pack:
            if type(result) is type and issubclass(result, Exception):
                self.assertRaises(result, t.pack, value)
            else:
                self.assertEquals(t.pack(value), result)

    def test_unpack(self):
        for t, value, result in self._unpack:
            if type(result) is type and issubclass(result, Exception):
                self.assertRaises(result, t.unpack, value)
            else:
                self.assertEquals(t.unpack(value), result)


class TestInt(TestType):

    def setUp(self):
        basic = Int()
        default = Int(default=11)
        optional = Int(optional=True)
        default_optional = Int(default=11, optional=True)
        self.minmax = Int(min=5, max=6)

        self._pack = [
            (basic, 5, "5"),
            (basic, -5, "-5"),
            (basic, "a", TypeError),
            (basic, None, ValueError),
            (self.minmax, 5, "5"),
            (self.minmax, 6, "6"),
            (self.minmax, 4, ValueError),
            (self.minmax, 7, ValueError),
            (default, None, "11"),
            (default_optional, None, "11"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "5", 5),
            (basic, "-5", -5),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (self.minmax, "5", 5),
            (self.minmax, "6", 6),
            (self.minmax, "4", ValueError),
            (self.minmax, "7", ValueError),
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
        self.minmax = Float(min=5.0, max=6.0)

        self._pack = [
            (basic, 5.0, "5"),
            (basic, -5.0, "-5"),
            (basic, "a", TypeError),
            (basic, None, ValueError),
            (self.minmax, 5.0, "5"),
            (self.minmax, 6.0, "6"),
            (self.minmax, 4.5, ValueError),
            (self.minmax, 6.5, ValueError),
            (default, None, "11"),
            (default_optional, None, "11"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "5", 5.0),
            (basic, "-5", -5.0),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (self.minmax, "5", 5.0),
            (self.minmax, "6", 6.0),
            (self.minmax, "4.5", ValueError),
            (self.minmax, "6.5", ValueError),
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
            (basic, True, "1"),
            (basic, False, "0"),
            (basic, 1, "1"),
            (basic, 0, "0"),
            (basic, "a", "1"),
            (basic, None, ValueError),
            (default, None, "1"),
            (default_optional, None, "1"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "1", True),
            (basic, "0", False),
            (basic, "a", ValueError),
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
            (basic, "VAL1", "VAL1"),
            (basic, "VAL2", "VAL2"),
            (basic, "a", ValueError),
            (basic, "val1", ValueError),
            (basic, None, ValueError),
            (default, None, "VAL1"),
            (default_optional, None, "VAL1"),
            (optional, None, ValueError),
            (case_insensitive, "VAL1", "VAL1"),
            (case_insensitive, "vAl2", "vAl2"),
            (case_insensitive, "a", ValueError),
        ]

        self._unpack = [
            (basic, "VAL1", "VAL1"),
            (basic, "VAL2", "VAL2"),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, "VAL1"),
            (default_optional, None, "VAL1"),
            (optional, None, None),
            (case_insensitive, "val1", "val1"),
            (case_insensitive, "vAl2", "vAl2"),
            (case_insensitive, "a", ValueError),
        ]


class TestLru(TestType):

    def setUp(self):
        basic = Lru()
        default = Lru(default=Lru.LRU_NOMINAL)
        optional = Lru(optional=True)
        default_optional = Lru(default=Lru.LRU_NOMINAL, optional=True)

        self._pack = [
            (basic, Lru.LRU_NOMINAL, "nominal"),
            (basic, Lru.LRU_ERROR, "error"),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, "nominal"),
            (default_optional, None, "nominal"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "nominal", Lru.LRU_NOMINAL),
            (basic, "error", Lru.LRU_ERROR),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, Lru.LRU_NOMINAL),
            (default_optional, None, Lru.LRU_NOMINAL),
            (optional, None, None),
        ]


class TestTimestamp(TestType):

    def setUp(self):
        basic = Timestamp()
        default = Timestamp(default=1235475793.0324881)
        optional = Timestamp(optional=True)
        default_optional = Timestamp(default=1235475793.0324881, optional=True)

        self._pack = [
            (basic, 1235475381.6966901, "1235475381696"),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, "1235475793032"),
            (default_optional, None, "1235475793032"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "1235475381696", 1235475381.6960001),
            (basic, "a", ValueError),
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
            (basic, 1235475381.69669, "1235475381696.69"),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, "1235475793032.49"),
            (default_optional, None, "1235475793032.49"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "1235475381696", 1235475381.6960001),
            (basic, "a", ValueError),
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
            (basic, 1235475381.6966901, "1235475381696"),
            (basic, "a", ValueError),
            (basic, TimestampOrNow.NOW, "now"),
            (basic, None, ValueError),
            (default, None, "1235475793032"),
            (default, TimestampOrNow.NOW, "now"),
            (default_optional, None, "1235475793032"),
            (optional, None, ValueError),
            (default_now, None, "now"),
        ]

        self._unpack = [
            (basic, "1235475381696", 1235475381.6960001),
            (basic, "a", ValueError),
            (basic, "now", TimestampOrNow.NOW),
            (basic, None, ValueError),
            (default, None, 1235475793.0324881),
            (default, "now", TimestampOrNow.NOW),
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

        self._pack = [
            (basic, "adsasdasd", "adsasdasd"),
            (basic, None, ValueError),
            (default, None, "something"),
            (default_optional, None, "something"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "adsasdasd", "adsasdasd"),
            (basic, None, ValueError),
            (default, None, "something"),
            (default_optional, None, "something"),
            (optional, None, None),
        ]


class TestStruct(TestType):

    def setUp(self):
        basic = Struct(">isf")
        default = Struct(">isf", default=(1, "f", 3.4))
        optional = Struct(">isf", optional=True)
        default_optional = Struct(">isf", default=(1, "f", 3.4), optional=True)

        self._pack = [
            (basic, (5, "s", 2.5), "\x00\x00\x00\x05s@ \x00\x00"),
            (basic, ("s", 5, 2.5), ValueError),
            (basic, (5, "s"), ValueError),
            (basic, None, ValueError),
            (default, None, "\x00\x00\x00\x01f@Y\x99\x9a"),
            (default_optional, None, "\x00\x00\x00\x01f@Y\x99\x9a"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "\x00\x00\x00\x05s@ \x00\x00", (5, "s", 2.5)),
            (basic, "asdfgasdfas", ValueError),
            (basic, None, ValueError),
            (default, None, (1, "f", 3.4)),
            (default_optional, None, (1, "f", 3.4)),
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
            (basic, "12:34:56", "12:34:56"),
            (basic, "sdfasdfsadf", ValueError),
            (basic, None, ValueError),
            (default, None, "00:00:00"),
            (default_optional, None, "00:00:00"),
            (optional, None, ValueError),
        ]

        self._unpack = [
            (basic, "12:34:56", "12:34:56"),
            (basic, "sdfasdfsadf", ValueError),
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
            (basic, ["VAL1"], "VAL1"),
            (basic, ["VAL2"], "VAL2"),
            (basic, ["VAL1", "VAL2"], "VAL1,VAL2"),
            (basic, "a", ValueError),
            (basic, "VAL1", ValueError),
            (basic, ["aaa"], ValueError),
            (basic, ["val1"], ValueError),
            (basic, ["VAL1", "val2"], ValueError),
            (basic, ["VAL1", "aaa"], ValueError),
            (basic, None, ValueError),
            (default, None, "VAL1"),
            (default_optional, None, "VAL1"),
            (optional, None, ValueError),
            (case_insensitive, ["VAL1"], "VAL1"),
            (case_insensitive, ["vAl2"], "vAl2"),
            (case_insensitive, ["VAL1", "val2"], "VAL1,val2"),
            (case_insensitive, ["aaa"], ValueError),
        ]

        self._unpack = [
            (basic, "VAL1", ["VAL1"]),
            (basic, "VAL2", ["VAL2"]),
            (basic, "VAL1,VAL2", ["VAL1", "VAL2"]),
            (basic, "all", ["VAL1", "VAL2"]),
            (basic, "VAL1,aaa", ValueError),
            (basic, "VAL1,val2", ValueError),
            (basic, "a", ValueError),
            (basic, None, ValueError),
            (default, None, ["VAL1"]),
            (default_optional, None, ["VAL1"]),
            (optional, None, None),
            (case_insensitive, "val1", ["val1"]),
            (case_insensitive, "vAl2", ["vAl2"]),
            (case_insensitive, "VAL1,val2", ["VAL1", "val2"]),
            (case_insensitive, "VAL1,aaa", ValueError),
            (case_insensitive, "a", ValueError),
        ]


class TestDevice(object):
    def __init__(self):
        self.sent_messages = []

    @request(Int(min=1, max=10), Discrete(("on", "off")), Bool())
    @return_reply(Int(min=1, max=10), Discrete(("on", "off")), Bool())
    def request_one(self, sock, i, d, b):
        if i == 3:
            return ("fail", "I failed!")
        if i == 5:
            return ("bananas", "This should never be sent")
        if i == 6:
            return ("ok", i, d, b, "extra parameter")
        if i == 9:
            # This actually gets put in the callback params automatically
            orig_msg = Message.request("one", "foo", "bar")
            self.finish_request_one(orig_msg, sock, i, d, b)
            raise AsyncReply()
        return ("ok", i, d, b)

    @send_reply(Int(min=1, max=10), Discrete(("on", "off")), Bool())
    def finish_request_one(self, msg, sock, i, d, b):
        return (sock, msg, "ok", i, d, b)

    def reply(self, sock, msg, orig_msg):
        self.sent_messages.append([sock, msg])

    @request(Int(min=1, max=3, default=2),
             Discrete(("on", "off"), default="off"), Bool(default=True))
    @return_reply(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def request_two(self, sock, i, d, b):
        return ("ok", i, d, b)

    @return_reply(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    @request(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def request_three(self, sock, i, d, b):
        return ("ok", i, d, b)

    @return_reply()
    @request()
    def request_four(self, sock):
        return ["ok"]

    @inform(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def inform_one(self, sock, i, d, b):
        pass

    @request(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    @return_reply(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def request_five(self, i, d, b):
        return ("ok", i, d, b)

    @return_reply(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    @request(Int(min=1, max=3), Discrete(("on", "off")), Bool())
    def request_six(self, i, d, b):
        return ("ok", i, d, b)

    @return_reply(Int(), Str())
    @request(Int(), include_msg=True)
    def request_seven(self, msg, i):
        return ("ok", i, msg.name)

    @return_reply(Int(), Str())
    @request(Int(), include_msg=True)
    def request_eight(self, sock, msg, i):
        return ("ok", i, msg.name)

    @request(Int(), Float(multiple=True))
    @return_reply(Int(), Float(multiple=True))
    def request_int_multifloat(self, sock, i, *floats):
        return ('ok', i) + floats

class TestDecorator(unittest.TestCase):
    def setUp(self):
        self.device = TestDevice()

    def test_request_multi(self):
        with self.assertRaises(TypeError) as ex:
            request(Bool(multiple=True), Int())
        self.assertEqual(
            ex.exception.message,
            'Only the last parameter type can accept multiple arguments.')

    def test_return_reply_multi(self):
        with self.assertRaises(TypeError) as ex:
            return_reply(Bool(multiple=True), Int())
        self.assertEqual(
            ex.exception.message,
            'Only the last parameter type can accept multiple arguments.')

    def test_request_one(self):
        """Test request with no defaults."""
        sock = ""
        self.assertEqual(str(self.device.request_one(sock, Message.request(
                        "one", "2", "on", "0"))), "!one ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_one, sock,
                          Message.request("one", "14", "on", "0"))
        self.assertRaises(FailReply, self.device.request_one, sock,
                          Message.request("one", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_one, sock,
                          Message.request("one", "2", "on", "3"))
        self.assertRaises(FailReply, self.device.request_one, sock,
                          Message.request("one", "2", "on", "0", "3"))

        self.assertRaises(FailReply, self.device.request_one, sock,
                          Message.request("one", "2", "on"))

        self.assertEqual(str(self.device.request_one(sock, Message.request(
                        "one", "3", "on", "0"))), "!one fail I\\_failed!")
        self.assertRaises(ValueError, self.device.request_one, sock,
                          Message.request("one", "5", "on", "0"))
        self.assertRaises(ValueError, self.device.request_one, sock,
                          Message.request("one", "6", "on", "0"))

        self.assertRaises(AsyncReply, self.device.request_one, "mysock",
                          Message.request("one", "9", "on", "0"))
        self.assertEqual(len(self.device.sent_messages), 1)
        self.assertEqual(self.device.sent_messages[0][0], "mysock")
        self.assertEqual(str(self.device.sent_messages[0][1]),
                         "!one ok 9 on 0")

    def test_request_two(self):
        """Test request with defaults."""
        sock = ""
        self.assertEqual(str(self.device.request_two(sock, Message.request(
                        "two", "2", "on", "0"))), "!two ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_two, sock,
                          Message.request("two", "4", "on", "0"))
        self.assertRaises(FailReply, self.device.request_two, sock,
                          Message.request("two", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_two, sock,
                          Message.request("two", "2", "on", "3"))

        self.assertEqual(str(self.device.request_two(sock, Message.request(
                        "two", "2", "on"))), "!two ok 2 on 1")
        self.assertEqual(str(self.device.request_two(sock, Message.request(
                        "two", "2"))), "!two ok 2 off 1")
        self.assertEqual(str(self.device.request_two(sock, Message.request(
                        "two"))), "!two ok 2 off 1")

    def test_request_three(self):
        """Test request with no defaults and decorators in reverse order."""
        sock = ""
        self.assertEqual(str(self.device.request_three(sock, Message.request(
                        "three", "2", "on", "0"))), "!three ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_three, sock,
                          Message.request("three", "4", "on", "0"))
        self.assertRaises(FailReply, self.device.request_three, sock,
                          Message.request("three", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_three, sock,
                          Message.request("three", "2", "on", "3"))

        self.assertRaises(FailReply, self.device.request_three, sock,
                          Message.request("three", "2", "on"))

    def test_request_four(self):
        """Test request with no defaults and no parameters or return values"""
        sock = ""
        self.assertEqual(str(self.device.request_four(sock, Message.request(
                        "four"))), "!four ok")

    def test_inform_one(self):
        """Test inform with no defaults."""
        sock = ""
        self.assertEqual(self.device.inform_one(sock, Message.inform(
                         "one", "2", "on", "0")), None)

    def test_request_five(self):
        """Test client request with no sock."""
        self.assertEqual(str(self.device.request_five(Message.request(
                         "five", "2", "on", "0"))), "!five ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_five,
                          Message.request("five", "14", "on", "0"))
        self.assertRaises(FailReply, self.device.request_five,
                          Message.request("five", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_five,
                          Message.request("five", "2", "on", "3"))
        self.assertRaises(FailReply, self.device.request_five,
                          Message.request("five", "2", "on", "0", "3"))

        self.assertRaises(FailReply, self.device.request_five,
                          Message.request("five", "2", "on"))

    def test_request_six(self):
        """Test client request with no sock and decorators in reverse order."""
        self.assertEqual(str(self.device.request_six(Message.request(
                         "six", "2", "on", "0"))), "!six ok 2 on 0")
        self.assertRaises(FailReply, self.device.request_six,
                          Message.request("six", "4", "on", "0"))
        self.assertRaises(FailReply, self.device.request_six,
                          Message.request("six", "2", "dsfg", "0"))
        self.assertRaises(FailReply, self.device.request_six,
                          Message.request("six", "2", "on", "3"))

        self.assertRaises(FailReply, self.device.request_six,
                          Message.request("six", "2", "on"))

    def test_request_seven(self):
        """Test client request with no sock but with a message."""
        self.assertEqual(str(self.device.request_seven(Message.request(
                         "seven", "7"))), "!seven ok 7 seven")

    def test_request_eight(self):
        """Test server request with a message argument."""
        sock = ""
        self.assertEqual(str(self.device.request_eight(sock, Message.request(
                         "eight", "8"))), "!eight ok 8 eight")

    def test_request_int_multifloat(self):
        req = self.device.request_int_multifloat
        desired_i, desired_floats = (7, (1.2, 999, 71.43))
        self.assertEqual(str(req('sock', Message.request(
            'int-multifloat', desired_i, *desired_floats))),
                         '!int-multifloat ok 7 1.2 999 71.43')
        with self.assertRaises(FailReply) as ex:
            req('sock', Message.request('int-multifloat', desired_i, 1.2, 'abc'))
        self.assertEqual(
            ex.exception.message,
            "Error in parameter 3 (): Could not parse value 'abc' as float.")

