from __future__ import print_function, division, absolute_import
###############################################################################
# SKA South Africa (http://ska.ac.za/)                                        #
# Author: cam@ska.ac.za                                                       #
# Copyright @ 2013 SKA SA. All rights reserved.                               #
#                                                                             #
# THIS SOFTWARE MAY NOT BE COPIED OR DISTRIBUTED IN ANY FORM WITHOUT THE      #
# WRITTEN PERMISSION OF SKA SA.                                               #
###############################################################################
from __future__ import division, print_function, absolute_import

# Python 2/3 compatibility stuff
from future import standard_library
standard_library.install_aliases()
from builtins import str
from builtins import object
#

import unittest
import logging
import copy
import time
import threading

import tornado
import mock

from _thread import get_ident as get_thread_ident
from functools import partial

from concurrent.futures import TimeoutError

from katcp.testutils import (DeviceTestServer, DeviceTestSensor,
                             start_thread_with_cleanup, TimewarpAsyncTestCase,
                             TimewarpAsyncTestCaseTimeAdvancer)

from katcp import resource, inspecting_client, ioloop_manager, Message, Sensor
from katcp.core import AttrDict, AsyncEvent, ProtocolFlags

# module under test
from katcp import resource_client

logger = logging.getLogger(__name__)


class test_transform_future(tornado.testing.AsyncTestCase):
    def test_transform(self):
        orig_f = tornado.concurrent.Future()
        transform = mock.Mock()
        trans_f = resource_client.transform_future(transform, orig_f)
        retval = mock.Mock()
        orig_f.set_result(retval)
        self.assertIs(trans_f.result(), transform.return_value)
        transform.assert_called_once_with(retval)

    @tornado.testing.gen_test
    def test_exception_in_future(self):
        class AnException(Exception): pass
        @tornado.gen.coroutine
        def raiser():
            raise AnException
        orig_f = raiser()
        transform = mock.Mock()
        trans_f = resource_client.transform_future(transform, orig_f)
        with self.assertRaises(AnException):
            trans_f.result()

    def test_exception_in_transform(self):
        orig_f = tornado.concurrent.Future()
        transform = mock.Mock()
        class AnException(Exception): pass
        transform.side_effect = AnException
        trans_f = resource_client.transform_future(transform, orig_f)
        retval = mock.Mock()
        orig_f.set_result(retval)
        transform.assert_called_once_with(retval)
        with self.assertRaises(AnException):
            trans_f.result()


class test_KATCPClientResourceRequest(unittest.TestCase):
    def setUp(self):
        self.mock_client = mock.Mock()
        self.DUT = resource_client.KATCPClientResourceRequest(
            {'name': 'the-request',
             'description': 'The description',
             'timeout_hint': 33.34},
            self.mock_client)

    def test_init(self):
        self.assertEqual(self.DUT.name, 'the-request')
        self.assertEqual(self.DUT.description, 'The description')
        self.assertEqual(self.DUT.timeout_hint, 33.34)
        # Check that we are registered to the correct ABC
        self.assertIsInstance(self.DUT, resource.KATCPRequest)

    def test_request_with_timeout_hint(self):
        reply = self.DUT('parm1', 2)
        self.mock_client.wrapped_request.assert_called_once_with(
            'the-request', 'parm1', 2, timeout=33.34)
        self.assertIs(reply, self.mock_client.wrapped_request.return_value)

    def test_request_no_timeout_hint(self):
        DUT_no_timeout_hint = resource_client.KATCPClientResourceRequest(
            {'name': 'the-other-request',
             'description': 'The other description',
             'timeout_hint': None},
            self.mock_client)
        reply = DUT_no_timeout_hint('aparm', 3)
        self.mock_client.wrapped_request.assert_called_once_with(
            'the-other-request', 'aparm', 3, timeout=None)
        self.assertIs(reply, self.mock_client.wrapped_request.return_value)


class test_KATCPClientResource(tornado.testing.AsyncTestCase):
    def test_init(self):
        resource_spec = dict(
            name='testdev',
            description='resource for testing',
            address=('testhost', 12345),
            controlled=True)
        DUT = resource_client.KATCPClientResource(dict(resource_spec))
        self.assertEqual(DUT.address, resource_spec['address'])
        self.assertEqual(DUT.state, 'disconnected')
        self.assertEqual(DUT.name, resource_spec['name'])
        self.assertEqual(DUT.description, resource_spec['description'])
        self.assertEqual(DUT.parent, None)
        self.assertEqual(DUT.children, {})
        self.assertEqual(DUT.controlled, True)

        # Now try with a parent and no control
        resource_spec['controlled'] = False
        parent = mock.Mock()
        DUT = resource_client.KATCPClientResource(
            dict(resource_spec), parent=parent)
        self.assertEqual(DUT.parent, parent)
        self.assertEqual(DUT.controlled, False)

    @tornado.testing.gen_test
    def test_dummy_requests(self):
        resource_spec_nodummy = dict(
            name='testdev',
            description='resource for testing',
            address=('testhost', 12345),
            controlled=True)
        resource_spec_dummy = dict(resource_spec_nodummy)
        resource_spec_dummy['dummy_unknown_requests'] = True
        requests = ('req-one', 'req_two')
        DUT_nodummy = self.get_DUT_mock_inspecting_client(
            resource_spec_nodummy)
        DUT_dummy = self.get_DUT_mock_inspecting_client(
            resource_spec_dummy)
        yield DUT_dummy._add_requests(requests)
        yield DUT_nodummy._add_requests(requests)
        # Check dummy flag
        self.assertFalse(DUT_nodummy.dummy_unknown_requests)
        self.assertTrue(DUT_dummy.dummy_unknown_requests)
        # First check that actual requests are handled correctly
        for DUT in (DUT_nodummy, DUT_dummy):
            # For real requests we expect a string, see
            # get_DUT_mock_inspecting_client() below.
            req = DUT_nodummy.req.req_one
            self.assertEqual(req.name, 'req-one')

        # Check that the non-dummy client doesn't have non-existing requests
        with self.assertRaises(AttributeError):
            DUT_nodummy.req.blah

        # Check that we get a dummy request for the dummied client
        dummy_req = DUT_dummy.req.blah
        dummy_reply = yield dummy_req('abc', 'def', 123)
        self.assertTrue(dummy_reply.succeeded)

        # Repeat dummy tests for a simple ClientGroup
        group = resource_client.ClientGroup('group', (DUT_nodummy, DUT_dummy))
        # A real request should appear on the group level too
        req = group.req.req_one
        self.assertEqual(req.name, 'req_one')
        # Since group contains at least one dummy client, it too has dummy requests
        dummy_req = group.req.blah
        dummy_reply = yield dummy_req('abc', 'def', 123)
        self.assertTrue(dummy_reply.succeeded)
        # Check that group without dummy clients doesn't have non-existing requests
        group_nodummy = resource_client.ClientGroup('group', (DUT_nodummy,))
        with self.assertRaises(AttributeError):
            group_nodummy.req.blah

    def get_DUT_mock_inspecting_client(self, resource_spec, *args, **kwargs):
        """Return a KATCPClientResource instance with a mocked inspecting client

        Note that the inspecting client request factory is moced to return a
        string matching the name of the request rather than a KATCPRequest object

        """
        DUT = resource_client.KATCPClientResource(
            dict(resource_spec), *args, **kwargs)
        ic = DUT._inspecting_client = mock.Mock()
        def future_get_request(key):
            f = tornado.concurrent.Future()
            req_obj = resource_client.KATCPClientResourceRequest(
                dict(name=key, description=key, timeout_hint=None), ic)
            f.set_result(req_obj)
            return f
        ic.future_get_request.side_effect = future_get_request
        return DUT

    @tornado.testing.gen_test
    def test_control(self):
        always_allow = ('req-one', 'req_two', 'exclude_one')
        always_exclude = ('exclude_one', 'exclude-two')
        normal = ('normal', 'another-normal')
        def katcp_form(reqs):
            return tuple(r.replace('_', '-') for r in reqs)

        dev_requests = set(katcp_form(always_allow + always_exclude + normal))

        resource_spec = dict(
            name='testdev',
            address=('testhost', 12345),
            always_allowed_requests=always_allow,
            always_excluded_requests=always_exclude,
            controlled=True)

        DUT = self.get_DUT_mock_inspecting_client(resource_spec)
        yield DUT._add_requests(dev_requests)
        # We expect all the requests, except for those in the always_exclude list to be
        # available. Note, exclude-one should not be available even though it is in
        # always_allow, since always_exclude overrides always_allow.
        self.assertEqual(sorted(DUT.req),
                         sorted(['req_one', 'req_two', 'normal', 'another_normal']))

        # Now try one with no control, only req-one and req-two should be available
        resource_spec['controlled'] = False
        DUT = self.get_DUT_mock_inspecting_client(resource_spec)
        yield DUT._add_requests(dev_requests)
        self.assertEqual(sorted(DUT.req), sorted(['req_one', 'req_two']))

    @tornado.testing.gen_test
    def test_lowlevel_client_attributes(self):
        resource_spec = dict(
            name='testdev',
            description='resource for testing',
            address=('testhost', 12345),
            controlled=True)
        DUT = resource_client.KATCPClientResource(dict(resource_spec))
        with self.assertRaises(RuntimeError):
            # Before calling start() a runtime error should be raised since the inspecting
            # client has not yet been instantiated
            DUT.versions
        with self.assertRaises(RuntimeError):
            DUT.last_connect_time

        ic = DUT.inspecting_client_factory(DUT.address[0], DUT.address[1], None)
        DUT._inspecting_client = mock.Mock(spec_set=ic)
        DUT._inspecting_client.katcp_client = mock.Mock(spec_set=ic.katcp_client)
        v = DUT._inspecting_client.katcp_client.versions = mock.Mock()
        l = DUT._inspecting_client.katcp_client.last_connect_time = mock.Mock()
        self.assertIs(DUT.versions, v)
        self.assertIs(DUT.last_connect_time, l)

    @tornado.testing.gen_test
    def test_list_sensors(self):
        resource_spec = dict(
            name='testdev',
            address=('testhost', 12345))
        DUT = resource_client.KATCPClientResource(resource_spec)
        sens_manager = mock.create_autospec(
            resource_client.KATCPClientResourceSensorsManager(mock.Mock(), "test"))
        test_sensors_info = AttrDict(
            sens_one=AttrDict(name='sens-one', description='sensor one', value=1),
            sens_two=AttrDict(name='sens.two', description='sensor one', value=2),
            sens_three=AttrDict(name='sens_three', description='sensor three', value=3))
        sensor_strategies = dict(sens_one='event', sens_three='period 10')

        def make_test_sensors(sensors_info):
            test_sensors = AttrDict()
            # TODO PY3 check if we can get rid of the list() call
            for sens_pyname, info in list(sensors_info.items()):
                info = dict(info)
                info['sensor_type'] = Sensor.INTEGER
                val = info.pop('value')
                timestamp = val*10
                received_timestamp = timestamp + 1
                sens = test_sensors[sens_pyname] =  resource.KATCPSensor(
                    info, sens_manager)
                sens._reading = resource.KATCPSensorReading(
                    received_timestamp, timestamp, Sensor.NOMINAL, val)
                test_sensors[sens_pyname] = sens
            return test_sensors

        test_sensors = make_test_sensors(test_sensors_info)

        sens_manager.get_sampling_strategy.side_effect = (
            lambda sens_name: resource.normalize_strategy_parameters(
                sensor_strategies.get(
                    resource.escape_name(sens_name), 'none')) )

        DUT.sensor.update(test_sensors)

        # Simple search based on python identifier
        result = yield DUT.list_sensors('sens_one')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], resource.SensorResultTuple(
            test_sensors.sens_one, test_sensors_info.sens_one.name,
            'sens_one', test_sensors_info.sens_one.description, 'integer', '',
            test_sensors.sens_one.reading))

        # Now get all the sensors
        result = yield DUT.list_sensors('')
        expected_result = sorted(resource.SensorResultTuple(
            test_sensors[s_id], test_sensors_info[s_id].name,
            s_id, test_sensors_info[s_id].description, 'integer', '',
            test_sensors[s_id].reading)
                                 for s_id in test_sensors_info)
        self.assertEqual(sorted(result), expected_result)

        # Test that all sensors are found using their Python identifiers
        result = yield DUT.list_sensors('sens_two')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].object, test_sensors.sens_two)
        result = yield DUT.list_sensors('sens_three')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].object, test_sensors.sens_three)

        # Test using actual sensor name
        result = yield DUT.list_sensors('sens_one', use_python_identifiers=False)
        self.assertEqual(len(result), 0)
        result = yield DUT.list_sensors('sens-one', use_python_identifiers=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'sens-one')

        # Now test with strategy filter
        result = yield DUT.list_sensors('', strategy=True)
        self.assertEqual(len(result), len(sensor_strategies))

    def test_until_sync_states(self):
        resource_spec = dict(
            name='testdev',
            address=('testhost', 12345))
        DUT = resource_client.KATCPClientResource(resource_spec)

        # We expect the initial state to be 'disconnected', which means until_synced()
        # should return an unresolved future and until_not_synced() a resolved future
        self.assertEqual(DUT.state, 'disconnected')
        self.assertFalse(DUT.until_synced().done())
        self.assertTrue(DUT.until_not_synced().done())

        # Force state to 'syncing', same expectation as for 'disconnected'
        DUT._state.set_state('syncing')
        self.assertFalse(DUT.until_synced().done())
        self.assertTrue(DUT.until_not_synced().done())

        # Force state to 'synced', opposite expectation as for 'disconnected'
        DUT._state.set_state('synced')
        self.assertTrue(DUT.until_synced().done())
        self.assertFalse(DUT.until_not_synced().done())


class test_KATCPClientResource_Integrated(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(test_KATCPClientResource_Integrated, self).setUp()
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server)
        self.host, self.port = self.server.bind_address
        self.default_resource_spec = dict(
            name='thething',
            address=self.server.bind_address,
            controlled=True)

    @tornado.gen.coroutine
    def _get_DUT_and_sync(self, resource_spec):
        DUT = resource_client.KATCPClientResource(self.default_resource_spec)
        DUT.start()
        yield DUT.until_state('synced')
        raise tornado.gen.Return(DUT)

    @tornado.testing.gen_test(timeout=1)
    def test_requests(self):
        DUT = yield self._get_DUT_and_sync(self.default_resource_spec)
        # Check that all the test-device requests are listed
        self.assertEqual(sorted(DUT.req),
                         sorted(n.replace('-', '_')
                                for n in self.server.request_names))

    @tornado.testing.gen_test(timeout=1)
    def test_active(self):
        DUT = yield self._get_DUT_and_sync(self.default_resource_spec)
        self.assertTrue(DUT.is_active(), 'Expect DUT to be active initialy')
        reply = yield DUT.req.new_command()
        self.assertTrue(reply.succeeded, 'Expect request to be succesful in active state')

        # Set DUT to 'inactive'
        DUT.set_active(False)
        with self.assertRaises(resource.KATCPResourceInactive):
            # Should raise if we attempt to do the request when inactive
            yield DUT.req.new_command()

        # Set DUT to back to 'active'
        DUT.set_active(True)
        reply = yield DUT.req.new_command()
        self.assertTrue(reply.succeeded, 'Expect request to be succesful in active state')


    @tornado.testing.gen_test(timeout=1)
    def test_sensors(self):
       DUT = yield self._get_DUT_and_sync(self.default_resource_spec)
       # Check that all the test-device sensors are listed
       self.assertEqual(sorted(DUT.sensor),
                        sorted(n.replace('-', '_').replace('.', '_')
                               for n in self.server.sensor_names))

    @tornado.testing.gen_test(timeout=1)
    def test_interface_change(self):
        DUT = yield self._get_DUT_and_sync(self.default_resource_spec)
        sensors_before = set(DUT.sensor)
        reqs_before = set(DUT.req)

        # Add a new sensor to the server
        sensor = DeviceTestSensor(DeviceTestSensor.INTEGER, "another.int",
                                  "An Integer.",
                                  "count", [-5, 5], timestamp=self.io_loop.time(),
                                  status=DeviceTestSensor.NOMINAL, value=3)
        self.server.add_sensor(sensor)
        # Check that the sensor does not exist currently
        self.assertNotIn(resource.escape_name(sensor.name), sensors_before)

        # Add a new request to the server
        def request_sparkling_new(self, req, msg):
            """A new command."""
            return Message.reply(msg.name, "ok", "bling1", "bling2")
        self.server._request_handlers['sparkling-new'] = request_sparkling_new
        # Check that the request did not exist before
        self.assertNotIn('sparkling-new', reqs_before)

        # Issue #interface-changed
        self.server.mass_inform(Message.inform('interface-changed'))
        yield DUT.until_state('syncing')
        yield DUT.until_state('synced')

        # Check if sensor/request was added
        self.assertEqual(set(DUT.sensor) - sensors_before, set(['another_int']))
        self.assertEqual(set(DUT.req) - reqs_before, set(['sparkling_new']))

        # And now remove them again
        self.server._request_handlers.pop('sparkling-new')
        self.server.remove_sensor('another.int')

        # Issue #interface-changed
        self.server.mass_inform(Message.inform('interface-changed'))
        yield DUT.until_state('syncing')
        yield DUT.until_state('synced')

        # Check if sensor/request was removed
        self.assertEqual(set(DUT.sensor), sensors_before)
        self.assertEqual(set(DUT.req), reqs_before)


class test_KATCPClientResource_IntegratedTimewarp(TimewarpAsyncTestCase):
    def setUp(self):
        super(test_KATCPClientResource_IntegratedTimewarp, self).setUp()
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server)
        self.host, self.port = self.server.bind_address
        self.default_resource_spec = dict(
            name='thething',
            address=self.server.bind_address,
            controlled=True)

    @tornado.gen.coroutine
    def _get_DUT_and_sync(self, resource_spec):
        DUT = resource_client.KATCPClientResource(self.default_resource_spec)
        DUT.start()
        yield DUT.until_state('synced')
        raise tornado.gen.Return(DUT)

    @tornado.testing.gen_test
    def test_disconnect(self):
        # Test that a device disconnect / reconnect is correctly handled
        DUT = yield self._get_DUT_and_sync(self.default_resource_spec)
        initial_reqs = set(DUT.req)
        initial_sensors = set(DUT.sensor)
        self.server.stop()
        self.server.join(timeout=1)
        yield DUT.until_state('disconnected')

        # Test that requests fail
        rep = yield DUT.req.watchdog()
        self.assertFalse(rep.succeeded)

        # Restart device so that we can reconnect
        self.server.start()
        # timewarp beyond reconect delay
        self.set_ioloop_time(self.ioloop_time + 1)
        yield DUT.until_state('syncing')
        yield DUT.until_state('synced')
        # check that sensors / requests are unchanged
        self.assertEqual(set(DUT.req), initial_reqs)
        self.assertEqual(set(DUT.sensor), initial_sensors)

        # Now disconnect and change the device, to check that it is properly resynced.
        self.server.stop()
        self.server.join(timeout=1)
        yield DUT.until_state('disconnected')

        # Add a new request to the server
        def request_sparkling_new(self, req, msg):
            """A new command."""
            return Message.reply(msg.name, "ok", "bling1", "bling2")
        self.server._request_handlers['sparkling-new'] = request_sparkling_new
        # Check that the request does not exist currently
        self.assertNotIn('sparkling_new', initial_reqs)

        # Add a new sensor to the server
        sensor = DeviceTestSensor(DeviceTestSensor.INTEGER, "another.int",
                                  "An Integer.",
                                  "count", [-5, 5], timestamp=self.io_loop.time(),
                                  status=DeviceTestSensor.NOMINAL, value=3)
        self.server.add_sensor(sensor)
        # Check that the sensor does not exist currently
        escaped_new_sensor = resource.escape_name(sensor.name)
        self.assertNotIn(resource.escape_name(sensor.name), initial_sensors)

        # Restart device so that we can reconnect
        self.server.start()
        # timewarp beyond reconect delay
        self.set_ioloop_time(self.ioloop_time + 1)
        yield DUT.until_state('syncing')
        yield DUT.until_state('synced')
        # check that sensors / requests are correctly updated
        self.assertEqual(set(DUT.req), initial_reqs | set(['sparkling_new']))
        self.assertEqual(set(DUT.sensor), initial_sensors | set([escaped_new_sensor]))

    @tornado.testing.gen_test(timeout=1000)
    def test_set_sampling_strategy(self):
        self.server.stop()
        self.server.join()
        DUT = resource_client.KATCPClientResource(self.default_resource_spec)
        DUT.start()
        yield tornado.gen.moment
        test_strategy = ('period', '2.5')
        yield DUT.set_sampling_strategy('an_int', test_strategy)
        self.assertEqual(DUT._sensor_strategy_cache['an_int'], ('period', '2.5'))
        # Double-check that the sensor does not yet exist
        self.assertNotIn('an_int', DUT.sensor)
        self.server.start()
        self.server.wait_running(timeout=1)
        advancer = TimewarpAsyncTestCaseTimeAdvancer(self, quantum=0.55)
        advancer.start()
        yield DUT.until_synced()
        self.assertEqual(DUT.sensor.an_int.sampling_strategy, test_strategy)

        # Now call set_sampling_strategy with a different strategy and check that it is
        # applied to the real sensor
        new_test_strategy = ('event',)
        yield DUT.set_sampling_strategy('an_int', new_test_strategy)
        self.assertEqual(DUT.sensor.an_int.sampling_strategy, new_test_strategy)
        self.assertEqual(DUT._sensor_strategy_cache['an_int'], ('event',))

    @tornado.testing.gen_test(timeout=1000)
    def test_set_sampling_strategies(self):
        self.server.stop()
        self.server.join()
        DUT = resource_client.KATCPClientResource(self.default_resource_spec)
        DUT.start()
        yield tornado.gen.moment
        test_strategy = ('period', '2.5')
        yield DUT.set_sampling_strategy('an_int', test_strategy)
        self.assertEqual(DUT._sensor_strategy_cache['an_int'], ('period', '2.5'))
        # Double-check that the sensor does not yet exist
        self.assertNotIn('an_int', DUT.sensor)
        self.server.start()
        self.server.wait_running(timeout=1)
        advancer = TimewarpAsyncTestCaseTimeAdvancer(self, quantum=0.55)
        advancer.start()
        yield DUT.until_synced()
        self.assertEqual(DUT.sensor.an_int.sampling_strategy, test_strategy)

        # Now call set_sampling_strategy with a different strategy and check that it is
        # applied to the real sensor
        new_test_strategy = ('event',)
        yield DUT.set_sampling_strategies('int', new_test_strategy)
        self.assertEqual(DUT.sensor.an_int.sampling_strategy, new_test_strategy)
        self.assertEqual(DUT._sensor_strategy_cache['an_int'], ('event',))

    @tornado.testing.gen_test(timeout=1000)
    def test_drop_sampling_strategy(self):
        self.server.stop()
        self.server.join()
        DUT = resource_client.KATCPClientResource(self.default_resource_spec)
        DUT.start()
        yield tornado.gen.moment

        # Check no error for a non-existant sensor
        DUT.drop_sampling_strategy('not_a_valid_sensor')

        test_strategy = ('period', '2.5')
        yield DUT.set_sampling_strategy('an_int', test_strategy)
        self.assertEqual(DUT._sensor_strategy_cache['an_int'], test_strategy)

        # Now drop the strategy from the cache
        DUT.drop_sampling_strategy('an_int')
        self.assertNotIn('an_int', DUT._sensor_strategy_cache)
        self.assertNotIn('an.int', DUT._sensor_manager._strategy_cache)

        # Strategy was dropped before starting server, so expect NO match
        self.server.start()
        self.server.wait_running(timeout=1)
        advancer = TimewarpAsyncTestCaseTimeAdvancer(self, quantum=0.55)
        advancer.start()
        yield DUT.until_synced()
        self.assertNotEqual(DUT.sensor.an_int.sampling_strategy, test_strategy)

        # set strategy while running - it must be cached in two places
        # the DUT, and the DUT's sensor manager
        test_strategy = ('period', '3.5')
        yield DUT.set_sampling_strategy('an_int', test_strategy)
        self.assertEqual(DUT._sensor_strategy_cache['an_int'], test_strategy)
        self.assertEqual(DUT._sensor_manager._strategy_cache['an.int'], test_strategy)
        self.assertEqual(DUT.sensor.an_int.sampling_strategy, test_strategy)

        # Disconnect the device
        self.server.stop()
        self.server.join(timeout=1)
        yield DUT.until_state('disconnected')

        # Drop the strategy from the cache
        DUT.drop_sampling_strategy('an_int')
        self.assertNotIn('an_int', DUT._sensor_strategy_cache)
        self.assertNotIn('an.int', DUT._sensor_manager._strategy_cache)

        # restart server and check that strategy was NOT applied
        self.server.start()
        yield DUT.until_synced()
        self.assertNotEqual(DUT.sensor.an_int.sampling_strategy, test_strategy)

    @tornado.testing.gen_test(timeout=1000)
    def test_set_sensor_listener(self):
        self.server.stop()
        self.server.join()
        resource_spec = self.default_resource_spec
        DUT = resource_client.KATCPClientResource(resource_spec)
        DUT.start()
        yield tornado.gen.moment
        test_listener1 = lambda *x : None
        test_listener2 = lambda *y : None
        yield DUT.set_sensor_listener('an_int', test_listener1)
        # Double-check that the sensor does not yet exist
        self.assertNotIn('an_int', DUT.sensor)
        self.server.start()
        self.server.wait_running(timeout=1)
        advancer = TimewarpAsyncTestCaseTimeAdvancer(self, quantum=0.55)
        advancer.start()
        yield DUT.until_synced()
        self.assertTrue(DUT.sensor.an_int.is_listener, test_listener1)

        # Now call set_sensor_lister with a different listener and check that it is
        # also subscribed
        yield DUT.set_sensor_listener('an_int', test_listener2)
        self.assertTrue(DUT.sensor.an_int.is_listener, test_listener2)
        self.assertTrue(DUT.sensor.an_int.is_listener, test_listener1)

    # TODO tests
    #
    # * Sensor strategy re-application
    # * Request through request object, also with timeouts
    # * Sensor callbacks (probably in test_resource.py, no need for full integrated test)


class test_KATCPClientResourceContainer(tornado.testing.AsyncTestCase):
    def setUp(self):
        self.default_spec_orig = dict(clients={
            'client1': dict(address=('client1-addr', 1234), controlled=True),
            'client-2': dict(address=('client2-addr', 1235), controlled=True),
            'another-client': dict(address=('another-addr', 1231), controlled=True)},
                                      name='test-container',
                                      description='container for testing')
        # make a copy in case the test or DUT messes up any of the original dicts.
        self.default_spec = copy.deepcopy(self.default_spec_orig)
        super(test_KATCPClientResourceContainer, self).setUp()

    @tornado.testing.gen_test
    def test_groups(self):
        spec = self.default_spec
        spec['groups'] = dict(group1=['client1', 'another-client'],
                              group2=['client1', 'client-2'],
                              group3=['client1', 'client-2', 'another-client'])
        DUT = resource_client.KATCPClientResourceContainer(copy.deepcopy(spec))
        DUT.add_group('group4', ('client-2', 'another-client'))
        self.assertEqual(sorted(DUT.groups), ['group1', 'group2', 'group3', 'group4'])
        spec['groups']['group4'] = ('client-2', 'another-client')

        # TODO PY3 check if we can get rid of the list() call
        for group_name, group in list(DUT.groups.items()):
            # Smoke test that no errors are raised
            group.req
            # Check that the correct clients are in each group
            self.assertEqual(sorted(client.name for client in group.clients),
                             sorted(spec['groups'][group_name]))

        # now some surgery, mocking _inspecting_client and calling _add_requests manually
        def mock_inspecting_client(client):

            make_fake_requests = lambda mock_client: {
                req: resource_client.KATCPClientResourceRequest(
                    {'name':req,
                     'description': 'Description for {}'.format(req),
                     'timeout_hint': None},
                    mock_client)
                for req in ['req-1', 'req-2', 'req-3']}

            def _install_inspecting_client_mocks(mock_client):
                fake_requests = make_fake_requests(mock_client)

                def future_get_request(key):
                    f = tornado.concurrent.Future()
                    f.set_result(fake_requests[key])
                    return f

                def wrapped_request(request_name, *args, **kwargs):
                    f = tornado.concurrent.Future()
                    retval = resource.KATCPReply(Message.reply(request_name, 'ok'), [])
                    f.set_result(retval)
                    return f

                mock_client.future_get_request.side_effect = future_get_request
                mock_client.wrapped_request.side_effect = wrapped_request
                return future_get_request

            client._inspecting_client = mock_inspecting_client = mock.Mock(
                spec_set=resource_client.ReplyWrappedInspectingClientAsync)
            _install_inspecting_client_mocks(mock_inspecting_client)

            return mock_inspecting_client

        m_i_c_1 = mock_inspecting_client(DUT.children.client1)
        m_i_c_2 = mock_inspecting_client(DUT.children.client_2)
        m_i_c_a = mock_inspecting_client(DUT.children.another_client)

        # TODO PY3 check if we can get rid of the list() call
        normalize_reply = lambda reply: {c:r if r is None else str(r.reply)
                                          for c, r in list(reply.items())}

        yield DUT.children.client1._add_requests(['req-1'])
        g1_reply = yield DUT.groups.group1.req.req_1()
        self.assertEqual(normalize_reply(g1_reply),
                         {'client1': '!req-1 ok', 'another-client': None})
        # Should evaluate false since not all the clients replied
        self.assertFalse(g1_reply)

        yield DUT.children.another_client._add_requests(['req-1'])
        g1_reply = yield DUT.groups.group1.req.req_1()
        self.assertEqual(normalize_reply(g1_reply),
                         {'client1': '!req-1 ok', 'another-client': '!req-1 ok'})
        # Should evaluate True since all the clients replied succesfully
        self.assertTrue(g1_reply)

        yield DUT.children.client_2._add_requests(['req-2'])
        # client-2 is in group2 and group3, so req-2 should now show up.
        self.assertIn('req_2', DUT.groups.group2.req)
        self.assertIn('req_2', DUT.groups.group3.req)
        # Check that the requests weren't accidentally added to another group
        self.assertFalse('req_2' in DUT.groups.group1.req)

    def test_init(self):
        m_logger = mock.Mock()
        DUT = resource_client.KATCPClientResourceContainer(
            self.default_spec, logger=m_logger)
        self.assertEqual(DUT.name, 'test-container')
        self.assertEqual(DUT.description, 'container for testing')
        child_specs = self.default_spec_orig['clients']
        self.assertEqual(sorted(DUT.children),
                         sorted(resource.escape_name(n) for n in child_specs))
        # TODO PY3 check if we can get rid of the list() call
        for child_name, child_spec in list(child_specs.items()):
            child = DUT.children[resource.escape_name(child_name)]
            self.assertEqual(child.name, child_name)
            self.assertEqual(child.parent, DUT)
            self.assertEqual(child.address, child_spec['address'])
            self.assertIs(child._logger, m_logger)

    def test_set_active(self):
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        mock_children = {n: mock.Mock(spec_set=c, wraps=c)
                         for n, c in dict.items(DUT.children)}
        dict.update(DUT.children, mock_children)

        self.assertTrue(DUT.is_active(), "'active' should be True initially")
        # TODO PY3 check if we can get rid of the list() call
        for child_name, child in list(DUT.children.items()):
            self.assertTrue(child.is_active(),
                            "Child {} should be active".format(child_name))

        # Now set active to false
        DUT.set_active(False)
        self.assertFalse(DUT.is_active(),
                         "'active' should be False after set_active(False)")

        # TODO PY3 check if we can get rid of the list() call
        for child_name, child in list(DUT.children.items()):
            self.assertFalse(child.is_active(),
                            "Child {} should not be active".format(child_name))

        # And now back to to active
        DUT.set_active(True)
        self.assertTrue(DUT.is_active(),
                        "'active' should be True after set_active(True)")
        # TODO PY3 check if we can get rid of the list() call
        for child_name, child in list(DUT.children.items()):
            self.assertTrue(child.is_active(),
                            "Child {} should be active".format(child_name))

    def test_until_sync_states(self):
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        # All children should be in 'disconnected' state, so until_synced() should return
        # an unresolved future and until_not_synced() a resolved future
        self.assertFalse(DUT.until_synced().done())
        self.assertTrue(DUT.until_not_synced().done())

        # Set all child states sync functions to resolved and not_synced to unresolved
        # TODO PY3 check if we can get rid of the list() call
        for child in list(DUT.children.values()):
            f = tornado.concurrent.Future()
            f.set_result(None)
            child.until_synced = mock.create_autospec(
                child.until_synced, return_value=f)
            child.until_not_synced = mock.create_autospec(
                child.until_not_synced, return_value=tornado.concurrent.Future())

        # Now until_synced() should be resolved and until_not_synced() unresolved
        self.assertTrue(DUT.until_synced().done())
        self.assertFalse(DUT.until_not_synced().done())

        # Set only _one_ of the children to not-synced, should be the same as if all of
        # them are disconnected
        for i, child in enumerate(DUT.children.values()):
            if i == 1:
                # Set child to not synced
                f = tornado.concurrent.Future()
                f.set_result(None)
                child.until_not_synced.return_value = f
                child.until_synced.return_value = tornado.concurrent.Future()
            else:
                f = tornado.concurrent.Future()
                f.set_result(None)
                child.until_synced.return_value = tornado.concurrent.Future()
                child.until_not_synced.return_value = f

        self.assertFalse(DUT.until_synced().done())
        self.assertTrue(DUT.until_not_synced().done())


    def test_set_ioloop(self):
        # Make two tornado IOLoop instances, one that is installed as the current thread
        # IOLoop, and one that we will explicity pass to set_ioloop. If set_ioloop is not
        # doing it's job, the children would automatically use thread_ioloop instance.
        thread_ioloop = tornado.ioloop.IOLoop()
        self.addCleanup(thread_ioloop.close, all_fds=True)
        thread_ioloop.make_current()
        our_ioloop = tornado.ioloop.IOLoop()
        self.addCleanup(our_ioloop.close, all_fds=True)
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.set_ioloop(our_ioloop)
        DUT.start()
        for child_name in self.default_spec_orig['clients']:
            self.assertIs(DUT.children[resource.escape_name(child_name)].ioloop,
                          our_ioloop)


class test_KATCPClientResourceContainerIntegrated(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(test_KATCPClientResourceContainerIntegrated, self).setUp()
        self.default_spec = dict(clients={
            'resource1' : dict(controlled=True),
            'resource2' : dict(controlled=True),
            'resource3' : dict(controlled=True)},
                                 name='intgtest')
        self.resource_names = list(self.default_spec['clients'].keys())
        self.servers = {rn: DeviceTestServer('', 0) for rn in self.resource_names}
        for i, (s_name, s) in enumerate(sorted(self.servers.items())):
            start_thread_with_cleanup(self, s)
            self.default_spec['clients'][s_name]['address'] = s.bind_address
            # Add a unique sensor to each server
            sensor = DeviceTestSensor(DeviceTestSensor.INTEGER, "int."+s_name,
                                      "An Integer.",
                                      "count", [-50, 50], timestamp=self.io_loop.time(),
                                      status=DeviceTestSensor.NOMINAL, value=i)
            s.add_sensor(sensor)
            # Add a unique request to each server
            def handler(self, req, msg):
                """A new command."""
                return Message.reply(msg.name, "ok", "bling1", "bling2")
            s._request_handlers['sparkling-new-'+s_name] = handler

    @tornado.testing.gen_test
    def test_preset_protocol_flags(self):
        pf1 = ProtocolFlags(4, 0, '')
        pf2 = ProtocolFlags(5, 0, 'M')
        pf3 = ProtocolFlags(5, 0, 'I')
        clients = self.default_spec['clients']
        clients['resource1']['preset_protocol_flags'] = pf1
        clients['resource2']['preset_protocol_flags'] = pf2
        clients['resource3']['preset_protocol_flags'] = pf3
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.start()
        children = DUT.children
        self.assertEqual(
            children.resource1._inspecting_client.katcp_client.protocol_flags,
            pf1)
        self.assertEqual(
            children.resource2._inspecting_client.katcp_client.protocol_flags,
            pf2)
        self.assertEqual(
            children.resource3._inspecting_client.katcp_client.protocol_flags,
            pf3)

    @tornado.testing.gen_test
    def test_timeout_of_until_synced(self):
        self.default_spec_orig = copy.deepcopy(self.default_spec)
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.start()
        # Test for timing out
        with self.assertRaises(tornado.gen.TimeoutError):
            yield DUT.until_synced(timeout=0.001)
        # Test for NOT timing out
        yield DUT.until_synced(timeout=0.5)

    @tornado.testing.gen_test(timeout=1000)
    def test_set_sampling(self):
        self.default_spec_orig = copy.deepcopy(self.default_spec)
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.start()

        def side_effect(*args, **kwargs):
            f = tornado.concurrent.futures.Future()
            f.set_result(None)
            return f

        additional = {'resource1': 'sensor_1',
                      'resource2': 'agg_sensor,sensor_1',
                      'resource3': 'sensor_3'}
        for x in additional:
            s = self.servers[x]
            for sens in additional[x].split(","):
                sensor = DeviceTestSensor(DeviceTestSensor.INTEGER, sens,
                                      "An Integer.",
                                      "count", [-50, 50], timestamp=self.io_loop.time(),
                                      status=DeviceTestSensor.NOMINAL, value=0)
                s.add_sensor(sensor)

        yield DUT.until_synced()

        DUT.children.resource1.set_sampling_strategy = mock.Mock(side_effect=side_effect)
        DUT.children.resource2.set_sampling_strategy = mock.Mock(side_effect=side_effect)
        DUT.children.resource3.set_sampling_strategy = mock.Mock(side_effect=side_effect)

        strat1 = ('period', '2.1')
        strat2 = ('event',)
        strat3 = ('event-rate', '2', '3')
        yield DUT.set_sampling_strategy('resource1_sensor_1', strat1)
        DUT.children.resource1.set_sampling_strategy.assert_called_once_with(
            'sensor_1', strat1)
        DUT.children.resource2.set_sampling_strategy.assert_not_called()
        DUT.children.resource3.set_sampling_strategy.assert_not_called()
        DUT.children.resource1.set_sampling_strategy.reset_mock()

        yield DUT.set_sampling_strategy('resource2.sensor_1', strat2)
        DUT.children.resource2.set_sampling_strategy.assert_called_once_with(
            'sensor_1', strat2)
        DUT.children.resource1.set_sampling_strategy.assert_not_called()
        DUT.children.resource3.set_sampling_strategy.assert_not_called()
        DUT.children.resource2.set_sampling_strategy.reset_mock()

        yield DUT.set_sampling_strategy('agg_sensor', strat1)
        DUT.children.resource2.set_sampling_strategy.assert_called_once_with(
            'agg_sensor', strat1)
        DUT.children.resource1.set_sampling_strategy.assert_not_called()
        DUT.children.resource3.set_sampling_strategy.assert_not_called()
        DUT.children.resource2.set_sampling_strategy.reset_mock()

        yield DUT.set_sampling_strategy('resource3_sensor_3', strat3)
        DUT.children.resource3.set_sampling_strategy.assert_called_once_with(
            'sensor_3', strat3)
        DUT.children.resource1.set_sampling_strategy.assert_not_called()
        DUT.children.resource2.set_sampling_strategy.assert_not_called()
        DUT.children.resource3.set_sampling_strategy.reset_mock()

        yield DUT.set_sampling_strategies('sensor_1', strat2)
        DUT.children.resource1.set_sampling_strategy.assert_called_once_with(
            'sensor_1', strat2)
        DUT.children.resource2.set_sampling_strategy.assert_called_once_with(
            'sensor_1', strat2)
        DUT.children.resource3.set_sampling_strategy.assert_not_called()
        DUT.children.resource1.set_sampling_strategy.reset_mock()
        DUT.children.resource2.set_sampling_strategy.reset_mock()

    @tornado.testing.gen_test(timeout=1000)
    def test_set_sensor_listener(self):

        self.default_spec_orig = copy.deepcopy(self.default_spec)
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.start()

        def side_effect(*args, **kwargs):
            f = tornado.concurrent.futures.Future()
            f.set_result(None)
            return f

        additional = {'resource1': 'sensor_1',
                      'resource2': 'agg_sensor,sensor_1',
                      'resource3': 'sensor_3'}
        for x in additional:
            s = self.servers[x]
            for sens in additional[x].split(","):
                sensor = DeviceTestSensor(DeviceTestSensor.INTEGER, sens,
                                      "An Integer.",
                                      "count", [-50, 50], timestamp=self.io_loop.time(),
                                      status=DeviceTestSensor.NOMINAL, value=0)
                s.add_sensor(sensor)

        yield DUT.until_synced()

        DUT.children.resource1.set_sensor_listener = mock.Mock(side_effect=side_effect)
        DUT.children.resource2.set_sensor_listener = mock.Mock(side_effect=side_effect)
        DUT.children.resource3.set_sensor_listener = mock.Mock(side_effect=side_effect)

        listener1 = lambda *x : None
        listener2 = lambda *y : None
        listener3 = lambda *z : None

        yield DUT.set_sensor_listener('resource1_sensor_1', listener1)
        DUT.children.resource1.set_sensor_listener.assert_called_once_with(
            'sensor_1', listener1)
        DUT.children.resource2.set_sensor_listener.assert_not_called()
        DUT.children.resource3.set_sensor_listener.assert_not_called()
        DUT.children.resource1.set_sensor_listener.reset_mock()

        yield DUT.set_sensor_listener('resource2_sensor_1', listener2)
        DUT.children.resource2.set_sensor_listener.assert_called_once_with(
            'sensor_1', listener2)
        DUT.children.resource1.set_sensor_listener.assert_not_called()
        DUT.children.resource3.set_sensor_listener.assert_not_called()
        DUT.children.resource2.set_sensor_listener.reset_mock()

        yield DUT.set_sensor_listener('agg_sensor', listener2)
        DUT.children.resource2.set_sensor_listener.assert_called_once_with(
            'agg_sensor', listener2)
        DUT.children.resource1.set_sensor_listener.assert_not_called()
        DUT.children.resource3.set_sensor_listener.assert_not_called()
        DUT.children.resource2.set_sensor_listener.reset_mock()

        yield DUT.set_sensor_listener('resource3.sensor_3', listener3)
        DUT.children.resource3.set_sensor_listener.assert_called_once_with(
            'sensor_3', listener3)
        DUT.children.resource1.set_sensor_listener.assert_not_called()
        DUT.children.resource2.set_sensor_listener.assert_not_called()
        DUT.children.resource3.set_sensor_listener.reset_mock()


    @tornado.testing.gen_test(timeout=1000)
    def test_listening(self):
        spec = dict(name="resource1",
            description='resource for testing',
            address=('testhost', 12345),
            controlled=True)

        ##DUT = resource_client.KATCPClientResource(spec)
        ##DUT.start()
        self.default_spec_orig = copy.deepcopy(self.default_spec)
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.start()


        # Setup a new sensor with listener and trigger some sensor updates
        new_sensor = DeviceTestSensor(DeviceTestSensor.INTEGER, 'new_sensor',
                                      "An Integer.",
                                      "count", [-50, 50], timestamp=self.io_loop.time(),
                                      status=DeviceTestSensor.NOMINAL, value=0)
        server = self.servers["resource1"]
        server.add_sensor(new_sensor)
        yield DUT.until_synced()
        katcp_sensor = DUT.children.resource1.sensor.new_sensor
        import time
        t0 = time.time() - 5 # A little old
        t, status, value = (t0, Sensor.WARN, -1)
        katcp_sensor.set(t, status, value)

        listener = mock.Mock()
        yield DUT.children.resource1.set_sensor_listener('new_sensor', listener)
        t, status, value = (t0+0.5, Sensor.NOMINAL, -2)
        katcp_sensor.set(t, status, value)
        self.assertEqual(katcp_sensor.reading.status, 'nominal')
        self.assertEqual(katcp_sensor.reading.value, -2)
        katcp_sensor.set(t, status, value)
        t, status, value = (t0+1, Sensor.NOMINAL, 10)
        katcp_sensor.set(t, status, value)
        t, status, value = (t0+1.5, Sensor.NOMINAL, 5)
        katcp_sensor.set(t, status, value)
        t, status, value = (t0+2, Sensor.NOMINAL, 4)
        katcp_sensor.set(t, status, value)
        t, status, value = (t0+2.5, Sensor.NOMINAL, 3)
        katcp_sensor.set(t, status, value)
        print("listener.mock_calls", listener.mock_calls)
        self.assertEquals(len(listener.mock_calls), 6)
        return

    def get_expected(self, testserv_attr):
        expected_items = []
        for i, (serv_name, serv) in enumerate(sorted(self.servers.items())):
            for item_name in getattr(serv, testserv_attr):
                expected_items.append((serv_name+'_'+item_name)
                                        .replace('.', '_')
                                        .replace('-', '_'))
        return expected_items

    @tornado.gen.coroutine
    def get_DUT_synced(self):
        # make a copy in case the test or DUT messes up any of the original dicts.
        self.default_spec_orig = copy.deepcopy(self.default_spec)
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.start()
        yield DUT.until_synced()
        raise tornado.gen.Return(DUT)


    @tornado.testing.gen_test(timeout=1)
    def test_sensors(self):
        DUT = yield self.get_DUT_synced()
        expected_sensors = self.get_expected('sensor_names')
        self.assertEqual(sorted(DUT.sensor), sorted(expected_sensors))
        # Test that some sensor objects are correctly mapped between container and client
        self.assertIs(DUT.sensor.resource1_int_resource1,
                      DUT.children['resource1'].sensor.int_resource1)
        self.assertIs(DUT.sensor.resource2_int_resource2,
                      DUT.children['resource2'].sensor.int_resource2)
        self.assertIs(DUT.sensor.resource3_an_int,
                      DUT.children['resource3'].sensor.an_int)


    @tornado.testing.gen_test(timeout=1)
    def test_requests(self):
        r2_spec = self.default_spec['clients']['resource2']
        r2_spec['always_allowed_requests'] = ['sparkling-new-resource2']
        r2_spec['controlled'] = False
        DUT = yield self.get_DUT_synced()
        # Strip out all resource2 requests (since it is not controlled) except for
        # sparkling-new-resource2 which is in always_allowed_requests.
        expected_requests = [r for r in self.get_expected('request_names')
                             if (not r.startswith('resource2_') or
                                 r == 'resource2_sparkling_new_resource2')]
        self.assertEqual(sorted(DUT.req), sorted(expected_requests))
        # Test that some request objects are correctly mapped between container and client
        self.assertIs(DUT.req.resource1_sparkling_new_resource1,
                      DUT.children['resource1'].req.sparkling_new_resource1)
        self.assertIs(DUT.req.resource2_sparkling_new_resource2,
                      DUT.children['resource2'].req.sparkling_new_resource2)
        self.assertIs(DUT.req.resource3_halt,
                      DUT.children['resource3'].req.halt)

    @tornado.testing.gen_test(timeout=1)
    def test_group_wait(self):
        # Start new container
        self.default_spec_orig = copy.deepcopy(self.default_spec)
        DUT = resource_client.KATCPClientResourceContainer(self.default_spec)
        DUT.add_group('test', list(DUT.children.keys()))
        DUT.start()
        # TODO PY3 check if we can get rid of the list() call
        for server in list(self.servers.values()):
            # Setup a new sensor on all clients to wait on
            wait_sensor = DeviceTestSensor(DeviceTestSensor.INTEGER,
                                           'wait_sensor', "An Integer.",
                                           "count", [-50, 50], value=0,
                                           timestamp=self.io_loop.time(),
                                           status=DeviceTestSensor.NOMINAL)
            server.add_sensor(wait_sensor)
        yield DUT.until_synced()
        # Ensure strategies are set for wait() to work
        # TODO PY3 check if we can get rid of the list() call
        for client in list(DUT.children.values()):
            client.set_sampling_strategy('wait_sensor', 'event')
        group = DUT.groups['test']
        # Test the no timeout case too for what it's worth
        result = yield group.wait('wait_sensor', 0, timeout=None)
        self.assertTrue(result)
        result = yield group.wait('wait_sensor', 0, timeout=None, quorum=1.0)
        self.assertTrue(result)
        # Check detailed results per client
        # TODO PY3 check if we can get rid of the list() call
        for client in list(DUT.children.values()):
            self.assertTrue(result[client.name])
        result = yield group.wait('wait_sensor', 1, timeout=0.1)
        self.assertFalse(result)
        # TODO PY3 check if we can get rid of the list() call
        for client in list(DUT.children.values()):
            self.assertFalse(result[client.name])
        # Test quorum functionality
        with self.assertRaises(TypeError):
            yield group.wait('wait_sensor', 1, timeout=0.1, quorum=1.1)
        # TODO PY3 check if we can get rid of the list() call
        selected_client = list(DUT.children.keys())[0]
        self.servers[selected_client].get_sensor('wait_sensor').set_value(1)
        result = yield group.wait('wait_sensor', 1, timeout=0.1, quorum=1)
        self.assertTrue(result)
        # Be warned that quorum == 1.0 is not the same as quorum == 1 ...
        result = yield group.wait('wait_sensor', 1, timeout=0.1, quorum=1.0)
        self.assertFalse(result)
        result = yield group.wait('wait_sensor', 1, timeout=0.1, quorum=0.1)
        self.assertTrue(result)
        # TODO PY3 check if we can get rid of the list() call
        for client in list(DUT.children.values()):
            if client.name == selected_client:
                self.assertTrue(result[client.name])
            else:
                self.assertFalse(result[client.name])
        result = yield group.wait('wait_sensor', 1, timeout=0.1, quorum=2)
        self.assertFalse(result)
        # TODO PY3 check if we can get rid of the list() call
        for client in list(DUT.children.values()):
            if client.name == selected_client:
                self.assertTrue(result[client.name])
            else:
                self.assertFalse(result[client.name])


class test_AttrMappingProxy(unittest.TestCase):
    def test_wrapping(self):
        test_dict = AttrDict(a=2, b=1)
        class TestWrapper(object):
            def __init__(self, wrappee):
                self.wrappee = wrappee

            def __eq__(self, other):
                return self.wrappee == other.wrappee

        wrapped_dict = resource_client.AttrMappingProxy(test_dict, TestWrapper)
        # Test keys
        # TODO PY3 check if we can get rid of the list() call
        self.assertEqual(list(wrapped_dict.keys()), list(test_dict.keys()))
        # Test key access:
        for key in test_dict:
            self.assertEqual(wrapped_dict[key].wrappee, test_dict[key])
        # Test attribute access
        for key in test_dict:
            self.assertEqual(getattr(wrapped_dict, key).wrappee,
                             getattr(test_dict, key))
        # Test whole dict comparison
        # TODO PY3 check if we can get rid of the list() call
        self.assertEqual(
            wrapped_dict,
            {k : TestWrapper(v) for k, v in list(test_dict.items())})


class test_ThreadSafeKATCPClientResourceWrapper(unittest.TestCase):
    def setUp(self):
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server)

        self.ioloop_manager = ioloop_manager.IOLoopManager(managed_default=True)
        self.io_loop = self.ioloop_manager.get_ioloop()
        self.host, self.port = self.server.bind_address
        self.default_resource_spec = dict(
            name='thething',
            address=self.server.bind_address,
            controlled=True)
        self.client_resource = resource_client.KATCPClientResource(
            self.default_resource_spec)
        self.client_resource.set_ioloop(self.io_loop)
        self.io_loop.add_callback(self.client_resource.start)

        self.ioloop_thread_wrapper = ioloop_manager.IOLoopThreadWrapper(self.io_loop)
        start_thread_with_cleanup(self, self.ioloop_manager, start_timeout=1)
        self.ioloop_thread_wrapper.default_timeout = 1

        self.DUT = resource_client.ThreadSafeKATCPClientResourceWrapper(
            self.client_resource, self.ioloop_thread_wrapper)
        self.DUT.until_synced()

    def test_wrapped_timeout(self):
        self.assertEqual(self.client_resource.state, 'synced')
        # Test timeout
        self.ioloop_thread_wrapper.default_timeout = 0.001
        t0 = time.time()
        with self.assertRaises(TimeoutError):
            self.DUT.until_state('disconnected')
        self.assertLess(time.time() - t0, 0.2)
        # Now make sure we can actualy still wait on the state
        self.ioloop_thread_wrapper.default_timeout = 1
        self.server.stop()
        self.server.join()
        self.DUT.until_state('disconnected')
        self.assertEqual(self.client_resource.state, 'disconnected')
        self.server.start()
        self.DUT.until_state('synced')
        self.assertEqual(self.client_resource.state, 'synced')

    def test_request(self):
        reply = self.DUT.req.sensor_value('an.int')
        last_server_msg = self.server.messages[-1]
        self.assertTrue(reply.succeeded)
        self.assertEqual(str(last_server_msg),
                         '?sensor-value[{}] an.int'.format(reply.reply.mid))

    def test_sensor(self):
        server_sensor = self.server.get_sensor('an.int')
        reading = self.DUT.sensor.an_int.get_reading()
        self.assertEqual(reading.value, server_sensor.read().value)
        server_sensor.set_value(server_sensor.read().value + 5)
        reading = self.DUT.sensor.an_int.get_reading()
        self.assertEqual(reading.value, server_sensor.read().value)


class test_ThreadSafeKATCPClientResourceWrapper_container(unittest.TestCase):

    def setUp(self):
        self.ioloop_manager = ioloop_manager.IOLoopManager(managed_default=True)
        self.io_loop = self.ioloop_manager.get_ioloop()
        self.io_loop.make_current()

        self.ioloop_thread_wrapper = ioloop_manager.IOLoopThreadWrapper(self.io_loop)
        start_thread_with_cleanup(self, self.ioloop_manager, start_timeout=1)
        self.ioloop_thread_wrapper.default_timeout = 1

        self.default_spec = dict(clients={
            'resource1' : dict(controlled=True),
            'resource2' : dict(controlled=True)},
                                 name='wraptest')
        # TODO PY3 check if we can get rid of the list() call
        self.resource_names = list(self.default_spec['clients'].keys())
        self.servers = {rn: DeviceTestServer('', 0) for rn in self.resource_names}
        for i, (s_name, s) in enumerate(sorted(self.servers.items())):
            start_thread_with_cleanup(self, s)
            self.default_spec['clients'][s_name]['address'] = s.bind_address
            # Add a unique sensor to each server
            sensor = DeviceTestSensor(DeviceTestSensor.INTEGER, "int."+s_name,
                                      "An Integer.",
                                      "count", [-50, 50], timestamp=self.io_loop.time(),
                                      status=DeviceTestSensor.NOMINAL, value=i)
            s.add_sensor(sensor)
            # Add a unique request to each server
            def handler(self, req, msg):
                """A new command."""
                return Message.reply(msg.name, "ok", "bling1", "bling2")
            s._request_handlers['sparkling-new-'+s_name] = handler

        self.resource_container = resource_client.KATCPClientResourceContainer(
            self.default_spec)
        self.DUT = resource_client.ThreadSafeKATCPClientResourceWrapper(
            self.resource_container, self.ioloop_thread_wrapper)
        self.DUT.start()
        self.DUT.until_synced()

    def test_sensor(self):
        self.assertEqual(self.DUT.sensor.resource1_int_resource1,
                         self.DUT.children['resource1'].sensor.int_resource1)
        self.assertIs(self.DUT.sensor.resource1_int_resource1.reading,
                      self.resource_container.sensor.resource1_int_resource1.reading)
        self.servers['resource2'].get_sensor('int.resource2').set_value(17)
        reading = self.DUT.sensor.resource2_int_resource2.get_reading()
        self.assertEqual(reading.value, 17)
        self.assertEqual(reading.status, Sensor.STATUSES[Sensor.NOMINAL])
        self.servers['resource2'].get_sensor('int.resource2').set_value(14)
        self.assertEqual(self.DUT.sensor.resource2_int_resource2.get_value(), 14)
        self.servers['resource2'].get_sensor('int.resource2').set_value(
            10, Sensor.WARN)
        self.assertEqual(self.DUT.sensor.resource2_int_resource2.get_status(),
                         Sensor.STATUSES[Sensor.WARN])
        self.assertEqual(self.DUT.sensor.resource2_int_resource2.value, 10)

    def test_children(self):
        self.assertIs(type(self.DUT.children['resource1']),
                      resource_client.ThreadSafeKATCPClientResourceWrapper)
        self.assertIs(self.DUT.children['resource1'].__subject__,
                      self.resource_container.children['resource1'])

        self.assertIs(type(self.DUT.children['resource2']),
                      resource_client.ThreadSafeKATCPClientResourceWrapper)
        self.assertIs(self.DUT.children['resource2'].__subject__,
                      self.resource_container.children['resource2'])


class test_monitor_resource_sync_state(tornado.testing.AsyncTestCase):
    @tornado.testing.gen_test
    def test_monitor_resource_sync_state(self):
        m_res = mock.Mock()
        callback = mock.Mock()
        exit_event = AsyncEvent()
        synced = AsyncEvent()
        not_synced = AsyncEvent()
        m_res.until_synced = synced.until_set
        m_res.until_not_synced = not_synced.until_set
        def set_synced(sync):
            if sync:
                not_synced.clear()
                synced.set()
            else:
                synced.clear()
                not_synced.set()
        loop_done_future = resource_client.monitor_resource_sync_state(
            m_res, callback, exit_event)
        yield tornado.gen.moment
        self.assertEqual(callback.call_args_list, [mock.call(False)])
        callback.reset_mock()
        # Check that it exits if exit_event is set
        exit_event.set()
        yield tornado.gen.moment
        self.assertFalse(callback.called,
                         'No callback should be made when exit_event is set')
        self.assertTrue(loop_done_future.done(),
                        'Monitor loop should terminate when exit_event is set')
        exit_event.clear()
        loop_done_future = resource_client.monitor_resource_sync_state(
            m_res, callback, exit_event)
        set_synced(True)
        yield tornado.gen.moment
        self.assertEqual(callback.call_args_list, [mock.call(False), mock.call(True)])
        callback.reset_mock()
        set_synced(False)
        yield tornado.gen.moment
        self.assertEqual(callback.call_args_list, [mock.call(False)])
        callback.reset_mock()
        # Now check exit_event when synced is set
        set_synced(True)
        yield tornado.gen.moment
        self.assertEqual(callback.call_args_list, [mock.call(True)])
        callback.reset_mock()
        self.assertFalse(loop_done_future.done(),
                        'Monitor loop should only terminate is exit_event is set')
        exit_event.set()
        yield tornado.gen.moment
        self.assertFalse(callback.called)
        self.assertTrue(loop_done_future.done(),
                        'Monitor loop should terminate when exit_event is set')
