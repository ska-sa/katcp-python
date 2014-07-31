# sampling.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Different sampling strategies as well as reactor for coordinating
   sampling of multiple sensors each with different strategies.
   """

import threading
import time
import logging
import heapq
import Queue
import os

from functools import partial
from .core import Message, Sensor, ExcepthookThread, SEC_TO_MS_FAC, MS_TO_SEC_FAC


log = logging.getLogger("katcp.sampling")


# pylint: disable-msg=W0142

def format_inform_v4(sensor_name, timestamp, status, value):
    timestamp = int(float(timestamp) * SEC_TO_MS_FAC)
    return Message.inform(
        "sensor-status", timestamp, "1", sensor_name, status, value)

def format_inform_v5(sensor_name, timestamp, status, value):
    return Message.inform(
        "sensor-status", timestamp, "1", sensor_name, status, value)


class SampleStrategy(object):
    """Base class for strategies for sampling sensors.

    Parameters
    ----------
    inform_callback : callable
        Callback to send inform messages with,
        used as inform_callback(msg).
    sensor : Sensor object
        Sensor to sample.
    params : list of objects
        Custom sampling parameters.
    """

    # Sampling strategy constants
    NONE, AUTO, PERIOD, EVENT, DIFFERENTIAL, EVENT_RATE, DIFFERENTIAL_RATE = range(7)

    ## @brief Mapping from strategy constant to strategy name.
    SAMPLING_LOOKUP = {
        NONE: "none",
        AUTO: "auto",
        PERIOD: "period",
        EVENT: "event",
        DIFFERENTIAL: "differential",
        EVENT_RATE: "event-rate",
        DIFFERENTIAL_RATE: "differential-rate",
    }

    # SAMPLING_LOOKUP not found by pylint
    #
    # pylint: disable-msg = E0602

    ## @brief Mapping from strategy name to strategy constant.
    SAMPLING_LOOKUP_REV = dict((v, k) for k, v in SAMPLING_LOOKUP.items())

    # pylint: enable-msg = E0602

    def __init__(self, inform_callback, sensor, *params):
        self._inform_callback = inform_callback
        self._sensor = sensor
        self._params = params

    @classmethod
    def get_strategy(cls, strategyName, inform_callback, sensor, *params):
        """Factory method to create a strategy object.

        Parameters
        ----------
        inform_callback : callable
            Callback to send inform messages with,
            used as inform_callback(msg).
        sensor : Sensor object
            Sensor to sample.
        params : list of objects
            Custom sampling parameters.

        Returns
        -------
        strategy : SampleStrategy object
            The created sampling strategy.
        """
        if strategyName not in cls.SAMPLING_LOOKUP_REV:
            raise ValueError("Unknown sampling strategy '%s'."
                                " Known strategies are %s."
                                % (strategyName, cls.SAMPLING_LOOKUP.values()))

        strategyType = cls.SAMPLING_LOOKUP_REV[strategyName]
        if strategyType == cls.NONE:
            return SampleNone(inform_callback, sensor, *params)
        elif strategyType == cls.AUTO:
            return SampleAuto(inform_callback, sensor, *params)
        elif strategyType == cls.EVENT:
            return SampleEvent(inform_callback, sensor, *params)
        elif strategyType == cls.DIFFERENTIAL:
            return SampleDifferential(inform_callback, sensor, *params)
        elif strategyType == cls.PERIOD:
            return SamplePeriod(inform_callback, sensor, *params)
        elif strategyType == cls.EVENT_RATE:
            return SampleEventRate(inform_callback, sensor, *params)
        elif strategyType == cls.DIFFERENTIAL_RATE:
            return SampleDifferentialRate(inform_callback, sensor, *params)

    def update(self, sensor):
        """Callback used by the sensor's notify method.

        This update method is called whenever the sensor value is set
        so sensor will contain the right info. Note that the strategy
        does not really need to be passed sensor because it already has
        a handle to it but receives it due to the generic observer
        mechanism.

        Sub-classes should override this method or :meth:`periodic` to
        provide the necessary sampling strategy.

        Parameters
        ----------
        sensor : Sensor object
            The sensor which was just updated.
        """
        pass

    def periodic(self, timestamp):
        """This method is called when a period strategy is being configured
           or periodically after that.

        Sub-classes should override this method or :meth:`update` to
        provide the necessary sampling strategy.

        Parameters
        ----------
        timestamp : float in seconds
            The time at which the next sample was requested.

        Returns
        -------
        next_timestamp : float in seconds
            The desired timestamp for the next sample.
        """
        pass

    def inform(self):
        """Inform strategy creator of the sensor status."""
        timestamp, status, value = self._sensor.read_formatted()
        self._inform_callback(self._sensor.name, timestamp, status, value)

    def set_new_period_callback(self, new_period_callback):
        """Set a function that will be called whenever a new period callback needs to be set

        Arguments
        ---------

        new_period_callback(strategy, next_time) -- Callback function that takes a strategy
            to be re-periodeded as parameter.

        It is epxected that the reactor will not remove other periodic entries
        that were previously created, so the period() update logic should
        suppress spurious updates.
        """

        self._new_period_callback = partial(new_period_callback, self)

    def get_sampling(self):
        """Return the Strategy constant for this sampling strategy.

        Sub-classes should implement this method and return the
        appropriate constant.

        Returns
        -------
        strategy : Strategy constant
            The strategy type constant for this strategy.
        """
        raise NotImplementedError

    def get_sampling_formatted(self):
        """Return the current sampling strategy and parameters.

        The strategy is returned as a string and the values
        in the parameter list are formatted as strings using
        the formatter for this sensor type.

        Returns
        -------
        strategy_name : string
            KATCP name for the strategy.
        params : list of strings
            KATCP formatted parameters for the strategy.
        """
        strategy = self.get_sampling()
        strategy = self.SAMPLING_LOOKUP[strategy]
        params = [str(p) for p in self._params]
        return strategy, params

    def attach(self):
        """Attach strategy to its sensor."""
        self._sensor.attach(self)

    def detach(self):
        """Detach strategy from its sensor."""
        self._sensor.detach(self)


class SampleAuto(SampleStrategy):
    """Strategy which sends updates whenever the sensor itself is updated."""

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if params:
            raise ValueError("The 'auto' strategy takes no parameters.")

    def update(self, sensor):
        self.inform()

    def get_sampling(self):
        return SampleStrategy.AUTO

    def attach(self):
        self.update(self._sensor)
        super(SampleAuto, self).attach()


class SampleNone(SampleStrategy):
    """Sampling strategy which never sends any updates."""

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if params:
            raise ValueError("The 'none' strategy takes no parameters.")

    def get_sampling(self):
        return SampleStrategy.NONE


class SampleDifferential(SampleStrategy):
    """Differential sampling strategy for integer and float sensors.

    Sends updates only when the value has changed by more than some
    specified threshold, or the status changes.
    """
    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if len(params) != 1:
            raise ValueError("The 'differential' strategy takes"
                             " one parameter.")
        if sensor._sensor_type not in (Sensor.INTEGER, Sensor.FLOAT,
                                       Sensor.TIMESTAMP):
            raise ValueError("The 'differential' strategy is only valid for"
                             " float, integer and timestamp sensors.")
        if sensor._sensor_type == Sensor.INTEGER:
            self._threshold = int(params[0])
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive integer.")
        elif sensor._sensor_type == Sensor.FLOAT:
            self._threshold = float(params[0])
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive float.")
        else:
            # _sensor_type must be Sensor.TIMESTAMP

            # There is a potential snafu here if katcpv4 server is used, since
            # the timestamp sensor type should be in milliseconds. For now, just
            # ignore this eventuality, and fix if anyone actually needs this
            self._threshold = float(params[0])
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive number"
                                 " of seconds.")
        self._lastStatus = None
        self._lastValue = None

    def update(self, sensor):
        _timestamp, status, value = sensor.read()
        if status != self._lastStatus or \
                abs(value - self._lastValue) > self._threshold:
            self._lastStatus = status
            self._lastValue = value
            self.inform()

    def get_sampling(self):
        return SampleStrategy.DIFFERENTIAL

    def attach(self):
        self.update(self._sensor)
        super(SampleDifferential, self).attach()

class SamplePeriod(SampleStrategy):
    """Periodic sampling strategy.

    For periodic sampling of any sensor.
    """

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if len(params) != 1:
            raise ValueError("The 'period' strategy takes one parameter. "
                             "Parameters passed: %r, in pid : %s" % (params, os.getpid()))
        period = float(params[0])
        if period <= 0:
            raise ValueError("The period must be a positive float in seconds. "
                             "Parameters passed: %r, in pid : %s" % (params,os.getpid()))
        self._period = period

    def periodic(self, timestamp):
        self.inform()
        return timestamp + self._period

    def get_sampling(self):
        return SampleStrategy.PERIOD


class SampleEventRate(SampleStrategy):
    """Event rate sampling strategy.

    Report the sensor value whenever it changes or if more than
    longest_period seconds have passed since the last reported
    update. However, do not report the value if less than
    shortest_period seconds have passed since the last reported
    update.
    """

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if len(params) != 2:
            raise ValueError("The 'event-rate' strategy takes two parameters.")
        shortest_period = float(params[0])
        longest_period = float(params[1])
        # Last sensor status / value, used to determine if it has changed
        self._last_sv = (None, None)
        self._next_periodic = 1e99

        if not 0 <= shortest_period <= longest_period:
            raise ValueError("The shortest and longest periods must"
                             " satisfy 0 <= shortest_period <= longest_period")
        self._shortest_period = shortest_period
        self._longest_period = longest_period
        # don't send updates until timestamp _not_before
        self._not_before = 0
        # time between _not_before and next required update
        self._not_after_delta = (self._longest_period - self._shortest_period)
        self._time = time.time

    def update(self, sensor, now=None):
        if now is None:
            now = self._time()

        _, status, value = sensor.read()
        last_s, last_v = self._last_sv
        sensor_changed = status != last_s or value != last_v

        if now < self._not_before:
            if self._next_periodic != self._not_before and sensor_changed:
                # If you get an AttributeError here it's because
                # set_new_period_callback() should have been called
                self._next_periodic = self._not_before
                self._new_period_callback(self._not_before)
            return

        past_longest = now >= self._not_before + self._not_after_delta

        if past_longest or sensor_changed:
            self._not_before = now + self._shortest_period
            self._last_sv = (status, value)
            self.inform()

    def periodic(self, timestamp):
        self.update(self._sensor, now=timestamp)
        np = self._next_periodic = self._not_before + self._not_after_delta
        # Don't bother with a next update if it is going to be beyond the
        # approximate age of the universe
        return np if np < 4.32329886e17 else None

    def get_sampling(self):
        return SampleStrategy.EVENT_RATE

    def attach(self):
        self.update(self._sensor)
        super(SampleEventRate, self).attach()


class SampleEvent(SampleEventRate):
    """
    Strategy which sends updates when the sensor value or status changes.

    This implementation of the event strategy extends the KATCP guidelines
    to allow an optional minimum time between updates (in millseconds) to
    be specified as a parameter. If further sensor updates occur before
    this time has elapsed, no additional events are sent out.
    """

    # Since SampleEvent is just a special case of SampleEventRate, we use
    # SampleEventRate with the appropriate default values to implement
    # SampleEvent

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if len(params) > 0:
            raise ValueError("The 'event' strategy takes no parameters.")
        super(SampleEvent, self).__init__(inform_callback, sensor, 0, 1e99)
        # Fix up the parameters so we don't see the extra parameters that were
        # passed to SampleEventRate
        self._params = params

    def get_sampling(self):
        return SampleStrategy.EVENT

class SampleDifferentialRate(SampleStrategy):
    """Event rate sampling strategy.

    Report the value whenever it changes by more than `difference` from the last
    reported value or if more than longest-period seconds have passed since the
    last reported update. However, do not report the value until shortest-period
    seconds have passed since the last reported update. The behaviour if
    shortest-period is greater than longest-period is undefined. May only be
    implemented for float and integer sensors.
    """

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if len(params) != 3:
            raise ValueError("The 'differential-rate' strategy takes three parameters.")
        difference = params[0]
        if sensor.stype ==  'integer':
            difference = int(difference)
        elif sensor.stype == 'float':
            difference = float(difference)
        else:
            raise ValueError('The differential-rate strategy can only be defined '
                             'for integer or float sensors')
        shortest_period = float(params[1])
        longest_period = float(params[2])
        # Last sensor status / value, used to determine if it has changed
        self._last_sv = (None, None)
        self._next_periodic = 1e99

        if not 0 <= shortest_period <= longest_period:
            raise ValueError("The shortest and longest periods must"
                             " satisfy 0 <= shortest_period <= longest_period")
        self._shortest_period = shortest_period
        self._longest_period = longest_period
        self.difference = difference
        # don't send updates until timestamp _not_before
        self._not_before = 0
        # time between _not_before and next required update
        self._not_after_delta = (self._longest_period - self._shortest_period)
        self._time = time.time

    def update(self, sensor, now=None):
        if now is None:
            now = self._time()

        _, status, value = sensor.read()
        last_s, last_v = self._last_sv
        sensor_changed = (status != last_s or
                          abs(value - last_v) > self.difference)

        if now < self._not_before:
            if self._next_periodic != self._not_before and sensor_changed:
                # If you get an AttributeError here it's because
                # set_new_period_callback() should have been called
                self._next_periodic = self._not_before
                self._new_period_callback(self._not_before)
            return

        past_longest = now >= self._not_before + self._not_after_delta

        if past_longest or sensor_changed:
            self._not_before = now + self._shortest_period
            self._last_sv = (status, value)
            self.inform()

    def periodic(self, timestamp):
        self.update(self._sensor, now=timestamp)
        np = self._next_periodic = self._not_before + self._not_after_delta
        # Don't bother with a next update if it is going to be beyond the
        # approximate age of the universe
        return np if np < 4.32329886e17 else None

    def get_sampling(self):
        return SampleStrategy.DIFFERENTIAL_RATE

    def attach(self):
        self.update(self._sensor)
        super(SampleDifferentialRate, self).attach()

class SampleReactor(ExcepthookThread):
    """SampleReactor manages sampling strategies.

    This class keeps track of all the sensors and what strategy
    is currently used to sample each one.  It also provides a
    thread that calls periodic sampling strategies as needed.

    Parameters
    ----------
    logger : logging.Logger object
        Python logger to write logs to.
    """
    def __init__(self, logger=log, excepthook=None):
        super(SampleReactor, self).__init__(excepthook=excepthook)
        self._strategies = set()
        self._stopEvent = threading.Event()
        self._wakeEvent = threading.Event()
        self._heap = []
        self._removal_events = Queue.Queue()
        self._adding_events = Queue.Queue()
        self._logger = logger
        # set daemon True so that the app can stop even if the thread
        # is running
        self.setDaemon(True)

    def add_strategy(self, strategy):
        """Add a sensor strategy to the reactor.

        Strategies should be removed using :meth:`remove_strategy`.

        The new strategy is then attached to the sensor for updates and a
        periodic sample is triggered to schedule the next one.

        Parameters
        ----------
        strategy : SampleStrategy object
            The sampling strategy to add to the reactor.
        """
        self._strategies.add(strategy)
        strategy.set_new_period_callback(self.adjust_strategy_update_time)
        strategy.attach()

        next_time = strategy.periodic(time.time())
        if next_time is not None:
            self._adding_events.put((next_time, strategy))
            self._wakeEvent.set()

    def adjust_strategy_update_time(self, strategy, next_time):
        """Called by a strategy if it needs to have a periodic update time adjusted"""
        if next_time is not None:
            self._adding_events.put((next_time, strategy))
            self._wakeEvent.set()

    def remove_strategy(self, strategy):
        """Remove a strategy from the reactor.

        Strategies are added with :meth:`add_strategy`.

        Parameters
        ----------
        strategy : SampleStrategy object
            The sampling strategy to remove from the reactor.
        """
        strategy.detach()
        self._strategies.remove(strategy)
        self._removal_events.put(strategy)
        self._wakeEvent.set()


    def stop(self):
        """Send event to processing thread and wait for it to stop."""
        self._stopEvent.set()
        self._wakeEvent.set()

    def run(self):
        """Run the sample reactor."""
        self._logger.debug("Starting thread %s" %
                           (threading.currentThread().getName()))
        heap = self._heap
        wake = self._wakeEvent

        # save globals so that the thread can run cleanly
        # even while Python is setting module globals to
        # None.
        _time = time.time
        _currentThread = threading.currentThread
        _push = heapq.heappush
        _pop = heapq.heappop
        self._heapify = heapq.heapify

        while not self._stopEvent.isSet():
            # Push new events into the heap
            while True:
                try:
                    _push(heap, self._adding_events.get(block=False))
                except Queue.Empty:
                    break
            self._remove_dead_events()
            wake.clear()
            if heap:
                next_time, strategy = _pop(heap)
                if strategy not in self._strategies:
                    continue

                wake.wait(next_time - _time())
                if wake.isSet():
                    _push(heap, (next_time, strategy))
                    continue

                try:
                    next_time = strategy.periodic(next_time)
                    if next_time is not None:
                        _push(heap, (next_time, strategy))
                except Exception, e:
                    self._logger.exception(e)
                    # push ten seconds into the future and hope whatever was
                    # wrong sorts itself out
                    _push(heap, (next_time + 10.0, strategy))
            else:
                wake.wait()

        self._stopEvent.clear()
        self._logger.debug("Stopped thread %s" % (_currentThread().getName()))

    def _remove_dead_events(self):
        """Remove event from event heap to prevent memory leaks caused by
        far-future-dated sampling events"""
        # Find strateg(ies) in sampling heap, set to (None, None) so that it
        # will sort to the top and re-heapify. Next run through the reactor loop
        # should discard the item.

        # XX TODO O(n), but oh well. Also happens in the sampling event loop, so
        # may affect sampling accuracy while strategies are being set by
        # increasing the latency of the loop. Might need to use a different data
        # structure in the future, but it should also be fixed if we use the
        # twisted reactor :)

        heap = self._heap
        removals = []
        while True:
            try:
                removals.append(self._removal_events.get_nowait())
            except Queue.Empty:
                break
        if not removals:
            return

        removals = set(removals)
        for i in range(len(heap)):
            if heap[i][1] in removals:
                heap[i] = (None, None)

        self._heapify(self._heap)
