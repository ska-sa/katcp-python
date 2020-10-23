# Copyright 2010 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

""" This is a benchmark client for scenario 2, which cooperate with
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

    def got_sensor_value(self, v):
        self.counter += 1
        self.send_request('sensor-value', 'int_sensor').addCallback(
            self.got_sensor_value)

    def periodic_check(self):
        print(self.counter)
        sys.stdout.flush()
        reactor.callLater(TIMEOUT, self.periodic_check)
        self.counter = 0

def connected(protocol, options):
    protocol.send_request('sensor-value', 'int_sensor').addCallback(
        protocol.got_sensor_value)
    reactor.callLater(TIMEOUT, protocol.periodic_check)

def not_connected(failure):
    print(failure, file=sys.stderr)
    print("Exiting", file=sys.stderr)
    reactor.stop()

if __name__ == '__main__':
    parser = standard_parser()
    options, args = parser.parse_args()
    run_client(('localhost', options.port), DemoClient, connected,
               (options,), not_connected)
    reactor.run()
