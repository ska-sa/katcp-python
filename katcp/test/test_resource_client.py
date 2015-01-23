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
import time
import threading

import tornado
import mock

from thread import get_ident as get_thread_ident

from concurrent.futures import Future, TimeoutError

from katcp.testutils import (DeviceTestServer, DeviceTestSensor,
                             start_thread_with_cleanup, TimewarpAsyncTestCase)

from katcp import resource, inspecting_client, ioloop_manager, Message, Sensor
from katcp.core import AttrDict

# module under test
from katcp import resource_client

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

    @tornado.testing.gen_test(timeout=1000000)
    def test_groups(self):
        spec = self.default_spec
        spec['groups'] = dict(group1=['client1', 'another-client'],
                              group2=['client1', 'client-2'],
                              group3=['client1', 'client-2', 'another-client'])
        DUT = resource_client.KATCPClientResourceContainer(copy.deepcopy(spec))
        self.assertEqual(sorted(DUT.groups), ['group1', 'group2', 'group3'])

        for group_name, group in DUT.groups.items():
            # Smoke test that no errors are raised
            group.req
            # Check that the correct clients are in each group
            self.assertEqual(sorted(client.name for client in group.clients),
                             sorted(spec['groups'][group_name]))

        # now some surgery, mocking _inspecting_client and calling _add_requests manually
        def mock_inspecting_client(client):

            make_fake_requests = lambda mock_client: {
                req: resource_client.KATCPClientResourceRequest(
                    req, 'Description for {}'.format(req), mock_client)
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

        normalize_reply = lambda reply: {c:r if r is None else str(r.reply)
                                          for c, r in reply.items()}

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
        for child_name, child_spec in child_specs.items():
            child = DUT.children[resource.escape_name(child_name)]
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

class test_ThreadsafeMethodAttrWrapper(unittest.TestCase):
    def setUp(self):
        self.ioloop_manager = ioloop_manager.IOLoopManager(managed_default=True)
        self.ioloop = self.ioloop_manager.get_ioloop()
        self.ioloop_thread_wrapper = resource_client.IOLoopThreadWrapper(self.ioloop)
        start_thread_with_cleanup(self, self.ioloop_manager, start_timeout=1)

    def test_wrapping(self):
        test_inst = self
        class Wrappee(object):
            def __init__(self, ioloop_thread_id):
                self.thread_id = ioloop_thread_id

            def a_callable(self, arg, kwarg='abc'):
                test_inst.assertEqual(get_thread_ident(), self.thread_id)
                return (arg * 2, kwarg * 3)

            @property
            def not_in_ioloop(self):
                test_inst.assertNotEqual(get_thread_ident(), self.thread_id)
                return 'not_in'

            @property
            def only_in_ioloop(self):
                test_inst.assertEqual(get_thread_ident(), self.thread_id)
                return 'only_in'

        class TestWrapper(resource_client.ThreadSafeMethodAttrWrapper):
            @property
            def only_in_ioloop(self):
                return self._getattr('only_in_ioloop')


        id_future = Future()
        self.ioloop.add_callback(lambda : id_future.set_result(get_thread_ident()))
        wrappee = Wrappee(id_future.result(timeout=1))
        wrapped = TestWrapper(wrappee, self.ioloop_thread_wrapper)
        # First test our assumptions about Wrappee
        with self.assertRaises(AssertionError):
            wrappee.a_callable(3, 'a')
        with self.assertRaises(AssertionError):
            wrappee.only_in_ioloop
        self.assertEqual(wrappee.not_in_ioloop, 'not_in')

        # Now test the wrapped version
        self.assertEqual(wrapped.a_callable(5, kwarg='bcd'), (10, 'bcd'*3))
        self.assertEqual(wrapped.only_in_ioloop, 'only_in')
        self.assertEqual(wrapped.not_in_ioloop, 'not_in')

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
        self.assertEqual(wrapped_dict.keys(), test_dict.keys())
        # Test key access:
        for key in test_dict:
            self.assertEqual(wrapped_dict[key].wrappee, test_dict[key])
        # Test attribute access
        for key in test_dict:
            self.assertEqual(getattr(wrapped_dict, key).wrappee,
                             getattr(test_dict, key))
        # Test whole dict comparison
        self.assertEqual(wrapped_dict,
                         {k : TestWrapper(v) for k, v in test_dict.items()})




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

        self.ioloop_thread_wrapper = resource_client.IOLoopThreadWrapper(self.io_loop)
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

        self.ioloop_thread_wrapper = resource_client.IOLoopThreadWrapper(self.io_loop)
        start_thread_with_cleanup(self, self.ioloop_manager, start_timeout=1)
        self.ioloop_thread_wrapper.default_timeout = 1

        self.default_spec = dict(clients={
            'resource1' : dict(controlled=True),
            'resource2' : dict(controlled=True)},
                                 name='wraptest')
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
        reading = self.DUT.children['resource2'].sensor.int_resource2.get_reading()
        self.assertEqual(reading.value, 17)

    def test_children(self):
        self.assertIs(type(self.DUT.children['resource1']),
                      resource_client.ThreadSafeKATCPClientResourceWrapper)
        self.assertIs(self.DUT.children['resource1'].__subject__,
                      self.resource_container.children['resource1'])

        self.assertIs(type(self.DUT.children['resource2']),
                      resource_client.ThreadSafeKATCPClientResourceWrapper)
        self.assertIs(self.DUT.children['resource2'].__subject__,
                      self.resource_container.children['resource2'])
