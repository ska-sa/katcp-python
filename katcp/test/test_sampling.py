# test_sampling.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the katcp.sampling module.
   """

import unittest
import time
import logging
import katcp
from katcp.testutils import TestLogHandler, DeviceTestSensor
from katcp import sampling

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)


class TestSampling(unittest.TestCase):

    def setUp(self):
        """Set up for test."""
        # test sensor
        self.sensor = DeviceTestSensor(
                katcp.Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3)

        # test callback
        def inform(msg):
            self.calls.append(msg)

        self.calls = []
        self.inform = inform

    def test_sampling(self):
        """Test getting and setting the sampling."""
        s = self.sensor

        sampling.SampleNone(None, s)
        sampling.SampleAuto(None, s)
        sampling.SamplePeriod(None, s, 10)
        sampling.SampleEvent(None, s)
        sampling.SampleDifferential(None, s, 2)
        self.assertRaises(ValueError, sampling.SampleNone, None, s, "foo")
        self.assertRaises(ValueError, sampling.SampleAuto, None, s, "bar")
        self.assertRaises(ValueError, sampling.SamplePeriod, None, s)
        self.assertRaises(ValueError, sampling.SamplePeriod, None, s, "1.5")
        self.assertRaises(ValueError, sampling.SamplePeriod, None, s, "-1")
        self.assertRaises(ValueError, sampling.SampleEvent, None, s, "foo")
        self.assertRaises(ValueError, sampling.SampleDifferential, None, s)
        self.assertRaises(ValueError, sampling.SampleDifferential,
                          None, s, "-1")
        self.assertRaises(ValueError, sampling.SampleDifferential,
                          None, s, "1.5")

        sampling.SampleStrategy.get_strategy("none", None, s)
        sampling.SampleStrategy.get_strategy("auto", None, s)
        sampling.SampleStrategy.get_strategy("period", None, s, "15")
        sampling.SampleStrategy.get_strategy("event", None, s)
        sampling.SampleStrategy.get_strategy("differential", None, s, "2")
        self.assertRaises(ValueError, sampling.SampleStrategy.get_strategy,
                          "random", None, s)
        self.assertRaises(ValueError, sampling.SampleStrategy.get_strategy,
                          "period", None, s, "foo")
        self.assertRaises(ValueError, sampling.SampleStrategy.get_strategy,
                          "differential", None, s, "bar")

    def test_event(self):
        """Test SampleEvent strategy."""
        event = sampling.SampleEvent(self.inform, self.sensor)
        self.assertEqual(self.calls, [])

        event.attach()
        self.assertEqual(len(self.calls), 1)

        self.sensor.set_value(2)
        self.assertEqual(len(self.calls), 2)

    def test_event_with_rate_limit(self):
        """Test SampleEvent strategy with a rate limit."""
        event = sampling.SampleEvent(self.inform, self.sensor, 100)
        self.assertEqual(self.calls, [])

        event.attach()
        self.assertEqual(len(self.calls), 1)

        for i in [-4, -3, -2, -1, 0, 1, 2, 3]:
            self.sensor.set_value(i)
        self.assertEqual(len(self.calls), 1)

        time.sleep(0.1)
        self.sensor.set_value(3)
        self.assertEqual(len(self.calls), 1)

    def test_differential(self):
        """Test SampleDifferential strategy."""
        diff = sampling.SampleDifferential(self.inform, self.sensor, 5)
        self.assertEqual(self.calls, [])

        diff.attach()
        self.assertEqual(len(self.calls), 1)

    def test_periodic(self):
        """Test SamplePeriod strategy."""
        # period = 10s
        period = sampling.SamplePeriod(self.inform, self.sensor, 10000)
        self.assertEqual(self.calls, [])

        period.attach()
        self.assertEqual(self.calls, [])

        period.periodic(1)
        self.assertEqual(len(self.calls), 1)

        period.periodic(11)
        self.assertEqual(len(self.calls), 2)

        period.periodic(12)
        self.assertEqual(len(self.calls), 3)


class TestReactor(unittest.TestCase):

    def setUp(self):
        """Set up for test."""
        # test sensor
        self.sensor = DeviceTestSensor(
                katcp.Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=katcp.Sensor.NOMINAL, value=3)

        # test callback
        def inform(msg):
            self.calls.append(msg)

        # test reactor
        self.reactor = sampling.SampleReactor()
        self.reactor.start()

        self.calls = []
        self.inform = inform

    def tearDown(self):
        """Clean up after test."""
        self.reactor.stop()
        self.reactor.join(1.0)

    def test_periodic(self):
        """Test reactor with periodic sampling."""
        period = sampling.SamplePeriod(self.inform, self.sensor, 10)
        start = time.time()
        self.reactor.add_strategy(period)
        time.sleep(0.1)
        self.reactor.remove_strategy(period)
        end = time.time()

        expected = int(round((end - start) / 0.01))
        emax, emin = expected + 1, expected - 1

        self.assertTrue(emin <= len(self.calls) <= emax,
                        "Expect %d to %d informs, got:\n  %s" %
                        (emin, emax, "\n  ".join(str(x) for x in self.calls)))
