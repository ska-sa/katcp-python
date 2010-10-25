#!/usr/bin/env python

from twisted.internet import reactor
from twisted.internet.protocol import ProcessProtocol
from optparse import OptionParser
from twisted.python import log
import re, os, sys, signal

class Master(object):
    def __init__(self, no_of_clients):
        self.no_of_clients = no_of_clients
        self.count = 0
        self.clients = []

    def notify_server_lost(self):
        reactor.stop()

    def notify_connection_made(self, pid):
        self.clients.append(pid)

    def notify_client_lost(self, info):
        self.no_of_clients -= 1
        self.count += info
        if self.no_of_clients == 0:
            print "Total: %d" % self.count
            os.kill(self.server.transport.pid, signal.SIGTERM)

    def stop(self):
        for pid in self.clients:
            os.kill(pid, signal.SIGTERM)

class BenchmarkClient(ProcessProtocol):
    id = 0

    def __init__(self, master):
        self.master = master
        self.id = BenchmarkClient.id
        BenchmarkClient.id += 1

    def outReceived(self, out):
        sys.stdout.write('[%d] ' % self.id + out)
        m = re.match('AVG: (\d+), LAST: (\d+), SENSORS: (\d+)', out)
        self.info = int(m.group(1))
        self.sensors.append(int(m.group(3)))
        if len(self.sensors) > 20:
            self.sensors.pop(0)
            for count in self.sensors:
                if abs(count - self.sensors[0]) > 0:
                    break
            else:
                self.master.stop()

    def errReceived(self, err):
        sys.stdout.write("ERR: " + err)

    def processEnded(self, status):
        self.master.notify_client_lost(self.info)

    def connectionMade(self):
        self.master.notify_connection_made(self.transport.pid)
        self.sensors = []

class BenchmarkServer(ProcessProtocol):
    def __init__(self, python, options, master):
        self.options = options
        self.python  = python
        self.master  = master

    def outReceived(self, out):
        reactor.spawnProcess(BenchmarkClient(self.master), self.python,
                             args=[self.python, 'benchtxclient.py',
                                   '--port', '1235', '--allow-sensor-creation'])
        for i in range(self.options.no_of_clients - 1):
            reactor.spawnProcess(BenchmarkClient(self.master),
                                 self.python,
                                 args=[self.python, 'benchtxclient.py',
                                       '--port', '1235'])

    def processEnded(self, status):
        self.master.notify_server_lost()

    def errReceived(self, err):
        print "ERR:", err

def main(python=sys.executable):
    parser = OptionParser()
    parser.add_option('--no-of-clients', dest='no_of_clients',
                      default=4, help='number of clients', type=int)
    options, args = parser.parse_args()
    master = Master(options.no_of_clients)
    server = BenchmarkServer(python, options, master)
    master.server = server
    reactor.spawnProcess(server, python,
                         args=[python, 'benchtxserver.py',
                               '--port', '1235']) # or normal
    reactor.run()

if __name__ == '__main__':
    main()
