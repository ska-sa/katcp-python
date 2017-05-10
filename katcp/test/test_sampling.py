# test_sampling.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the katcp.sampling module.
   """

from __future__ import division, print_function, absolute_import

import unittest2 as unittest
import threading
import time
import logging
import katcp
import mock
import Queue
import tornado.testing
import concurrent.futures

from thread import get_ident
from tornado import gen
from katcp import sampling, Sensor
from katcp.testutils import (TestLogHandler, DeviceTestSensor, TimewarpAsyncTestCase)

log_handler = TestLogHandler()
logging.getLogger("katcp").addHandler(log_handler)
logger = logging.getLogger(__name__)

class TestSampling(TimewarpAsyncTestCase):
    # TODO Also test explicit ioloop passing

    def setUp(self):
        """Set up for test."""
        # test sensor
        super(TestSampling, self).setUp()
        self.sensor = DeviceTestSensor(
                Sensor.INTEGER, "an.int", "An integer.", "count",
                [-40, 30],
                timestamp=self.ioloop_time, status=Sensor.NOMINAL, value=3)
        # test callback
        def inform(sensor, reading):
            assert get_ident() == self.ioloop_thread_id, (
                "inform must be called from in the ioloop")
            self.calls.append((sensor, reading))

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

    @tornado.testing.gen_test(timeout=200)
    # Timeout needs to be longer than 'fake' duration of the test, since the tornado
    # ioloop is using out time-warped clock to determine timeouts too!
    def test_periodic(self):
        t0 = self.ioloop_time
        sample_p = 10                            # sample DUT in seconds
        DUT = sampling.SamplePeriod(self.inform, self.sensor, sample_p)
        self.assertEqual(self.calls, [])
        t, status, value = self.sensor.read()
        DUT.start()
        yield self.wake_ioloop()
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []
        # Warp the ioloop clock forward a bit more than one DUT. Check that
        #   1) a sample is sent,
        #   2) the next sample is scheduled at t0+DUT, not t0+DUT+extra delay
        yield self.set_ioloop_time(t0 + sample_p*1.15)
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []
        # Don't expect an update, since we are at just before the next sample DUT
        yield self.set_ioloop_time(t0 + sample_p*1.99)
        self.assertEqual(self.calls, [])
        # Now we are at exactly the next sample time, expect update
        yield self.set_ioloop_time(t0 + sample_p*2)
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []
        # Bit past previous sample time, expect no update
        yield self.set_ioloop_time(t0 + sample_p*2.16)
        self.assertEqual(self.calls, [])

        # Check that no update is sent if the sensor is updated, but that the next
        # periodic update is correct
        t, status, value = (t0 + sample_p*2.5, Sensor.WARN, -1)
        self.sensor.set(t, status, value)
        yield self.set_ioloop_time(t0 + sample_p*2.6)
        self.assertEqual(self.calls, [])
        yield self.set_ioloop_time(t0 + sample_p*3)
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Cancel strategy and check that its timeout call is cancelled.
        DUT.cancel()
        yield self.wake_ioloop()
        yield self.set_ioloop_time(t0 + sample_p*4.1)
        self.assertEqual(self.calls, [])


    @tornado.testing.gen_test(timeout=200)
    def test_auto(self):
        t0 = self.ioloop_time
        DUT = sampling.SampleAuto(self.inform, self.sensor)
        self.assertEqual(self.calls, [])
        t, status, value = self.sensor.read()
        DUT.start()
        yield self.wake_ioloop()
        # Check that it is attached
        self.assertTrue(DUT in self.sensor._observers)
        # The initial update
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []
        # Move along in time, don't expect any updates
        yield self.set_ioloop_time(t0 + 20)
        self.assertEqual(self.calls, [])
        # Now update the sensor a couple of times
        t1, status1, value1 = t0 + 21, Sensor.ERROR, 2
        t2, status2, value2 = t0 + 22, Sensor.NOMINAL, -1
        self.sensor.set(t1, status1, value1)
        self.sensor.set(t2, status2, value2)
        self.assertEqual(self.calls, [(self.sensor, (t1, status1, value1)),
                                      (self.sensor, (t2, status2, value2))])
        self.calls = []

        self._thread_update_check(t, status, value)
        yield self._check_cancel(DUT)

    @gen.coroutine
    def _thread_update_check(self, ts, status, value):
        # Check update from thread (inform() raises if called from the wrong thread)
        # Clears out self.calls before starting
        self.calls = []
        f = concurrent.futures.Future()
        def do_update():
            try:
                self.sensor.set(ts, status, value)
            finally:
                f.set_result(None)
            return f
        t = threading.Thread(target=do_update)
        t.start()
        yield f
        yield self.wake_ioloop()
        self.assertEqual(self.calls, [(self.sensor, (ts, status, value))])

    @gen.coroutine
    def _check_cancel(self, DUT):
        # Check post-cancel cleanup
        DUT.cancel()
        yield self.wake_ioloop()
        self.assertFalse(DUT in self.sensor._observers)

    @tornado.testing.gen_test(timeout=200)
    def test_differential(self):
        """Test SampleDifferential strategy."""
        t, status, value = self.sensor.read()
        delta = 3
        DUT = sampling.SampleDifferential(self.inform, self.sensor, delta)
        self.assertEqual(len(self.calls), 0)
        DUT.start()
        yield self.wake_ioloop()
        # Check initial update
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []
        # Some Updates less than delta from initial value
        self.sensor.set_value(value + 1)
        self.sensor.set_value(value + delta)
        self.sensor.set_value(value)
        self.sensor.set_value(value - 2)
        self.assertEqual(len(self.calls), 0)
        # Now an update bigger than delta from initial value
        self.sensor.set(t, status, value + delta + 1)
        yield self.wake_ioloop()
        self.assertEqual(self.calls, [(self.sensor, (t, status, value + delta + 1))])
        self.calls = []
        # Now change only the status, should update
        t, status, value = self.sensor.read()
        self.sensor.set(t, Sensor.ERROR, value)
        self.assertEqual(self.calls, [(self.sensor, (t, Sensor.ERROR, value))])
        # Test threaded update
        yield self._thread_update_check(t, status, value)
        yield self._check_cancel(DUT)

    def test_differential_timestamp(self):
        # Test that the timetamp differential is stored correctly as
        # seconds. This is mainly to check the conversion of the katcp spec from
        # milliseconds to seconds for katcp v5 spec.
        time_diff = 4.12                  # Time differential in seconds
        ts_sensor = Sensor(Sensor.TIMESTAMP, 'ts', 'ts sensor', '')
        diff = sampling.SampleDifferential(self.inform, ts_sensor, time_diff)
        self.assertEqual(diff._threshold, time_diff)

    @tornado.testing.gen_test(timeout=200)
    def test_event_rate(self):
        """Test SampleEventRate strategy."""
        shortest = 1.5
        longest = 4.5
        t, status, value = self.sensor.read()

        DUT = sampling.SampleEventRate(self.inform, self.sensor, shortest, longest)
        self.assertEqual(len(self.calls), 0)
        DUT.start()
        yield self.wake_ioloop()
        # Check initial update
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Too soon, should not send update
        yield self.set_ioloop_time(t_last_sent + shortest*0.99)
        value = value + 3
        t = self.ioloop_time
        self.sensor.set(t, status, value)
        self.assertEqual(len(self.calls), 0)

        # Too soon again, should not send update
        yield self.set_ioloop_time(t_last_sent + shortest*0.999)
        value = value + 1
        t = self.ioloop_time
        self.sensor.set(t, status, value)
        self.assertEqual(len(self.calls), 0)

        # Should now get minimum time update
        yield self.set_ioloop_time(t_last_sent + shortest)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Warp to just before longest period, should not update
        yield self.set_ioloop_time(t_last_sent + longest*0.999)
        self.assertEqual(len(self.calls), 0)

        # Warp to longest period, should update
        yield self.set_ioloop_time(t_last_sent + longest)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Warp to just before next longest period, should not update
        yield self.set_ioloop_time(t_last_sent + longest*0.999)
        self.assertEqual(len(self.calls), 0)

        # Warp to longest period, should update
        yield self.set_ioloop_time(t_last_sent + longest)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Set identical value, jump past min update time, no update should happen
        self.sensor.set(self.ioloop_time, status, value)
        yield self.set_ioloop_time(t_last_sent + shortest)
        self.assertEqual(len(self.calls), 0)

        # Set new value, update should happen
        value = value - 2
        self.sensor.set(self.ioloop_time, status, value)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t_last_sent, status, value))])
        self.calls = []

        # Now warp to after min period, change only status, update should happen
        yield self.set_ioloop_time(t_last_sent + shortest)
        status = Sensor.ERROR
        self.sensor.set(self.ioloop_time, status, value)
        self.assertEqual(self.calls, [(self.sensor, (self.ioloop_time, status, value))])
        t_last_sent = self.ioloop_time
        self.calls = []

        yield self.set_ioloop_time(t_last_sent + shortest)
        status = Sensor.NOMINAL
        value = value + 1
        yield self._thread_update_check(self.ioloop_time, status, value)
        yield self._check_cancel(DUT)
        self.calls = []

        # Since strategy is cancelled, no futher updates should be sent
        yield self.set_ioloop_time(self.ioloop_time + 5*longest)
        self.sensor.set(self.ioloop_time, Sensor.WARN, value + 3)
        self.assertEqual(len(self.calls), 0)

    @tornado.testing.gen_test(timeout=2000000)
    def test_event(self):
        """Test SampleEvent strategy."""
        DUT = sampling.SampleEvent(self.inform, self.sensor)
        self.assertEqual(DUT.get_sampling_formatted(), ('event', []) )
        self.assertEqual(self.calls, [])
        DUT.start()
        yield self.wake_ioloop()
        # Check initial update
        self.assertEqual(len(self.calls), 1)

        # Jump forward a lot, should not result in another sample
        yield self.set_ioloop_time(200000)
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

    @tornado.testing.gen_test(timeout=200)
    def test_differential_rate(self):
        shortest = 1.5
        longest = 4.5
        delta = 2
        t, status, value = self.sensor.read()
        # TODO change to be differentialrate test

        DUT = sampling.SampleDifferentialRate(
            self.inform, self.sensor, delta, shortest, longest)
        self.assertEqual(len(self.calls), 0)
        DUT.start()
        yield self.wake_ioloop()
        # Check initial update
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Too soon, should not send update
        yield self.set_ioloop_time(t_last_sent + shortest*0.99)
        value = value + delta + 1
        t = self.ioloop_time
        self.sensor.set(t, status, value)
        self.assertEqual(len(self.calls), 0)

        # Too soon again, should not send update
        yield self.set_ioloop_time(t_last_sent + shortest*0.999)
        value = value + delta + 1
        t = self.ioloop_time
        self.sensor.set(t, status, value)
        self.assertEqual(len(self.calls), 0)

        # Should now get minimum time update
        yield self.set_ioloop_time(t_last_sent + shortest)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Warp to just before longest period, should not update
        yield self.set_ioloop_time(t_last_sent + longest*0.999)
        self.assertEqual(len(self.calls), 0)

        # Warp to longest period, should update
        yield self.set_ioloop_time(t_last_sent + longest)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Warp to just before next longest period, should not update
        yield self.set_ioloop_time(t_last_sent + longest*0.999)
        self.assertEqual(len(self.calls), 0)

        # Warp to longest period, should update
        yield self.set_ioloop_time(t_last_sent + longest)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t, status, value))])
        self.calls = []

        # Set value with to small a change, jump past min update time, no update should
        # happen
        value = value - delta
        self.sensor.set(self.ioloop_time, status, value)
        yield self.set_ioloop_time(t_last_sent + shortest)
        self.assertEqual(len(self.calls), 0)

        # Set new value with large enough difference, update should happen
        value = value - 1
        self.sensor.set(self.ioloop_time, status, value)
        t_last_sent = self.ioloop_time
        self.assertEqual(self.calls, [(self.sensor, (t_last_sent, status, value))])
        self.calls = []

        # Now warp to after min period, change only status, update should happen
        yield self.set_ioloop_time(t_last_sent + shortest)
        status = Sensor.ERROR
        self.sensor.set(self.ioloop_time, status, value)
        self.assertEqual(self.calls, [(self.sensor, (self.ioloop_time, status, value))])
        t_last_sent = self.ioloop_time
        self.calls = []

        yield self.set_ioloop_time(t_last_sent + shortest)
        status = Sensor.NOMINAL
        value = value + 1
        yield self._thread_update_check(self.ioloop_time, status, value)
        yield self._check_cancel(DUT)
        self.calls = []

        # Since strategy is cancelled, no futher updates should be sent
        yield self.set_ioloop_time(self.ioloop_time + 5*longest)
        self.sensor.set(self.ioloop_time, Sensor.WARN, value + 3)
        self.assertEqual(len(self.calls), 0)



