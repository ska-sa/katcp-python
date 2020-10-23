# Copyright 2015 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import copy

from builtins import object

import tornado.gen
import tornado.testing

# module under test
from katcp import Sensor, fake_clients, resource_client
from katcp.inspecting_client import InspectingClientAsync
from katcp.kattypes import Float, Int, request, return_reply
from katcp.resource import escape_name
from katcp.testutils import SensorComparisonMixin


class test_FakeInspectingClient(tornado.testing.AsyncTestCase,
                                SensorComparisonMixin):
    def setUp(self):
        super(test_FakeInspectingClient, self).setUp()
        self.host = 'fake-host'
        self.port = 12345
        self.fake_inspecting_client, self.fake_inspecting_manager = (
            fake_clients.fake_inspecting_client_factory(
            InspectingClientAsync, {}, self.host, self.port, ioloop=self.io_loop) )

    @tornado.testing.gen_test
    def test_sensors(self):
        sensor_info = {
            'an-int': ('An integer sensor', 'things', 'integer', 0, 10),
            'a-string' : ('A string sensor', '', 'string'),
            'a-discrete': (b'A discrete sensor', b'', 'discrete', b'one', b'two',
                           b'three'),
            'a-timestamp': (b'A timestamp sensor', b'', 'timestamp'),
            'a-float': (b'A float sensor', b'', 'float', b'-123.4', b'123.4'),
            'a-boolean': (b'A boolean sensor', b'', 'boolean'),
            'an-address': (b'An address sensor', b'', 'address'),
        }

        yield self.fake_inspecting_client.connect()
        self.fake_inspecting_manager.add_sensors(sensor_info)
        yield self.fake_inspecting_client.until_not_synced()
        yield self.fake_inspecting_client.until_synced()

        an_int = yield self.fake_inspecting_client.future_get_sensor('an-int')
        s_description, s_units = sensor_info['an-int'][0:2]
        self.assert_sensor_equal_description(
            an_int,
            {
                'name': 'an-int',
                'type': Sensor.INTEGER,
                'description': 'An integer sensor',
                'params': [0, 10]
            }
        )
        a_string = yield self.fake_inspecting_client.future_get_sensor('a-string')
        self.assert_sensor_equal_description(
            a_string,
            {
                'name': 'a-string',
                'type': Sensor.STRING,
                'description': 'A string sensor',
                'params': []
            }
        )
        a_discrete = yield self.fake_inspecting_client.future_get_sensor("a-discrete")
        self.assert_sensor_equal_description(
            a_discrete,
            {
                "name": "a-discrete",
                "type": Sensor.DISCRETE,
                "description": "A discrete sensor",
                "params": ["one", "two", "three"],
            },
        )
        a_timestamp = yield self.fake_inspecting_client.future_get_sensor("a-timestamp")
        self.assert_sensor_equal_description(
            a_timestamp,
            {
                "name": "a-timestamp",
                "type": Sensor.TIMESTAMP,
                "description": "A timestamp sensor",
            },
        )
        a_float = yield self.fake_inspecting_client.future_get_sensor("a-float")
        self.assert_sensor_equal_description(
            a_float,
            {
                "name": "a-float",
                "type": Sensor.FLOAT,
                "description": "A float sensor",
                "params": [-123.4, 123.4],
            },
        )
        a_boolean = yield self.fake_inspecting_client.future_get_sensor("a-boolean")
        self.assert_sensor_equal_description(
            a_boolean,
            {
                "name": "a-boolean",
                "type": Sensor.BOOLEAN,
                "description": "A boolean sensor",
            },
        )
        an_address = yield self.fake_inspecting_client.future_get_sensor("an-address")
        self.assert_sensor_equal_description(
            an_address,
            {
                "name": "an-address",
                "type": Sensor.ADDRESS,
                "description": "An address sensor",
            },
        )

class FakeHandlers(object):
    @request(Int(), Int())
    @return_reply(Int())
    def request_add_test(self, req, a, b):
        "Add numbers"
        req.inform(a*2, b*3)
        return ('ok', a + b)

    @request(Int(), Int())
    @return_reply(Float())
    @tornado.gen.coroutine
    def request_async_divide(self, req, a, b):
        "Divide numbers"
        req.inform(a/2, b/10)
        req.inform('polony-is-real meat')
        raise tornado.gen.Return( ('ok', a / b) )


    @tornado.testing.gen_test
    def test_request_handlers(self):
        yield self.fake_inspecting_client.connect()
        test_handlers = FakeHandlers()

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


class test_FakeKATCPClientResource(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(test_FakeKATCPClientResource, self).setUp()
        self.resource_spec = dict(
            name='testdev',
            description='resource for testing',
            address=('testhost', 12345),
            controlled=True)

    @tornado.testing.gen_test
    def test_sensors(self):
        DUT, DUT_manager = fake_clients.fake_KATCP_client_resource_factory(
            resource_client.KATCPClientResource, {}, dict(self.resource_spec))
        DUT.start()
        yield DUT.until_synced()
        self.assertEqual(len(DUT.sensor), 0)
        sensor_info = {
            'an-int': ('An integer sensor', 'things', 'integer', 0, 10),
            'a-string' : ('A string sensor', '', 'string'),
        }
        DUT_manager.add_sensors(sensor_info)
        yield DUT.until_state('syncing')
        yield DUT.until_synced()
        self.assertEqual(len(DUT.sensor), 2)
        self.assertEqual(sorted(dict.keys(DUT.sensor)), ['a_string', 'an_int'])

class test_FakeKATCPClientResourceContainer(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(test_FakeKATCPClientResourceContainer, self).setUp()
        self.resources_spec = dict(clients={
            'client1': dict(address=('client1-addr', 1234), controlled=True),
            'client-2': dict(address=('client2-addr', 1235), controlled=False),
            'another-client': dict(address=('another-addr', 1231), controlled=True)},
                                   name='test-container',
                                   description='container for testing')
    @tornado.testing.gen_test
    def test_sensors(self):
        DUT, DUT_manager = fake_clients.fake_KATCP_client_resource_container_factory(
            resource_client.KATCPClientResourceContainer, {},
            copy.deepcopy(self.resources_spec))
        sensor_info = {
            'an-int': ('An integer sensor', 'things', 'integer', 0, 10),
            'a-string' : ('A string sensor', '', 'string'),
        }
        DUT.start()
        self.assertEqual(len(DUT.sensor), 0)
        DUT_manager.add_sensors('client_2', sensor_info)
        yield DUT.until_any_child_in_state('syncing')
        yield DUT.until_synced()
        self.assertEqual(sorted(dict.keys(DUT.sensor)),
                         ['client_2_a_string', 'client_2_an_int'])
        client1_sensor_info = dict(sensor_info)
        client1_sensor_info['uniquely-1'] = ('Unique client2 sensor', '', 'boolean')
        DUT_manager.add_sensors('client1', client1_sensor_info)
        yield DUT.until_any_child_in_state('syncing')
        yield DUT.until_synced()
        # TODO test value setting also

    @tornado.testing.gen_test
    def test_requests(self):
        DUT, DUT_manager = fake_clients.fake_KATCP_client_resource_container_factory(
            resource_client.KATCPClientResourceContainer, {},
            copy.deepcopy(self.resources_spec))
        DUT.start()
        yield DUT.until_any_child_in_state('syncing')
        yield DUT.until_synced()
        # Check the standard requests as implemented by
        # fake_clients.FakeInspectingClientManager. Expect this test to break if
        # FakeInspectingClientManager implements more requests.
        standard_requests = ('help', 'sensor_list')
        controlled_clients = [
            escape_name(c_name) for c_name, c in self.resources_spec['clients'].items()
            if c.get('controlled')]
        desired_requests = sorted(
            escape_name(c)+'_'+r for c in controlled_clients for r in standard_requests)
        self.assertEqual(sorted(DUT.req.keys()), desired_requests)

        # Now add some requests
        DUT_manager.add_request_handlers_object('client1', FakeHandlers())
        yield DUT.until_any_child_in_state('syncing')
        yield DUT.until_synced()

        desired_requests += ['client1_add_test', 'client1_async_divide']
        desired_requests.sort()
        self.assertEqual(sorted(DUT.req.keys()), desired_requests)
        reply, informs = yield DUT.req.client1_add_test(1, 5, mid='1233')
        self.assertEqual(len(informs), 1)
        self.assertEqual(str(informs[0]), '#add-test[1233] 2 15')
        self.assertEqual(str(reply), '!add-test[1233] ok 6')
