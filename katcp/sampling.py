"""Different sampling strategies as well as reactor for coordinating
   sampling of multiple sensors each with different strategies.
   """

import threading
import time
import logging

logger = logging.getLogger("katcp.sampling")

# pylint: disable-msg=W0142

class SampleStrategy(object):
    """Base class for strategies for sampling sensors."""

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
        """Create a SampleStrategy instance.

           @param inform_callback  Callback to send inform messages with,
                                   used as inform_callback(msg).
           @param sensor  Sensor to sample.
           @param params  Custom sampling parameters.
           """
        self._inform_callback = inform_callback
        self._sensor = sensor
        self._params = params

    @staticmethod
    def get_strategy(strategyName, server, sensor, *params):
        """Factory method to create a suitable strategy object given the
           necessary details.
           FIXME: Reimplement using singleton factory which new strategies
                  can register with. You can probably register lambda
                  functions to create the objects.
           """
        if strategyName not in SampleStrategy.SAMPLING_LOOKUP_REV:
            raise ValueError("Unknown sampling strategy '%s'."
                                " Known strategies are %s."
                                % (strategyName, SampleStrategy.SAMPLING_LOOKUP.values()))

        strategyType = SampleStrategy.SAMPLING_LOOKUP_REV[strategyName]
        if strategyType == SampleStrategy.NONE:
            return SampleNone(server, sensor, *params)
        elif strategyType == SampleStrategy.AUTO:
            return SampleAuto(server, sensor, *params)
        elif strategyType == SampleStrategy.EVENT:
            return SampleEvent(server, sensor, *params)
        elif strategyType == SampleStrategy.DIFFERENTIAL:
            return SampleDifferential(server, sensor, *params)
        elif strategyType == SampleStrategy.PERIOD:
            return SamplePeriod(server, sensor, *params)

    def update(self, sensor):
        """This update method is called whenever the sensor value is set
           so sensor will contain the right info. Note that the strategy
           does not really need to be passed sensor because it already has
           a handle to it but receives it due to the generic observer
           mechanism.
           """
        pass

    def periodic(self, timestamp):
        """This method is called when a period strategy is being configured
           or periodically after that.

           @param self This object.
           @param timestamp is the time at which the next sample was requested
           @return the desired timestamp for the next sample
           """
        pass

    def inform(self):
        """Inform strategy creator of the sensor status."""
        from katcp import Message

        timestamp_ms, status, value = self._sensor.read_formatted()
        self._inform_callback(Message.inform("sensor-status",
                    timestamp_ms, "1", self._sensor.name, status, value))

    def get_sampling(self):
        """FIXME: deprecate this method and rather return the sampling name from
           each strategy. Then we can live without the SAMPLING_LOOKUP. This goes
           hand in hand with the changes to the factory method, ie removing the
           switch and introducing a dynamic mechanism.
           """
        raise NotImplementedError

    def get_sampling_formatted(self):
        """Return the current sampling strategy and parameters.

           The strategy is returned as a string and the values
           in the parameter list are formatted as strings using
           the formatter for this sensor type.
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
    """Sampling strategy implementation which sends updates on any event of
       the sensor.
       """

    def __init__(self, server, sensor, *params):
        SampleStrategy.__init__(self, server, sensor, *params)
        if params:
            raise ValueError("The 'event' strategy takes no parameters.")
        self._lastStatus = None
        self._lastValue = None

    def update(self, sensor):
        if sensor._status != self._lastStatus or sensor._value != self._lastValue:
            self._lastStatus = sensor._status
            self._lastValue = sensor._value
            self.inform()

    def get_sampling(self):
        return SampleStrategy.EVENT


class SampleAuto(SampleStrategy):
    """Sampling strategy implementation which sends updates
       whenever the sensor itself is updated.
       """

    def __init__(self, server, sensor, *params):
        SampleStrategy.__init__(self, server, sensor, *params)
        if params:
            raise ValueError("The 'auto' strategy takes no parameters.")

    def update(self, sensor):
        self.inform()

    def get_sampling(self):
        return SampleStrategy.AUTO


class SampleNone(SampleStrategy):
    """Sampling strategy which never sends any updates."""

    def __init__(self, server, sensor, *params):
        SampleStrategy.__init__(self, server, sensor, *params)
        if params:
            raise ValueError("The 'none' strategy takes no parameters.")

    def get_sampling(self):
        return SampleStrategy.NONE

class SampleDifferential(SampleStrategy):
    """Sampling strategy for integer and float sensors which sends updates only
       when the value has changed by more than some specified threshold, or the
       status changes.
       """

    def __init__(self, server, sensor, *params):
        from katcp import Sensor

        SampleStrategy.__init__(self, server, sensor, *params)
        if len(params) != 1:
            raise ValueError("The 'differential' strategy takes one parameter.")
        if sensor._sensor_type not in (Sensor.INTEGER, Sensor.FLOAT, Sensor.TIMESTAMP):
            raise ValueError("The 'differential' strategy is only valid for float, integer and timestamp sensors.")
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
            self._threshold = int(params[0]) / 1000.0 # convert threshold in ms to s
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive number of milliseconds.")
        self._lastStatus = None
        self._lastValue = None

    def update(self, sensor):
        if sensor._status != self._lastStatus or abs(sensor._value - self._lastValue) > self._threshold:
            self._lastStatus = sensor._status
            self._lastValue = sensor._value
            self.inform()

    def get_sampling(self):
        return SampleStrategy.DIFFERENTIAL


class SamplePeriod(SampleStrategy):
    """Sampling strategy for periodic sampling of any sensor. Note that the
       requested period can be decoupled from the rate at which the sensor changes.
       """

    ## @brief Number of milliseconds in a second (as a float).
    MILLISECOND = 1e3

    def __init__(self, server, sensor, *params):
        SampleStrategy.__init__(self, server, sensor, *params)
        if len(params) != 1:
            raise ValueError("The 'period' strategy takes one parameter.")
        period_ms = int(params[0])
        if period_ms <= 0:
            raise ValueError("The period must be a positive integer in ms.")
        self._period = period_ms / SamplePeriod.MILLISECOND
        self._status = sensor._status
        self._value = sensor._value
        self._nextTime = 0

    def periodic(self, timestamp):
        if timestamp > self._nextTime:
            self._sensor._timestamp = timestamp
            self.inform()
            self._nextTime += self._period
            if self._nextTime < timestamp:
                self._nextTime = timestamp + self._period
        return self._nextTime

    def get_sampling(self):
        return SampleStrategy.PERIOD


class SampleReactor(threading.Thread):
    """This class keeps track of all the sensors and what strategy is currently
       used to sample each one.
       """

    ## @brief Finest granularity of calls to periodic (10ms)
    PERIOD_DELAY = 0.01

    def __init__(self):
        """Create a SampleReactor."""
        super(SampleReactor, self).__init__()
        self._strategies = set()
        self._stopEvent = threading.Event()
        # set daemon True so that the app can stop even if the thread is running
        self.setDaemon(True)

    def add_strategy(self, strategy):
        """Add a sensor strategy to the reactor. Strategies should be removed
           using remove_strategy.

           The new strategy is then attached to the sensor for updates and a
           periodic sample is triggered to schedule the next one.
           """
        self._strategies.add(strategy)
        strategy.attach()
        self.periodic(strategy, time.time())

    def remove_strategy(self, strategy):
        """Remove a strategy from the reactor."""
        strategy.detach()
        self._strategies.remove(strategy)

    @staticmethod
    def periodic(strategy, timestamp):
        """Callback method which is called by the scheduler for the next periodic
           sample. It will schedule the next sample if indicated by the strategy.
           """
        _nextTime = strategy.periodic(timestamp)
        # If required later we could use nextTime to schedule the next call to
        # periodic for this strategy

    def stop(self):
        """Send event to processing thread and wait for it to stop."""
        self._stopEvent.set()

    def run(self):
        """Run the sample reactor."""
        logger.debug("Starting thread %s" % (threading.currentThread().getName()))
        while not self._stopEvent.isSet():
            timestamp = time.time()
            for strategy in self._strategies:
                SampleReactor.periodic(strategy, timestamp)
            self._stopEvent.wait(SampleReactor.PERIOD_DELAY)
        self._stopEvent.clear()
        logger.debug("Stopping thread %s" % (threading.currentThread().getName()))
