
""" This is a benchmark client, which cooperate with demotxserver
"""

import time
import sys
from optparse import OptionParser
from katcp.tx.core import run_client, ClientKatCP
from twisted.internet import reactor
from util import standard_parser

class DemoClient(ClientKatCP):
    counter = 0
    no_of_sensors = 0
    last = 0

    def inform_sensor_status(self, msg):
        self.last = int(msg.arguments[0])
        self.counter += 1

    def start_sampling(self, _):
        name = 'int_sensor%d' % self.no_of_sensors
        self.no_of_sensors += 1
        self.send_request('sensor-sampling', name, 'period', 1)

    def periodic_check(self):
        print int(time.time() * 1000) - self.last
        print self.counter, self.no_of_sensors
        if (abs(self.counter - self.no_of_sensors * 200) <
            (self.no_of_sensors * 100)):
            self.send_request('add-sensor').addCallback(self.start_sampling)
        self.counter = 0
        reactor.callLater(.2, self.periodic_check)

def connected(protocol):
    reactor.callLater(.2, protocol.periodic_check)
    protocol.send_request('add-sensor').addCallback(protocol.start_sampling)

if __name__ == '__main__':
    parser = standard_parser()
    options, args = parser.parse_args()
    run_client(('localhost', options.port), DemoClient, connected)
    reactor.run()
