
from twisted.protocols.basic import LineReceiver
from twisted.internet.defer import Deferred
from katcp import MessageParser

class UnhandledMessage(Exception):
    pass

class NoQuerriesProcessed(Exception):
    pass

class WrongQueryOrder(Exception):
    pass

class UnknownType(Exception):
    pass

class KatCP(LineReceiver):
    delimiter = '\n'

    def __init__(self, *args, **kwds):
        self.queries = []
        self.parser = MessageParser()

    def do_halt(self):
        d = Deferred()
        self.transport.write("?halt\n")
        self.queries.append(('halt', d, []))
        return d

    def do_help(self):
        self.transport.write("?help\n")
        d = Deferred()
        self.queries.append(('help', d, [])) # hopefully it would be only 1
        return d

    def lineReceived(self, line):
        tp = line[0]
        rest = line[1:]
        if tp == '#':
            self.handle_inform(rest)
        elif tp == '!':
            self.handle_reply(rest)
        else:
            raise UnknownType(tp)

    # some default informs
    def inform_version(self, args):
        self.version = args[0]

    def inform_build_state(self, args):
        self.build_state = args[0]

    def inform_disconnect(self, args):
        pass # unnecessary, we have a callback on looseConnection

    def handle_inform(self, msg):
        parts = msg.split(" ")
        # if we have a request being processed, store all the informs
        # in a list of stuff to process
        name = parts[0]
        name = name.replace('-', '_')
        meth = getattr(self, 'inform_' + name, None)
        if meth is not None:
            meth(parts[1:])
        elif self.queries:
            name, d, queue = self.queries[0]
            if name != parts[0]:
                raise WrongQueryOrder(name, parts[0])
            queue.append(parts[1:]) # unespace?
        else:
            raise UnhandledMessage(msg)

    def handle_reply(self, msg):
        parts = msg.split(" ")
        if not self.queries:
            raise NoQuerriesProcessed()
        name, d, args = self.queries[0]
        if name != parts[0]:
            d.errback("Wrong request order")
        self.queries.pop(0) # hopefully it's not large
        d.callback(args)
