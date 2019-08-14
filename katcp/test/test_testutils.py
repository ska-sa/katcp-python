from __future__ import division, print_function, absolute_import

import unittest2 as unittest
import threading
import time
import tornado.testing
import tornado.gen

from katcp import Sensor
from katcp import testutils


def get_sensor(sensor_type, name=None):
    if name is None:
        name = 'test_%s_sensor' % sensor_type
    params = None
    if sensor_type in (Sensor.INTEGER, Sensor.FLOAT):
        params = [0, 1000]
    elif sensor_type == Sensor.DISCRETE:
        params = ['value1', 'value2', 'value3']
    sensor = Sensor(
        sensor_type, name, "Dummy %s Sensor" % sensor_type, "Units",
        params)

    return sensor

class test_SensorTransitionWaiter(unittest.TestCase):
    def test_wait_float_timeout(self):
        timeout = 0.1
        expected_conditions = (
            lambda x: x < 0.7,
            lambda x: x >= 0.7,
            lambda x: x >= 1,
            lambda x: x < 1,
            lambda x: x < 0.3)
        value_series = (0.5, 0.6, 1, 0.7, 0.299)
        def sensor_stream(): # Target for thread that pushes values to DUT
            # We are purposefully not adjusting the timestamps (1st 2
            # parameters to sensor.set_value()) to check that events
            # that come in faster than timer precision are also caught
            thread_alive.set()
            for val in value_series:
                time.sleep(delay_value)
                sensor.set_value(val)

        # Set up the DUT
        sensor = get_sensor(Sensor.FLOAT)
        DUT = testutils.SensorTransitionWaiter(sensor, expected_conditions)

        # Set up the thread that will push the values to DUT
        sensor_thread = threading.Thread(target=sensor_stream)
        sensor_thread.daemon = True
        thread_alive = threading.Event()
        delay_value = 0.005 # Should be fast enough to complete within timeout
        thread_alive.clear()
        sensor_thread.start()
        # wait until the thread starts working
        thread_alive.wait(timeout=0.5)
        self.assertTrue(DUT.wait(timeout=timeout))
        self.assertFalse(DUT.timed_out)
        sensor_thread.join()

        # Now try it too slow
        delay_value = 0.021
        DUT = testutils.SensorTransitionWaiter(sensor, expected_conditions)
        sensor_thread = threading.Thread(target=sensor_stream)
        sensor_thread.daemon = True
        thread_alive.clear()
        # wait until the thread starts working
        sensor_thread.start()
        thread_alive.wait(timeout=0.5)
        self.assertFalse(DUT.wait(timeout=timeout))
        self.assertTrue(DUT.timed_out)
        sensor_thread.join()

    def test_init_teardown(self):
        now = time.time()
        sensor = get_sensor(Sensor.INTEGER)
        sensor.set_value(0)
        # Test that an assertion is raised if the initial value of the
        # sensor does not match the first value in the expected
        # sequence.
        with self.assertRaises(ValueError):
            testutils.SensorTransitionWaiter(sensor,(1,2,3))

        DUT = testutils.SensorTransitionWaiter(sensor,(0,2,3))
        # Test that we are attached to the sensor
        self.assertTrue(DUT._observer in sensor._observers)
        self.assertEqual(DUT._observer.update, DUT._sensor_callback)

        DUT.teardown()
        # Check that the callback is unregistered
        self.assertFalse(DUT._observer in sensor._observers)
        self.assertTrue(DUT._torn_down)
        with self.assertRaises(RuntimeError):
            DUT.wait() # should not allow waiting if we're torn down

class test_wait_sensor(unittest.TestCase):
    def _wait_sensor(self, vals, val, status):
        sensor = get_sensor(Sensor.INTEGER)
        sensor.set_value(0, Sensor.NOMINAL)
        def set_vals():
            time.sleep(0.05)
            for v in vals:
                if status is None:
                    sensor.set_value(v)
                else:
                    sensor.set_value(*v)
        sensor_thread = threading.Thread(target=set_vals)
        # test timeout
        self.assertFalse(testutils.wait_sensor(sensor, val, status=status, timeout=0.05))
        # Now start setting vals
        sensor_thread.start()
        self.assertTrue(testutils.wait_sensor(sensor, val, status=status, timeout=1))

    def test_values(self):
        vals = (1,2,0,3)
        self._wait_sensor(vals, 3, status=None)

    def test_values_and_status(self):
        vals = ((1, Sensor.NOMINAL),
                (7, Sensor.WARN),
                (2, Sensor.ERROR),
                (0, Sensor.ERROR))
        self._wait_sensor(vals, 0, status=Sensor.ERROR)


class _test_WaitingMockBase(unittest.TestCase):

    DUTClass = None

    def test_reset_mock(self):
        # Verify that the call_count and call_args_list variables
        # are initially zero, and get cleared by calling reset_mock
        DUT = self.DUTClass()
        self.assertEqual(DUT.call_count, 0)
        self.assertEqual(len(DUT.call_args_list), 0)
        DUT()
        self.assertEqual(DUT.call_count, 1)
        self.assertEqual(len(DUT.call_args_list), 1)
        DUT.reset_mock()
        self.assertEqual(DUT.call_count, 0)
        self.assertEqual(len(DUT.call_args_list), 0)


class test_WaitingMock(_test_WaitingMockBase):

    DUTClass = testutils.WaitingMock

    def test_assert_wait_call_count_success(self):
        # Test the normal case, in which the mock was called
        DUT = self.DUTClass()
        DUT(123)
        DUT.assert_wait_call_count(1, timeout=0.1)

    def test_assert_wait_call_count_fail_on_call_count(self):
        # Test the negative case, when the mock was not called.
        # This should cause an assertion error after the timeout.
        DUT = self.DUTClass()
        with self.assertRaises(AssertionError):
            DUT.assert_wait_call_count(1, timeout=0.01)

    def test_assert_wait_call_count_fail_on_call_args(self):
        # Synthetic test case where the call_count is correct, but the
        # call_args_list has not been updated yet.  This might occur if
        # the mock call is done in a different thread to the unit test.
        # In this case we expect a runtime error after the timeout.
        DUT = self.DUTClass()
        DUT(123)
        DUT.call_args_list = []
        with self.assertRaises(RuntimeError):
            DUT.assert_wait_call_count(1, timeout=0.1)

class test_AsyncWaitingMock(
        tornado.testing.AsyncTestCase, _test_WaitingMockBase):

    DUTClass = testutils.AsyncWaitingMock

    @tornado.testing.gen_test
    def test_assert_wait_call_count_success(self):
        # Test the normal case, in which the mock was called
        DUT = self.DUTClass()
        DUT(123)
        yield DUT.assert_wait_call_count(1, timeout=0.1)

    @tornado.testing.gen_test
    def test_assert_wait_call_count_fail_on_call_count(self):
        # Test the negative case, when the mock was not called.
        # This should cause an assertion error after the timeout.
        DUT = self.DUTClass()
        with self.assertRaises(AssertionError):
            yield DUT.assert_wait_call_count(1, timeout=0.01)

    @tornado.testing.gen_test
    def test_ioloop_hogging(self):
        # Test implementation detail, if the waiting loop forgets to clear
        # DUT._call_event after each call, it will go in a "busy waiting" loop
        # that blocks the ioloop and prevents the second call (done using
        # add_callback) from happening until after assert_wait_call_count()
        # times out.
        DUT = self.DUTClass()
        DUT(1)
        self.io_loop.add_callback(DUT, 2)
        yield DUT.assert_wait_call_count(2, timeout=0.1)
