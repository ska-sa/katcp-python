"""Different sampling strategies as well as reactor for coordinating
   sampling of multiple sensors each with different strategies.
   """
   
class SampleStrategy:
    """Base class for strategies for sampling sensors."""
    
    def __init__(self, server, name):
        self._server = server
        self._name = name
        
    def update(self, timestamp_ms, status, value):
        pass
    
    def periodic(self, timestamp_ms):
        pass
    
    def mass_inform(self, timestamp_ms, status, value):
        self._server.mass_inform(Message.inform("sensor-status",
                    timestamp_ms, "1", self._name, status, value))
    
class SampleEvent(SampleStrategy):
    
    def update(self, timestamp_ms, status, value):
        self.mass_inform(timestamp_ms, status, value)
        
class SampleNone(SampleStrategy):
    
    pass

class SampleDifferential(SampleStrategy):
    
    def __init__(self, server, name, sensor, threshold):
        super(SampleDifferential, self).__init__(server, name)
        assert(sensor._sensor_type in [Sensor.INTEGER, Sensor.FLOAT])
        self._threshold = threshold
        self._lastStatus = None
        self._lastValue = None

    def update(self, timestamp_ms, status, value):
        if status != self._lastStatus or abs(value - self._lastValue) > self._threshold:
            self._status = status
            self._value = value
            self.mass_inform(timestamp_ms, status, value)
        
class SamplePeriodic(SampleStrategy):
    
    def __init__(self, sensor, period_ms):
        super(SamplePeriodic, self).__init__(server, name)
        assert(period_ms > 5)
        self._period_ms = period_ms
        self._status = sensor._status
        self._value = sensor._value
        self._nextTime_ms = 0
        
    def update(self, timestamp_ms, status, value):
        self._status = status
        self._value = value
        
    def periodic(self, timestamp_ms):
        if timeStamp_ms > self._nextTime_ms:
            self.mass_inform(timestamp_ms, status, value)
            self._nextTime_ms += self._period_ms
            if self._nextTime_ms < timestamp_ms:
                self._nextTime_ms = timestamp_ms + self._period_ms
        return self._nextTime_ms
    