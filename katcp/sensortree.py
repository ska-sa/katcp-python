"""Sensor tree implementation.

A sensor tree is a DAG (directed acyclic graph) of sensor objects
where an edge represents a dependency. E.g.

S1 -> S2
  |
  -> S3 -> S4
        |
S6 -------> S5

is a sensor tree where the value of S3 depends on the values of S4
and S5, the value of S1 depends on S3 and S2 and the value of S6
depends on just S5.

When a sensor is added to the tree, the tree attaches itself to the
sensor's update notification list. A sensor update triggers a recalculation
of the sensor values that depend on it. These value changes may then trigger
further updates.

The acyclic requirement on the graph structure is required to ensure
that the update chain eventually terminates. It is not enforced.

"""

from __future__ import division, print_function, absolute_import

class GenericSensorTree(object):
    """A tree of generic sensors."""
    # TODO: consider adding detection of cycles.
    #       Fastest method is probably maintaining a spanning tree
    #       and checking for back edges. As always, deletions are likely
    #       to be a bit of a pain and require tree shuffling.
    #       Disconnected graphs will also make life a little more difficult.
    def __init__(self):
        # map of child -> set of all parent sensors
        self._child_to_parents = {}
        # map of parent -> set of all child sensors
        self._parent_to_children = {}

    def update(self, sensor, reading):
        """Update callback used by sensors to notify obervers of changes.

        Parameters
        ----------
        sensor : :class:`katcp.Sensor` object
            The sensor whose value has changed.
        reading : (timestamp, status, value) tuple
            Sensor reading as would be returned by sensor.read()

        """
        parents = list(self._child_to_parents[sensor])
        for parent in parents:
            self.recalculate(parent, (sensor,))

    def recalculate(self, parent, updates):
        """Re-calculate the value of parent sensor.

        Sub-classes should override this method and call parent.set_value(...)
        with the new parent sensor value.

        Recalculate is called with a single child sensor when a sensor value
        is updated. It is called by add_links and remove_links with the same
        list of children they were called with when once links have been
        added or removed.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The sensor that needs to be updated.
        updates : sequence of :class:`katcp.Sensor` objects
            The child sensors which triggered the update.

        """
        raise NotImplementedError

    def _add_sensor(self, sensor):
        """Add a new sensor to the tree.

        Parameters
        ----------
        sensor : :class:`katcp.Sensor` object
            New sensor to add to the tree.

        """
        self._parent_to_children[sensor] = set()
        self._child_to_parents[sensor] = set()

    def _remove_sensor(self, sensor):
        """Remove a sensor from the tree.

        Parameters
        ----------
        sensor : :class:`katcp.Sensor` object
            Sensor to remove from the tree.

        """
        del self._parent_to_children[sensor]
        del self._child_to_parents[sensor]

    def add_links(self, parent, children):
        """Create dependency links from parent to child.

        Any sensors not in the tree are added. After all dependency links have
        been created, the parent is recalculated and the tree attaches to any
        sensors it was not yet attached to. Links that already exist are
        ignored.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The sensor that depends on children.
        children : sequence of :class:`katcp.Sensor` objects
            The sensors parent depends on.

        """
        new_sensors = []
        if parent not in self:
            self._add_sensor(parent)
            new_sensors.append(parent)
        for child in children:
            if child not in self:
                self._add_sensor(child)
                new_sensors.append(child)
            self._parent_to_children[parent].add(child)
            self._child_to_parents[child].add(parent)

        self.recalculate(parent, children)
        for sensor in new_sensors:
            sensor.attach(self)

    def remove_links(self, parent, children):
        """Remove dependency links from parent to child.

        Any sensors that have no dependency links are removed from the tree and
        the tree detaches from each sensor removed. After all dependency links
        have been removed the parent is recalculated. Links that don't exist
        are ignored.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The sensor that used to depend on children.
        children : sequence of :class:`katcp.Sensor` objects
            The sensors that parent used to depend on.

        """
        old_sensors = []
        if parent in self:
            for child in children:
                if child not in self:
                    continue
                self._parent_to_children[parent].discard(child)
                self._child_to_parents[child].discard(parent)
                if not self._child_to_parents[child] and \
                        not self._parent_to_children[child]:
                    self._remove_sensor(child)
                    old_sensors.append(child)
            if not self._child_to_parents[parent] and \
                    not self._parent_to_children[parent]:
                self._remove_sensor(parent)
                old_sensors.append(parent)

        for sensor in old_sensors:
            sensor.detach(self)
        self.recalculate(parent, children)

    def children(self, parent):
        """Return set of children of parent.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            Parent whose children to return.

        Returns
        -------
        children : set of :class:`katcp.Sensor` objects
            The child sensors of parent.

        """
        if parent not in self._parent_to_children:
            raise ValueError("Parent sensor %r not in tree." % parent)
        return self._parent_to_children[parent].copy()

    def parents(self, child):
        """Return set of parents of child.

        Parameters
        ----------
        child : :class:`katcp.Sensor` object
            Child whose parents to return.

        Returns
        -------
        parents : set of :class:`katcp.Sensor` objects
            The parent sensors of child.

        """
        if child not in self._child_to_parents:
            raise ValueError("Child sensor %r not in tree." % child)
        return self._child_to_parents[child].copy()

    def __contains__(self, sensor):
        """Return True if sensor is in the tree, False otherwise.

        Parameters
        ----------
        sensor : object
            Sensor to check for in tree. Objects that are not sensors
            cannot appear in the tree and so will return False.

        """
        return sensor in self._parent_to_children


class BooleanSensorTree(GenericSensorTree):
    """A tree of boolean sensors.

    Non-leaf sensors have their values updated to be the logical AND
    of their child nodes.

    Examples
    --------
    >>> from katcp import Sensor, BooleanSensorTree
    >>> tree = BooleanSensorTree()
    >>> sensor1 = Sensor(Sensor.BOOLEAN, "sensor1", "First sensor", "")
    >>> sensor2 = Sensor(Sensor.BOOLEAN, "sensor2", "Second sensor", "")
    >>> tree.add(sensor1, sensor2)
    >>> sensor2.set_value(True)
    >>> sensor1.value()
    >>> sensor2.set_value(False)
    >>> sensor1.value()
    >>> tree.remove(sensor1, sensor2)
    >>> sensor1.value()

    """
    def __init__(self):
        super(BooleanSensorTree, self).__init__()
        # map of parent -> set of child sensors not ok
        # key None points to root nodes
        self._parent_to_not_ok = {}

    def add(self, parent, child):
        """Add a pair of boolean sensors.

        Parent depends on child.

        Parameters
        ----------
        parent : boolean instance of :class:`katcp.Sensor`
            The sensor that depends on child.
        child : boolean instance of :class:`katcp.Sensor`
            The sensor parent depends on.

        """
        if parent not in self:
            if parent.stype != "boolean":
                raise ValueError("Parent sensor %r is not boolean" % child)
            self._parent_to_not_ok[parent] = set()
        if child not in self:
            if child.stype != "boolean":
                raise ValueError("Child sensor %r is not booelan" % child)
            self._parent_to_not_ok[child] = set()
        self.add_links(parent, (child,))

    def remove(self, parent, child):
        """Remove a dependency between parent and child.

        Parameters
        ----------
        parent : boolean instance of :class:`katcp.Sensor`
            The sensor that used to depend on child.
        child : boolean instance of :class:`katcp.Sensor` or None
            The sensor parent used to depend on.

        """
        self.remove_links(parent, (child,))
        if parent not in self and parent in self._parent_to_not_ok:
            del self._parent_to_not_ok[parent]
        if child not in self and child in self._parent_to_not_ok:
            del self._parent_to_not_ok[child]

    def recalculate(self, parent, updates):
        """Re-calculate the value of parent sensor.

        Parent's value is the boolean AND of all child sensors.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The sensor that needs to be updated.
        updates : sequence of :class:`katcp.Sensor` objects
            The child sensors which triggered the update.

        """
        not_ok = self._parent_to_not_ok[parent]
        children = self.children(parent) if parent in self else set()
        for sensor in updates:
            if sensor not in children or sensor.value():
                not_ok.discard(sensor)
            else:
                not_ok.add(sensor)
        parent.set_value(not not_ok)


class AggregateSensorTree(GenericSensorTree):
    """A collection of aggregate sensors.

    Examples
    --------

    Example where sensors are available when rules are added::

    >>> from katcp import Sensor, AggregateSensorTree
    >>> tree = AggregateSensorTree()
    >>> def add_rule(parent, children):
    >>>     parent.set_value(sum(child.value() for child in children))
    >>> sensor1 = Sensor(Sensor.INTEGER, "sensor1", "First sensor", "",
    ...                  [-1000, 1000])
    >>> sensor2 = Sensor(Sensor.INTEGER, "sensor2", "Second sensor", "",
    ...                  [-1000, 1000])
    >>> agg = Sensor(Sensor.INTEGER, "sum", "The total", "", [-2000, 2000])
    >>> tree.add(agg, add_rule, (sensor1, sensor2))
    >>> agg.value()
    >>> sensor1.set_value(1)
    >>> agg.value()
    >>> sensor2.set_value(2)
    >>> agg.value()
    >>> tree.remove(agg)
    >>> agg.value()

    Example where rules need to be added before dependent sensors are
    available::

    >>> from katcp import Sensor, AggregateSensorTree
    >>> tree = AggregateSensorTree()
    >>> def add_rule(parent, children):
    >>>     parent.set_value(sum(child.value() for child in children))
    >>> agg = Sensor(Sensor.INTEGER, "sum", "The total", "", [-2000, 2000])
    >>> tree.add_delayed(agg, add_rule, ("sensor1", "sensor2"))
    >>> agg.value()
    >>> sensor1 = Sensor(Sensor.INTEGER, "sensor1", "First sensor", "",
    ...                  [-1000, 1000])
    >>> sensor1.set_value(5)
    >>> tree.register_sensor(sensor1)
    >>> agg.value() # still 0
    >>> sensor2 = Sensor(Sensor.INTEGER, "sensor2", "Second sensor", "",
    ...                  [-1000, 1000])
    >>> sensor2.set_value(3)
    >>> tree.register_sensor(sensor2)
    >>> agg.value() # now 8

    """
    def __init__(self):
        super(AggregateSensorTree, self).__init__()
        # map of aggregate sensor -> (rule_function, children)
        self._aggregates = {}
        # map of incomplete aggregate sensor -> (rule_function, names, sensors)
        # as sensor are found they are migrated from names to sensors
        self._incomplete_aggregates = {}
        # map of sensor name -> registered sensor
        self._registered_sensors = {}

    def add(self, parent, rule_function, children):
        """Create an aggregation rule.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The aggregate sensor.
        rule_function : f(parent, children)
            Function to update the parent sensor value.
        children : sequence of :class:`katcp.Sensor` objects
            The sensors the aggregate sensor depends on.

        """
        if parent in self._aggregates or parent in self._incomplete_aggregates:
            raise ValueError("Sensor %r already has an aggregate rule "
                             "associated" % parent)
        self._aggregates[parent] = (rule_function, children)
        self.add_links(parent, children)

    def add_delayed(self, parent, rule_function, child_names):
        """Create an aggregation rule before child sensors are present.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The aggregate sensor.
        rule_function : f(parent, children)
            Function to update the parent sensor value.
        child_names : sequence of str
            The names of the sensors the aggregate sensor depends on. These
            sensor must be registered using :meth:`register_sensor` to become
            active.

        """
        if parent in self._aggregates or parent in self._incomplete_aggregates:
            raise ValueError("Sensor %r already has an aggregate rule"
                             " associated" % parent)
        reg = self._registered_sensors
        names = set(name for name in child_names if name not in reg)
        sensors = set(reg[name] for name in child_names if name in reg)
        if names:
            self._incomplete_aggregates[parent] = (rule_function, names,
                                                   sensors)
        else:
            self.add(parent, rule_function, sensors)

    def register_sensor(self, child):
        """Register a sensor required by an aggregate sensor registered with
        add_delayed.

        Parameters
        ----------
        child : :class:`katcp.Sensor` object
            A child sensor required by one or more delayed aggregate sensors.

        """
        child_name = self._get_sensor_reference(child)
        if child_name in self._registered_sensors:
            raise ValueError("Sensor %r already registered with aggregate"
                             " tree" % child)
        self._registered_sensors[child_name] = child
        completed = []
        for parent, (_rule, names, sensors) in \
                self._incomplete_aggregates.iteritems():
            if child_name in names:
                names.remove(child_name)
                sensors.add(child)
                if not names:
                    completed.append(parent)
        for parent in completed:
            rule_function, _names, sensors = \
                self._incomplete_aggregates[parent]
            del self._incomplete_aggregates[parent]
            self.add(parent, rule_function, sensors)

    def deregister_sensor(self, child):
        child_name = self._get_sensor_reference(child)
        if child_name not in self._registered_sensors:
            raise ValueError("Sensor %r not registered with aggregate"
                             " tree" % child_name)
        child = self._registered_sensors[child_name]
        del self._registered_sensors[child_name]
        try:
            parents = self.parents(child)
            for parent in parents:
                if parent in self._incomplete_aggregates:
                    (rule, names, sensors) = self._incomplete_aggregates[parent]
                    names.add(child_name)
                else:
                    (rule, sensors) = self._aggregates[parent]
                    names = set()
                    names.add(child_name)
                sensors.discard(child)
                self._incomplete_aggregates[parent] = (rule, names, sensors)
                self.remove_links(parent, [child])
                self.remove(parent)
                if parent.stype == 'bool':
                    parent.set_value(False)
        except ValueError:
            pass

    def _child_from_reference(self, reference):
        """Returns the child sensor from its reference.

        Parameters
        ----------
        reference : str
            Reference to sensor (typically its name).

        Returns
        -------
        child : :class:`katcp.Sensor` object
            A child sensor linked to one or more aggregate sensors.

        """
        for child in self._child_to_parents:
            if self._get_sensor_reference(child) == reference:
                return child

    def remove(self, parent):
        """Remove an aggregation rule.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The aggregate sensor to remove.

        """
        if parent not in self._aggregates:
            raise ValueError("Sensor %r does not have an aggregate rule "
                             "associated" % parent)
        children = self.children(parent)
        try:
            self.remove_links(parent, children)
        except Exception:
            pass
        del self._aggregates[parent]

    def fetch(self, parent):
        """Retrieve an aggregation rule.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object

        Returns
        -------
        rule_function : f(parent, children)
            Function give to update the parent sensor value.
        children : sequence of :class:`katcp.Sensor` objects
            The sensors the aggregate sensor depends on.

        """
        return self._aggregates[parent]

    def recalculate(self, parent, updates):
        """Re-calculate the value of parent sensor.

        Parent's value is calculated by calling the associate aggregation rule.

        Parameters
        ----------
        parent : :class:`katcp.Sensor` object
            The sensor that needs to be updated.
        updates : sequence of :class:`katcp.Sensor` objects
            The child sensors which triggered the update.

        """
        rule_function, children = self._aggregates[parent]
        rule_function(parent, children)

    def _get_sensor_reference(self, sensor):
        """Returns sensor name as reference for sensors to be registered by.

        Parameters
        ----------
        sensor : :class:`katcp.Sensor` object
            Sensor to refer to.

        Returns
        -------
        reference : str
            Sensor name as reference.

        """
        return sensor.name
