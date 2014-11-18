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

from katcp import resource
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

    @tornado.testing.gen_test(timeout=0.5)
    def test_requests(self):
        DUT = resource_client.KATCPResourceClient(self.default_resource_spec)
        DUT.start()
        yield DUT.until_state('synced')
        # Check that all the test-device requests are listed
        self.assertEqual(sorted(DUT.req),
                         sorted(n.replace('-', '_')
                                for n in self.server.request_names))
        
