import time

from old_io import *

from katcp import Sensor

class S(DeviceServer):
    def setup_sensors(self):
        self.add_sensor(Sensor.boolean('asens'))
        pass

s = S('0.0.0.0', 5000)
s.start(timeout=1)
try:
    # s.join(1000000000)
    import IPython ; IPython.embed()
finally:
    s.stop()
