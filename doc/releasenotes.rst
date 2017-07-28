.. _Release Notes:

*************
Release Notes
*************
0.6.2
=====
* Various bug fixes
* Docstring and code style improvements
* Bumped the tornado dependency to at least 4.3
* Added the ability to let ClientGroup wait for a quorum of clients
* Added default request-timeout-hint implementation to server.py
* Moved IOLoopThreadWrapper to ioloop_manager.py, a more sensible location
* Added a random-exponential retry backoff process

See also :download:`CHANGELOG` for more details on changes.

0.6.1
=====

* Various bug fixes
* Improvements to testing utilities
* Improvements to various docstrings
* Use `katversion` to determine version string on install
* Better dependency management using setup.py with `setuptools`
* Fixed a memory leak when using KATCPResourceContainer

See also :download:`CHANGELOG` for more details on changes.

0.6.0
=====

* Major change: Use the tornado event loop and async socket routines.

See also :download:`CHANGELOG` for more details on changes.

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

Releasing
---------

Notes for whomever becomes the release manager in the future. This assumes that
you have `git@github.com:ska-sa/katcp-python.git` (or the https equivalent) as
your git origin.

* Edit CHANGELOG with the details of the changes, and doc/releasenotes.rst (this
  file) with the change summary. Consider more detailed document updates /
  examples.

* Be aware that `https://pypi.python.org/pypi/katversion` generates the version
  string. katversion looks at git tags to figure out the version number, so we
  need to add a tag and push that to github.

* Commit and tag the final changes::

    git commit -a -m "Final updates for release 0.X.Y"
    git tag -a v0.X.Y -m "Version 0.X.Y Stable"
    git push --tags

* Execute this the first time you do a release::

    pip install sphinx-pypi-upload Sphinx
    python setup.py register

* Check the documentation by building it (should be in the `build/sphinx/html`
  directory)::

    python setup.py build_sphinx

* Next execute::

   python setup.py sdist upload
   python setup.py build_sphinx upload_sphinx --upload-dir=build/sphinx/html

* Check the pypi page to see that the upload and doc build was successful.

* Host package docs on `http://readthedocs.org`

  1. Create an account on Read the Docs. You will get an email verifying your
     email address which you should accept within 7 days.
  2. Log in and click on "Import".
  3. Give your project a name, add the HTTPS link for your GitHub project, and
     select Git as your repository type.
  4. Fill in the rest of the form as needed and click "Create".
  5. Then click "Build"

* Automatically updated when you push to GitHub

  6. On GitHub, navigate to your repository and click on "Settings".
  7. In the sidebar, click on "Integrations & services", then find and click on
     the "ReadTheDocs service".
  8. Check the "Active" setting and click "Update Settings".

* Good, it seems you have managed to release katcp!

  - All done. Commit away and your project will auto-update.
