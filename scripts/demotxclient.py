
""" This is a demo client, which cooperate with demotxserver
"""

import sys
from katcp.tx import run_client, ClientKatCPProtocol
from twisted.internet import reactor

class DemoClient(ClientKatCPProtocol):
    def inform_out_of_band(self, msg):
        print "out of band", msg

def got_sensor_value((informs, reply), protocol):
    print informs
    print reply
    reactor.stop()

def got_foo((informs, reply), protocol):
    print informs
    print reply
    protocol.send_request('sensor-value',
                          'int_sensor').addCallback(got_sensor_value, protocol)

def connected(protocol):
    print "Connected"
    protocol.send_request('foo').addCallback(got_foo, protocol)

if __name__ == '__main__':
    # override with options if necessary
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 1235
    run_client(('localhost', port), DemoClient, connected)
    reactor.run()
