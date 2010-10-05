
import sys
from twisted.internet import reactor
from twisted.internet.protocol import ProcessProtocol

class BenchmarkClient(ProcessProtocol):
    def outReceived(self, out):
        print "OUT:", out

    def errReceived(self, err):
        print "ERR:", err

def main(python=sys.executable):
    reactor.spawnProcess(BenchmarkClient(), python,
                         args=[python, 'benchtxclient.py'])
    reactor.run()

if __name__ == '__main__':
    main()
