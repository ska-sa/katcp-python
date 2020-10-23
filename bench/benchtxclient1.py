# Copyright 2010 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

""" This is a benchmark client for scenario 1, which cooperate with
benchtxserver or benchserver
"""
from __future__ import absolute_import, division, print_function

import sys
import time
from optparse import OptionParser

from katcp.tx.core import ClientKatCP, run_client
from twisted.internet import reactor
from twisted.python import log
from util import standard_parser

TIMEOUT = 0.2

class DemoClient(ClientKatCP):
    counter = 0
    no_of_sensors = 0 # number of sensors sampled
    last = 0
    sampling = False

    def __init__(self, *args, **kwds):
        ClientKatCP.__init__(self, *args, **kwds)
        self.avg = []

    def inform_sensor_status(self, msg):
        self.last = int(msg.arguments[0])
        self.counter += 1

    def start_sampling(self, _):
        name = 'int_sensor%d' % self.no_of_sensors
        self.no_of_sensors += 1
        self.send_request('sensor-sampling', name, 'period', 1)
        self.sampling = False

    def sample_next_sensor(self):
        if not self.sampling:
            self.sampling = True
            self.send_request('sensor-list').addCallback(self.check_sensor_list)

    def check_sensor_list(self, informs_reply):
        informs, reply = informs_reply
        sensor_no = len(informs)
        if self.no_of_sensors < sensor_no:
            self.start_sampling(None)
        elif self.options.allow_sensor_creation:
            self.send_request('add-sensor').addCallback(self.start_sampling)

    def periodic_check(self):
        self.avg.append(self.counter)
        if len(self.avg) > 10:
            self.avg.pop(0)
        print("AVG: %d, LAST: %d, SENSORS: %d" % (
            sum(self.avg)/len(self.avg), self.counter, self.no_of_sensors))
        sys.stdout.flush()
        if (not self.options.allow_sensor_creation or
            (abs(self.counter - self.no_of_sensors * 200) <=
            (self.no_of_sensors * 100))):
            self.sample_next_sensor()
        self.counter = 0
        reactor.callLater(TIMEOUT, self.periodic_check)

    def connectionLost(self, failure):
        print("Connection lost, exiting", file=sys.stderr)
        if reactor.running:
            reactor.stop()

def connected(protocol, options):
    protocol.options = options
    reactor.callLater(TIMEOUT, protocol.periodic_check)
    protocol.sample_next_sensor()

def not_connected(failure):
    print(failure, file=sys.stderr)
    print("Exiting", file=sys.stderr)
    reactor.stop()

if __name__ == '__main__':
    parser = standard_parser()
    parser.add_option('--allow-sensor-creation', dest='allow_sensor_creation',
                      default=False, action='store_true')
    options, args = parser.parse_args()
    run_client(('localhost', options.port), DemoClient, connected,
               (options,), not_connected)
    reactor.run()
