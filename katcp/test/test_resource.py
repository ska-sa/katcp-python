###############################################################################
# SKA South Africa (http://ska.ac.za/)                                        #
# Author: cam@ska.ac.za                                                       #
# Copyright @ 2013 SKA SA. All rights reserved.                               #
#                                                                             #
# THIS SOFTWARE MAY NOT BE COPIED OR DISTRIBUTED IN ANY FORM WITHOUT THE      #
# WRITTEN PERMISSION OF SKA SA.                                               #
###############################################################################

import unittest2 as unittest
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
        timeout = 1
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

        # wait for value and status
        waiting_value = 3
        waiting_status = Sensor.WARN
        self.io_loop.add_callback(DUT.set_value, 3, Sensor.WARN)
        yield DUT.wait(waiting_value, waiting_status)
        self.assertEqual(DUT.value, waiting_value)
        self.assertEqual(DUT.status, waiting_status)
        # Check that no stray listeners are left behind
        self.assertFalse(DUT._listeners)
