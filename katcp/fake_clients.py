# Copyright 2015 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details
import tornado.concurrent

from thread import get_ident as get_thread_ident

from tornado.gen import Return

from katcp import client
from katcp.core import AttrDict, ProtocolFlags, Message

def get_fake_inspecting_client_instance(InspectingClass, host, port, *args, **kwargs):
    # katcp methods used:
    #
    #  set_ioloop()
    #  hook_inform(): sensor-status, interface-changed, device-changed
    #  is_connected()
    #  until_protocol()
    #  until_connected()
    #  disconnect()
    #  start()
    #  stop()
    #  join()
    #  future_request()
    #
    # attributes:
    #
    #  protocol_flags

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
        self._fkc.request_handlers['sensor-list'] = self.handle_sensor_list
        self._sensor_infos = {}

    @tornado.gen.coroutine
    def handle_sensor_list(self, msg):
        if msg.arguments:
            name = (msg.arguments[0],)
            keys = (name, )
            if name not in self._sensor_infos:
                raise Return((Message.reply(msg.name, 'fail', 'Sensor not found'), []))
        else:
            keys = self._sensor_infos.keys()

        informs = []
        for sensor_name in keys:
            infos = self._sensor_infos[sensor_name]
            informs.append(Message.inform(msg.name, sensor_name, *infos, mid=msg.mid))

        raise Return((Message.reply(msg.name, 'ok', len(informs), mid=msg.mid),
                      informs))

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

class FakeAsyncClient(client.AsyncClient):
    """Fake version of :class:`katcp.client.AsyncClient`

    Useful for testing and simulation

    By default assumes that the client is connected to a fully featured KATCPv5
    server. Call preset_protocol_flags() to override
    """

    def __init__(self, *args, **kwargs):
        self.request_handlers = {}
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
            reply_msg, reply_informs = yield tornado.gen.maybe_future(
                self.request_handlers[msg.name](msg))
        else:
            reply_msg = Message.reply(msg.name, 'ok')
            reply_informs = []

        reply_msg.mid = mid
        raise Return((reply_msg, reply_informs))

