"""Tests for the kattypes module."""

import unittest
from katcp import kattypes, Message

class TestInt(unittest.TestCase):

    def test_pack(self):
        """Test packing integers."""
        i = kattypes.Int()
        self.assertEqual(i.pack(5), "5")
        self.assertEqual(i.pack(-5), "-5")
        self.assertRaises(TypeError, i.pack, "a")
        self.assertRaises(ValueError, i.pack, None)

        i = kattypes.Int(min=5, max=6)
        self.assertEqual(i.pack(5), "5")
        self.assertEqual(i.pack(6), "6")
        self.assertRaises(ValueError, i.pack, 4)
        self.assertRaises(ValueError, i.pack, 7)

        i = kattypes.Int(default=11)
        self.assertEqual(i.pack(None), "11")

    def test_unpack(self):
        """Test unpacking integers."""
        i = kattypes.Int()
        self.assertEqual(i.unpack("5"), 5)
        self.assertEqual(i.unpack("-5"), -5)
        self.assertRaises(ValueError, i.unpack, "a")
        self.assertRaises(ValueError, i.unpack, None)

        i = kattypes.Int(min=5, max=6)
        self.assertEqual(i.unpack("5"), 5)
        self.assertEqual(i.unpack("6"), 6)
        self.assertRaises(ValueError, i.unpack, "4")
        self.assertRaises(ValueError, i.unpack, "7")

        i = kattypes.Int(default=11)
        self.assertEqual(i.unpack(None), 11)

class TestFloat(unittest.TestCase):

    def test_pack(self):
        """Test packing floats."""
        f = kattypes.Float()
        self.assertEqual(f.pack(5.0), "%e" % 5.0)
        self.assertEqual(f.pack(-5.0), "%e" % -5.0)
        self.assertRaises(TypeError, f.pack, "a")
        self.assertRaises(ValueError, f.pack, None)

        f = kattypes.Float(min=5.0, max=6.0)
        self.assertEqual(f.pack(5.0), "%e" % 5.0)
        self.assertEqual(f.pack(6.0), "%e" % 6.0)
        self.assertRaises(ValueError, f.pack, 4.5)
        self.assertRaises(ValueError, f.pack, 6.5)

        f = kattypes.Float(default=11.0)
        self.assertEqual(f.pack(None), "%e" % 11.0)

    def test_unpack(self):
        """Test unpacking floats."""
        f = kattypes.Float()
        self.assertAlmostEqual(f.unpack("5.0"), 5.0)
        self.assertAlmostEqual(f.unpack("-5.0"), -5.0)
        self.assertRaises(ValueError, f.unpack, "a")
        self.assertRaises(ValueError, f.unpack, None)

        f = kattypes.Float(min=5.0, max=6.0)
        self.assertAlmostEqual(f.unpack("5.0"), 5.0)
        self.assertAlmostEqual(f.unpack("6.0"), 6.0)
        self.assertRaises(ValueError, f.unpack, "4.5")
        self.assertRaises(ValueError, f.unpack, "6.5")

        f = kattypes.Float(default=11.0)
        self.assertAlmostEqual(f.unpack(None), 11.0)

class TestBool(unittest.TestCase):

    def test_pack(self):
        """Test packing booleans."""
        b = kattypes.Bool()
        self.assertEqual(b.pack(True), "1")
        self.assertEqual(b.pack(False), "0")
        self.assertEqual(b.pack(1), "1")
        self.assertEqual(b.pack(0), "0")
        self.assertRaises(ValueError, b.pack, None)

        b = kattypes.Bool(default=True)
        self.assertEqual(b.pack(None), "1")

    def test_unpack(self):
        """Test unpacking booleans."""
        b = kattypes.Bool()
        self.assertEqual(b.unpack("1"), True)
        self.assertEqual(b.unpack("0"), False)
        self.assertRaises(ValueError, b.unpack, "2")
        self.assertRaises(ValueError, b.unpack, None)

        b = kattypes.Bool(default=True)
        self.assertEqual(b.unpack(None), True)

class TestDiscrete(unittest.TestCase):

    def test_pack(self):
        """Test packing discrete types."""
        d = kattypes.Discrete(("VAL1", "VAL2"))
        self.assertEqual(d.pack("VAL1"), "VAL1")
        self.assertEqual(d.pack("VAL2"), "VAL2")
        self.assertRaises(ValueError, d.pack, "VAL3")
        self.assertRaises(ValueError, d.pack, None)

        d = kattypes.Discrete(("VAL1", "VAL2"), default="VAL1")
        self.assertEqual(d.pack(None), "VAL1")

    def test_unpack(self):
        """Test unpacking discrete types."""
        d = kattypes.Discrete(("VAL1", "VAL2"))
        self.assertEqual(d.unpack("VAL1"), "VAL1")
        self.assertEqual(d.unpack("VAL2"), "VAL2")
        self.assertRaises(ValueError, d.unpack, "VAL3")
        self.assertRaises(ValueError, d.unpack, None)

        d = kattypes.Discrete(("VAL1", "VAL2"), default="VAL1")
        self.assertEqual(d.unpack(None), "VAL1")

class TestLru(unittest.TestCase):

    def test_pack(self):
        """Test packing lru types."""
        l = kattypes.Lru()
        self.assertEqual(l.pack(kattypes.Lru.LRU_NOMINAL), "nominal")
        self.assertEqual(l.pack(kattypes.Lru.LRU_ERROR), "error")
        self.assertRaises(ValueError, l.pack, None)
        self.assertRaises(ValueError, l.pack, 5)
        self.assertRaises(ValueError, l.pack, "aaa")

        l = kattypes.Lru(default=kattypes.Lru.LRU_NOMINAL)
        self.assertEqual(l.pack(None), "nominal")

    def test_unpack(self):
        """Test unpacking lru types."""
        l = kattypes.Lru()
        self.assertEqual(l.unpack("nominal"), kattypes.Lru.LRU_NOMINAL)
        self.assertEqual(l.unpack("error"), kattypes.Lru.LRU_ERROR)
        self.assertRaises(ValueError, l.unpack, "aaa")
        self.assertRaises(ValueError, l.unpack, None)

        l = kattypes.Lru(default=kattypes.Lru.LRU_NOMINAL)
        self.assertEqual(l.unpack(None), kattypes.Lru.LRU_NOMINAL)

class TestTimestamp(unittest.TestCase):

    def test_pack(self):
        """Test packing timestamps."""
        t = kattypes.Timestamp()
        self.assertEqual(t.pack(1235475381.6966901), "1235475381696")
        self.assertRaises(ValueError, t.pack, "a")
        self.assertRaises(ValueError, t.pack, None)

        t = kattypes.Timestamp(default=1235475793.0324881)
        self.assertEqual(t.pack(None), "1235475793032")

    def test_unpack(self):
        """Test unpacking timestamps."""
        t = kattypes.Timestamp()
        self.assertEqual(t.unpack("1235475381696"), 1235475381.6960001)
        self.assertRaises(ValueError, t.unpack, "a")
        self.assertRaises(ValueError, t.unpack, None)

        t = kattypes.Int(default=1235475793.0324881)
        self.assertEqual(t.unpack(None), 1235475793.0324881)

class TestDevice(object):
    @kattypes.request((kattypes.Discrete(("ok",)),),kattypes.Int(min=1,max=3),kattypes.Discrete(("on","off")),kattypes.Bool())
    def request_one(self, i, d, b):
        return ("ok",)


class TestDecorator(unittest.TestCase):
    def setUp(self):
        self.device = TestDevice()

    def test_request_one(self):
        """Test request with no defaults."""
        sock = ""
        self.assertEqual(str(self.device.request_one(sock, Message.request("one", "2", "on", "0"))), "!one ok")
        self.assertRaises(ValueError, self.device.request_one, sock, Message.request("one", "4", "on", "0"))
        self.assertRaises(ValueError, self.device.request_one, sock, Message.request("one", "2", "dsfg", "0"))
        self.assertRaises(ValueError, self.device.request_one, sock, Message.request("one", "2", "on", "3"))

        self.assertRaises(ValueError, self.device.request_one, sock, Message.request("one", "2", "on"))
