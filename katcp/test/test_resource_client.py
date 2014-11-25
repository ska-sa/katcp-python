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
import copy

import tornado
import mock

from katcp.testutils import (DeviceTestServer, DeviceTestSensor,
                             start_thread_with_cleanup, TimewarpAsyncTestCase)

from katcp import resource, inspecting_client, Message, Sensor
from katcp.core import AttrDict

# module under test
from katcp import resource_client

class test_KATCPClientresourceRequest(unittest.TestCase):
    def setUp(self):
        self.mock_client = mock.Mock()
        self.DUT = resource_client.KATCPClientResourceRequest(
            'the-request', 'The description', self.mock_client)

    def test_init(self):
        self.assertEqual(self.DUT.name, 'the-request')
        self.assertEqual(self.DUT.description, 'The description')
        # Check that we are registered to the correct ABC
        self.assertIsInstance(self.DUT, resource.KATCPRequest)

    def test_request(self):
        reply = self.DUT('parm1', 2)
        self.mock_client.wrapped_request.assert_called_once_with(
            'the-request', 'parm1', 2)
        self.assertIs(reply, self.mock_client.wrapped_request.return_value)

class test_KATCPClientresource(tornado.testing.AsyncTestCase):
    def test_init(self):
        resource_spec = dict(
            name='testdev',
            address=('testhost', 12345),
            controlled=True)
        DUT = resource_client.KATCPClientResource(dict(resource_spec))
        self.assertEqual(DUT.address, resource_spec['address'])
        self.assertEqual(DUT.state, 'disconnected')
        self.assertEqual(DUT.name, resource_spec['name'])
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

        def get_DUT():
            DUT = resource_client.KATCPClientResource(dict(resource_spec))
            ic = DUT._inspecting_client = mock.Mock()
            def future_get_request(key):
                f = tornado.concurrent.Future()
                f.set_result(key)
                return f
            ic.future_get_request.side_effect = future_get_request
            return DUT

        DUT = get_DUT()
        yield DUT._add_requests(dev_requests)
        # We expect all the requests, except for those in the always_exclude list to be
        # available. Note, exclude-one should not be available even though it is in
        # always_allow, since always_exclude overrides always_allow.
        self.assertEqual(sorted(DUT.req),
                         sorted(['req_one', 'req_two', 'normal', 'another_normal']))

        # Now try one with no control, only req-one and req-two should be available
        resource_spec['controlled'] = False
        DUT = get_DUT()
        yield DUT._add_requests(dev_requests)
        self.assertEqual(sorted(DUT.req), sorted(['req_one', 'req_two']))

    def test_list_sensors(self):
        resource_spec = dict(
            name='testdev',
            address=('testhost', 12345))
        DUT = resource_client.KATCPClientResource(resource_spec)
        sens_manager = mock.create_autospec(
            resource_client.KATCPClientResourceSensorsManager(mock.Mock()))
        test_sensors_info = AttrDict(
            sens_one=AttrDict(name='sens-one', description='sensor one', value=1),
            sens_two=AttrDict(name='sens.two', description='sensor one', value=2),
            sens_three=AttrDict(name='sens_three', description='sensor three', value=3))
        sensor_strategies = dict(sens_one='event', sens_three='period 10')

        def make_test_sensors(sensors_info):
            test_sensors = AttrDict()
            for sens_pyname, info in sensors_info.items():
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
        result = DUT.list_sensors('sens_one')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], resource.SensorResultTuple(
            test_sensors.sens_one, test_sensors_info.sens_one.name,
            'sens_one', test_sensors_info.sens_one.description, 'integer', '',
            test_sensors.sens_one.reading))

        # Now get all the sensors
        result = DUT.list_sensors('')
        expected_result = sorted(resource.SensorResultTuple(
            test_sensors[s_id], test_sensors_info[s_id].name,
            s_id, test_sensors_info[s_id].description, 'integer', '',
            test_sensors[s_id].reading)
                                 for s_id in test_sensors_info)
        self.assertEqual(sorted(result), expected_result)

        # Test that all sensors are found using their Python identifiers
        result = DUT.list_sensors('sens_two')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].object, test_sensors.sens_two)
        result = DUT.list_sensors('sens_three')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].object, test_sensors.sens_three)

        # Test using actual sensor name
        result = DUT.list_sensors('sens_one', use_python_identifiers=False)
        self.assertEqual(len(result), 0)
        result = DUT.list_sensors('sens-one', use_python_identifiers=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'sens-one')

        # Now test with strategy filter
        result = DUT.list_sensors('', strategy=True)
        self.assertEqual(len(result), len(sensor_strategies))

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

class test_KATCPClientresource_IntegratedTimewarp(TimewarpAsyncTestCase):
    def setUp(self):
        super(test_KATCPClientresource_IntegratedTimewarp, self).setUp()
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

    # TODO tests
    #
    # * Sensor strategy re-application
    # * Request through request object, also with timeouts
    # * Sensor callbacks (probably in test_resource.py, no need for full integrated test)

class test_KATCPClientResourceContainer(unittest.TestCase):
    def setUp(self):
        self.default_spec_orig = dict(clients={
            'client1' : dict(address=('client1-addr', 1234)),
            'client-2' : dict(address=('client2-addr', 1235))})
        # make a copy in case the test or DUT messes up any of the original dicts.
        self.default_spec = copy.deepcopy(self.default_spec_orig)

    def test_init(self):
        m_logger = mock.Mock()
        DUT = resource_client.KATCPClientResourceContainer(
            self.default_spec, logger=m_logger)
        child_specs = self.default_spec_orig['clients']
        self.assertEqual(sorted(DUT.children), sorted(child_specs))
        for child_name, child_spec in child_specs.items():
            child = DUT.children[child_name]
            self.assertEqual(child.name, child_name)
            self.assertEqual(child.parent, DUT)
            self.assertEqual(child.address, child_spec['address'])
            self.assertIs(child._logger, m_logger)

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
            self.assertIs(DUT.children[child_name].ioloop, our_ioloop)

class test_KATCPClientResourceContainerIntegrated(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(test_KATCPClientResourceContainerIntegrated, self).setUp()
        self.default_spec = dict(clients={
            'resource1' : dict(controlled=True),
            'resource2' : dict(controlled=True),
            'resource3' : dict(controlled=True)})
        self.resource_names = self.default_spec['clients'].keys()
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

