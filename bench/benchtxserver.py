
""" This is a benchmark device server using twisted katcp impl
"""

from twisted.internet import reactor
from katcp.tx import DeviceServer, DeviceProtocol
from katcp import Sensor, Message
from twisted.internet.protocol import Factory
from twisted.python import log
from katcp import Message
from katcp.tx.test.testserver import IntSensor, FloatSensor
from util import standard_parser

import sys

class BenchmarkProtocol(DeviceProtocol):
    def request_add_sensor(self, msg):
        self.factory.add_sensor(Sensor(int,
                                   'int_sensor%d' % len(self.factory.sensors),
                                    'descr', 'unit', params=[-10, 10]))
        return Message.reply('add-sensor', 'ok')


class BenchmarkServer(DeviceServer):
    protocol = BenchmarkProtocol

    def setup_sensors(self):
        pass

def main():
    parser = standard_parser()
    options, args = parser.parse_args()
    factory = BenchmarkServer(options.port, '')
    print factory.start().getHost()
    sys.stdout.flush()
    reactor.run() # run the main twisted reactor

if __name__ == '__main__':
    main()
