
""" This is a demo device server for showing capabilities of twisted based
katcp implementation of a device server
"""

from twisted.internet import reactor
from katcp.tx import DeviceServer, DeviceProtocol
from katcp import Sensor, Message
from twisted.internet.protocol import Factory
from twisted.python import log
from katcp import Message
from katcp.tx.test.testserver import IntSensor, FloatSensor

PORT = 1235 # or 0

import sys

class DemoProtocol(DeviceProtocol):
    def request_foo(self, msg):
        """ This is called when ?foo is called from the other side.
        """
        # send one inform
        self.send_message(Message.inform('foo', 'fine'))
        # return reply
        return Message.reply('foo', 'ok', '1')

    def connectionMade(self):
        """ Called when a connection is made, send out-of-band inform
        """
        self.send_message(Message.inform('out-of-band'))
        DeviceProtocol.connectionMade(self)

class DemoServerFactory(DeviceServer):
    production = True # says whether halt request should stop the reactor
    # (and hence the whole process)

    protocol = DemoProtocol # meaning we're using custom protocol

    def setup_sensors(self):
        """ This is a method that overloaded provides a way to add sensors
        to the device
        """
        self.add_sensor(FloatSensor(Sensor.FLOAT, "float_sensor", "descr",
                                    "milithaum", params=[-1.0, 1.0]))
        self.add_sensor(IntSensor(Sensor.INTEGER, "int_sensor", "descr2",
                               "cows", params=[-100, 100]))


def main():
    factory = DemoServerFactory(PORT, '')
    print factory.start().getHost()
    reactor.run() # run the main twisted reactor

if __name__ == '__main__':
    main()
