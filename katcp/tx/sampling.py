
from twisted.internet import reactor
import time


class SamplingStrategy(object):
    """ Base class for all sampling strategies
    """
    def __init__(self, protocol, sensor):
        self.protocol = protocol
        self.sensor = sensor

    def run(self):
        """ Run the strategy. Override in subclasses.
        """
        raise NotImplementedError("purely abstract base class")

    def cancel(self):
        """ Cancel running strategy. Override in subclasses.
        """
        pass


class PeriodicStrategy(SamplingStrategy):
    next = None

    def _run_once(self):
        self.protocol.send_sensor_status(self.sensor)
        self.next = reactor.callLater(self.period, self._run_once)

    def cancel(self):
        self.next.cancel()

    def run(self, period):
        self.period = float(period)
        self._run_once()


class EventRateStrategy(SamplingStrategy):
    next = None

    def _run_once(self):
        self.update(self.sensor)
        now = self._time()
        wait = self.last_plus_shortest + (self.longest_period -
                                          self.shortest_period) - now
        self.next = reactor.callLater(wait, self._run_once)

    def update(self, sensor):
        now = self._time()
        if now < self.last_plus_shortest:
            return
        self.last_plus_shortest = now + self.shortest_period
        self.inform()

    def cancel(self):
        self.sensor.detach(self)
        self.next.cancel()

    def run(self, shortest_period, longest_period):
        self.shortest_period = float(shortest_period)
        self.longest_period = float(longest_period)
        self.last_plus_shortest = 0
        self._time = time.time
        self._run_once()
        self.sensor.attach(self)


class NoStrategy(SamplingStrategy):
    def run(self):
        pass


class ObserverStrategy(SamplingStrategy):
    """ A common superclass for strategies that watch sensors and take
    actions accordingly
    """
    def run(self):
        self.sensor.attach(self)

    def cancel(self):
        self.sensor.detach(self)


class AutoStrategy(ObserverStrategy):
    def update(self, sensor):
        self.protocol.send_sensor_status(sensor)


class EventStrategy(ObserverStrategy):
    def __init__(self, protocol, sensor):
        ObserverStrategy.__init__(self, protocol, sensor)
        _timestamp, self.status, self.value = sensor.read()

    def update(self, sensor):
        _timestamp, newval, newstatus = sensor.read()
        if self.status != newstatus or self.value != newval:
            self.status = newstatus
            self.value = newval
            self.protocol.send_sensor_status(sensor)


class DifferentialStrategy(ObserverStrategy):
    def __init__(self, protocol, sensor):
        ObserverStrategy.__init__(self, protocol, sensor)
        _timestamp, self.status, self.value = sensor.read()

    def run(self, threshold):
        self.threshold = float(threshold)
        ObserverStrategy.run(self)

    def update(self, sensor):
        _timestamp, newstatus, newval = sensor.read()
        if (self.status != newstatus or
            abs(self.value - newval) > self.threshold):
            self.protocol.send_sensor_status(sensor)
            self.status = newstatus
            self.value = newval
