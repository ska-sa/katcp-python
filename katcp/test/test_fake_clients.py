# Copyright 2015 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details
from __future__ import division

import unittest2 as unittest
import logging

import tornado.testing
import tornado.gen

from katcp import Sensor
from katcp.kattypes import request, return_reply, Int, Float
from katcp.testutils import SensorComparisonMixin
from katcp.inspecting_client import InspectingClientAsync

# module under test
from katcp import fake_clients


class test_FakeInspectingClient(tornado.testing.AsyncTestCase,
                                SensorComparisonMixin):
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
        s_description, s_units = sensor_info['an-int'][0:2]
        self.assert_sensor_equal_description(an_int, dict(
            name='an-int', type=Sensor.INTEGER, description='An integer sensor',
            params=[0, 10]))
        a_string = yield self.fake_inspecting_client.future_get_sensor('a-string')
        self.assert_sensor_equal_description(a_string, dict(
            name='a-string', type=Sensor.STRING, description='A string sensor',
            params=[]))

    @tornado.testing.gen_test
    def test_request_handlers(self):
        class TestHandlers(object):
            @request(Int(), Int())
            @return_reply(Int())
            def request_add_test(self, req, a, b):
                req.inform(a*2, b*3)
                return ('ok', a + b)

            @request(Int(), Int())
            @return_reply(Float())
            @tornado.gen.coroutine
            def request_async_divide(self, req, a, b):
                req.inform(a/2, b/10)
                req.inform('polony-is-real meat')
                raise tornado.gen.Return( ('ok', a / b) )

        test_handlers = TestHandlers()

        self.fake_inspecting_manager.add_request_handlers_object(test_handlers)
        reply, informs = yield self.fake_inspecting_client.simple_request(
            'add-test', 1, 5, mid='123')
        self.assertEqual(len(informs), 1)
        self.assertEqual(str(informs[0]), '#add-test[123] 2 15')
        self.assertEqual(str(reply), '!add-test[123] ok 6')
        reply, informs = yield self.fake_inspecting_client.simple_request(
            'async-divide', 7, 2, mid='112')
        self.assertEqual(len(informs), 2)
        self.assertEqual(str(informs[0]), '#async-divide[112] {} {}'
                         .format(7/2, 2/10))
        self.assertEqual(str(informs[1]), '#async-divide[112] polony-is-real\\_meat')

