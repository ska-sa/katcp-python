.. Tutorial

********
Tutorial
********

Installing the Python Katcp Library
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can install the latest version of the KATCP library by
running :command:`easy_install katcp`. This requires the
setuptools Python package to be installed.

Using the Blocking Client
^^^^^^^^^^^^^^^^^^^^^^^^^

The blocking client is the most straight-forward way of
querying a KATCP device. It is used as follows::

    from katcp import BlockingClient, Message

    device_host = "www.example.com"
    device_port = 5000

    client = BlockingClient(device_host, device_port)
    client.start()

    reply, informs = client.blocking_request(
        Message.request("help"))

    print reply
    for msg in informs:
        print msg

    client.stop()
    client.join()

After creating the :class:`BlockingClient <katcp.BlockingClient>`
instance, the :meth:`start` method is called to launch the client
thread.  Once you have finished with the client, :meth:`stop`
can be called to request that the thread shutdown. Finally,
:meth:`join` is used to wait for the client thread to finish.

While the client is active the :meth:`blocking_request` method
can be used to send messages to the KATCP server and wait for
replies. If a reply is not received within the allowed time, a
:exc:`RuntimeError` is raised.

If a reply is received :meth:`blocking_request` returns two
values. The first is the :class:`Message <katcp.Message>`
containing the reply. The second is a list of messages
containing any KATCP informs associated with the reply. 


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

    reply, informs = client.request(
        Message.request("help"),
        reply_cb=reply_cb,
        inform_cb=inform_cb,
    )

    client.stop()
    client.join()

Note that the :func:`reply_cb` and :func:`inform_cb` callback
functions are both called inside the clients' processing thread
and so should not perform any operations that block. If needed,
pass the data out to from the callback function to another
thread using a :class:`Queue <Queue.Queue>` or similar structure.


Writing your own Client
^^^^^^^^^^^^^^^^^^^^^^^

If neither the :class:`BlockingClient <katcp.BlockingClient>` nor
the :class:`CallbackClient <katcp.CallbackClient>` provide the
functionality you need then you can sub-class
:class:`DeviceClient <katcp.DeviceClient>` which is the base class
from which both are derived.

:class:`DeviceClient` has two methods for sending messages:

    * :meth:`request` for sending request :class:`Messages <katcp.Message>`
    * :meth:`send_message` for sending arbitrary :class:`Messages <katcp.Message>`

Internally :meth:`request` calls :meth:`send_message` to pass messages to
the server.

.. note::

    The :meth:`send_message` method does not return an error code or raise an
    exception if sending the message fails. Since the underlying protocol is
    entirely asynchronous, the only means to check that a request was successful
    is receive a reply message. One can check that the client is connected
    before sending a message using :meth:`is_connected`.

When the :class:`DeviceClient` thread receives a completed message
:meth:`handle_message` is called.  The default :meth:`handle_message`
implementation calls one of :meth:`handle_reply`, :meth:`handle_inform`
or :meth:`handle_request` depending on the type of message received.

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

Your own client may hook into this dispath tree at any point by implementing
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


Writing your own Server
^^^^^^^^^^^^^^^^^^^^^^^

Creating a server requires sub-classing :class:`DeviceServer <katcp.DeviceServer>`.
This class already provides all the requests and inform messages required by the
KATCP protocol.  However, its implementations require a little assistance from the
sub-class in order to function.

A very simple server example looks like::

    from katcp import DeviceServer, Sensor
    from katcp.kattypes import Str, Float, Timestamp, Discrete, request, return_reply
    import time
    import random

    server_host = ""
    server_port = 5000

    class MyServer(DeviceServer):

        VERSION_INFO = ("example-api", 1, 0)
        BUILD_INFO = ("example-implementation", 0, 1, "")

        FRUIT = [
            "apple", "banana", "pear", "kiwi",
        ]

        def setup_sensors(self):
            """Setup some server sensors."""
            self._add_result = Sensor(Sensor.FLOAT, "add.result",
                "Last ?add result.", "", [-10000, 10000])

            self._time_result = Sensor(Sensor.TIMESTAMP, "time.result",
                "Last ?time result.", "")

            self._eval_result = Sensor(Sensor.STRING, "eval.result",
                "Last ?eval result.", "")

            self._fruit_result = Sensor(Sensor.DISCRETE, "fruit.result",
                "Last ?pick-fruit result.", "", self.FRUIT)

            self.add_sensor(self._add_result)
            self.add_sensor(self._time_result)
            self.add_sensor(self._eval_result)
            self.add_sensor(self._fruit_result)

        @request(Float(), Float())
        @return_reply(Float())
        def request_add(self, sock, x, y):
            """Add two numbers"""
            r = x + y
            self._add_result.set_value(r)
            return ("ok", r)

        @request()
        @return_reply(Timestamp())
        def request_time(self, sock):
            """Return the current time in ms since the Unix Epoch."""
            r = time.time()
            self._time_result.set_value(r)
            return ("ok", r)

        @request(Str())
        @return_reply(Str())
        def request_eval(self, sock, expression):
            """Evaluate a Python expression."""
            r = str(eval(expression))
            self._eval_result.set_value(r)
            return ("ok", r)

        @request()
        @return_reply(Discrete(FRUIT))
        def request_pick_fruit(self, sock):
            """Pick a random fruit."""
            r = random.choice(self.FRUIT + [None])
            if r is None:
                return ("fail", "No fruit.")
            self._fruit_result.set_value(r)
            return ("ok", r)

    if __name__ == "__main__":

        server = MyServer(server_host, server_port)
        server.start()
        server.join()


Notice that :class:`MyServer` has two special class attributes :const:`VERSION_INFO` and
:const:`BUILD_INFO`. :const:`VERSION_INFO` gives the version of the server API. Many
implementations might use the same :const:`VERSION_INFO`. :const:`BUILD_INFO` gives the
version of the software that provides the device. Each device implementation should have
a unique :const:`BUILD_INFO`.

The :meth:`setup_sensors` method registers :class:`Sensor <katcp.Sensor>` objects with
the device server. The base class uses this information to implement the :samp:`?sensor-list`,
:samp:`?sensor-value` and :samp:`?sensor-sampling` requests.  :meth:`add_sensor` should be
called once for each sensor the device should contain. You may create the sensor objects
inside :meth:`setup_sensors` (as done in the example) or elsewhere if you wish.

Request handlers are added to the server by creating methods whose names start with
"request\_".  These methods take two arguments -- the client socket that the request
came from and the request message.  Notice that the message argument is missing from the
methods in the example. This is a result of the :meth:`request` decorator that has been
applied to the methods.

The :meth:`request` decorator takes a list of :class:`kattype <katcp.kattypes.KatcpType`
objects describing the request arguments. Once the arguments have been checked they are
passed in to the underly request method as additional parameters instead of the request
message.

The :meth:`return_reply` decorator performs a similar operation for replies. Once the
request method returns a tuple (or list) of reply arguments, the decorator checks the
values of the arguments and constructs a suitable reply message.

Use of the :meth:`request` and :meth:`return_reply` decorators is encouraged but entirely
optional.
