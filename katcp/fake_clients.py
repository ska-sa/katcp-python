# Copyright 2015 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details
import tornado.concurrent

from thread import get_ident as get_thread_ident

from tornado.gen import Return
from tornado.concurrent import Future

from katcp import client, server, kattypes
from katcp.core import AttrDict, ProtocolFlags, Message, convert_method_name

def get_fake_inspecting_client_instance(InspectingClass, host, port, *args, **kwargs):
    # Note, that this mechanism makes many assumptions about the internals of
    # InspectingClientAsync and DeviceClient, so it may well break if substantial changes
    # are made to the implementation of either
    class FakeInspectingClass(InspectingClass):
        def get_inform_hook_client(self, host, port, *args, **kwargs):
            fac = FakeAsyncClient(host, port, *args, **kwargs)
            # We'll be ignoring the inform hooks, rather stimulating the InspectingClass at a
            # higher level
            fac.hook_inform = lambda *x, **xx : None
            return fac

    ic = FakeInspectingClass(host, port, *args, **kwargs)

    return ic

class FakeInspectingClientManager(object):
    def __init__(self, fake_inspecting_client):
        self._fic = fake_inspecting_client
        self._fkc = fake_inspecting_client.katcp_client
        self._fkc.request_handlers['sensor-list'] = self.request_sensor_list
        self._sensor_infos = {}

    @kattypes.return_reply(kattypes.Int())
    def request_sensor_list(self, req, msg):
        if msg.arguments:
            name = (msg.arguments[0],)
            keys = (name, )
            if name not in self._sensor_infos:
                return ("fail", "Unknown sensor name.")
        else:
            keys = self._sensor_infos.keys()

        num_informs = 0
        for sensor_name in keys:
            infos = self._sensor_infos[sensor_name]
            num_informs += 1
            req.inform(sensor_name, *infos)

        return ('ok', num_informs)

    def add_sensors(self, sensor_infos):
        """Add fake sensors

        sensor_infos is a dict <sensor-name> : (
            <description>, <unit>, <sensor-type>, <params>*)
        The sensor info is string-reprs of whatever they are, as they would be on
        the wire in a real KATCP connection. Values are passed to a katcp.Message object,
        so some automatic conversions are done, hence it is OK to pass numbers without
        stringifying them.

        """
        self._sensor_infos.update(sensor_infos)
        self._fic._interface_changed.set()

    def add_request_handlers_object(self, rh_obj):
        for name in dir(rh_obj):
            if not callable(getattr(rh_obj, name)):
                continue
            if name.startswith("request_"):
                request_name = convert_method_name("request_", name)
                self._fkc.request_handlers[request_name] = getattr(rh_obj, name)

    def add_request_handlers_dict(self, rh_dict):
        self._fkc.request_handlers.update(rh_dict)

class FakeKATCPServerError(Exception):
    """Raised if a FakeKATCPServer is used in an unsupported way"""

class FakeKATCPServer(object):
    """Fake the parts of a KATCP server used by katcp.server.ClientConnection"""
    def get_address(self, conn_id):
        return '<fake-client-connection: {!r}>'.format(conn_id)

    def send_message(self, conn_id, msg):
        raise FakeKATCPServerError(
            'Cannot send messages via fake request/conection object')

    def mass_send_message(self, msg):
        raise FakeKATCPServerError(
            'Cannot send messages via fake request/conection object')

    def flush_on_close(self):
        f = Future()
        f.set_result(None)
        return f

class FakeClientRequestConnection(server.ClientRequestConnection):
    def __init__(self, *args, **kwargs):
        super(FakeClientRequestConnection, self).__init__(*args, **kwargs)
        self.informs_sent = []

    def inform(self, *args):
        inf_msg = Message.reply_inform(self.msg, *args)
        self.informs_sent.append(inf_msg)

class FakeAsyncClient(client.AsyncClient):
    """Fake version of :class:`katcp.client.AsyncClient`

    Useful for testing and simulation

    By default assumes that the client is connected to a fully featured KATCPv5
    server. Call preset_protocol_flags() to override
    """

    def __init__(self, *args, **kwargs):
        self.request_handlers = {}
        self.fake_server = FakeKATCPServer()
        self.client_connection = server.ClientConnection(
            self.fake_server, 'fake-async-client')
        super(FakeAsyncClient, self).__init__(*args, **kwargs)

    @tornado.gen.coroutine
    def _install(self):
        self.ioloop_thread_id = get_thread_ident()
        self._running.set()
        yield self._connect()

    @tornado.gen.coroutine
    def _connect(self):
        assert get_thread_ident() == self.ioloop_thread_id
        self._stream = AttrDict(error=None)
        self._connected.set()
        self.notify_connected(True)
        self.preset_protocol_flags(ProtocolFlags(5, 0, set('IM')))

    def _disconnect(self):
        if self._stream:
            self._stream_closed_callback(self._stream)

    @tornado.gen.coroutine
    def future_request(self, msg, timeout=None, use_mid=None):
        """Send a request messsage, with future replies.

        Parameters
        ----------
        msg : Message object
            The request Message to send.
        timeout : float in seconds
            How long to wait for a reply. The default is the
            the timeout set when creating the AsyncClient.
        use_mid : boolean, optional
            Whether to use message IDs. Default is to use message IDs
            if the server supports them.

        Returns
        -------
        A tornado.concurrent.Future that resolves with:

        reply : Message object
            The reply message received.
        informs : list of Message objects
            A list of the inform messages received.

        """
        mid = self._get_mid_and_update_msg(msg, use_mid)

        if msg.name in self.request_handlers:
            req = FakeClientRequestConnection(self.client_connection, msg)
            reply_msg = yield tornado.gen.maybe_future(
                self.request_handlers[msg.name](req, msg))
            reply_informs = req.informs_sent
        else:
            reply_msg = Message.reply(msg.name, 'ok')
            reply_informs = []

        reply_msg.mid = mid
        raise Return((reply_msg, reply_informs))

