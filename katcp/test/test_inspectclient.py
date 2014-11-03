
import time
import unittest2 as unittest

import tornado

from katcp import Sensor, Message
from katcp.testutils import (DeviceTestServer, start_thread_with_cleanup,
                             DeviceTestSensor)
from katcp.inspecting_client import (InspectingClientBlocking,
                                     InspectingClientAsync)


class TestICAClass(tornado.testing.AsyncTestCase):

    def setUp(self):
        super(TestICAClass, self).setUp()
        self.client = InspectingClientAsync('', 0, full_inspection=False,
                                            ioloop=self.io_loop)

        self.client.set_sensor_added_callback(self._cb_add)
        self.client.set_sensor_removed_callback(self._cb_rem)

    def _cb_add(self, val):
        """A callback used in the test."""
        self.stop(('add', val))

    def _cb_rem(self, val):
        """A callback used in the test."""
        self.stop(('rem', val))

    def test_util_method_difference_add(self):
        """Test the _difference utility method on add."""

        name = None
        original_keys = ['B', 'C']
        updated_keys = ['B', 'C', 'D']
        self.client._sensors_index = {}
        for sen in original_keys:
            data = {'description': "This is {0}.".format(sen)}
            self.client._update_index('sensor', sen, data)
        added, removed = self.client._difference(original_keys, updated_keys,
                                                 name, 'sensor')
        self.assertIn('D', added)
        self.assertIn('B', self.client.sensors)
        self.assertIn('C', self.client.sensors)
        # Wait for the cb to be called.
        self.assertEqual(self.wait(), ('add', ['D']))

    def test_util_method_difference_rem(self):
        """Test the _difference utility method on remove."""

        name = None
        original_keys = ['A', 'B', 'C']
        updated_keys = ['B', 'C']
        self.client._sensors_index = {}
        for sen in original_keys:
            data = {'description': "This is {0}.".format(sen)}
            self.client._update_index('sensor', sen, data)

        added, removed = self.client._difference(original_keys, updated_keys,
                                                 name, 'sensor')
        self.assertIn('A', removed)
        self.assertNotIn('A', self.client.sensors)
        self.assertIn('B', self.client.sensors)
        self.assertIn('C', self.client.sensors)
        # Wait for the cb to be called.
        self.assertEqual(self.wait(), ('rem', ['A']))

    def test_util_method_difference_rem_named(self):
        """Test the _difference utility method on remove with name set."""

        name = 'A'
        original_keys = ['B', 'C']
        updated_keys = []
        self.client._sensors_index = {}
        for sen in original_keys:
            data = {'description': "This is {0}.".format(sen)}
            self.client._update_index('sensor', sen, data)
        added, removed = self.client._difference(original_keys, updated_keys,
                                                 name, 'sensor')
        self.assertNotIn('A', removed)
        self.assertNotIn('A', self.client.sensors)
        self.assertIn('B', self.client.sensors)
        self.assertIn('C', self.client.sensors)

    def test_util_method_difference_changed(self):
        """Test the _difference utility method on changed."""

        name = None
        original_keys = ['B']
        updated_keys = ['B']

        self.client._sensors_index = {}
        for sen in original_keys:
            data = {'description': "This is {0}.".format(sen), '_changed': True}
            self.client._update_index('sensor', sen, data)

        added, removed = self.client._difference(original_keys, updated_keys,
                                                 name, 'sensor')
        self.assertIn('B', self.client.sensors)
        # Wait for the cb to be called.
        self.assertEqual(self.wait(), ('add', ['B']))

    def test_update_index(self):
        """Test the update_index method."""

        self.client._sensors_index = {}

        data = {'description': "This is {0}.".format('A')}
        self.client._update_index('sensor', 'A', data)

        data = {'description': "This is {0}.".format('B')}
        self.client._update_index('sensor', 'B', data)

        data = {'description': "This is {0}.".format('A')}
        self.client._update_index('sensor', 'A', data)

        data = {'description': "This is new {0}.".format('B')}
        self.client._update_index('sensor', 'B', data)

        self.assertIn('new', self.client._sensors_index['B'].get('description'))
        self.assertFalse(self.client._sensors_index['A'].get('_changed', False))
        self.assertTrue(self.client._sensors_index['B'].get('_changed'))


class TestInspectingClientBlocking(unittest.TestCase):

    def setUp(self):
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)

        host, port = self.server.bind_address

        self.client = InspectingClientBlocking(host, port)
        start_thread_with_cleanup(self, self.client, start_timeout=1)
        self.assertTrue(self.client.wait_synced(timeout=1))

    def test_sensor_access(self):
        """Test access to sensors."""

        # There is on test sensor on this device.
        sensor_name = 'an.int'
        sensor = self.client.get_sensor(sensor_name)
        self.assertEquals(sensor.name, sensor_name)
        self.assertEquals(sensor.stype, 'integer')

        # Unknown sensor requests return a None.
        sensor_name = 'thing.unknown_sensor'
        sensor = self.client.get_sensor(sensor_name)
        self.assertIsNone(sensor)

    def test_request_access(self):
        """Test access to requests."""
        request_name = 'watchdog'
        self.assertIn(request_name, self.client.requests)
        request = self.client.get_request(request_name)
        self.assertEqual(request.name, request_name)
        self.assertTrue(request.description,
                        'Expected an description: got nothing.')
        # Unknown request return a None.
        request_name = 'watchcat'
        self.assertNotIn(request_name, self.client.requests)
        request = self.client.get_request(request_name)
        self.assertIsNone(request)

    def test_simple_request(self):
        """Perform a basic request."""
        reply, informs = self.client.simple_request('help', 'watchdog')
        self.assertIn('ok', str(reply))
        self.assertEquals(len(informs), 1)

    def _inc_verify_number(self, *args, **kwargs):
        self.vn += 1

    def test_sensor_add_remove(self):
        """Test a sensor being added and then remove it."""
        self.vn = 1

        self.client.set_sensor_added_callback(self._inc_verify_number)
        self.client.set_sensor_removed_callback(self._inc_verify_number)

        sensor = DeviceTestSensor(Sensor.INTEGER, "another.int",
                                  "An Integer.",
                                  "count", [-5, 5], timestamp=time.time(),
                                  status=Sensor.NOMINAL, value=3)
        # Add a sensor.
        self.server.add_sensor(sensor)
        self.server.mass_inform(Message.inform('interface-change'))

        self.client.wait_synced()
        counter = 10
        while counter and 'another.int' not in self.client.sensors:
            time.sleep(1)
            counter -= 1

        self.assertIn('another.int', self.client.sensors)
        self.assertGreater(self.vn, 1)

        # Remove a sensor.
        self.server.remove_sensor(sensor)
        self.server.mass_inform(Message.inform('interface-change'))

        self.client.wait_synced()
        counter = 30
        while (counter and
               (self.vn < 2 or 'another.int' in self.client.sensors)):
            time.sleep(1)
            counter -= 1
        self.assertNotIn('another.int', self.client.sensors)
        self.assertGreater(self.vn, 2)

    def test_request_add_remove(self):
        """Test a request being added and then remove it."""

        self.vn = 1
        self.client.set_request_added_callback(self._inc_verify_number)
        self.client.set_request_removed_callback(self._inc_verify_number)

        def request_sparkling_new(self, req, msg):
            """A new command."""
            return Message.reply(msg.name, "ok", "bling1", "bling2")

        # Add a request.
        self.server.request_sparkling_new = request_sparkling_new
        self.server._request_handlers['sparkling_new'] = request_sparkling_new
        self.server.mass_inform(Message.inform('interface-change'))

        self.client.wait_synced()
        self.client.get_request('sparkling_new')
        counter = 10
        while (counter and
               (self.vn < 2 or 'sparkling_new' not in self.client.requests)):
            # The condition for the while is a bit ugly, but it insure
            # consistency for the asserts to follow.
            # Spin while self.vn is less than 2.
            # Spin while sparkling_new not in requests.
            time.sleep(1)
            counter -= 1
        self.assertIn('sparkling_new', self.client.requests)
        self.assertEqual(self.vn, 2)

        # Remove a request.
        self.server.request_sparkling_new = None
        del(self.server._request_handlers['sparkling_new'])
        self.server.mass_inform(Message.inform('interface-change'))

        self.client.wait_synced()
        # self.client.get_request('sparkling_new')
        counter = 30
        while (counter and
               (self.vn < 3 or 'sparkling_new' in self.client.requests)):
            time.sleep(1)
            counter -= 1
        self.assertNotIn('sparkling_new', self.client.requests)
        self.assertEqual(self.vn, 3)


class TestInspectingClientAsync(tornado.testing.AsyncTestCase):

    def setUp(self):
        super(TestInspectingClientAsync, self).setUp()
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)
        self.host, self.port = self.server.bind_address

        self.client = InspectingClientAsync(self.host, self.port,
                                            ioloop=self.io_loop)
        self.io_loop.add_callback(self.client.connect)

    @tornado.testing.gen_test
    def test_sensor(self):
        """Access the sensor with the Async client."""
        sensor_name = 'an.int'
        sensor = yield self.client.future_get_sensor(sensor_name)
        self.assertEquals(sensor.name, sensor_name)
        self.assertEquals(sensor.stype, 'integer')

    @tornado.testing.gen_test
    def test_send_request(self):
        """Very high level test.

        Calling methods to insure they do not raise exception.

        """
        client = InspectingClientAsync(self.host, self.port,
                                       ioloop=self.io_loop,
                                       full_inspection=False)

        yield client.connect()
        yield client.until_connected()
        yield client.until_synced()
        self.assertEquals(len(client.sensors), 0)

        self.assertEquals(len(client.requests), 0)
        self.assertTrue(client.synced)
        self.assertTrue(client.is_connected())
        self.assertTrue(client.connected)
        yield client.simple_request(
            'sensor-sampling', 'an.int', 'period', '1')
        time.sleep(1.1)  # We want time to pass so the sensor sampling happen.
        # Wait for sync and check if the sensor was automaticaly added.
        yield client.until_synced()
        self.assertEquals(len(client.sensors), 1)
        # Get the sensor object and see if it has data.
        sensor = yield client.future_get_sensor('an.int')
        self.assertTrue(sensor.read())
        self.assertEquals(len(client.requests), 0)
