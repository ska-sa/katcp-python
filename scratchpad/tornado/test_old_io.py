import time
import Queue

from old_io import *

from katcp import Sensor

class S(DeviceServer):
    def setup_sensors(self):
        self.add_sensor(Sensor.boolean('asens'))
        pass

restart_queue = Queue.Queue()

s = S('0.0.0.0', 5000)
s.set_restart_queue(restart_queue)
s.start(timeout=1)
try:
    while True:
        try:
            restart_device = restart_queue.get(timeout=100000000)
        except Queue.Empty:
            continue
        restart_device.stop()
        restart_device.join()
        restart_device.start()
finally:
    s.stop()
