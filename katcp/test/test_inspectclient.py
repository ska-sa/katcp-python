
import time
import unittest2 as unittest
import tornado

from katcp import Sensor, Message
from katcp.testutils import (DeviceTestServer, start_thread_with_cleanup,
                             DeviceTestSensor)
from katcp.inspecting_client import (InspectingClientBlocking,
                                     InspectingClientAsync)


class TestInspectingClientBlocking(unittest.TestCase):

    def setUp(self):
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)

        host, port = self.server.bind_address

        self.client = InspectingClientBlocking(host, port)
        self.client.connect()
        self.assertTrue(self.client.wait_synced(timeout=1))

    def tearDown(self):
        if self.client:
            self.client.close()

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

    def test_sensor_added(self):
        """Test a sensor being added."""
        self.vn = 1

        self.client.set_sensor_added_callback(self._inc_verify_number)

        sensor = DeviceTestSensor(Sensor.INTEGER, "another.int",
                                  "An Integer.",
                                  "count", [-5, 5], timestamp=time.time(),
                                  status=Sensor.NOMINAL, value=3)
        self.server.add_sensor(sensor)
        self.server.mass_inform(Message.inform('interface-change'))

        self.client.wait_synced()
        counter = 10
        while counter and 'another.int' not in self.client.sensors:
            time.sleep(1)
            counter -= 1

        self.assertIn('another.int', self.client.sensors)
        self.assertGreater(self.vn, 1)

    def test_request_added(self):
        """Test a request being added."""

        self.vn = 1
        self.client.set_request_added_callback(self._inc_verify_number)

        def request_sparkling_new(self, req, msg):
            """A new command."""
            return Message.reply(msg.name, "ok", "bling1", "bling2")

        self.server.request_sparkling_new = request_sparkling_new
        self.server._request_handlers['sparkling_new'] = request_sparkling_new
        self.server.mass_inform(Message.inform('interface-change'))

        self.client.wait_synced()
        self.client.get_request('sparkling_new')
        counter = 10
        while counter and 'sparkling_new' not in self.client.requests:
            time.sleep(1)
            counter -= 1

        self.assertIn('sparkling_new', self.client.requests)


class TestInspectingClientAsync(tornado.testing.AsyncTestCase):

    def setUp(self):
        super(TestInspectingClientAsync, self).setUp()
        self.server = DeviceTestServer('', 0)
        start_thread_with_cleanup(self, self.server, start_timeout=1)
        self.host, self.port = self.server.bind_address

        self.client = InspectingClientAsync(self.host, self.port,
                                            io_loop=self.io_loop)
        self.io_loop.add_callback(self.client.connect)

    def tearDown(self):
        if self.client:
            self.client.close()

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
                                       io_loop=self.io_loop,
                                       full_inspection=False)

        yield client.connect()
        yield client.until_connected()
        sync = yield client.until_synced()
        self.assertEquals(len(client.sensors), 0)

        self.assertEquals(len(client.requests), 0)
        self.assertTrue(sync)
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
#
