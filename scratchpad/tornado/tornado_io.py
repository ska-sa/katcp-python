from katcp.server import *

from thread import get_ident as get_thread_ident

class KATCPServerTornado(object):
    BACKLOG = 5                           # Size of server socket backlog

    @property
    def bind_address(self):
        """(host, port) where the server is listening for connections"""
        return self._bindaddr

    def __init__(self, device, host, port, tb_limit=20, logger=log):
        self._device = device
        self._bindaddr = (host, port)
        self._tb_limit = tb_limit
        self._logger = logger
        self._parser = MessageParser()
        # Indicate that server is running and ready to accept connections
        self._running = threading.Event()
        # Indicate that we are stopped, i.e. join() can return
        self._stopped = threading.Event()
        self.send_timeout = 5 # Timeout to catch spinning sends
        self._ioloop = None   # The Tornado IOloop to use, set by self.set_ioloop()
        # ID of Thread that hosts the IOLoop. Used to check that we are running in the
        # ioloop.
        self._ioloop_thread_id = None
        # True if we manage the ioloop. Will be updated by self.set_ioloop()
        self._ioloop_managed = True
        # Thread object that a managed ioloop is running in
        self._ioloop_thread = None
        # map from tornado IOStreams to ClientConnection objects
        self._connections = {}

    def set_ioloop(self, ioloop=None):
        """Set the tornado.ioloop.IOLoop instance to use, default to IOLoop.current()

        If set_ioloop() is never called the IOLoop is started in a new thread, and will
        be stopped if self.stop() is called.
        """
        if self._ioloop:
            raise RuntimeError('IOLoop instance can only be set once')
        if ioloop:
            self._ioloop = ioloop
        else:
            self._ioloop = tornado.ioloop.IOLoop.current()
        self._ioloop_managed = False

    def start(self, timeout=None, daemon=None, excepthook=None):
        """Install the server on its IOLoop, starting the IOLoop in a thread if needed

        Parameters
        ----------
        timeout : float in seconds
            Time to wait for server thread to start.
        daemon : boolean
            If not None, the thread's setDaemon method is called with this
            parameter before the thread is started.
        excepthook : function
            Function to call if the client throws an exception. Signature
            is as for sys.excepthook.
        """
        if self._running.isSet():
            raise RuntimeError('Server already started')
        self._stopped.clear()
        # Make sure we have an ioloop
        if self._ioloop_managed:
            self._start_ioloop()

        self._tcp_server = tornado.tcpserver.TCPServer(self._ioloop)
        self._tcp_server.handle_stream = self._handle_stream
        self._server_sock = self._bind_socket(self._bindaddr)
        self._bindaddr = self._server_sock.getsockname()

        self._ioloop.add_callback(self._install)
        if timeout:
            self._running.wait(timeout)

    def stop(self, timeout=1.0):
        """Stop a running server (from another thread).

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for server to have *started*.
        """
        if timeout:
            self._running.wait(timeout)
        self._ioloop.add_callback(self._uninstall)

    def join(self, timeout=None):
        """Rejoin the server thread.

        Parameters
        ----------
        timeout : float in seconds
            Time to wait for the thread to finish.

        Notes
        -----

        If the ioloop is not managed, this function will block until the server port is
        closed, meaning a new server can be listen at the same port

        """
        if self._ioloop_managed:
            try:
                self._ioloop_thread.join(timeout)
            except AttributeError:
                raise RuntimeError('Cannot join if not started')
        else:
            self._stopped.wait(timeout)

    def _bind_socket(self, bindaddr):
        """Create a listening server socket."""
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)
        if hasattr(socket, 'TCP_NODELAY'):
            # our message packets are small, don't delay sending them.
            sock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
        try:
            sock.bind(bindaddr)
        except Exception, e:
            self._logger.exception("Unable to bind to %s" % str(bindaddr))
            raise
        sock.listen(self.BACKLOG)
        return sock

    def _start_ioloop(self):
        if not self._ioloop:
            self._ioloop = tornado.ioloop.IOLoop()
        self._ioloop_thread = threading.Thread(target=self._ioloop.start)
        self._ioloop_thread.start()

    def _install(self):
        # Do stuff to put us on the IOLoop
        self._ioloop_thread_id = get_thread_ident()
        self._tcp_server.add_socket(self._server_sock)
        self._running.set()

    def _uninstall(self):
        # Remove us from the IOLoop
        assert get_thread_ident() == self._ioloop_thread_id
        self._tcp_server.stop()
        # TODO Call something to close all sockets, one at a time in a nice non-blocking fashion
        if self._ioloop_managed:
            self._ioloop.stop()
        self._running.clear()
        self._stopped.set()

    def _handle_stream(self, stream, address):
        """Handle a new connection as a tornado.iostream.IOStream instance"""
        assert get_thread_ident() == self._ioloop_thread_id
        client_conn = ClientConnection(self, stream)
        self._connections[stream] = client_conn
        self._device.on_client_connect(client_conn)

    def get_address(self, stream):
        """Text representation of the network address of a connection"""
        sock = stream.socket
        try:
            addr = ":".join(str(part) for part in sock.getpeername())
        except socket.error:
            # client may be gone, in which case just send a description
            addr = repr(sock)
        return addr

    def send_message(self, stream, msg):
        """Send an arbitrary message to a particular client.

        Note that failed sends disconnect the client sock and call
        on_client_disconnect. They do not raise exceptions.

        Parameters
        ----------
        stream : tornado.iostream.IOStream object
            The stream to send the message to.
        msg : Message object
            The message to send.

        Returns
        -------
        a Future

        """
        assert get_thread_ident() == self._ioloop_thread_id
        return stream.write(str(msg) + '\n')

    def mass_send_message(self, msg):
        """Send a message to all connected clients"""
        for stream in self._connections.keys():
            self.send_message(stream, msg)



# TODO issues
#
# IOStream.write() only does a callback when all writes have been flushed to the
# socket. How do we handle timeouts? Or blocking?
#
# Ideas:
#
# Set the tornado output buffer relatively small.
#  Keep sending until it raises StreamBufferFullError.
#     Start a catchup timeout (5s?), that terminates the connection
#     Use the send-done callback to unblock sending if it was blocking.
#     Use the send-done callback to cancel connection-termination timeout


    
    tornado.iostream.IOStream
    # API with device:
    #
    # self.send_message(conn_id, self._device._log_msg("error", reason, "root"))
    #   Called when a received message could not be parsed
    #
    # self._device.handle_message(client_conn, msg)
    #   client_conn -- ClientConnection instance
    #   msg -- katcp.Message instance
    #
    # self._device._process_deferred_queue()
    #   Called at various times. Could perhaps move this back to the DeviceServer to
    #   handle itself
    #
    # self._device.on_client_disconnect(
    #     conn, "Client socket died" " with error %s" % (e,), False)
    #         conn : ClientConnection object
    #              Client connection being disconnected.
    #         msg : str
    #              Reason client is being disconnected.
    #         connection_valid : boolean
    #              True if connection is still open for sending,
    #              False otherwise.
