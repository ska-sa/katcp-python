###############################################################################
# SKA South Africa (http://ska.ac.za/)                                        #
# Author: cam@ska.ac.za                                                       #
# Copyright @ 2013 SKA SA. All rights reserved.                               #
#                                                                             #
# THIS SOFTWARE MAY NOT BE COPIED OR DISTRIBUTED IN ANY FORM WITHOUT THE      #
# WRITTEN PERMISSION OF SKA SA.                                               #
###############################################################################
from __future__ import division, print_function, absolute_import

import unittest
import logging
import mock

import tornado.testing

from katcp import Sensor
from katcp.testutils import TimewarpAsyncTestCase

# Module under test
from katcp import resource

logger = logging.getLogger(__name__)

class test_escape_name(unittest.TestCase):
    def test_escape_name(self):
        desired_mappings = {
            'blah-bal' : 'blah_bal',
            'bief_bof.ba32f-blief' : 'bief_bof_ba32f_blief',
            'already_escape_name' : 'already_escape_name'}
        for input, expected_output in desired_mappings.items():
            self.assertEqual(resource.escape_name(input), expected_output)

class test_KATCPSensor(TimewarpAsyncTestCase):
    @tornado.testing.gen_test
    def test_wait(self):
        sensor_manager = mock.Mock()
        sensor_manager.get_sampling_strategy.return_value = (
            resource.normalize_strategy_parameters('none'))
        DUT = resource.KATCPSensor(dict(sensor_type=Sensor.INTEGER,
                                        name='test.int', params=[-1000, 1000],
                                        default=0, initial_status=Sensor.NOMINAL),
                                   sensor_manager)
        # Test that an exception is raised if no strategy is set
        with self.assertRaises(resource.KATCPSensorError):
            yield DUT.wait(1)
        # Check that no stray listeners are left behind
        self.assertFalse(DUT._listeners)

        # Test that timeout works
        sensor_manager.get_sampling_strategy.return_value = (
            resource.normalize_strategy_parameters('event'))
        # Add an ioloop callback to timewarp so that we don't get stuck
        timeout = 0.25
        self.io_loop.add_callback(
            self.set_ioloop_time, self.io_loop.time() + timeout + 0.1)
        # Check that the timout error is raised
        with self.assertRaises(tornado.gen.TimeoutError):
            yield DUT.wait(1, timeout=timeout)
        # Check that no stray listeners are left behind
        self.assertFalse(DUT._listeners)

        # Wait for a value that changes within the timeout
        self.io_loop.add_callback(
            self.set_ioloop_time, self.io_loop.time() + timeout - 0.1)
        waiting_value = 2
        self.io_loop.add_callback(DUT.set_value, waiting_value)
        yield DUT.wait(waiting_value, timeout=timeout)
        self.assertEqual(DUT.value, waiting_value)
        # Check that no stray listeners are left behind
        self.assertFalse(DUT._listeners)

        # Wait using a callable, comparison times out
        self.io_loop.add_callback(
            self.set_ioloop_time, self.io_loop.time() + timeout + 0.1)
        waiting_value = 11
        waiting_condition = lambda reading: reading.value == waiting_value
        # Check that the timout error is raised
        with self.assertRaises(tornado.gen.TimeoutError):
            yield DUT.wait(waiting_value, timeout=timeout)
        # Check that no stray listeners are left behind
        self.assertFalse(DUT._listeners)

        # Wait for a callable condition that is reached within the timeout
        self.io_loop.add_callback(
            self.set_ioloop_time, self.io_loop.time() + timeout - 0.1)
        waiting_value = 12
        waiting_condition = lambda reading: reading.value == waiting_value
        self.io_loop.add_callback(DUT.set_value, waiting_value)
        yield DUT.wait(waiting_condition, timeout=timeout)
        self.assertEqual(DUT.value, waiting_value)
        # Check that no stray listeners are left behind
        self.assertFalse(DUT._listeners)

        # wait for value and status
        waiting_value = 3
        waiting_status = 'warn'
        waiting_condition = lambda reading: reading.value == waiting_value and \
                                            reading.status == waiting_status
        self.io_loop.add_callback(DUT.set_value, 3, Sensor.WARN)
        yield DUT.wait(waiting_condition, timeout=timeout)
        self.assertEqual(DUT.value, waiting_value)
        self.assertEqual(DUT.status, waiting_status)
        # Check that no stray listeners are left behind
        self.assertFalse(DUT._listeners)

    @tornado.testing.gen_test
    def test_sampling_strategy_property(self):
        sensor_name = 'test.int'
        strategy = 'testing 123'
        sensor_manager = mock.Mock()
        sensor_manager.get_sampling_strategy.return_value = strategy
        DUT = resource.KATCPSensor(dict(sensor_type=Sensor.INTEGER,
                                        name=sensor_name),
                                   sensor_manager)
        self.assertEqual(DUT.sampling_strategy, strategy)

    @tornado.testing.gen_test
    def test_set_strategy(self):
        sensor_name = 'test.int'
        sensor_manager = mock.Mock()
        DUT = resource.KATCPSensor(dict(sensor_type=Sensor.INTEGER,
                                        name=sensor_name),
                                   sensor_manager)
        DUT.set_strategy('none')
        DUT.set_strategy('period', 0.5)
        DUT.set_strategy('event-rate', '1.1 1.2')
        DUT.set_strategy('event-rate', [2.1, 2.2])

        sensor_manager.set_sampling_strategy.assert_has_calls([
            mock.call(sensor_name, 'none'),
            mock.call(sensor_name, 'period 0.5'),
            mock.call(sensor_name, 'event-rate 1.1 1.2'),
            mock.call(sensor_name, 'event-rate 2.1 2.2'),
        ])

    @tornado.testing.gen_test
    def test_set_sampling_strategy(self):
        sensor_name = 'test.int'
        strategy = 'period 2.5'
        sensor_manager = mock.Mock()
        DUT = resource.KATCPSensor(dict(sensor_type=Sensor.INTEGER,
                                        name=sensor_name),
                                   sensor_manager)
        DUT.set_sampling_strategy(strategy)
        sensor_manager.set_sampling_strategy.assert_called_once_with(
            sensor_name, strategy)

    @tornado.testing.gen_test
    def test_drop_sampling_strategy(self):
        sensor_name = 'test.int'
        sensor_manager = mock.Mock()
        DUT = resource.KATCPSensor(dict(sensor_type=Sensor.INTEGER,
                                        name=sensor_name),
                                   sensor_manager)
        DUT.drop_sampling_strategy()
        sensor_manager.drop_sampling_strategy.assert_called_once_with(
            sensor_name)


class ConcreteKATCPRequest(resource.KATCPRequest):
    def issue_request(self, *args, **kwargs):
        pass

class test_KATCPRequest(unittest.TestCase):
    def test_active(self):
        active = False
        is_active = lambda: active
        req_name = 'test-request'
        req_description = {
            'name': req_name,
            'description': '?test-request description',
            'timeout_hint': None}

        DUT = ConcreteKATCPRequest(req_description, is_active=is_active)
        DUT.issue_request = mock.Mock()

        req_args = ('arg1', 'arg2')
        req_kwargs = dict(timeout=123, mid=456)

        # Do a test request when active = False, should raise
        with self.assertRaises(resource.KATCPResourceInactive):
            DUT(*req_args, **req_kwargs)
        self.assertEqual(
            DUT.issue_request.call_count, 0,
            'issue_request should not have been called when request is inactive')

        # now set active to True, and check that the request is made
        active = True
        rv = DUT(*req_args, **req_kwargs)
        self.assertEqual(rv, DUT.issue_request.return_value)
        DUT.issue_request.assert_called_once_with(*req_args, **req_kwargs)
