"""Tests for the nodeman module."""

import unittest
import katcp

class BaseTreeTest(unittest.TestCase):

    def assertSensorValues(self, sensors, values):
        for sensor, value in zip(sensors, values):
            self.assertEqual(sensor.value(), value, "Expected %r to equal %s" % (sensor, value))

    @staticmethod
    def make_sensors(number, stype, params=None):
        """Create a number of sensors of the same type."""
        sensors = []
        for i in range(number):
            sensors.append(katcp.Sensor(stype, "sensor%d" % i,
                "Boolean Sensor %d" % i, "", params=params))
        return sensors


class TestGenericSensorTree(BaseTreeTest):
    # TODO: write tests
    pass


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
        s0, s1, s2, s3 = sensors = self.make_sensors(4, katcp.Sensor.INTEGER, params=[-100, 100])
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

    def test_delayed(self):
        tree = katcp.AggregateSensorTree()
        s0, s1, s2, s3 = sensors = self.make_sensors(4, katcp.Sensor.INTEGER, params=[-100, 100])
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
