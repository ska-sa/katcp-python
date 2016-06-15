.. _Tutorial:

********
Tutorial
********

 .. module:: katcp

Installing the Python Katcp Library
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can install the latest version of the KATCP library by running :command:`pip
install katcp` if pip is installed. Alternatively, :command:`easy_install
katcp`; requires the setuptools Python package to be installed.

Using the Blocking Client
^^^^^^^^^^^^^^^^^^^^^^^^^

The blocking client is the most straight-forward way of
querying a KATCP device. It is used as follows::

    from katcp import BlockingClient, Message

    device_host = "www.example.com"
    device_port = 5000

    client = BlockingClient(device_host, device_port)
    client.start()
    client.wait_protocol() # Optional

    reply, informs = client.blocking_request(
        Message.request("help"))

    print reply
    for msg in informs:
        print msg

    client.stop()
    client.join()

After creating the :class:`BlockingClient <katcp.BlockingClient>` instance, the
:meth:`start` method is called to launch the client thread.  The
:meth:`wait_protocol` method waits until katcp version information has been
received from the server, allowing the KATCP version spoken by the server to be
known; server protocol iformation is stores in client.protocol_flags. Once you
have finished with the client, :meth:`stop` can be called to request that the
thread shutdown. Finally, :meth:`join` is used to wait for the client thread to
finish.

While the client is active the :meth:`blocking_request()
<katcp.BlockingClient.blocking_request>` method can be used to send messages to
the KATCP server and wait for replies. If a reply is not received within the
allowed time, a :exc:`RuntimeError` is raised.

If a reply is received :meth:`blocking_request()
<katcp.BlockingClient.blocking_request>` returns two values. The first is the
:class:`Message <katcp.Message>` containing the reply. The second is a list of
messages containing any KATCP informs associated with the reply.


Using the Callback Client
^^^^^^^^^^^^^^^^^^^^^^^^^

For situations where one wants to communicate with a server
but doesn't want to wait for a reply, the
:class:`CallbackClient <katcp.CallbackClient>` is provided::


    from katcp import CallbackClient, Message

    device_host = "www.example.com"
    device_port = 5000

    def reply_cb(msg):
        print "Reply:", msg

    def inform_cb(msg):
        print "Inform:", msg

    client = CallbackClient(device_host, device_port)
    client.start()

    reply, informs = client.callback_request(
        Message.request("help"),
        reply_cb=reply_cb,
        inform_cb=inform_cb,
    )

    client.stop()
    client.join()

Note that the :func:`reply_cb` and :func:`inform_cb` callback functions are both
called inside the client's event-loop thread so should not perform any
operations that block. If needed, pass the data out from the callback
function to another thread using a :class:`Queue.Queue` or similar
structure.


Writing your own Client
^^^^^^^^^^^^^^^^^^^^^^^

If neither the :class:`BlockingClient <katcp.BlockingClient>` nor
the :class:`CallbackClient <katcp.CallbackClient>` provide the
functionality you need then you can sub-class
:class:`DeviceClient <katcp.DeviceClient>` which is the base class
from which both are derived.

:class:`DeviceClient` has two methods for sending messages:

    * :meth:`request() <katcp.DeviceClient.request>` for sending request
      :class:`Messages <katcp.Message>`
    * :meth:`send_message <katcp.DeviceClient.send_message>` for sending
      arbitrary :class:`Messages <katcp.Message>`

Internally :meth:`request <katcp.DeviceClient.request>` calls
:meth:`send_message <katcp.DeviceClient.send_message>` to pass messages to the
server.

.. note::

    The :meth:`send_message() <DeviceClient.send_message>` method does not
    return an error code or raise an exception if sending the message
    fails. Since the underlying protocol is entirely asynchronous, the only
    means to check that a request was successful is receive a reply message. One
    can check that the client is connected before sending a message using
    :meth:`is_connected() <DeviceClient.is_connected>`.

When the :class:`DeviceClient` thread receives a completed message,
:meth:`handle_message` is called.  The default :meth:`handle_message()
<DeviceClient.handle_message>` implementation calls one of :meth:`handle_reply()
<DeviceClient.handle_reply>`, :meth:`handle_inform()
<DeviceClient.handle_inform>` or :meth:`handle_request()
<DeviceClient.handle_request>` depending on the type of message received.

.. note::

    Sending requests to clients is discouraged. The :meth:`handle_request`
    is provided mostly for completeness and to deal with unforseen
    circumstances.

Each of :meth:`handle_reply`, :meth:`handle_inform` and :meth:`handle_request`
dispatches messages to methods based on the message name. For example,
a reply message named :samp:`foo` will be dispatched to :meth:`reply_foo`.
Similarly an inform message named :samp:`bar` will be dispatched to
:meth:`inform_bar`.  If no corresponding method is found then one of
:meth:`unhandled_reply`, :meth:`unhandled_inform` or :meth:`unhandled_request`
is called.

Your own client may hook into this dispatch tree at any point by implementing
or overriding the appropriate methods.

An example of a simple client that only handles replies to :samp:`help`
messages is presented below::

    from katcp import DeviceClient, Message
    import time

    device_host = "www.example.com"
    device_port = 5000

    class MyClient(DeviceClient):

        def reply_help(self, msg):
            """Print out help replies."""
            print msg.name, msg.arguments

        def inform_help(self, msg):
            """Print out help inform messages."""
            meth, desc = msg.arguments[:2]
            print "---------", meth, "---------"
            print
            print desc
            print "----------------------------"

        def unhandled_reply(self, msg):
            """Print out unhandled replies."""
            print "Unhandled reply", msg.name

        def unhandled_inform(self, msg):
            "Print out unhandled informs."""
            print "Unhandled inform", msg.name


    client = MyClient(device_host, device_port)
    client.start()

    client.request(Message.request("help"))
    client.request(Message.request("watchdog"))

    time.sleep(0.5)

    client.stop()
    client.join()


Client handler functions can use the :func:`unpack_message()
<katcp.kattypes.unpack_message>` decorator from `kattypes` module to unpack
messages into function arguments in the same way the :func:`request()
<katcp.kattypes.request>` decorator is used in the server example below, except
that the `req` parameter is omitted.

.. _Tutorial_high_level_client:

Using the high-level client API
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The high level client API inspects a KATCP device server and presents requests as
method calls and sensors as objects.

A high level client for the example server presented in the following section: ::

    import tornado

    from tornado.ioloop import IOLoop
    from katcp import resource_client

    ioloop = IOLoop.current()

    client = resource_client.KATCPClientResource(dict(
        name='demo-client',
        address=('localhost', 5000),
        controlled=True))

    @tornado.gen.coroutine
    def demo():
        # Wait until the client has finished inspecting the device
        yield client.until_synced()
        help_response = yield client.req.help()
        print "device help:\n ", help_response
        add_response = yield client.req.add(3, 6)
        print "3 + 6 response:\n", add_response
        # By not yielding we are not waiting for the response
        pick_response_future = client.req.pick_fruit()
        # Instead we wait for the fruit.result sensor status to change to
        # nominal. Before we can wait on a sensor, a strategy must be set:
        client.sensor.fruit_result.set_strategy('event')
        # If the condition does not occur within the timeout (default 5s), we will
        # get a TimeoutException
        yield client.sensor.fruit_result.wait(
            lambda reading: reading.status == 'nominal')
        fruit = yield client.sensor.fruit_result.get_value()
        print 'Fruit picked: ', fruit
        # And see how the ?pick-fruit request responded by yielding on its future
        pick_response = yield pick_response_future
        print 'pick response: \n', pick_response
        # Finally stop the ioloop so that the program exits
        ioloop.stop()

    # Note, katcp.resource_client.ThreadSafeKATCPClientResourceWrapper can be used to
    # turn the client into a 'blocking' client for use in e.g. ipython. It will turn
    # all functions that return tornado futures into blocking calls, and will bounce
    # all method calls through the ioloop. In this case the ioloop must be started
    # in a separate thread. katcp.ioloop_manager.IOLoopManager can be used to manage
    # the ioloop thread.

    ioloop.add_callback(client.start)
    ioloop.add_callback(demo)
    ioloop.start()


Writing your own Server
^^^^^^^^^^^^^^^^^^^^^^^

Creating a server requires sub-classing :class:`DeviceServer
<katcp.DeviceServer>`.  This class already provides all the requests and inform
messages required by the KATCP protocol.  However, its implementation requires a
little assistance from the subclass in order to function.

A very simple server example looks like::

  import threading
  import time
  import random

  from katcp import DeviceServer, Sensor, ProtocolFlags, AsyncReply
  from katcp.kattypes import (Str, Float, Timestamp, Discrete,
                              request, return_reply)

  server_host = ""
  server_port = 5000

  class MyServer(DeviceServer):

      VERSION_INFO = ("example-api", 1, 0)
      BUILD_INFO = ("example-implementation", 0, 1, "")

      # Optionally set the KATCP protocol version and features. Defaults to
      # the latest implemented version of KATCP, with all supported optional
      # features
      PROTOCOL_INFO = ProtocolFlags(5, 0, set([
          ProtocolFlags.MULTI_CLIENT,
          ProtocolFlags.MESSAGE_IDS,
      ]))

      FRUIT = [
          "apple", "banana", "pear", "kiwi",
      ]

      def setup_sensors(self):
          """Setup some server sensors."""
          self._add_result = Sensor.float("add.result",
              "Last ?add result.", "", [-10000, 10000])

          self._time_result = Sensor.timestamp("time.result",
              "Last ?time result.", "")

          self._eval_result = Sensor.string("eval.result",
              "Last ?eval result.", "")

          self._fruit_result = Sensor.discrete("fruit.result",
              "Last ?pick-fruit result.", "", self.FRUIT)

          self.add_sensor(self._add_result)
          self.add_sensor(self._time_result)
          self.add_sensor(self._eval_result)
          self.add_sensor(self._fruit_result)

      @request(Float(), Float())
      @return_reply(Float())
      def request_add(self, req, x, y):
          """Add two numbers"""
          r = x + y
          self._add_result.set_value(r)
          return ("ok", r)

      @request()
      @return_reply(Timestamp())
      def request_time(self, req):
          """Return the current time in seconds since the Unix Epoch."""
          r = time.time()
          self._time_result.set_value(r)
          return ("ok", r)

      @request(Str())
      @return_reply(Str())
      def request_eval(self, req, expression):
          """Evaluate a Python expression."""
          r = str(eval(expression))
          self._eval_result.set_value(r)
          return ("ok", r)

      @request()
      @return_reply(Discrete(FRUIT))
      def request_pick_fruit(self, req):
          """Pick a random fruit."""
          r = random.choice(self.FRUIT + [None])
          if r is None:
              return ("fail", "No fruit.")
          delay = random.randrange(1,5)
          req.inform("Picking will take %d seconds" % delay)

          def pick_handler():
              self._fruit_result.set_value(r)
              req.reply("ok", r)

          self.ioloop.add_callback(
            self.ioloop.call_later, delay, pick_handler)

          raise AsyncReply

      def request_raw_reverse(self, req, msg):
          """
          A raw request handler to demonstrate the calling convention if
          @request decoraters are not used. Reverses the message arguments.
          """
          # msg is a katcp.Message.request object
          reversed_args = msg.arguments[::-1]
          # req.make_reply() makes a katcp.Message.reply using the correct request
          # name and message ID
          return req.make_reply('ok', *reversed_args)


  if __name__ == "__main__":

      server = MyServer(server_host, server_port)
      server.start()
      server.join()


Notice that :class:`MyServer` has three special class attributes
:const:`VERSION_INFO`, :const:`BUILD_INFO` and
:const:`PROTOCOL_INFO`. :const:`VERSION_INFO` gives the version of the server
API. Many implementations might use the same
:const:`VERSION_INFO`. :const:`BUILD_INFO` gives the version of the software
that provides the device. Each device implementation should have a unique
:const:`BUILD_INFO`. :const:`PROTOCOL_INFO` is an instance of
:class:`ProtocolFlags` that describes the KATCP dialect spoken by the server. If
not specified, it defaults to the latest implemented version of KATCP, with all
supported optional features. Using a version different from the default may
change server behaviour; furthermore version info may need to be passed to the
:func:`@request <katcp.kattypes.request>` and :func:`@return_reply
<katcp.kattypes.return_reply>` decorators.

The :meth:`setup_sensors` method registers :class:`Sensor <katcp.Sensor>`
objects with the device server. The base class uses this information to
implement the :samp:`?sensor-list`, :samp:`?sensor-value` and
:samp:`?sensor-sampling` requests.  :meth:`add_sensor()
<katcp.DeviceServer.add_sensor>` should be called once for each sensor the
device should contain. You may create the sensor objects inside
:meth:`setup_sensors` (as done in the example) or elsewhere if you wish.

Request handlers are added to the server by creating methods whose names start
with "request\_".  These methods take two arguments -- the client-request object
(abstracts the client socket and the request context) that the request came from,
and the request message.  Notice that the message argument is missing from the
methods in the example. This is a result of the :meth:`request()
<katcp.kattypes.request>` decorator that has been applied to the methods.

The :meth:`request() <katcp.kattypes.request>` decorator takes a list of
:class:`KatcpType <katcp.kattypes.KatcpType>` objects describing the request
arguments. Once the arguments have been checked they are passed in to the
underlying request method as additional parameters instead of the request
message.

The :meth:`return_reply <katcp.kattypes.return_reply()>` decorator performs a
similar operation for replies. Once the request method returns a tuple (or list)
of reply arguments, the decorator checks the values of the arguments and
constructs a suitable reply message.

Use of the :func:`request() <katcp.kattypes.request>` and :func:`return_reply()
<katcp.kattypes.return_reply>` decorators is encouraged but entirely optional.

Message dispatch is handled in much the same way as described in the client
example, with the exception that there are no :meth:`unhandled_request`,
:meth:`unhandled_reply` or :meth:`unhandled_request` methods. Instead, the
server will log an exception.

Writing your own Async Server
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To write a server in the typical tornado async style, modify the example above by
adding the following imports ::

  import signal
  import tornado

  from katcp import AsyncDeviceServer

Also replace `class MyServer(DeviceServer)` with `class
MyServer(AsyncDeviceServer)` and replace the `if __name__ == "__main__":` block
with ::

  @tornado.gen.coroutine
  def on_shutdown(ioloop, server):
      print('Shutting down')
      yield server.stop()
      ioloop.stop()

  if __name__ == "__main__":
      ioloop = tornado.ioloop.IOLoop.current()
      server = MyServer(server_host, server_port)
      # Hook up to SIGINT so that ctrl-C results in a clean shutdown
      signal.signal(signal.SIGINT, lambda sig, frame: ioloop.add_callback_from_signal(
	  on_shutdown, ioloop, server))
      ioloop.add_callback(server.start)
      ioloop.start()

If multiple servers are started in a single ioloop, :func:`on_shutdown` should
be modified to call :meth:`stop` on each server. This is needed to allow a clean
shutdown that adheres to the KATCP spec requirement that a `#disconnect` inform
is sent when a server shuts down.

Event Loops and Thread Safety
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

As of version 0.6.0, katcp-python was completely reworked to use Tornado as an
event- and network library. A typical Tornado application would only use a
single `tornado.ioloop.IOLoop` event-loop instance. Logically independent parts of the
application would all share the same ioloop using e.g. coroutines to allow
concurrent tasks.

However, to maintain backwards compatiblity with the thread-semantics of older
versions of this library, it supports starting a `tornado.ioloop.IOLoop`
instance in a new thread for each client or server. Instantiating the
:class:`BlockingClient` or :class:`CallbackClient` client classes or the
:class:`DeviceServer` server class will implement the backward compatible
behaviour by default, while using :class:`AsyncClient` or
:class:`AsyncDeviceServer` will by default use `tornado.ioloop.IOLoop.current()`
as the ioloop (can be overidden using their `set_ioloop` methods), and won't
enable thread safety by default (can be overridden using
:meth:`AsyncDeviceServer.set_concurrency_options` and
:meth:`AsyncClient.enable_thread_safety`)

Note that any message (request, reply, iform) handling methods should not
block. A blocking handler will block the ioloop, causing all timed operations
(e.g. sensor strategies), network io, etc. to block. This is particularly
important when multiple servers/clients share a single ioloop. A good solution
for handlers that need to wait on other tasks is to implement them as Tornado
couroutines. A :class:`DeviceServer` will not accept another request message
from a client connection until the request handler has completed / resolved its
future. Multiple outstanding requests can be handled concurrently by raising the
:class:`AsyncReply` exception in a request handler. It is then the
responsibility of the user to ensure that a reply is eventually sent using the
`req` object.

If :meth:`DeviceServer.set_concurrency_options` has `handler_thread=True` (the
default for :class:`DeviceServer`, :class:`AsyncDeviceServer` defaults to
`False`), all the requests to a server is serialised and handled in a separate
request handing thread. This allows request handlers to block without preventing
sensor strategy updates, providing backwards-compatible concurrency
semantics.

In the case of a purely network-event driven server or client, all user code
would execute in the thread context of the server or client event
loop. Therefore all handler functions must be non-blocking to prevent
unresponsiveness. Unhandled exceptions raised by handlers running in the network
event-thread are caught and logged; in the case of servers, an error reply
including the traceback is sent over the network interface. Slow operations
(such as picking fruit) may be delegated to another thread (if a threadsafe
server is used), a callback (as shown in the `request_pick_fruit` handler in the
server example) or tornado coroutine.

If a device is linked to processing that occurs independently of network events,
one approach would be a model thread running in the background. The KATCP
handler code would then defer requests to the model. The model must provide a
thread-safe interface to the KATCP code. If using an async server
(e.g. :class:`AsyncDeviceServer` or :meth:`DeviceServer.set_concurrency_options`
called with `thread_safe=False`), all interaction with the device server needs
to be through the :meth:`tornado.ioloop.Ioloop.add_callback` method of the
server's ioloop. The server's ioloop instance can be accessed through its
`ioloop` attribute. If a threadsafe server (e.g. :class:`DeviceServer` with
default concurrency options) or client (e.g. :class:`CallbackClient`) is used, 
all the public methods provided by this katcp library for sending `!replies` or
`#informs` are thread safe.

Updates to :class:`Sensor` objects using the public setter methods are always
thread-safe, provided that the same is true for all the observers attached to
the sensor. The server observers used to implement sampling strategies are
threadsafe, even if an asyc server is used.

Backwards Compatibility
^^^^^^^^^^^^^^^^^^^^^^^

Server Protocol Backwards Compatibility
---------------------------------------

A minor modification of the first several lines of the example in
`Writing your own Server`_ suffices to create a KATCP v4 server::

  from katcp import DeviceServer, Sensor, ProtocolFlags, AsyncReply
  from katcp.kattypes import (Str, Float, Timestamp, Discrete,
                              request, return_reply)

  from functools import partial
  import threading
  import time
  import random

  server_host = ""
  server_port = 5000

  # Bind the KATCP major version of the request and return_reply decorators
  # to version 4
  request = partial(request, major=4)
  return_reply = partial(return_reply, major=4)

  class MyServer(DeviceServer):

      VERSION_INFO = ("example-api", 1, 0)
      BUILD_INFO = ("example-implementation", 0, 1, "")

      # Optionally set the KATCP protocol version as 4.
      PROTOCOL_INFO = ProtocolFlags(4, 0, set([
          ProtocolFlags.MULTI_CLIENT,
      ]))

The rest of the example follows as before.

Client Protocol Backwards Compatibility
---------------------------------------

The :meth:`DeviceClient <katcp.DeviceClient>` client automatically detects the
version of the server if it can, see
:ref:`release_notes_0_5_0a0_server_version_auto_detection`. For a simple client
this means that no changes are required to support different KATCP
versions. However, the semantics of the messages might be different for
different protocl versions. Using the :func:`unpack_message
<katcp.kattypes.unpack_message>` decorator with `major=4` for reply or inform
handlers might help here, although it could use some `improvement
<https://github.com/ska-sa/katcp-python/issues/1>`_.

In the case of version auto-dection failing for a given server, 
:meth:`preset_protocol_flags <katcp.DeviceClient.preset_protocol_flags>` can be
used to set the KATCP version before calling the client's :meth:`start` method.
