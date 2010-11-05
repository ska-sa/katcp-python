#!/usr/bin/env python

from twisted.internet import reactor
from twisted.internet.protocol import ProcessProtocol
from optparse import OptionParser
from twisted.python import log
import re, os, sys, signal

log.startLogging(open("log", "w"), setStdout=False)

BASE_PORT = 1235

class Master(object):
    def __init__(self, no_of_clients, python, servname, scenario):
        self.current_iteration = 1
        self.python = python
        self.servname = servname
        self.max_clients = no_of_clients
        self.count = 0
        self.clients = []
        self.totals = []
        self.current_runs = 0
        self.max_current_runs = 5
        self.scenario = scenario

    def notify_server_lost(self):
        if self.current_iteration != self.max_clients:
            if self.current_runs != self.max_current_runs:
                self.current_runs += 1
            else:
                self.current_runs = 0
                self.current_iteration += 1
            self.clients = []
            self.run()
            print "next run"
        else:
            print self.totals
            reactor.stop()

    def notify_connection_made(self, pid):
        self.clients.append(pid)

    def notify_client_lost(self, info):
        self.no_of_clients -= 1
        self.count += info
        if self.no_of_clients == 0:
            self.totals.append((self.count, self.current_iteration))
            self.count = 0
            os.kill(self.server.transport.pid, signal.SIGTERM)

    def stop(self):
        for pid in self.clients:
            os.kill(pid, signal.SIGTERM)

    def run(self):
        self.no_of_clients = self.current_iteration
        port = str(BASE_PORT + self.current_iteration)
        self.server = BenchmarkServer(self.python, self.current_iteration, self,
                                      port)
        reactor.spawnProcess(self.server, self.python,
                             args=[self.python, self.servname,
                                   '--port', port], env=os.environ)

class BenchmarkClient(ProcessProtocol):
    id = 0

    def __init__(self, master):
        self.master = master
        self.id = BenchmarkClient.id
        BenchmarkClient.id += 1

    def outReceived(self, out):
        getattr(self, 'process_output_' + self.master.scenario)(out)

    def process_output_1(self, out):
        sys.stdout.write('[%d] ' % self.id + out)
        m = re.match('AVG: (\d+), LAST: (\d+), SENSORS: (\d+)', out)
        self.info = int(m.group(1))
        self.sensors.append(int(m.group(3)))
        if len(self.sensors) > 30:
            self.sensors.pop(0)
            for count in self.sensors:
                if abs(count - self.sensors[0]) > 0:
                    break
            else:
                self.master.stop()

    def process_output_2(self, out):
        val = int(out.strip())
        self.sensors.append(val)
        sys.stdout.write('[%d] ' % self.id + out)
        avg = float(sum(self.sensors))/len(self.sensors)
        self.info = avg
        if len(self.sensors) > 30:
            self.sensors.pop(0)
            diff = (max(self.sensors) - min(self.sensors))
            if diff < 0.30*avg:
                self.master.stop()
            else:
                print diff/avg

    def errReceived(self, err):
        sys.stdout.write("ERR: " + err)

    def processEnded(self, status):
        self.master.notify_client_lost(self.info)

    def connectionMade(self):
        self.master.notify_connection_made(self.transport.pid)
        self.sensors = []

class BenchmarkServer(ProcessProtocol):
    def __init__(self, python, no_of_clients, master, port):
        self.no_of_clients = no_of_clients
        self.python        = python
        self.master        = master
        self.port          = port

    def outReceived(self, out):
        if self.master.scenario == '1':
            reactor.spawnProcess(BenchmarkClient(self.master), self.python,
                                 args=[self.python, 'benchtxclient1.py',
                                       '--port', self.port, '--allow-sensor-creation'],
                                 env=os.environ)
            for i in range(self.no_of_clients - 1):
                reactor.spawnProcess(BenchmarkClient(self.master),
                                     self.python,
                                     args=[self.python, 'benchtxclient1.py',
                                           '--port', self.port],
                                     env=os.environ)
        elif self.master.scenario == '2':
            for i in range(self.no_of_clients):
                reactor.spawnProcess(BenchmarkClient(self.master),
                                     self.python,
                                     args=[self.python, 'benchtxclient2.py',
                                           '--port', self.port],
                                     env=os.environ)
        else:
            raise Exception("Unknown scenario %s" % self.master.scenario)

    def processEnded(self, status):
        self.master.notify_server_lost()

    def errReceived(self, err):
        print "ERR:", err

def main(python=sys.executable):
    parser = OptionParser()
    parser.add_option('--no-of-clients', dest='no_of_clients',
                      default=4, help='number of clients', type=int)
    parser.add_option('--tx', dest='twisted',
                      default=False, action='store_true',
                      help='use twisted server')
    parser.add_option('--scenario', default='1',
                      help=('scenario number, 1 or 2'))
    options, args = parser.parse_args()
    if options.twisted:
        servname = 'benchtxserver.py'
    else:
        servname = 'benchserver.py'
    master = Master(options.no_of_clients, python, servname, options.scenario)
    master.run()
    reactor.run()

if __name__ == '__main__':
    main()
