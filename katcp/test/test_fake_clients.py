# Copyright 2015 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

import unittest2 as unittest
import logging

import tornado.testing

from katcp.inspecting_client import InspectingClientAsync

# module under test
from katcp import fake_clients


class test_FakeInspectingClient(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(test_FakeInspectingClient, self).setUp()
        self.host = 'fake-host'
        self.port = 12345
        self.fake_inspecting_client = fake_clients.get_fake_inspecting_client_instance(
            InspectingClientAsync, self.host, self.port, ioloop=self.io_loop)
        self.fake_inspecting_manager = fake_clients.FakeInspectingClientManager(
            self.fake_inspecting_client)

    @tornado.testing.gen_test
    def test_sensors(self):
        sensor_info = {
            'an-int': ('An integer sensor', 'things', 'integer', 0, 10),
            'a-string' : ('A string sensor', '', 'string'),
        }

        yield self.fake_inspecting_client.connect()
        self.fake_inspecting_manager.add_sensors(sensor_info)
        yield self.fake_inspecting_client.until_not_synced()
        yield self.fake_inspecting_client.until_synced()

        an_int = yield self.fake_inspecting_client.future_get_sensor('an-int')
        self.assertEqual
        a_string = yield self.fake_inspecting_client.future_get_sensor('a-string')
        import IPython ; IPython.embed()
