from katcp.server import *

class TCPServer(object):

    def __init__(self, device, host, port, tb_limit=20, logger=log):
        self._device = device
        self._parser = MessageParser()
        self._tb_limit = tb_limit
        self._bindaddr = (host, port)
        self._running = threading.Event()
        self._logger = logger
        self.send_timeout = 5 # Timeout to catch spinning sends
        self._sock = None
        self._thread = None
        self._logger = logger

        # sockets and data
        self._data_lock = threading.Lock()
        self._socks = []  # list of client sockets
        self._waiting_chunks = {}  # map from client sockets to messages pieces
        self._sock_locks = {}  # map from client sockets to sending locks
        # map from sockets to ClientConnectionTCP objects
        self._sock_connections = {}

    def _bind(self, bindaddr):
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
        sock.listen(5)
        return sock

    def _add_socket(self, sock):
        """Add a client socket to the socket and chunk lists."""
        with self._data_lock:
            self._socks.append(sock)
            self._waiting_chunks[sock] = ""
            self._sock_locks[sock] = threading.Lock()
            self._sock_connections[sock] = ClientConnectionTCP(self, sock)

    def _remove_socket(self, sock):
        """Remove a client socket from the socket and chunk lists."""
        sock.close()
        self._data_lock.acquire()
        try:
            if sock in self._socks:
                self._socks.remove(sock)
                del self._waiting_chunks[sock]
                del self._sock_locks[sock]
                del self._sock_connections[sock]
        finally:
            self._data_lock.release()

    def get_sockets(self):
        """Return the complete list of current client socket.

        Returns
        -------
        sockets : list of socket.socket objects
            A list of connected client sockets.
        """
        return list(self._socks)


    def _handle_chunk(self, sock, chunk):
        """Handle a chunk of data for socket sock."""
        chunk = chunk.replace("\r", "\n")
        lines = chunk.split("\n")

        waiting_chunk = self._waiting_chunks.get(sock, "")

        for line in lines[:-1]:
            full_line = waiting_chunk + line
            waiting_chunk = ""

            if full_line:
                try:
                    msg = self._parser.parse(full_line)
                # We do want to catch everything that inherits from Exception
                # pylint: disable-msg = W0703
                except Exception:
                    e_type, e_value, trace = sys.exc_info()
                    reason = "\n".join(traceback.format_exception(
                        e_type, e_value, trace, self._tb_limit))
                    self._logger.error("BAD COMMAND: %s in line %r" % (
                        reason, full_line))
                    self.tcp_inform(sock, self._log_msg("error", reason, "root"))
                else:
                    try:
                        client_conn = self._sock_connections[sock]
                    except KeyError:
                        self._logger.warn(
                        'Client disconnected while handling received message: %r'
                        % sock)
                    else:
                        self._device.handle_message(client_conn, msg)

        with self._data_lock:
            if sock in self._waiting_chunks:
                self._waiting_chunks[sock] = waiting_chunk + lines[-1]

    def _send_message(self, sock, msg):
        """Send an arbitrary message to a particular client.

        Note that failed sends disconnect the client sock and call
        on_client_disconnect. They do not raise exceptions.

        Parameters
        ----------
        sock : socket.socket object
            The socket to send the message to.
        msg : Message object
            The message to send.
        """
        # TODO: should probably implement this as a queue of sockets and
        #       messages to send and have the queue processed in the main
        #       loop.
        data = str(msg) + "\n"
        datalen = len(data)
        totalsent = 0

        # Log all sent messages here so no one else has to.
        self._logger.debug(data)

        # sends are locked per-socket -- i.e. only one send per socket at
        # a time
        lock = self._sock_locks.get(sock)
        if lock is None:
            try:
                client_name = sock.getpeername()
            except socket.error:
                client_name = "<disconnected client>"
            msg = "Attempt to send to a socket %s which is no longer a" \
                " client." % (client_name,)
            self._logger.warn(msg)
            return

        # do not do anything inside here which could call send_message!
        send_failed = False
        lock.acquire()
        t0 = time.time()
        try:
            while totalsent < datalen:
                if time.time()-t0 > self.send_timeout:
                    self._logger.error(
                        'server._send_msg() timing out after %fs, sent %d/%d bytes'
                        % (self.send_timeout, totalsent, datalen) )
                    send_failed = True
                    break
                try:
                    sent = sock.send(data[totalsent:])
                except socket.error, e:
                    if len(e.args) == 2 and e.args[0] == errno.EAGAIN:
                        continue
                    else:
                        send_failed = True
                        break

                if sent == 0:
                    send_failed = True
                    break

                totalsent += sent
        finally:
            lock.release()

        if send_failed:
            try:
                client_name = sock.getpeername()
            except socket.error:
                client_name = "<disconnected client>"
            msg = "Failed to send message to client %s" % (client_name,)
            self._logger.error(msg)
            # Need to get connection before calling _remove_socket()
            conn = self._sock_connections.get(sock)
            self._remove_socket(sock)
            # Don't run on_client_disconnect if another thread has beaten us to
            # the punch of removing the connection object
            if conn:
                self._device.on_client_disconnect(conn, msg, False)

    def tcp_inform(self, sock, msg):
        """Send an inform message to a particular TCP client.

        Should only be used for asynchronous informs. Informs
        that are part of the response to a request should use
        :meth:`reply_inform` so that the message identifier
        from the original request can be attached to the
        inform.

        Parameters
        ----------
        sock : socket.socket object
            The client to send the message to.
        msg : Message object
            The inform message to send.
        """
        # could be a function but we don't want it to be
        # pylint: disable-msg = R0201
        assert (msg.mtype == Message.INFORM)
        self._send_message(sock, msg)

    def mass_inform(self, msg):
        """Send an inform message to all clients.

        Parameters
        ----------
        msg : Message object
            The inform message to send.
        """
        for sock in list(self._socks):
            if sock is self._sock:
                continue
            self.tcp_inform(sock, msg)

    def tcp_reply(self, sock, reply, orig_req):
        """Send an asynchronous reply to an earlier request for a tcp socket.

        Parameters
        ----------
        sock : socket.socket object
            The client to send the reply to.
        reply : Message object
            The reply message to send.
        orig_req : Message object
            The request message being replied to. The reply message's
            id is overridden with the id from orig_req before the
            reply is sent.
        """
        assert (reply.mtype == Message.REPLY)
        # TODO The Message-id copying should be handled by the
        # ClientConnClientConnectionTCP class
        reply.mid = orig_req.mid
        self._send_message(sock, reply)

    def tcp_reply_inform(self, sock, inform, orig_req):
        """Send an inform as part of the reply to an earlier request.

        Parameters
        ----------
        sock : socket.socket object
            The client to send the inform to.
        inform : Message object
            The inform message to send.
        orig_req : Message object
            The request message being replied to. The inform message's
            id is overridden with the id from orig_req before the
            inform is sent.
        """
        assert (inform.mtype == Message.INFORM)
        # TODO The Message-id copying should be handled by the
        # ClientConnClientConnectionTCP class
        inform.mid = orig_req.mid
        self._send_message(sock, inform)

    def run(self):
        """Listen for clients and process their requests."""
        timeout = 0.5  # s

        # save globals so that the thread can run cleanly
        # even while Python is setting module globals to
        # None.
        _select = select.select
        _socket_error = socket.error

        self._sock = self._bind(self._bindaddr)
        # replace bindaddr with real address so we can rebind
        # to the same port.
        self._bindaddr = self._sock.getsockname()

        self._running.set()
        while self._running.isSet():
            self._device._process_deferred_queue()
            all_socks = self._socks + [self._sock]
            try:
                readers, _writers, errors = _select(
                    all_socks, [], all_socks, timeout)
            except Exception, e:
                # catch Exception because class of exception thrown
                # varies drastically between Mac and Linux
                self._logger.debug("Select error: %s" % (e,))

                # search for broken socket
                for sock in list(self._socks):
                    try:
                        _readers, _writers, _errors = _select([sock], [], [],
                                                              0)
                    except Exception, e:
                        # Need to get connection before calling _remove_socket()
                        conn = self._sock_connections[sock]
                        self._remove_socket(sock)
                        self._device.on_client_disconnect(
                            conn, "Client socket died" " with error %s" % (e,),
                            False)
                # check server socket
                try:
                    _readers, _writers, _errors = _select([self._sock], [], [],
                                                          0)
                except:
                    self._logger.warn("Server socket died, attempting to"
                                      " restart it.")
                    self._sock = self._bind(self._bindaddr)
                # try select again
                continue

            for sock in errors:
                if sock is self._sock:
                    # server socket died, attempt restart
                    self._sock = self._bind(self._bindaddr)
                else:
                    # client socket died, remove it
                    # Need to get connection before calling _remove_socket()
                    conn = self._sock_connections.get(sock)
                    self._remove_socket(sock)
                    # Don't call on_client_disconnect if the connection has
                    # already been removed in another thread
                    if conn:
                        self._device.on_client_disconnect(conn, "Client socket died", False)

            for sock in readers:
                if sock is self._sock:
                    client, addr = sock.accept()
                    client.setblocking(0)
                    self.mass_inform(Message.inform("client-connected",
                        "New client connected from %s" % (addr,)))
                    self._add_socket(client)
                    conn = self._sock_connections.get(client)
                    if client:
                        self._device.on_client_connect(conn)
                    else:
                        self._logger.warn(
                            'Client connection for socket %s dissappeared before '
                            'on_client_connect could be called' % (client,))
                else:
                    try:
                        chunk = sock.recv(4096)
                    except _socket_error:
                        # an error when sock was within ready list presumably
                        # means the client needs to be ditched.
                        chunk = ""
                    if chunk:
                        self._handle_chunk(sock, chunk)
                    else:
                        # no data, assume socket EOF
                        # Need to get connection before calling _remove_socket()
                        conn = self._sock_connections.get(sock)
                        self._remove_socket(sock)
                        # Don't run on_client_disconnect if another thread has
                        # beaten us to the punch of removing the connection
                        # object
                        if conn:
                            self._device.on_client_disconnect(conn, "Socket EOF", False)

        for sock in list(self._socks):
            conn = self._sock_connections.get(sock)
            if conn:
                self._device.on_client_disconnect(
                    conn, "Device server shutting down.", True)
            self._device._process_deferred_queue()
            self._remove_socket(sock)

        self._sock.close()

    def start(self, timeout=None, daemon=None, excepthook=None):
        """Start the server in a new thread.

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
        if self._thread:
            raise RuntimeError("Device TCP server already started.")

        self._thread = ExcepthookThread(target=self.run, excepthook=excepthook)
        if daemon is not None:
            self._thread.setDaemon(daemon)
        self._thread.start()
        if timeout:
            self._running.wait(timeout)
            if not self._running.isSet():
                raise RuntimeError("Device TCP server failed to start.")

    def join(self, timeout=None):
        """Rejoin the server thread.

        Parameters
        ----------
        timeout : float in seconds
            Time to wait for the thread to finish.
        """
        if not self._thread:
            raise RuntimeError("Device server thread not started.")

        self._thread.join(timeout)
        if not self._thread.isAlive():
            self._thread = None

    def stop(self, timeout=1.0):
        """Stop a running server (from another thread).

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for server to have *started*.
        """
        self._running.wait(timeout)
        if not self._running.isSet():
            raise RuntimeError("Attempt to stop server that wasn't running.")
        self._running.clear()

    def running(self):
        """Whether the server is running."""
        return self._running.isSet()

    def wait_running(self, timeout=None):
        """Wait until the server is running"""
        return self._running.wait(timeout=timeout)





class ClientConnectionTCP(object):
    # XXX TODO We should factor the whole TCP select loop (or future twisted
    # implementation?) out of the server class and into a Connection class that
    # spews katcp messages and ClientConnection* objects onto the katcp
    # server. This will allow us to abstract out the connection and allow us to
    # support serial connections cleanly
    def __init__(self, server, raw_socket):
        self.inform = partial(server.tcp_inform, raw_socket)
        self.inform.__doc__ = (
"""Send an inform message to a particular client.

Should only be used for asynchronous informs. Informs
that are part of the response to a request should use
:meth:`reply_inform` so that the message identifier
from the original request can be attached to the
inform.

Parameters
----------
msg : Message object
    The inform message to send.
    """)
        self.reply_inform = partial(server.tcp_reply_inform, raw_socket)
        self.reply_inform.__doc__ = (
"""Send an inform as part of the reply to an earlier request.

Parameters
----------
inform : Message object
    The inform message to send.
orig_req : Message object
    The request message being replied to. The inform message's
    id is overridden with the id from orig_req before the
    inform is sent.
""")
        self.reply = partial(server.tcp_reply, raw_socket)
        self.reply.__doc__ = (
"""Send an asynchronous reply to an earlier request.

Parameters
----------
reply : Message object
    The reply message to send.
orig_req : Message object
    The request message being replied to. The reply message's
    id is overridden with the id from orig_req before the
    reply is sent.
""")


class ClientRequestConnection(object):
    def __init__(self, client_connection, req_msg):
        self.client_connection = client_connection
        assert(req_msg.mtype == Message.REQUEST)
        self.msg = req_msg

    def inform(self, *args):
        inf_msg = Message.reply_inform(self.msg, *args)
        self.client_connection.inform(inf_msg)

    def reply(self, *args):
        rep_msg = Message.reply_to_request(self.msg, *args)
        self.client_connection.reply(rep_msg, self.msg)
        self._post_reply()

    def reply_with_message(self, rep_msg):
        """
        Send a pre-created reply message to the client connection

        Will check that rep_msg.name matches the bound request
        """
        assert rep_msg.name == self.msg.name
        self.client_connection.reply(rep_msg, self.msg)
        self._post_reply()

    def _post_reply(self):
        # Future calls to reply_*() should error out.
        self.reply = self.reply_again
        self.reply_with_message = self.reply_again

    def reply_again(self, *args):
        raise RuntimeError('Reply to request %r already sent.' % self.msg)

    def make_reply(self, *args):
        return Message.reply_to_request(self.msg, *args)

class DeviceServerBase(object):
    """Base class for device servers.

    Subclasses should add .request\_* methods for dealing
    with request messages. These methods each take the client
    request connection and msg objects as arguments and should return
    the reply message or raise an exception as a result.

    Subclasses can also add .inform\_* and reply\_* methods to handle
    those types of messages.

    Should a subclass need to generate inform messages it should
    do so using either the .inform() or .mass_inform() methods.

    Finally, this class should probably not be subclassed directly
    but rather via subclassing DeviceServer itself which implements
    common .request\_* methods.

    Parameters
    ----------
    host : str
        Host to listen on.
    port : int
        Port to listen on.
    tb_limit : int
        Maximum number of stack frames to send in error tracebacks.
    logger : logging.Logger object
        Logger to log messages to.
    """

    __metaclass__ = DeviceMetaclass
    MAX_DEFERRED_QUEUE_SIZE = 100000      # Maximum size of deferred action queue

    ## @brief Protocol versions and flags. Default to version 5, subclasses
    ## should override PROTOCOL_INFO
    PROTOCOL_INFO = ProtocolFlags(DEFAULT_KATCP_MAJOR, 0, set([
        ProtocolFlags.MULTI_CLIENT,
        ProtocolFlags.MESSAGE_IDS,
        ]))


    def __init__(self, host, port, tb_limit=20, logger=log):
        self._server = TCPServer(self, host, port, tb_limit, logger)
        self._logger = logger
        self._tb_limit = tb_limit

        self._deferred_queue = Queue.Queue(maxsize=self.MAX_DEFERRED_QUEUE_SIZE)

    def _log_msg(self, level_name, msg, name, timestamp=None):
        """Create a katcp logging inform message.

           Usually this will be called from inside a DeviceLogger object,
           but it is also used by the methods in this class when errors
           need to be reported to the client.
           """

        if timestamp is None:
            timestamp = time.time()

        katcp_version = self.PROTOCOL_INFO.major
        timestamp_msg = ('%.6f' % timestamp if katcp_version >= SEC_TS_KATCP_MAJOR
                         else str(int(timestamp*1000)) )
        return Message.inform("log",
                level_name,
                timestamp_msg,  # time since epoch in seconds
                name,
                msg,
        )



    def handle_message(self, client_conn, msg):
        """Handle messages of all types from clients.

        Parameters
        ----------
        client_conn : ClientConnectionTCP object
            The client connection the message was from.
        msg : Message object
            The message to process.
        """
        # log messages received so that no one else has to
        self._logger.debug(str(msg))

        if msg.mtype == msg.REQUEST:
            self.handle_request(client_conn, msg)
        elif msg.mtype == msg.INFORM:
            self.handle_inform(client_conn, msg)
        elif msg.mtype == msg.REPLY:
            self.handle_reply(client_conn, msg)
        else:
            reason = "Unexpected message type received by server ['%s']." \
                     % (msg,)
            client_conn.inform(self._log_msg("error", reason, "root"))

    def handle_request(self, connection, msg):
        """Dispatch a request message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client connection the message was from.
        msg : Message object
            The request message to process.
        """
        send_reply = True
        # TODO Should check presence of Message-ids against protocol flags and
        # raise an error as needed.
        if msg.name in self._request_handlers:
            req_conn = ClientRequestConnection(connection, msg)
            try:
                reply = self._request_handlers[msg.name](self, req_conn, msg)
                assert (reply.mtype == Message.REPLY)
                assert (reply.name == msg.name)
                self._logger.debug("%s OK" % (msg.name,))
            except AsyncReply, e:
                self._logger.debug("%s ASYNC OK" % (msg.name,))
                send_reply = False
            except FailReply, e:
                reason = str(e)
                self._logger.error("Request %s FAIL: %s" % (msg.name, reason))
                reply = Message.reply(msg.name, "fail", reason)
            # We do want to catch everything that inherits from Exception
            # pylint: disable-msg = W0703
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("Request %s FAIL: %s" % (msg.name, reason))
                reply = Message.reply(msg.name, "fail", reason)
        else:
            self._logger.error("%s INVALID: Unknown request." % (msg.name,))
            reply = Message.reply(msg.name, "invalid", "Unknown request.")

        if send_reply:
            connection.reply(reply, msg)

    def handle_inform(self, connection, msg):
        """Dispatch an inform message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client connection the message was from.
        msg : Message object
            The inform message to process.
        """
        if msg.name in self._inform_handlers:
            try:
                self._inform_handlers[msg.name](self, connection, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("Inform %s FAIL: %s" % (msg.name, reason))
        else:
            self._logger.warn("%s INVALID: Unknown inform." % (msg.name,))

    def handle_reply(self, connection, msg):
        """Dispatch a reply message to the appropriate method.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client connection the message was from.
        msg : Message object
            The reply message to process.
        """
        if msg.name in self._reply_handlers:
            try:
                self._reply_handlers[msg.name](self, connection, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, self._tb_limit))
                self._logger.error("Reply %s FAIL: %s" % (msg.name, reason))
        else:
            self._logger.warn("%s INVALID: Unknown reply." % (msg.name,))


    def inform(self, connection, msg):
        """Send an inform message to a particular client.

        Should only be used for asynchronous informs. Informs
        that are part of the response to a request should use
        :meth:`reply_inform` so that the message identifier
        from the original request can be attached to the
        inform.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client to send the message to.
        msg : Message object
            The inform message to send.
        """
        if isinstance(connection, ClientRequestConnection):
            self._logger.warn(
                'Deprecation warning: do not use self.inform() '
                'within a reply handler context -- use req.inform()\n'
                'Traceback:\n %s', "".join(traceback.format_stack() ))
            # Get the underlying ClientConnectionTCP instance
            connection = connection.client_connection
        connection.inform(msg)

    def mass_inform(self, msg):
        """Send an inform message to all clients.

        Parameters
        ----------
        msg : Message object
            The inform message to send.
        """
        assert (msg.mtype == Message.INFORM)
        self._server.mass_inform(self, msg)


    def reply(self, connection, reply, orig_req):
        """Send an asynchronous reply to an earlier request.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client to send the reply to.
        reply : Message object
            The reply message to send.
        orig_req : Message object
            The request message being replied to. The reply message's
            id is overridden with the id from orig_req before the
            reply is sent.
        """
        if isinstance(connection, ClientRequestConnection):
            self._logger.warn(
                'Deprecation warning: do not use self.reply() '
                'within a reply handler context -- use req.reply(*msg_args)\n'
                'or req.reply_with_message(msg) Traceback:\n %s',
                "".join(traceback.format_stack() ))
            # Get the underlying ClientConnectionTCP instance
            connection = connection.client_connection
        connection.reply(reply, orig_req)

    def reply_inform(self, connection, inform, orig_req):
        """Send an inform as part of the reply to an earlier request.

        Parameters
        ----------
        connection : ClientConnectionTCP object
            The client to send the inform to.
        inform : Message object
            The inform message to send.
        orig_req : Message object
            The request message being replied to. The inform message's
            id is overridden with the id from orig_req before the
            inform is sent.
        """
        if isinstance(connection, ClientRequestConnection):
            self._logger.warn(
                'Deprecation warning: do not use self.reply_inform() '
                'within a reply handler context -- '
                'use req.inform(*inform_arguments)\n'
                'Traceback:\n %s', "".join(traceback.format_stack() ))
            # Get the underlying ClientConnectionTCP instance
            connection = connection.client_connection
        connection.reply_inform(inform, orig_req)

    def _process_deferred_queue(self, max_items=0):
        """
        Process deferred queue

        Items in self._deferred_queue are assumed to be functions that
        are to be called once.

        Parameters
        ==========

        max_items: int
            Maximum number of items to process, 0 if the whole
            queue is to be processed
        """
        processed = 0
        while max_items == 0 or processed < max_items:
            try:
                task = self._deferred_queue.get_nowait()
            except Queue.Empty:
                break
            task()                        # Execute the task
            self._deferred_queue.task_done()   # tell the queue that it is done
            processed = processed + 1


    def start(self, timeout=None, daemon=None, excepthook=None):
        """Start the server in a new thread.

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
        self._server.start(timeout, daemon, excepthook)

    def join(self, timeout=None):
        """Rejoin the server thread.

        Parameters
        ----------
        timeout : float in seconds
            Time to wait for the thread to finish.
        """
        self._server.join(timeout)

    def stop(self, timeout=1.0):
        """Stop a running server (from another thread).

        Parameters
        ----------
        timeout : float in seconds
            Seconds to wait for server to have *started*.
        """
        self._server.stop(timeout)

    def running(self):
        """Whether the server is running."""
        return self._server.running()

    def wait_running(self, timeout=None):
        """Wait until the server is running"""
        return self._server.wait_running(timeout)

    def on_client_connect(self, conn):
        """Called after client connection is established.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point.

        Parameters
        ----------
        conn : ClientConnectionTCP object
            The client connection that has been successfully established.
        """
        pass

    def on_client_disconnect(self, conn, msg, connection_valid):
        """Called before a client connection is closed.

        Subclasses should override if they wish to send clients
        message or perform house-keeping at this point. The server
        cannot guarantee this will be called (for example, the client
        might drop the connection). The message parameter contains
        the reason for the disconnection.

        Parameters
        ----------
        conn : ClientConnectionTCP object
            Client connection being disconnected.
        msg : str
            Reason client is being disconnected.
        connection_valid : boolean
            True if connection is still open for sending,
            False otherwise.
        """
        pass


