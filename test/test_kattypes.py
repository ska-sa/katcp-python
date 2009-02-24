"""Tests for the kattypes module."""

import unittest
from katcp import kattypes 

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
        pass

class TestBool(unittest.TestCase):
    def setUp(self):
        pass

    def test_pack(self):
        pass

class TestDiscrete(unittest.TestCase):
    def setUp(self):
        pass

    def test_pack(self):
        pass

class TestLru(unittest.TestCase):
    def setUp(self):
        pass

    def test_pack(self):
        pass

class TestTimestamp(unittest.TestCase):
    def setUp(self):
        pass

    def test_pack(self):
        pass
