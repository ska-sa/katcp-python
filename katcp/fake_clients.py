# Copyright 2015 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

from __future__ import division, print_function, absolute_import

import tornado.concurrent

from thread import get_ident as get_thread_ident

from tornado.gen import Return
from tornado.concurrent import Future

from katcp import client, server, kattypes, resource, Sensor
from katcp.core import AttrDict, ProtocolFlags, Message, convert_method_name

def fake_KATCP_client_resource_factory(
        KATCPClientResourceClass, fake_options, resource_spec, *args, **kwargs):
    """Create a fake KATCPClientResource-like class and a fake-manager

    Parameters
    ----------
    KATCPClientResourceClass : class
        Subclass of :class:`katcp.resource_client.KATCPClientResource`
    fake_options : dict
        Options for the faking process. Keys:
            allow_any_request : bool, default False
            (TODO not implemented behaves as if it were True)
    resource_spec, *args, **kwargs : passed to KATCPClientResourceClass

    A subclass of the passed-in KATCPClientResourceClass is created that replaces the
    internal InspecingClient instances with fakes using fake_inspecting_client_factory()
    based on the InspectingClient class used by KATCPClientResourceClass.

    Returns
    -------
    (fake_katcp_client_resource, fake_katcp_client_resource_manager):

    fake_katcp_client_resource : instance of faked subclass of KATCPClientResourceClass
    fake_katcp_client_resource_manager : :class:`FakeKATCPClientResourceManager` instance
        Bound to the `fake_katcp_client_resource` instance.

    """
    # TODO Implement allow_any_request functionality. When True, any unknown request (even
    # if there is no fake implementation) should succeed
    allow_any_request = fake_options.get('allow_any_request', False)

    class FakeKATCPClientResource(KATCPClientResourceClass):
        def inspecting_client_factory(self, host, port, ioloop_set_to):
            real_instance = (super(FakeKATCPClientResource, self)
                             .inspecting_client_factory(host, port, ioloop_set_to) )
            fic, fic_manager = fake_inspecting_client_factory(
                real_instance.__class__, fake_options, host, port,
                ioloop=ioloop_set_to, auto_reconnect=self.auto_reconnect)
            self.fake_inspecting_client_manager = fic_manager
            return fic

    fkcr = FakeKATCPClientResource(resource_spec, *args, **kwargs)
    fkcr_manager = FakeKATCPClientResourceManager(fkcr)
    return (fkcr, fkcr_manager)

def fake_KATCP_client_resource_container_factory(
        KATCPClientResourceContainerClass, fake_options, resources_spec,
        *args, **kwargs):
    """Create a fake KATCPClientResourceContainer-like class and a fake-manager

    Parameters
    ----------
    KATCPClientResourceContainerClass : class
        Subclass of :class:`katcp.resource_client.KATCPClientResourceContainer`
    fake_options : dict
        Options for the faking process. Keys:
            allow_any_request : bool, default False
            (TODO not implemented behaves as if it were True)
    resources_spec, *args, **kwargs : passed to KATCPClientResourceContainerClass

    A subclass of the passed-in KATCPClientResourceClassContainer is created that replaces the
    KATCPClientResource child instances with fakes using fake_KATCP_client_resource_factory()
    based on the KATCPClientResource class used by `KATCPClientResourceContainerClass`.

    Returns
    -------
    (fake_katcp_client_resource_container, fake_katcp_client_resource_container_manager):

    fake_katcp_client_resource_container : instance of faked subclass of
                                           KATCPClientResourceContainerClass
    fake_katcp_client_resource_manager : :class:`FakeKATCPClientResourceContainerManager`
                                         instance
        Bound to the `fake_katcp_client_resource_container` instance.

    """
    # TODO allow_any_request see comment in fake_KATCP_client_resource_factory()
    allow_any_request = fake_options.get('allow_any_request', False)

    class FakeKATCPClientResourceContainer(KATCPClientResourceContainerClass):
        def __init__(self, *args, **kwargs):
            self.fake_client_resource_managers = {}
            super(FakeKATCPClientResourceContainer, self).__init__(*args, **kwargs)

        def client_resource_factory(self, res_spec, parent, logger):
            real_instance = (super(FakeKATCPClientResourceContainer, self)
                             .client_resource_factory(res_spec, parent, logger) )
            fkcr, fkcr_manager = fake_KATCP_client_resource_factory(
                real_instance.__class__, fake_options,
                res_spec, parent=self, logger=logger)
            self.fake_client_resource_managers[
                resource.escape_name(fkcr.name)] = fkcr_manager
            return fkcr

    fkcrc = FakeKATCPClientResourceContainer(resources_spec, *args, **kwargs)
    fkcrc_manager = FakeKATCPClientResourceContainerManager(fkcrc)
    return (fkcrc, fkcrc_manager)


class FakeKATCPClientResourceManager(object):
    """Manage a fake KATCPClientResource instance"""
    def __init__(self, fake_katcp_resource_client):
        self._fkrc = fake_katcp_resource_client

    @property
    def _fic_manager(self):
        try:
           fic_manager = self._fkrc.fake_inspecting_client_manager
        except AttributeError:
            raise RuntimeError(
                'Fake KATCPClientResource must be started before it can be managed')
        return fic_manager

    @property
    def fake_sensor_infos(self):
        return self._fic_manager.fake_sensor_infos

    def add_sensors(self, sensor_infos):
        """Add fake sensors

        sensor_infos is a dict <sensor-name> : (
            <description>, <unit>, <sensor-type>, <params>*)
        The sensor info is string-reprs of whatever they are, as they would be on
        the wire in a real KATCP connection. Values are passed to a katcp.Message object,
        so some automatic conversions are done, hence it is OK to pass numbers without
        stringifying them.

        """
        self._fic_manager.add_sensors(sensor_infos)

    def add_request_handlers_object(self, rh_obj):
        """Add fake request handlers from an object with request_* method(s)

        See :class:`FakeInspectingClientManager` for detail
        """
        return self._fic_manager.add_request_handlers_object(rh_obj)

    def add_request_handlers_dict(self, rh_dict):
        """Add fake request handlers from a dict keyed by request name

        See :class:`FakeInspectingClientManager` for detail
        """
        return self._fic_manager.add_request_handlers_dict(rh_dict)

class FakeKATCPClientResourceContainerManager(object):
    def __init__(self, fake_katcp_resource_container):
        self._fkcrc = fake_katcp_resource_container

    @property
    def child_managers(self):
        return self._fkcrc.fake_client_resource_managers

    def add_sensors(self, child_name, sensor_infos):
        """Add fake sensors

        child_name : str
            Name of the client to which the sensor should be added as used in the keys of
            the FakeKATCPClientResourceContainer `children` container.
        sensor_infos is a dict <sensor-name> : (
            <description>, <unit>, <sensor-type>, <params>*)
        The sensor info is string-reprs of whatever they are, as they would be on
        the wire in a real KATCP connection. Values are passed to a katcp.Message object,
        so some automatic conversions are done, hence it is OK to pass numbers without
        stringifying them.

        """
        self.child_managers[child_name].add_sensors(sensor_infos)

    def add_request_handlers_object(self, child_name, rh_obj):
        """Add fake request handlers from an object with request_* method(s)

        See :class:`FakeInspectingClientManager` for detail
        """
        return self.child_managers[child_name].add_request_handlers_object(rh_obj)

    def add_request_handlers_dict(self, child_name, rh_dict):
        """Add fake request handlers from a dict keyed by request name

        See :class:`FakeInspectingClientManager` for detail
        """
        return self.child_managers[child_name].add_request_handlers_dict(rh_dict)

def fake_inspecting_client_factory(InspectingClass, fake_options, host, port,
                                   *args, **kwargs):
    # Note, that this mechanism makes many assumptions about the internals of
    # InspectingClientAsync and DeviceClient, so it may well break if substantial changes
    # are made to the implementation of either.

    allow_any_request = fake_options.get('allow_any_request', False)
    # TODO Implement any request stuff. See comment above in
    # fake_KATCP_client_resource_factory


    class FakeInspectingClass(InspectingClass):
        def inform_hook_client_factory(self, host, port, *args, **kwargs):
            fac = FakeAsyncClient(host, port, *args, **kwargs)
            # We'll be ignoring the inform hooks, rather stimulating the InspectingClass at a
            # higher level
            fac.hook_inform = lambda *x, **xx : None
            return fac

    fic = FakeInspectingClass(host, port, *args, **kwargs)
    fic_manager = FakeInspectingClientManager(fic)
    return (fic, fic_manager)

class FakeInspectingClientManager(object):
    def __init__(self, fake_inspecting_client):
        self._fic = fake_inspecting_client
        self._fkc = fake_inspecting_client.katcp_client
        self.add_request_handlers_object(self)
        self.fake_sensor_infos = {}

    @kattypes.return_reply(kattypes.Int())
    def request_sensor_list(self, req, msg):
        """Sensor list"""
        if msg.arguments:
            name = (msg.arguments[0],)
            keys = (name, )
            if name not in self.fake_sensor_infos:
                return ("fail", "Unknown sensor name.")
        else:
            keys = self.fake_sensor_infos.keys()

        num_informs = 0
        for sensor_name in keys:
            infos = self.fake_sensor_infos[sensor_name]
            num_informs += 1
            req.inform(sensor_name, *infos)

        return ('ok', num_informs)

    @property
    def _request_handlers(self):
        """For compatibility with methods stolen from server.DeviceServer"""
        return self._fkc.request_handlers

    request_help = server.DeviceServer.request_help.im_func

    def add_sensors(self, sensor_infos):
        """Add fake sensors

        sensor_infos is a dict <sensor-name> : (
            <description>, <unit>, <sensor-type>, <params>*)
        The sensor info is string-reprs of whatever they are, as they would be on
        the wire in a real KATCP connection. Values are passed to a katcp.Message object,
        so some automatic conversions are done, hence it is OK to pass numbers without
        stringifying them.

        """
        # Check sensor validity. parse_type() and parse_params should raise if not OK
        for s_name, s_info in sensor_infos.items():
            s_type = Sensor.parse_type(s_info[2])
            s_params = Sensor.parse_params(s_type, s_info[3:])
        self.fake_sensor_infos.update(sensor_infos)
        self._fic._interface_changed.set()

    def add_request_handlers_object(self, rh_obj):
        """Add fake request handlers from an object with request_* method(s)

        See :meth:`FakeInspectingClientManager.add_request_handlers_dict` for more detail.

        """
        rh_dict = {}
        for name in dir(rh_obj):
            if not callable(getattr(rh_obj, name)):
                continue
            if name.startswith("request_"):
                request_name = convert_method_name("request_", name)
                req_meth = getattr(rh_obj, name)
                rh_dict[request_name] = req_meth

        self.add_request_handlers_dict(rh_dict)

    def add_request_handlers_dict(self, rh_dict):
        """Add fake request handler functions from a dict keyed by request name

        Note the keys must be the KATCP message name (i.e. "the-request", not
        "the_request")

        The request-handler interface is more or less compatible with request handler API
        in :class:`katcp.server.DeviceServerBase`. The fake ClientRequestConnection req
        object does not support mass_inform() or reply_with_message.

        """
        # Check that all the callables have docstrings as strings
        for req_func in rh_dict.values():
            assert req_func.__doc__, "Even fake request handlers must have docstrings"
        self._fkc.request_handlers.update(rh_dict)
        self._fic._interface_changed.set()

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

