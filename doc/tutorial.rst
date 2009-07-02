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

Writing your own Server
^^^^^^^^^^^^^^^^^^^^^^^

Coming soon!

.. todo::

    Write this section.

