
from twisted.internet import reactor
from katcp.tx import DeviceServer
from katcp import Sensor
from twisted.internet.protocol import Factory
from twisted.python import log
from katcp import Message
from katcp.tx.test.testserver import IntSensor, FloatSensor

PORT = 1235 # or 0

import sys

class DemoServerFactory(DeviceServer):
    production = True
    
    def setup_sensors(self):
        self.add_sensor(FloatSensor(Sensor.FLOAT, "float_sensor", "descr",
                                    "milithaum", params=[-1.0, 1.0]))
        self.add_sensor(IntSensor(Sensor.INTEGER, "int_sensor", "descr2",
                               "cows", params=[-100, 100]))

def main():
    factory = DemoServerFactory(PORT, '')
    print factory.start().getHost()
    reactor.run()

if __name__ == '__main__':
    main()
