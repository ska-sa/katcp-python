# Copyright 2010 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

""" This is a benchmark device server using twisted katcp impl
"""
from __future__ import absolute_import, division, print_function

import sys

from katcp import Message, Sensor
from katcp.tx import DeviceProtocol, DeviceServer
from katcp.tx.test.testserver import FloatSensor, IntSensor
from twisted.internet import reactor
from twisted.internet.protocol import Factory
from twisted.python import log
from util import standard_parser


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
    print(factory.start().getHost())
    sys.stdout.flush()
    reactor.run() # run the main twisted reactor

if __name__ == '__main__':
    main()
