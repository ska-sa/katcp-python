from katcp.server import *

from thread import get_ident as get_thread_ident
import logging

class KATCPServerTornado(object):
    BACKLOG = 5                        # Size of server socket backlog
    MAX_MSG_SIZE = 128*1024
    """Maximum message size that can be sent or received in bytes

    If more than MAX_MSG_SIZE bytes are read from the client without encountering a
    message terminator (i.e. newline), the connection is closed.
    """

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
        # Set max_buffer_size to ensure streams are closed if too-large messages are
        # received
        self._tcp_server = tornado.tcpserver.TCPServer(
            self._ioloop, max_buffer_size=self.MAX_MSG_SIZE)
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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)
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
        def run_ioloop():
            try:
                self._ioloop.start()
            except Exception:
                self._logger.error('Error starting tornado IOloop: ', exc_info=True)
            finally:
                self._stopped.set()
                self._logger.info('Managed tornado IOloop {0} stopped'
                                   .format(self._ioloop))
        self._ioloop_thread = threading.Thread(target=run_ioloop)
        self._ioloop_thread.start()

    def _install(self):
        # Do stuff to put us on the IOLoop
        self._ioloop_thread_id = get_thread_ident()
        self._tcp_server.add_socket(self._server_sock)
        self._running.set()

    def _uninstall(self):
        # Remove us from the IOLoop
        assert get_thread_ident() == self._ioloop_thread_id
        try:
            self._tcp_server.stop()
            for stream, conn in self._connections.items():
                self._disconnect_client(stream, conn, 'Device server shutting down.')
        finally:
            self._running.clear()
            if not self._ioloop_managed:
                self._stopped.set()
            else:
                # Our thread run function that started the ioloop should set self._stopped
                # when it exits
                self._ioloop.add_callback(self._ioloop.stop)


    def _handle_stream(self, stream, address):
        """Handle a new connection as a tornado.iostream.IOStream instance"""
        try:
            assert get_thread_ident() == self._ioloop_thread_id
            stream.set_close_callback(partial(self._stream_closed_callback, stream))
            # our message packets are small, don't delay sending them.
            stream.set_nodelay(True)
            # Limit in-process write buffer size so that we can quickly know if the
            # client is slow
            stream.max_write_buffer_size = self.MAX_MSG_SIZE

            # Abuse the IOStream object slightly by adding an 'address' attribute. Use
            # nasty prefix to prevent collisions
            stream.KATCPServerTornado_address = address

            client_conn = ClientConnection(self, stream)
            self._connections[stream] = client_conn
            try:
                self._device.on_client_connect(client_conn)
            except Exception:
                # If on_client_connect fails there is no reason to continue trying to handle
                # this connection. Try and send exception info to the client and disconnect
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                log_msg = 'Device error initialising connection {0}'.format(reason)
                self._logger.error(log_msg)
                stream.write(log_msg)
                stream.close(exc_info=True)
            else:
                self._start_read_loop(stream, client_conn)
        except Exception:
            self._logger.error('Unhandled exception trying to handle new connection',
                              exc_info=True)

    def _start_read_loop(self, stream, client_conn):
        # TODO actually parse the message to katcp!
        logging.info('In _receive_msg')
        assert get_thread_ident() == self._ioloop_thread_id
        def line_read_callback(msg):
            logging.info('In line_read_callback')
            try:
                self._device.handle_message(client_conn, msg)
            except Exception:
                self._logger.error('Error handling message', exc_info=True)
            try:
                stream.read_until('\n', callback=line_read_callback)
            except tornado.iostream.StreamClosedError:
                # Assume that the _stream_closed_callback will handle this case
                pass
            except Exception:
                self._logger.warn('Unhandled Exception while reading from client {0}:'
                                  .format(self.get_address(stream)), exc_info=True)
        try:
            logging.info('reading')
            stream.read_until('\n', callback=line_read_callback)
        except tornado.iostream.StreamClosedError:
            self._logger.warn('Connection closed before we could start reading from new '
                              'client {0}'.format(self.get_address(stream)))
            # Assume that _stream_closed_callback() will handle this case
            return
        # Other exceptions should be caught and logged by caller.

    def _stream_closed_callback(self, stream):
        assert get_thread_ident() == self._ioloop_thread_id
        # Remove ClientConnection object for the current stream from our state
        conn = self._connections.pop(stream, None)
        error_repr = '{0!r}'.format(stream.error) if stream.error else ''
        if error_repr:
            self._logger.warn('Stream for client {0} closed with error {1}'
                              .format(self.get_address(stream), error_repr))
        if conn:
            reason = error_repr or "Socket EOF"
            self._disconnect_client(stream, conn, reason)

    def _disconnect_client(self, stream, conn, reason):
        assert get_thread_ident() == self._ioloop_thread_id
        stream_open = not stream.closed()
        try:
            if not conn.client_disconnect_called:
                try:
                    self._device.on_client_disconnect(conn, reason, stream_open)
                except Exception:
                    self._logger.error(
                        'Error while calling on_client_disconnect for client {0}'.format(
                            self.get_address(stream)),
                        exc_info=True)
                finally:
                    conn.on_client_disconnect_was_called()
        finally:
            # Make sure stream is closed.
            stream.close()

    def get_address(self, stream):
        """Text representation of the network address of a connection stream"""
        try:
            addr = ":".join(str(part) for part in stream.KATCPServerTornado_address)
        except AttributeError:
            # Something weird happened, but keep trucking
            addr = '<error>'
            self._logger.warn('Could not determine address of stream', exc_info=True)
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


        """
        assert get_thread_ident() == self._ioloop_thread_id
        try:
            return stream.write(str(msg) + '\n')
        except Exception:
            addr = self.get_address(stream)
            self._logger.warn('Could not send message to {0}'.format(addr), exc_info=True)
            stream.close(exc_info=True)

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
