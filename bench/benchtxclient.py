
""" This is a benchmark client, which cooperate with demotxserver
"""

import sys
from katcp.tx.core import run_client, ClientKatCP
from twisted.internet import reactor

class DemoClient(ClientKatCP):
    counter = 0
    
    def inform_out_of_band(self, msg):
        print "out of band", msg

def got_sensor_value((informs, reply), protocol):
    protocol.counter += 1
    protocol.send_request('sensor-value', 'int_sensor').addCallback(
        got_sensor_value, protocol)

def connected(protocol):
    print "Connected"
    reactor.callLater(1, f, protocol)
    protocol.send_request('sensor-value', 'int_sensor').addCallback(
        got_sensor_value, protocol)

def f(protocol):
    print protocol.counter
    protocol.counter = 0
    reactor.callLater(1, f, protocol)

if __name__ == '__main__':
    # override with options if necessary
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 1235
    run_client(('localhost', port), DemoClient, connected)
    reactor.run()
