"""Tests for the nodeman module."""

from __future__ import division, print_function, absolute_import

import unittest
import katcp


class BaseTreeTest(unittest.TestCase):

    def assertSensorValues(self, sensors, values):
        for sensor, value in zip(sensors, values):
            self.assertEqual(sensor.value(), value, "Expected %r to equal %s" %
                             (sensor, value))

    @staticmethod
    def make_sensors(number, stype, params=None):
        """Create a number of sensors of the same type."""
        sensors = []
        for i in range(number):
            sensors.append(katcp.Sensor(stype, "sensor%d" % i,
                "Boolean Sensor %d" % i, "", params=params))
        return sensors


class RecordingTree(katcp.GenericSensorTree):

    def __init__(self):
        super(RecordingTree, self).__init__()
        self.calls = []

    def recalculate(self, parent, updates):
        self.calls.append((parent, updates))
        # dummy update of the parent sensor value
        parent.set_value(parent.value())


class TestGenericSensorTree(BaseTreeTest):

    def setUp(self):
        self.tree = RecordingTree()
        self.calls = self.tree.calls
        self.sensor1 = katcp.Sensor(int, "sensor1", "First sensor", "",
                                    [0, 100])
        self.sensor2 = katcp.Sensor(int, "sensor2", "Second sensor", "",
                                    [0, 100])
        self.sensor3 = katcp.Sensor(int, "sensor3", "Third sensor", "",
                                    [0, 100])

    def test_add_links(self):
        self.tree.add_links(self.sensor1, [self.sensor2])
        self.assertEqual(self.calls, [(self.sensor1, [self.sensor2])])
        self.assertEqual(self.sensor1._observers, set([self.tree]))
        self.assertEqual(self.sensor2._observers, set([self.tree]))
        self.assertEqual(self.tree.children(self.sensor1), set([self.sensor2]))
        self.assertEqual(self.tree.children(self.sensor2), set())
        self.assertEqual(self.tree.parents(self.sensor1), set())
        self.assertEqual(self.tree.parents(self.sensor2), set([self.sensor1]))

    def test_remove_links(self):
        self.tree.add_links(self.sensor1, [self.sensor2])
        self.tree.remove_links(self.sensor1, [self.sensor2])
        self.assertEqual(self.calls, [
            (self.sensor1, [self.sensor2]),
            (self.sensor1, [self.sensor2]),
        ])
        self.assertEqual(self.sensor1._observers, set())
        self.assertEqual(self.sensor2._observers, set())
        self.assertRaises(ValueError, self.tree.children, self.sensor1)
        self.assertRaises(ValueError, self.tree.children, self.sensor2)
        self.assertRaises(ValueError, self.tree.parents, self.sensor1)
        self.assertRaises(ValueError, self.tree.parents, self.sensor2)

    def test_concurrent_update(self):
        # this test adds a new parent to a sensor while the sensor
        # is being updated. If it fails the call to set_value at the
        # end will raise 'RuntimeError: Set changed size during iteration'.
        self.tree.add_links(self.sensor1, [self.sensor2])

        class LinkOnUpdate(object):
            def update(linker, sensor, reading):
                self.tree.add_links(self.sensor3, [self.sensor2])

        linker = LinkOnUpdate()
        self.sensor1.attach(linker)
        self.sensor2.set_value(3)
        self.assertEqual(self.tree.parents(self.sensor2),
                         set([self.sensor1, self.sensor3]))


class TestBooleanSensorTree(BaseTreeTest):

    def test_basic(self):
        tree = katcp.BooleanSensorTree()
        s0, s1, s2, s3 = sensors = self.make_sensors(4, katcp.Sensor.BOOLEAN)
        tree.add(s0, s1)
        tree.add(s0, s2)
        tree.add(s1, s3)
        self.assertSensorValues(sensors, (False, False, False, False))

        s3.set_value(True)
        self.assertSensorValues(sensors, (False, True, False, True))
        s2.set_value(True)
        self.assertSensorValues(sensors, (True, True, True, True))
        s3.set_value(False)
        self.assertSensorValues(sensors, (False, False, True, False))
        s2.set_value(False)
        self.assertSensorValues(sensors, (False, False, False, False))

        s3.set_value(True)
        tree.remove(s0, s2)
        self.assertSensorValues(sensors, (True, True, False, True))

        s3.set_value(False)
        tree.remove(s1, s3)
        self.assertSensorValues(sensors, (True, True, False, False))

        s1.set_value(False)
        tree.remove(s0, s1)
        self.assertSensorValues(sensors, (True, False, False, False))


class TestAggregateSensorTree(BaseTreeTest):

    @staticmethod
    def _add_rule(parent, children):
        """Set a parent sensor to the sum of its children."""
        parent.set_value(sum(child.value() for child in children))

    def test_basic(self):
        tree = katcp.AggregateSensorTree()
        s0, s1, s2, s3 = sensors = self.make_sensors(4, katcp.Sensor.INTEGER,
                                                     params=[-100, 100])
        tree.add(s0, self._add_rule, (s1,))
        tree.add(s1, self._add_rule, (s2, s3))
        self.assertSensorValues(sensors, (0, 0, 0, 0))

        s2.set_value(1)
        self.assertSensorValues(sensors, (1, 1, 1, 0))
        s3.set_value(2)
        self.assertSensorValues(sensors, (3, 3, 1, 2))

        tree.remove(s1)
        self.assertSensorValues(sensors, (3, 3, 1, 2))

        s1.set_value(5)
        self.assertSensorValues(sensors, (5, 5, 1, 2))

        tree.remove(s0)
        self.assertSensorValues(sensors, (5, 5, 1, 2))

        # check s0 is no longer updated
        s1.set_value(7)
        self.assertSensorValues(sensors, (5, 7, 1, 2))

    def test_adding_and_removing_sensors(self):
        tree = katcp.AggregateSensorTree()
        s0, s1 = sensors = self.make_sensors(2, katcp.Sensor.INTEGER,
                                                     params=[-100, 100])
        # add s0 aggregate sensor and s1 child sensor pair with rule
        tree.add(s0, self._add_rule, (s1,))
        self.assertSensorValues(sensors, (0, 0))

        # check s0 aggregate sensor is updated
        s1.set_value(1)
        self.assertSensorValues(sensors, (1, 1))

        # remove s1 child sensor
        tree.remove_links(s0, [s1])

        # add s1 child sensor again
        tree.add_links(s0, [s1])
        self.assertSensorValues(sensors, (1, 1))

        # check s0 aggregate sensor is updated
        s1.set_value(2)
        self.assertSensorValues(sensors, (2, 2))

    def test_double_add(self):
        tree = katcp.AggregateSensorTree()
        s0, s1 = self.make_sensors(2, katcp.Sensor.INTEGER, params=[-100, 100])
        tree.add(s0, self._add_rule, (s1,))
        self.assertRaises(ValueError, tree.add, s0, self._add_rule, (s1,))

    def test_double_remove(self):
        tree = katcp.AggregateSensorTree()
        s0, s1 = self.make_sensors(2, katcp.Sensor.INTEGER, params=[-100, 100])
        tree.add(s0, self._add_rule, (s1,))
        tree.remove(s0)
        self.assertRaises(ValueError, tree.remove, s0)

    def test_removing_and_adding_registered_sensors(self):
        """Test removing the links of some or all child sensors of an aggregate
        sensor, and adding them to the aggregate sensor again.  The sensors are
        registered after add_delayed."""
        tree = katcp.AggregateSensorTree()
        s0, s1, s2 = sensors = self.make_sensors(3, katcp.Sensor.INTEGER,
                                                     params=[-100, 100])
        tree.add_delayed(s0, self._add_rule, (s1.name, s2.name))
        self.assertSensorValues(sensors, (0, 0, 0))
        tree.register_sensor(s1)
        tree.register_sensor(s2)
        s1.set_value(9)
        s2.set_value(9)
        self.assertSensorValues(sensors, (18, 9, 9))
        # remove s1
        tree.deregister_sensor(s1)
        self.assertSensorValues(sensors, (9, 9, 9))
        s1.set_value(100)
        self.assertSensorValues(sensors, (9, 100, 9))
        s2.set_value(22)
        self.assertSensorValues(sensors, (9, 100, 22))
        # add s1
        tree.register_sensor(s1)
        s1.set_value(10)
        self.assertSensorValues(sensors, (32, 10, 22))
        # remove s1 and s2
        tree.deregister_sensor(s1)
        tree.deregister_sensor(s2)
        s1.set_value(100)
        s2.set_value(100)
        self.assertSensorValues(sensors, (22, 100, 100))
        # add s1 and s2
        tree.register_sensor(s1)
        tree.register_sensor(s2)
        s1.set_value(1)
        s2.set_value(1)
        self.assertSensorValues(sensors, (2, 1, 1))

    def test_delayed(self):
        tree = katcp.AggregateSensorTree()
        s0, s1, s2, s3 = sensors = self.make_sensors(4, katcp.Sensor.INTEGER,
                                                     params=[-100, 100])
        tree.add_delayed(s0, self._add_rule, (s1.name,))
        tree.add_delayed(s1, self._add_rule, (s2.name, s3.name))
        self.assertSensorValues(sensors, (0, 0, 0, 0))

        s2.set_value(1)
        s3.set_value(2)
        self.assertSensorValues(sensors, (0, 0, 1, 2))

        tree.register_sensor(s2)
        tree.register_sensor(s3)
        self.assertSensorValues(sensors, (0, 3, 1, 2))

        tree.register_sensor(s1)
        self.assertSensorValues(sensors, (3, 3, 1, 2))
