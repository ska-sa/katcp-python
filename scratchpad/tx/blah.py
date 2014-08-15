import time

from twisted.internet import reactor, protocol
from twisted.protocols import basic

from crochet import setup
setup()

class FingerProtocol(basic.LineReceiver):
    def lineReceived(self, line):
        self.transport.write('You are terminated, {0}\n'.format(line))
        self.transport.loseConnection()

class FingerFactory(protocol.ServerFactory):
    protocol = FingerProtocol

reactor.listenTCP(1079, FingerFactory())
#reactor.run()
time.sleep(10000000)
