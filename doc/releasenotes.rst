.. _Release Notes:

*************
Release Notes
*************
0.9.3
=====
* Make compatible in Python 3.9 by addressing deprecation issues.

0.9.2
=====
* Consistent error message in py2 and py3 for error in Timestamp decode

0.9.1
=====
* Fix issues in KATCPReply `__repr__` in py3

0.9.0
=====
* Add asyncio compatible ioloop to ioloop manager.

0.8.0
=====
* Added bulk sensor sampling feature.

0.7.2
=====
* Support for handling generator expressions in ``Discrete`` type.
* Fix handling of strings and bytes in ``get_sensor`` in testutils.
* Allow strings or bytes for ``assert_request_fails`` and ``test_assert_request_succeeds`` function arguments.
* Handle ``str`` type correctly ('easier') in ``testutils.get_sensor`` for python 2 and python 3.
* Allow bytes and strings in ``test_sensor_list`` comparison of sensors.
* Correct handling of floats ``test_sensor_list``.
* black formatting on certain test files.

0.7.1
=====
* All params in ``future_get_sensor`` are now cast to byte strings.
* Added tests to ``test_fake_clients.py`` and ``test_inspecting_client.py``.
* Ensure ``testutils`` method casts expected requests to byte strings.

0.7.0
=====
* Added Python 3 compatibility.

See also :download:`CHANGELOG.md` for more details on changes.

Important changes for Python 3 compatibility
--------------------------------------------

General notes
^^^^^^^^^^^^^

The package is now compatible with both Python 2 and 3.  The goals of the
migration were:

* Do not change the public API.
* Do not break existing functionality for Python 2.
* Ease migration of packages using katcp to Python 3.

Despite these goals, some of the stricter type checking that has been added
may force minor updates in existing code.  E.g., using integer for the options
of a discrete sensor is no longer allowed.

Asynchronous code is still using tornado in the same Python 2 way.  The new
Python 3.5 ``async`` and ``await`` keywords are not used.  The tornado version
is also pinned to older versions that support both Python 2 and 3.  The 5.x
versions also support Python 2, but they are avoided as some significant
changes result in test failures.

The Python ``future`` package was used for the compatibility layer.  The use of
the ``newstr`` and ``newbytes`` compatibility types was avoided, to reduce
confusion.  I.e., ``from builtins import str, bytes`` is not done.

Docstrings
^^^^^^^^^^

In docstrings the interpretation of parameter and return types described
as "str" has changed slightly.  In Python 2 the ``str`` type is a byte
string, while in Python 3, ``str`` is a unicode string.  The ``str`` type
is referred to as the "native" string type.  In code, native literal strings
would have no prefix, for example: ``"native string"``, as opposed to
explicit byte strings, ``b"byte string"``, and explicit unicode strings,
``u"unicode string"``.  In the docstrings "bytes" means a byte string is
expected (or returned), "str" means a native string, and "str or bytes"
means either type.

Changes to types
^^^^^^^^^^^^^^^^

As part of the Python 3 compatibility update, note the following:

- :class:`katcp.Message`.
  - ``arguments`` and ``mid`` attributes will be forced to byte strings in all
  Python versions.  This is to match what is sent on the wire (serialised
  byte stream).
  - ``name``: is expected to be a native string.
  - ``repr()``:  the result will differ slightly in Python 3 - the arguments
  will be shown as quoted byte strings. E.g., Python 2: ``"<Message reply ok
  (123, zzz)>"``, vs. Python 3:  ``"<Message reply ok (b'123', b'zzz')>"``.
  In all versions, arguments longer than 1000 characters are now truncated.
- :class:`katcp.Sensor`.
  - ``name``, ``description``, ``units``, ``params`` (for discrete sensors):
  ``__init__`` can take byte strings or native strings, but attributes will
  be coerced to native strings.
  - ``set_formatted``, ``parse_value``:  only accept byte strings (stricter
  checking).
  - The ``float`` and ``strict_timestamp`` sensor values are now encoded using
  ``repr()`` instead of ``"%.15g"``.  This means that more significant digits
  are transmitted on the wire (16 to 17, instead of 15), and the client will
  be able to reconstruct the exact some floating point value.

Non-ASCII and UTF-8
^^^^^^^^^^^^^^^^^^^

Prior to these changes, all strings were byte strings, so there was no encoding
required.  Arbitrary bytes could be used for message parameters and string
sensor values.  After these changes, strings sensors and ``Str`` types are
considered "text".  In Python 3, UTF-8 encoding will be used when changing
between byte strings and unicode strings for "text".  This has the following
effects:

- :class:`katcp.Message`
  - the ``arguments`` are always using byte strings, so arbitrary bytes can
  still be sent and received using this class directly.
- :class:`katcp.Sensor`
  - Values for ``string`` and ``discrete`` sensor types cannot be arbitrary
  byte strings in Python 3 - they need to be UTF-8 compatible.
- :class:`kattypes.Str`, :class:`kattypes.Discrete`, :class:`kattypes.DiscreteMulti`
  - These types is still used in ``request`` and ``reply`` decorators.
  - For sending messages, they accept any type of object, but UTF-8 encoding
  is used if values are not already byte strings.
  - When decoding received messages, "native" strings are returned.

Keep in mind that a Python 2 server may be communicating with a Python 3
client, so sticking to ASCII is safest.  If you are sure both client and
server are on Python 3 (or understand the encoding the same), then UTF-8
could be used.  That is also the encoding option used by the
`aiokatcp <https://github.com/ska-sa/aiokatcp>`_ package.

Performance degradation
^^^^^^^^^^^^^^^^^^^^^^^

Adding the compatibility results in more checks and conversions.  From some
basic benchmarking, there appears to be up to 20% performance degradation
when instantiating message objects.

Benchmark, in ipython::

    import random, katcp

    args_groups = []
    for i in range(1000):
        args_groups.append((random.randint(0, 1) == 1,
                            random.randint(0, 1000),
                            random.random(),
                            str(random.random())))

    def benchmark():
        for args in args_groups:
            tx_msg = katcp.Message.reply('foo', *args)
            serialised = bytes(tx_msg)
            parser = katcp.MessageParser()
            rx_msg = parser.parse(serialised)
            assert tx_msg == rx_msg


    %timeit benchmark()

* Old Py2:  10 loops, best of 3: 23.4 ms per loop
* New Py2:  10 loops, best of 3: 29.9 ms per loop
* New Py3:  25.1 ms ± 86.8 µs per loop (mean ± std. dev. of 7 runs, 10 loops each)


0.6.4
=====
* Fix some client memory leaks, and add `until_stopped` methods.
* Increase server MAX_QUEUE_SIZE to handle more clients.
* Use correct ioloop for client AsyncEvent objects.

See also :download:`CHANGELOG.md` for more details on changes.

Important API changes
---------------------

Stopping KATCP clients
^^^^^^^^^^^^^^^^^^^^^^

When stopping KATCP client classes that use a *managed* ioloop (i.e., create their
own in a new thread), the traditional semantics are to call ``stop()`` followed by
``join()`` from another thread.  This is unchanged.  In the case of an *unmanaged*
ioloop (i.e., an existing ioloop instance is provided to the client), we typically
stop from the same thread, and calling ``join()`` does nothing.  For the case of
*unmanaged* ioloops, a new method, ``until_stopped()``, has been added.  It returns a
future that resolves when the client has stopped.  The caller can ``yield`` on this
future to be sure that the client has completed all its coroutines.  Using this new
method is not required.  If the ioloop will keep running, the stopped client's
coroutines will eventually exit.  However, it is useful in some cases, e.g., to
verify correct clean up in unit tests.

The new method is available on :class:`katcp.DeviceClient` and derived classes, on
:class:`katcp.inspecting_client.InspectingClientAsync`, and on the high-level
clients :class:`katcp.KATCPClientResource` and
:class:`katcp.KATCPClientResourceContainer`.

An additional change is that the inspecting client now sends a state update
(indicating that it is disconnected and not synced) when stopping.  This means
high-level clients that were waiting on ``until_not_synced`` when the client was
stopped will now be notified.  Previously, this was not the case.


0.6.3
=====
* Put docs on readthedocs.
* Better error handling for messages with non-ASCII characters (invalid).
* Increase container sync time to better support large containers.
* Limit tornado version to <5.
* Allow sampling strategy to be removed from cache.
* Improve error messages for DeviceMetaClass assertions.
* Increase server's message queue length handle more simultaneous client connections.
* Improve Jenkins pipeline configuration.
* Add information on how to contribute to the project.

See also :download:`CHANGELOG.md` for more details on changes.

0.6.2
=====
* Various bug fixes
* Docstring and code style improvements
* Bumped the tornado dependency to at least 4.3
* Added the ability to let ClientGroup wait for a quorum of clients
* Added default request-timeout-hint implementation to server.py
* Moved IOLoopThreadWrapper to ioloop_manager.py, a more sensible location
* Added a random-exponential retry backoff process

See also :download:`CHANGELOG.md` for more details on changes.

0.6.1
=====

* Various bug fixes
* Improvements to testing utilities
* Improvements to various docstrings
* Use `katversion` to determine version string on install
* Better dependency management using setup.py with `setuptools`
* Fixed a memory leak when using KATCPResourceContainer

See also :download:`CHANGELOG.md` for more details on changes.

0.6.0
=====

* Major change: Use the tornado event loop and async socket routines.

See also :download:`CHANGELOG.md` for more details on changes.

Important API changes
---------------------

Tornado based event loop(s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

While the networking stack and event loops have been re-implemented using
Tornado, this change should be largely invisible to existing users of the
library. All client and server classes now expose an `ioloop` attribute that is
the :class:`tornado.ioloop.IOLoop` instance being used. Unless new server or
client classes are used or default settings are changed, the thread-safety and
concurrency semantics of 0.5.x versions should be retained. User code that made
use of non-public interfaces may run into trouble.

High level auto-inspecting KATCP client APIs added
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The high level client API inspects a KATCP device server and present requests as
method calls and sensors as objects. See :ref:`Tutorial_high_level_client`.


Sensor observer API
^^^^^^^^^^^^^^^^^^^

The :class:`katcp.Sensor` sensor observer API has been changed to pass the
sensor reading in the `observer.update()` callback, preventing potential lost
updates due to race conditions. This is a backwards incompatible change.
Whereas before observers were called as `observer.update(sensor)`, they are now
called as `observer.update(sensor, reading)`, where `reading` is an instance of
:class:`katcp.core.Reading`.

Sample Strategy callback API
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sensor strategies now call back with the sensor object and raw Python datatype
values rather than the sensor name and KATCP formatted values. The sensor
classes have also grown a :meth:`katcp.Sensor.format_reading` method that
can be used to do KATCP-version specific formatting of the sensor reading.

0.5.5
=====

* Various cleanups (logging, docstrings, base request set, minor refactoring)
* Improvements to testing utilities
* Convenience utility functions in `katcp.version`, `katcp.client`,
  `katcp.testutils`.

0.5.4
=====

* Change event-rate strategy to always send an update if the sensor has
  changed and shortest-period has passed.
* Add differential-rate strategy.


0.5.3
=====

Add :meth:`convert_seconds` method to katcp client classes that converts seconds
into the device timestamp format.

0.5.2
=====

Fix memory leak in sample reactor, other minor fixes.

0.5.1
=====

Minor bugfixes and stability improvements

0.5.0
=====

First stable release supporting (a subset of) KATCP v5. No updates apart from
documentation since 0.5.0a0; please refer to the 0.5.0a release notes below.

0.5.0a0
=======

First alpha release supporting (a subset of) KATCP v5. The KATCP v5 spec brings
a number of backward incompatible changes, and hence requires care. This library
implements support for both KATCP v5 and for the older dialect. Some API changes
have also been made, mainly in aid of fool-proof support of the Message ID
feature of KATCP v5. The changes do, however, also eliminate a category of
potential bugs for older versions of the spec.

Important API changes
---------------------

`CallbackClient.request()`
^^^^^^^^^^^^^^^^^^^^^^^^^^

Renamed :meth:`request` to :meth:`callback_request()
<katcp.CallbackClient.callback_request>` to be more consistent with superclass
API.

Sending replies and informs in server request handlers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The function signature used for request handler methods in previous versions of
this library were `request_requestname(self, sock, msg)`, where `sock` is a
raw python socket object and msg is a katcp :class:`Message` object. The `sock`
object was never used directly by the request handler, but was passed to methods
on the server to send inform or reply messages.

Before:    ::

  class MyServer(DeviceServer):
      def request_echo(self, sock, msg):
          self.inform(sock, Message.inform('echo', len(msg.arguments)))
          return Message.reply('echo', 'ok', *msg.arguments)

The old method requires the name of the request to be repeated several times,
inviting error and cluttering code. The user is also required to instantiate
katcp :class:`Message` object each time a reply is made. The new method passes a
request-bound connection object that knows to what request it is replying, and
that automatically constructs :class:`Message` objects.

Now:     ::

  class MyServer(DeviceServer):
      def request_echo(self, req, msg):
          req.inform(len(msg.arguments)))
          return req.make_reply('ok', *msg.arguments)

A :meth:`req.reply` method with the same signature as :meth:`req.make_reply`
is also available for asyncronous reply handlers, and
:meth:`req.reply_with_message` which takes a :class:`Message` instance rather
than message arguments. These methods replace the use of
:meth:`DeviceServer.reply`.

The request object also contains the katcp request :class:`Message` object
(`req.msg`), and the equivalent of a socket object
(`req.client_connection`). See the next section for a description of
`client_connection`.

Using the server methods with a `req` object in place of `sock` will still work
as before, but will log deprecation warnings.

Connection abstraction
^^^^^^^^^^^^^^^^^^^^^^

Previously, the server classes internally used each connection's low-level
`sock` object as an identifier for the connection. In the interest of
abstracting out the transport backend, the `sock` object has been replaced by a
:class:`ClientConnectionTCP` object. This object is passed to all server handler
functions (apart from request handlers) instead of the `sock` object. The
connection object be used in the same places where `sock` was previously
used. It also defines :meth:`inform`, :meth:`reply_inform` and :meth:`reply`
methods for sending :class:`Message` objects to a client.


Backwards incompatible KATCP V5 changes
---------------------------------------

Timestamps
^^^^^^^^^^

Excerpted from :download:`NRF-KAT7-6.0-IFCE-002-Rev5.pdf`:

  All core messages involving time (i.e. timestamp or period specifications) have
  changed from using milliseconds to seconds. This provides consistency with SI
  units.  Note also that from version five timestamps should always be specified
  in UTC time.

Message Identifiers (mid)
^^^^^^^^^^^^^^^^^^^^^^^^^

Excerpted from :download:`NRF-KAT7-6.0-IFCE-002-Rev5.pdf`:

  Message identifiers were introduced in version 5 of the protocol to allow
  replies to be uniquely associated with a particular request. If a client sends
  a request with a message identifier the server must include the same
  identifier in the reply. Message identifiers are limited to integers in the
  range 1 to 231 − 1 inclusive. It is the client’s job to construct suitable
  identifiers – a server should not assume that these are unique.  Clients that
  need to determine whether a server supports message identifiers should examine
  the #version-connect message returned by the server when the client connects
  (see Section 4). If no #version-connect message is received the client may
  assume message identifiers are not supported.

also:

  If the request contained a message id each inform that forms part of the
  response should be marked with the original message id.

Support for message IDs is optional. A properly implemented server should never
use mids in replies unless the client request has an mid. Similarly, a client
should be able to detect whether a server supports MIDs by checking the
`#version-connect` informs sent by the server, or by doing a `!version-list`
request. Furthermore, a KATCP v5 server should never send `#build-state` or
`#version` informs.

.. _release_notes_0_5_0a0_server_version_auto_detection:

Server KATCP Version Auto-detection
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The :class:`DeviceClient <katcp.DeviceClient>` client uses the presence of
`#build-state` or `#version` informs as a heuristic to detect pre-v5 servers,
and the presence of `#version-connect` informs to detect v5+ servers. If mixed
messages are received the client gives up auto-detection and disconnects. In
this case :meth:`~katcp.DeviceClient.preset_protocol_flags` can be used to
configure the client before calling :meth:`~katcp.DeviceClient.start`.

Level of KATCP support in this release
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This release implements the majority of the KATCP v5 spec; excluded parts are:

* Support for optional warning/error range meta-information on sensors.
* Differential-rate sensor strategy.
