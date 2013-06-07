# test_sampling.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the katcp.sampling module.
   """

import unittest
import threading
import time
import logging
import katcp
import mock
import Queue
import contextlib

from katcp.testutils import (
    TestLogHandler, DeviceTestSensor, start_thread_with_cleanup)
from katcp import sampling, Sensor

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)


class TestSampling(unittest.TestCase):

    def setUp(self):
        """Set up for test."""
        # test sensor
        self.sensor = DeviceTestSensor(
                Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=Sensor.NOMINAL, value=3)

        # test callback
        def inform(sensor_name, timestamp, status, value):
            self.calls.append(sampling.format_inform_v5(
                sensor_name, timestamp, status, value) )

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
        self.assertRaises(ValueError, sampling.SamplePeriod, None, s, "0")
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
        self.assertEqual(event.get_sampling_formatted(),
                         ('event', []) )

        self.assertEqual(self.calls, [])

        event.attach()
        self.assertEqual(len(self.calls), 1)

        self.sensor.set_value(2, status=Sensor.NOMINAL)
        self.assertEqual(len(self.calls), 2)

        # Test that an update is suppressed if the sensor value is unchanged
        self.sensor.set_value(2, status=Sensor.NOMINAL)
        self.assertEqual(len(self.calls), 2)

        # Test that an update happens if the status changes even if the value is
        # unchanged
        self.sensor.set_value(2, status=Sensor.WARN)
        self.assertEqual(len(self.calls), 3)


    def test_differential(self):
        """Test SampleDifferential strategy."""
        diff = sampling.SampleDifferential(self.inform, self.sensor, 5)
        self.assertEqual(self.calls, [])

        diff.attach()
        self.assertEqual(len(self.calls), 1)

    def test_differential_timestamp(self):
        # Test that the timetamp differential is stored correctly as
        # seconds. This is mainly to check the conversion of the katcp spec from
        # milliseconds to seconds for katcp v5 spec.
        time_diff = 4.12                  # Time differential in seconds
        ts_sensor = Sensor(Sensor.TIMESTAMP, 'ts', 'ts sensor', '')
        diff = sampling.SampleDifferential(self.inform, ts_sensor, time_diff)
        self.assertEqual(diff._threshold, time_diff)

    def test_periodic(self):
        """Test SamplePeriod strategy."""
        sample_p = 10                            # sample period in seconds
        period = sampling.SamplePeriod(self.inform, self.sensor, sample_p)
        self.assertEqual(self.calls, [])

        period.attach()
        self.assertEqual(self.calls, [])

        next_p = period.periodic(1)
        self.assertEqual(next_p, 1 + sample_p)
        self.assertEqual(len(self.calls), 1)

        next_p = period.periodic(11)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(next_p, 11 + sample_p)

        next_p = period.periodic(12)
        self.assertEqual(next_p, 12 + sample_p)
        self.assertEqual(len(self.calls), 3)

    def test_event_rate(self):
        """Test SampleEventRate strategy."""
        shortest = 10
        longest = 20
        evrate = sampling.SampleEventRate(
            self.inform, self.sensor, shortest, longest)
        now = [1]
        evrate._time = lambda: now[0]
        self.assertEqual(self.calls, [])

        evrate.attach()
        self.assertEqual(len(self.calls), 1)

        self.sensor.set_value(1)
        self.assertEqual(len(self.calls), 1)

        now[0] = 11
        self.sensor.set_value(2)
        self.assertEqual(len(self.calls), 2)

        evrate.periodic(12)
        self.assertEqual(len(self.calls), 2)
        evrate.periodic(13)
        self.assertEqual(len(self.calls), 2)
        evrate.periodic(31)
        self.assertEqual(len(self.calls), 3)

        now[0] = 32
        self.sensor.set_value(3)
        self.assertEqual(len(self.calls), 3)

        now[0] = 41
        self.sensor.set_value(1)
        self.assertEqual(len(self.calls), 4)

    def test_event_rate_fractions(self):
        # Test SampleEventRate strategy in the presence of fractional seconds --
        # mainly to catch bugs when it was converted to taking seconds instead of
        # milliseconds, since the previous implementation used an integer number
        # of milliseconds
        shortest = 3./8
        longest = 6./8
        evrate = sampling.SampleEventRate(self.inform, self.sensor, shortest,
                                          longest)
        now = [0]
        evrate._time = lambda: now[0]

        evrate.attach()
        self.assertEqual(len(self.calls), 1)

        now[0] = 0.999*shortest
        self.sensor.set_value(1)
        self.assertEqual(len(self.calls), 1)

        now[0] = shortest
        self.sensor.set_value(1)
        self.assertEqual(len(self.calls), 2)

        next_time = evrate.periodic(now[0] + 0.99*shortest)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(next_time, now[0] + longest)

class FakeEvent(object):
    def __init__(self, waited_callback=None):
        self._event = threading.Event()
        self.waits = Queue.Queue()
        self._set = False
        self._set_lock = threading.Lock()
        self.waited_callback = waited_callback

    def set(self):
        with self._set_lock:
            self._set = True
            self.break_wait()

    def clear(self):
        with self._set_lock:
            self._set = False
            self._event.clear()

    def isSet(self):
        with self._set_lock:
            return self._set

    is_set = isSet

    def wait(self, timeout=None):
        self.waits.put(timeout)
        self._event.wait()
        with self._set_lock:
            if not self._set:
                self._event.clear()
            isset = self._set

        if self.waited_callback:
            self.waited_callback(timeout)
        return isset

    def break_wait(self):
        self._event.set()

class TestReactorIntegration(unittest.TestCase):
    def setUp(self):
        """Set up for test."""
        # test sensor
        # self._print_lock = threading.Lock()
        self._time_lock = threading.Lock()
        self.sensor = DeviceTestSensor(
                Sensor.INTEGER, "an.int", "An integer.", "count",
                [-4, 3],
                timestamp=12345, status=Sensor.NOMINAL, value=3)


        # test callback
        self.inform_called = threading.Event()
        def inform(sensor_name, timestamp, status, value):
            self.inform_called.set()
            self.calls.append((self.time(), sampling.format_inform_v5(
                sensor_name, timestamp, status, value)) )

        def waited_callback(timeout):
            # A callback that updates 'simulated time' whenever wake.wait() is
            # called
            if timeout:
                with self._time_lock:
                    self.time.return_value += timeout

        # test reactor
        self.reactor = sampling.SampleReactor()

        # Add a mock-spy to the reactor wake event
        self.reactor._wakeEvent = self.wake = wake = FakeEvent(waited_callback)
        orig_wait = wake.wait
        wake.wait = mock.Mock()
        wake.wait.side_effect = orig_wait

        # Patch time.time so that we can lie about time.
        with mock.patch('katcp.sampling.time') as mtime:
            self.time = mtime.time
            self.start_time = self.time.return_value = time.time()
            start_thread_with_cleanup(self, self.reactor)
            # Wait for the event loop to reach its first wake.wait()
            self.wake.waits.get(timeout=1)

        self.calls = []
        self.inform = inform

    @contextlib.contextmanager
    def patched_time(self):
        with mock.patch('katcp.sampling.time') as mtime:
            mtime.time = self.time
            try:
                yield
            finally:
                pass

    def _add_strategy(self, strat, wait_initial=True):
        # Add strategy to test reactor while taking care to mock time.time as
        # needed, and waits for the initial update (all strategies except None should send an initial update)

        # Patch time.time so that we can lie about time.
        with self.patched_time():
            self.reactor.add_strategy(strat)

        if wait_initial:
            self.inform_called.wait(1)
            self.inform_called.clear()
            self.wake.waits.get(timeout=1)

    def test_periodic(self):
        """Test reactor with periodic sampling."""
        period = 10.
        no_periods = 5
        period_strat = sampling.SamplePeriod(self.inform, self.sensor, period)
        self._add_strategy(period_strat)

        for i in range(no_periods):
            self.wake.break_wait()
            self.inform_called.wait(1)
            self.inform_called.clear()
            self.wake.waits.get(timeout=1)

        self.reactor.remove_strategy(period_strat)

        call_times = [t for t, vals in self.calls]
        self.assertEqual(len(self.calls), no_periods + 1)
        self.assertEqual(call_times,
                         [self.start_time + i*period
                          for i in range(no_periods + 1)])


    def test_event_rate(self):
        max_period = 10.
        min_period = 1.
        with self.patched_time():
            event_rate_strat = sampling.SampleEventRate(
                self.inform, self.sensor, min_period, max_period)
        self._add_strategy(event_rate_strat)

        no_max_periods = 3

        for i in range(no_max_periods):
            self.wake.break_wait()
            self.inform_called.wait(1)
            self.inform_called.clear()
            self.wake.waits.get(timeout=1)

        self.reactor.remove_strategy(event_rate_strat)

        call_times = [t for t, vals in self.calls]
        self.assertEqual(len(self.calls), no_max_periods + 1)
        self.assertEqual(call_times,
                         [self.start_time + i*max_period
                          for i in range(no_max_periods + 1)])


