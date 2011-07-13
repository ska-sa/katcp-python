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
from .core import Message, Sensor, ExcepthookThread

log = logging.getLogger("katcp.sampling")

# pylint: disable-msg=W0142


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
    NONE, AUTO, PERIOD, EVENT, DIFFERENTIAL = range(5)

    ## @brief Mapping from strategy constant to strategy name.
    SAMPLING_LOOKUP = {
        NONE: "none",
        AUTO: "auto",
        PERIOD: "period",
        EVENT: "event",
        DIFFERENTIAL: "differential",
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
        timestamp_ms, status, value = self._sensor.read_formatted()
        self._inform_callback(Message.inform("sensor-status",
                    timestamp_ms, "1", self._sensor.name, status, value))

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


class SampleEvent(SampleStrategy):
    """Strategy which sends updates when the sensor value or status changes.

       This implementation of the event strategy extends the KATCP guidelines
       to allow an optional minimum time between updates (in millseconds) to
       be specified as a parameter. If further sensor updates occur before
       this time has elapsed, no additional events are sent out.
       """

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if len(params) > 1:
            raise ValueError("The 'event' strategy takes one or"
                             " zero parameters.")
        elif len(params) == 1:
            self._minTimeSep = float(params[0]) / 1000.0
        else:
            self._minTimeSep = None
        self._lastStatus = None
        self._lastValue = None
        self._nextTime = None

    def update(self, sensor):
        now = time.time()

        if self._nextTime is not None and (self._nextTime > now):
            return

        _timestamp, status, value = sensor.read()
        if status != self._lastStatus or value != self._lastValue:
            self._lastStatus = status
            self._lastValue = value
            if self._minTimeSep:
                self._nextTime = now + self._minTimeSep
            self.inform()

    def get_sampling(self):
        return SampleStrategy.EVENT

    def attach(self):
        self.update(self._sensor)
        super(SampleEvent, self).attach()


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
            self._threshold = int(params[0]) / 1000.0  # convert threshold to s
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive number"
                                 " of milliseconds.")
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

    ## @brief Number of milliseconds in a second (as a float).
    MILLISECOND = 1e3

    def __init__(self, inform_callback, sensor, *params):
        SampleStrategy.__init__(self, inform_callback, sensor, *params)
        if len(params) != 1:
            raise ValueError("The 'period' strategy takes one parameter.")
        period_ms = int(params[0])
        if period_ms <= 0:
            raise ValueError("The period must be a positive integer in ms.")
        self._period = period_ms / SamplePeriod.MILLISECOND

    def periodic(self, timestamp):
        self.inform()
        return timestamp + self._period

    def get_sampling(self):
        return SampleStrategy.PERIOD


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
    def __init__(self, logger=log):
        super(SampleReactor, self).__init__()
        self._strategies = set()
        self._stopEvent = threading.Event()
        self._wakeEvent = threading.Event()
        self._heap = []
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
        strategy.attach()

        next_time = strategy.periodic(time.time())
        if next_time is not None:
            heapq.heappush(self._heap, (next_time, strategy))
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

        while not self._stopEvent.isSet():
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
                    _push(heap, (next_time, strategy))
                except Exception, e:
                    self._logger.exception(e)
                    # push ten seconds into the future and hope whatever was
                    # wrong sorts itself out
                    _push(heap, (next_time + 10.0, strategy))
            else:
                wake.wait()

        self._stopEvent.clear()
        self._logger.debug("Stopping thread %s" % (_currentThread().getName()))
