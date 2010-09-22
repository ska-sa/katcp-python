
from twisted.protocols.basic import LineReceiver
from twisted.internet.defer import Deferred
from twisted.internet import reactor
from twisted.internet.protocol import ClientCreator
from twisted.internet.protocol import Factory
from twisted.python import log
from katcp import MessageParser, Message
from katcp.core import FailReply
from katcp.server import DeviceLogger
from katcp.txsampling import (DifferentialStrategy, AutoStrategy,
    EventStrategy, NoStrategy, PeriodicStrategy)
import sys, traceback
import time

class UnhandledMessage(Exception):
    pass

class NoQuerriesProcessed(Exception):
    pass

class WrongQueryOrder(Exception):
    pass

class UnknownType(Exception):
    pass

TB_LIMIT = 20

def run_client((host, port), ClientClass, connection_made):
    cc = ClientCreator(reactor, ClientClass)
    d = cc.connectTCP(host, port)
    if connection_made is not None:
        d.addCallback(connection_made)
    return d

class KatCP(LineReceiver):
    """ A base class for protocol implementation of KatCP on top of twisted
    infrastructure. Specific subclasses provide client and server parts
    (which are not shared).
    """
    
    delimiter = '\n'
    
    def __init__(self):
        self.parser = MessageParser()
        self.queries = []

    def send_request(self, name, *args):
        d = Deferred()
        self.send_message(Message.request(name, *args))
        self.queries.append((name, d, []))
        return d

    def lineReceived(self, line):
        line = line.rstrip("\r")
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
        try:
            rep_msg = getattr(self, 'request_' + name, self.request_unknown)(msg)
            if rep_msg is not None:
                assert isinstance(rep_msg, Message)
                self.send_message(rep_msg)
            # otherwise reply will come at some later point
        except FailReply, fr:
            self.send_message(Message.reply(name, "fail", str(fr)))
        except Exception:
            e_type, e_value, trace = sys.exc_info()
            log.err()
            reason = "\n".join(traceback.format_exception(
                e_type, e_value, trace, TB_LIMIT
                ))
            
            self.send_message(Message.reply(msg.name, "fail", reason))

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

    def connectionLost(self, failure):
        # errback all waiting queries
        for _, d, _ in self.queries:
            d.errback(failure)

class ClientKatCP(KatCP):
    def inform_sensor_status(self, msg):
        self.update_sensor_status(msg)

    def inform_log(self, msg):
        """ Default inform when logging even happens. Ignore by default,
        can be overriden
        """

    def update_sensor_status(self, msg):
        raise NotImplementedError("Override update_sensor_status")

class ServerFactory(Factory):
    def __init__(self):
        self.sensors = {}
        self.setup_sensors()

    def add_sensor(self, sensor):
        self.sensors[sensor.name] = sensor
    
    def setup_sensors(self):
        pass # override to provide some sensors

class TxDeviceProtocol(KatCP):
    SAMPLING_STRATEGIES = {'period'       : PeriodicStrategy,
                           'none'         : NoStrategy,
                           'auto'         : AutoStrategy,
                           'event'        : EventStrategy,
                           'differential' : DifferentialStrategy}
    
    def __init__(self, *args, **kwds):
        KatCP.__init__(self, *args, **kwds)
        self.strategies = {}

    def connectionMade(self):
        """ Called when connection is made. Send default informs - version
        and build data
        """
        self.send_message(Message.inform("version", "txdeviceserver", "0.1"))

    def connectionLost(self, _):
        self.factory.deregister_client(self.transport.client)

    def send_sensor_status(self, sensor):
        timestamp_ms, status, value = sensor.read_formatted()
        self.send_message(Message.inform('sensor-status', timestamp_ms, "1",
                                         sensor.name, status, value))

    def request_sensor_value(self, msg):
        """Request the value of a sensor or sensors.

        A list of sensor values as a sequence of #sensor-value informs.

        Parameters
        ----------
        name : str, optional
            Name of the sensor to poll (the default is to send values for all sensors).

        Informs
        -------
        timestamp : float
            Timestamp of the sensor reading in milliseconds since the Unix epoch.
        count : {1}
            Number of sensors described in this #sensor-value inform. Will always
            be one. It exists to keep this inform compatible with #sensor-status.
        name : str
            Name of the sensor whose value is being reported.
        value : object
            Value of the named sensor. Type depends on the type of the sensor.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the list of values succeeded.
        informs : int
            Number of #sensor-value inform messages sent.

        Examples
        --------
        ::

            ?sensor-value
            #sensor-value 1244631611415.231 1 psu.voltage 4.5
            #sensor-value 1244631611415.200 1 cpu.status off
            ...
            !sensor-value ok 5

            ?sensor-value cpu.power.on
            #sensor-value 1244631611415.231 1 cpu.power.on 0
            !sensor-value ok 1
        """
        if not msg.arguments:
            for name, sensor in sorted(self.factory.sensors.iteritems()):
                timestamp_ms, status, value = sensor.read_formatted()
                self.send_message(Message.inform(msg.name, timestamp_ms, "1",
                                                 name, status, value))
            return Message.reply(msg.name, "ok", len(self.factory.sensors))
        try:
            sensor = self.factory.sensors[msg.arguments[0]]
        except KeyError:
            return Message.reply(msg.name, "fail", "Unknown sensor name")
        else:
            timestamp_ms, status, value = sensor.read_formatted()
            self.send_message(Message.inform(msg.name, timestamp_ms, "1",
                                             sensor.name, status, value))
            return Message.reply(msg.name, "ok", "1")

    def request_sensor_list(self, msg):
        """Request the list of sensors.

        The list of sensors is sent as a sequence of #sensor-list informs.

        Parameters
        ----------
        name : str, optional
            Name of the sensor to list (the default is to list all sensors).

        Informs
        -------
        name : str
            The name of the sensor being described.
        description : str
            Description of the named sensor.
        units : str
            Units for the value of the named sensor.
        type : str
            Type of the named sensor.
        params : list of str, optional
            Additional sensor parameters (type dependent). For integer and float
            sensors the additional parameters are the minimum and maximum sensor
            value. For discrete sensors the additional parameters are the allowed
            values. For all other types no additional parameters are sent.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the sensor list succeeded.
        informs : int
            Number of #sensor-list inform messages sent.

        Examples
        --------
        ::

            ?sensor-list
            #sensor-list psu.voltage PSU\_voltage. V float 0.0 5.0
            #sensor-list cpu.status CPU\_status. \@ discrete on off error
            ...
            !sensor-list ok 5

            ?sensor-list cpu.power.on
            #sensor-list cpu.power.on Whether\_CPU\_hase\_power. \@ boolean
            !sensor-list ok 1
        """
        if msg.arguments:
            xxx
        for name, sensor in sorted(self.factory.sensors.iteritems()):
            self.send_message(Message.inform(msg.name, name, sensor.description,
                                             sensor.units, sensor.stype,
                                             *sensor.formatted_params))
        return Message.reply(msg.name, "ok", len(self.factory.sensors))

    def request_help(self, msg):
        """Return help on the available requests.

        Return a description of the available requests using a seqeunce of #help informs.

        Parameters
        ----------
        request : str, optional
            The name of the request to return help for (the default is to return help for all requests).

        Informs
        -------
        request : str
            The name of a request.
        description : str
            Documentation for the named request.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the help succeeded.
        informs : int
            Number of #help inform messages sent.

        Examples
        --------
        ::

            ?help
            #help halt ...description...
            #help help ...description...
            ...
            !help ok 5

            ?help halt
            #help halt ...description...
            !help ok 1
        """
        if msg.arguments:
            xxxx
        count = 0
        for name in dir(self.__class__):
            item = getattr(self, name)
            if name.startswith('request_') and callable(item):
                sname = name[len('request_'):]
                self.send_message(Message.inform('help', sname, item.__doc__))
                count += 1
        return Message.reply(msg.name, "ok", str(count))

    def request_sensor_sampling(self, msg):
        """Configure or query the way a sensor is sampled.

        Sampled values are reported asynchronously using the #sensor-status
        message.

        Parameters
        ----------
        name : str
            Name of the sensor whose sampling strategy to query or configure.
        strategy : {'none', 'auto', 'event', 'differential', 'period'}, optional
            Type of strategy to use to report the sensor value. The differential
            strategy type may only be used with integer or float sensors.
        params : list of str, optional
            Additional strategy parameters (dependent on the strategy type).
            For the differential strategy, the parameter is an integer or float
            giving the amount by which the sensor value may change before an
            updated value is sent. For the period strategy, the parameter is the
            period to sample at in milliseconds. For the event strategy, an
            optional minimum time between updates in milliseconds may be given.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether the sensor-sampling request succeeded.
        name : str
            Name of the sensor queried or configured.
        strategy : {'none', 'auto', 'event', 'differential', 'period'}
            Name of the new or current sampling strategy for the sensor.
        params : list of str
            Additional strategy parameters (see description under Parameters).

        Examples
        --------
        ::

            ?sensor-sampling cpu.power.on
            !sensor-sampling ok cpu.power.on none

            ?sensor-sampling cpu.power.on period 500
            !sensor-sampling ok cpu.power.on period 500
        """
        if not msg.arguments:
            return Message.reply(msg.name, "fail", "No sensor name given.")
        sensor = self.factory.sensors.get(msg.arguments[0], None)
        if sensor is None:
            return Message.reply(msg.name, "fail", "Unknown sensor name.")
        StrategyClass = self.SAMPLING_STRATEGIES.get(msg.arguments[1], None)
        if StrategyClass is None:
            return Message.reply(msg.name, "fail", "Unknown strategy name.")
        # stop the previous strategy
        try:
            self.strategies[sensor.name].cancel()
        except KeyError:
            pass
        strategy = StrategyClass(self, sensor)
        strategy.run(*msg.arguments[2:])
        self.strategies[sensor.name] = strategy
        return Message.reply(msg.name, "ok", *msg.arguments)

    def request_halt(self, msg):
        """Halt the device server.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether scheduling the halt succeeded.

        Examples
        --------
        ::

            ?halt
            !halt ok
        """
        self.transport.write(str(Message(Message.REPLY, "halt",
                                         ["ok"])) + self.delimiter)
        self.factory.stop()

    def request_watchdog(self, msg):
        """Check that the server is still alive.

        Returns
        -------
            success : {'ok'}

        Examples
        --------
        ::

            ?watchdog
            !watchdog ok
        """
        return Message.reply("watchdog", "ok")

    def request_restart(self, msg):
        """Restart the device server.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether scheduling the restart succeeded.

        Examples
        --------
        ::

            ?restart
            !restart ok
        """
        pass

    def request_client_list(self, msg):
        """Request the list of connected clients.

        The list of clients is sent as a sequence of #client-list informs.

        Informs
        -------
        addr : str
            The address of the client as host:port with host in dotted quad
            notation. If the address of the client could not be determined
            (because, for example, the client disconnected suddenly) then
            a unique string representing the client is sent instead.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the client list succeeded.
        informs : int
            Number of #client-list inform messages sent.

        Examples
        --------
        ::

            ?client-list
            #client-list 127.0.0.1:53600
            !client-list ok 1
        """
        for ip, port in self.factory.clients:
            self.send_message(Message.inform(msg.name, "%s:%s" % (ip, port)))
        return Message.reply(msg.name, "ok", len(self.factory.clients))

    def request_log_level(self, msg):
        """Query or set the current logging level.

        Parameters
        ----------
        level : {'all', 'trace', 'debug', 'info', 'warn', 'error', 'fatal', 'off'}, optional
            Name of the logging level to set the device server to (the default is to leave the log level unchanged).

        Returns
        -------
        success : {'ok', 'fail'}
            Whether the request succeeded.
        level : {'all', 'trace', 'debug', 'info', 'warn', 'error', 'fatal', 'off'}
            The log level after processing the request.

        Examples
        --------
        ::

            ?log-level
            !log-level ok warn

            ?log-level info
            !log-level ok info
        """
        if msg.arguments:
            try:
                self.factory.log.set_log_level_by_name(msg.arguments[0])
            except ValueError, e:
                raise FailReply(str(e))
        return Message.reply("log-level", "ok", self.factory.log.level_name())

    def request_unknown(self, msg):
        return Message.reply(msg.name, "invalid", "Unknown request.")

class TxDeviceServer(ServerFactory):
    """ This is a device server listening on a given port and address
    """
    protocol = TxDeviceProtocol

    def __init__(self, port, host):
        self.log = DeviceLogger(self) # python logger is None
        ServerFactory.__init__(self)
        self.listen_port = port
        self.host = host
        self.clients = {}

    def start(self):
        self.port = reactor.listenTCP(self.listen_port, self,
                                      interface=self.host)
        return self.port

    def register_client(self, addr, protocol):
        self.clients[addr] = protocol

    def deregister_client(self, addr):
        del self.clients[addr]

    def buildProtocol(self, addr):
        protocol = ServerFactory.buildProtocol(self, addr)
        self.register_client((addr.host, addr.port), protocol)
        return protocol

    def stop(self):
        for client in self.clients.values():
            client.transport.loseConnection()
        self.port.stopListening()
        if getattr(self, 'production', None):
            reactor.stop()

    def _log_msg(self, level_name, msg, name, timestamp=None):
        """Create a katcp logging inform message.

           Usually this will be called from inside a DeviceLogger object,
           but it is also used by the methods in this class when errors
           need to be reported to the client.
           """
        if timestamp is None:
            timestamp = time.time()
        return Message.inform("log",
                level_name,
                str(int(timestamp * 1000.0)), # time since epoch in ms
                name,
                msg,
        )

    def mass_inform(self, msg):
        for client in self.clients.itervalues():
            client.send_message(msg)
