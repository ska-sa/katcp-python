
from twisted.internet import reactor
from katcp.txprotocol import KatCP
from twisted.internet.protocol import Factory
from twisted.python import log
from katcp import Message

PORT = 1235 # or 0

import sys

class DemoServer(KatCP):
    def connectionMade(self):
        self.send_message(Message(Message.INFORM, "version", ["demotxserver"]))

    def request_halt(self, msg):
        self.factory.port.stopListening()
        reactor.stop()

def main():
    f = Factory()
    f.protocol = DemoServer
    f.port = reactor.listenTCP(PORT, f)
    print f.port.getHost()
    reactor.run()

if __name__ == '__main__':
    main()
