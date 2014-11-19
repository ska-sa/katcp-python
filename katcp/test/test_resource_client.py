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

import tornado
import mock

from katcp.testutils import DeviceTestServer, start_thread_with_cleanup

from katcp import resource, inspecting_client
# module under test
from katcp import resource_client



class test_KATCPResourceClientRequest(unittest.TestCase):
    def setUp(self):
        self.mock_client = mock.Mock()
        self.DUT = resource_client.KATCPResourceClientRequest(
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

class test_KATCPResourceClient(tornado.testing.AsyncTestCase):
    def test_init(self):
        resource_spec = dict(
            name='testdev',
            address=('testhost', 12345),
            controlled=True)
        DUT = resource_client.KATCPResourceClient(dict(resource_spec))
        self.assertEqual(DUT.address, resource_spec['address'])
        self.assertEqual(DUT.state, 'disconnected')
        self.assertEqual(DUT.name, resource_spec['name'])
        self.assertEqual(DUT.parent, None)
        self.assertEqual(DUT.children, {})
        self.assertEqual(DUT.controlled, True)

        # Now try with a parent and no control
        resource_spec['controlled'] = False
        parent = mock.Mock()
        DUT = resource_client.KATCPResourceClient(
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
            DUT = resource_client.KATCPResourceClient(dict(resource_spec))
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


class test_KATCPResourceClient_Integration(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(test_KATCPResourceClient_Integration, self).setUp()
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server)
        self.host, self.port = self.server.bind_address
        self.default_resource_spec = dict(
            name='thething',
            address=self.server.bind_address,
            controlled=True)

    @tornado.gen.coroutine
    def _get_DUT_and_sync(self, resource_spec):
        DUT = resource_client.KATCPResourceClient(self.default_resource_spec)
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
        pass
