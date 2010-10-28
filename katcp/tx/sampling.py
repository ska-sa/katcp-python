
from zope.interface import implements

from twisted.internet import reactor
from twisted.internet.interfaces import IConsumer, IPushProducer

class SamplingStrategy(object):
    """ Base class for all sampling strategies
    """
    implements(IPushProducer)
    producing = True
    
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

    # IPushProducer interface

    def pauseProducing(self):
        self.producing = False
    stopProducing = pauseProducing

    def resumeProducing(self):
        self.producing = True

class PeriodicStrategy(SamplingStrategy):
    next = None

    def _run_once(self):
        if self.producing:
            self.protocol.send_sensor_status(self.sensor)
        self.next = reactor.callLater(self.period, self._run_once)

    def cancel(self):
        self.next.cancel()

    def run(self, period):
        self.period = float(period) / 1000
        self._run_once()

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
        if self.producing:
            self.protocol.send_sensor_status(sensor)

class EventStrategy(ObserverStrategy):
    def __init__(self, protocol, sensor):
        ObserverStrategy.__init__(self, protocol, sensor)
        self.value = sensor.value()
        self.status = sensor._status

    def update(self, sensor):
        newval = sensor.value()
        newstatus = sensor._status
        if self.status != newstatus or self.value != newval:
            if self.producing:
                self.status = newstatus
                self.value = newval
                self.protocol.send_sensor_status(sensor)

class DifferentialStrategy(ObserverStrategy):
    def __init__(self, protocol, sensor):
        ObserverStrategy.__init__(self, protocol, sensor)
        self.value = sensor.value()
        self.status = sensor._status

    def run(self, threshold):
        self.threshold = float(threshold)
        ObserverStrategy.run(self)

    def update(self, sensor):
        newval = sensor.value()
        newstatus = sensor._status
        if (self.status != newstatus or
            abs(self.value - newval) > self.threshold):
            if self.producing:
                self.protocol.send_sensor_status(sensor)
                self.status = newstatus
                self.value = newval
