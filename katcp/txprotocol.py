
from twisted.protocols.basic import LineReceiver
from twisted.internet.defer import Deferred
from twisted.internet import reactor
from twisted.internet.protocol import ClientCreator
from twisted.internet.protocol import Factory
from katcp import MessageParser, Message

class UnhandledMessage(Exception):
    pass

class NoQuerriesProcessed(Exception):
    pass

class WrongQueryOrder(Exception):
    pass

class UnknownType(Exception):
    pass

def run_client((host, port), ClientClass, connection_made):
    def client_connected(protocol):
        d = Deferred()
        protocol.setup_done = d
        return d
    
    cc = ClientCreator(reactor, ClientClass)
    d = cc.connectTCP(host, port)
    if ClientClass.needs_setup:
        d.addCallback(client_connected)
    if connection_made is not None:
        d.addCallback(connection_made)
    return d

class KatCP(LineReceiver):
    delimiter = '\n'
    
    def __init__(self):
        self.queries = []
        self.parser = MessageParser()

    def send_request(self, name, *args):
        d = Deferred()
        self.send_message(Message.request(name, *args))
        self.queries.append((name, d, []))
        return d

    def lineReceived(self, line):
        msg = self.parser.parse(line)
        if msg.mtype == msg.INFORM:
            self.handle_inform(msg)
        elif msg.mtype == msg.REPLY:
            self.handle_reply(msg)
        elif msg.mtype == msg.REQUEST:
            self.handle_request(msg)
        else:
            assert False # this could never happen since parser should complain

    # some default informs
    def inform_version(self, msg):
        self.version = msg.arguments[0]

    def inform_build_state(self, msg):
        self.build_state = msg.arguments[0]

    def inform_disconnect(self, args):
        pass # unnecessary, we have a callback on loseConnection

    def handle_inform(self, msg):
        # if we have a request being processed, store all the informs
        # in a list of stuff to process
        name = msg.name
        name = name.replace('-', '_')
        meth = getattr(self, 'inform_' + name, None)
        if meth is not None:
            meth(msg)
        elif self.queries:
            name, d, queue = self.queries[0]
            if name != msg.name:
                raise WrongQueryOrder(name, msg.name)
            queue.append(msg) # unespace?
        else:
            raise UnhandledMessage(msg)

    def handle_request(self, msg):
        name = msg.name
        name = name.replace('-', '_')
        getattr(self, 'request_' + name, self.request_unknown)(msg)

    def handle_reply(self, msg):
        if not self.queries:
            raise NoQuerriesProcessed()
        name, d, queue = self.queries[0]
        if name != msg.name:
            d.errback("Wrong request order")
        self.queries.pop(0) # hopefully it's not large
        d.callback((queue, msg))

    def send_message(self, msg):
        # just serialize a message
        self.transport.write(str(msg) + self.delimiter)

    # ---------- some default responses ------------------

    def request_halt(self, msg):
        self.transport.write(str(Message(Message.REPLY, "halt",
                                         ["ok"])) + self.delimiter)
        self.transport.loseConnection()

    def request_unknown(self, msg):
        self.send_message(Message.reply(msg.name, "invalid",
                                        "Unknown request."))

class ClientKatCP(KatCP):
    needs_setup = False

class ProxyKatCP(KatCP):
    needs_setup = True
    
    def got_sensor_list(self, (informs, reply)):
        lgt = int(reply.arguments[1])
        assert lgt == len(informs)
        self.sensors = []
        for inform in informs:
            # XXXX
            self.sensors.append(None)
        self.setup_done.callback(self)
    
    def connectionMade(self):
        d = self.send_request('sensor-list')
        d.addCallback(self.got_sensor_list)

class ServerFactory(Factory):
    def __init__(self):
        self.sensors = {}
        self.setup_sensors()

    def add_sensor(self, sensor):
        self.sensors[sensor.name] = sensor
    
    def setup_sensors(self):
        pass # override to provide some sensors

class ServerKatCP(KatCP):
    def request_sensor_value(self, msg):
        if not msg.arguments:
            for name, sensor in sorted(self.factory.sensors.iteritems()):
                timestamp_ms, status, value = sensor.read_formatted()
                self.send_message(Message.inform(msg.name, timestamp_ms, "1",
                                                 name, status, value))
            self.send_message(Message.reply(msg.name, "ok",
                                            len(self.factory.sensors)))
            return
        try:
            sensor = self.factory.sensors[msg.arguments[0]]
        except KeyError:
            self.send_message(Message.reply(msg.name, "fail",
                                            "Unknown sensor name"))
        else:
            timestamp_ms, status, value = sensor.read_formatted()
            self.send_message(Message.inform(msg.name, timestamp_ms, "1",
                                             sensor.name, status, value))
            self.send_message(Message.reply(msg.name, "ok", "1"))

    def request_sensor_list(self, msg):
        for name, sensor in sorted(self.factory.sensors.iteritems()):
            self.send_message(Message.inform(msg.name, name, sensor.description,
                                             sensor.units, sensor.stype,
                                             *sensor.formatted_params))
        self.send_message(Message.reply(msg.name, "ok",
                                        len(self.factory.sensors)))

    def request_help(self, msg):
        # for now
        self.send_message(Message.reply(msg.name, "ok", "0"))
