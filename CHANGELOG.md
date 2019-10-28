28 October 2019 (0.7.0)
* Added Python 3 compatibility (PR [#215](https://github.com/ska-sa/katcp-python/pull/215))

23 August 2019 (0.6.4)
* Fix some client memory leaks, and add `until_stopped` methods (PR #206).
* Increase server's MAX_QUEUE_SIZE from 30 to 300 to handle more simultaneous
  client connections (PR #208).
* Use correct ioloop for client AsyncEvent objects (PR #210).

14 May 2019 (0.6.3)
* Put docs on readthedocs (PR #192).
* Added error handling when parsing Message arguments that include non-ASCII
  characters (not following KATCP specification).  Invalid characters are replaced
  with question marks, possibly breaking functionality (PR #193).
* Increase container sync time from 8 sec to 20 sec, and initial resync
  timeout from 1 sec to 5 sec, to better support large containers (PR #194).
* Limit tornado version to <5 because the openssl version on Ubuntu 14.04
  is not compatible (PR #195).
* Allow sampling strategy to be removed from cache, to better support clients
  working with devices that dynamically remove sensors (PR #196).
* Provide a meaningful error message for the DeviceMetaClass assert statements
  (PR #198).
* Increase server's MAX_QUEUE_SIZE from 30 to 300 to handle more simultaneous
  client connections (PR #199).  (Not in release 0.6.3 - it was only on the
  release/karoocamv20 branch)
* Updates to Jenkinsfile for pipeline improvements (PR #200, #201, #202).
* Add information on how to contribute to the project (PR #203).

19 July 2017 (0.6.2)
 * Bumped the tornado dependency to at least 4.3
 * Started work to support for python3 (not production ready)
 * Added the ability to let ClientGroup wait for a quorum of clients
   Give ClientGroup.wait() the ability to wait until a subset of clients
   (a 'quorum') satisfy a sensor condition.
 * Added an until_some helper function
 * Added timeout hints usage to the resource client
 * Added default request-timeout-hint implementation to server.py
   It is only activated if the server protocol is set to the (proposed) KATCP v5.1
   spec and the timeout hint protocol flag is enabled.
 * Moved IOLoopThreadWrapper to ioloop_manager.py, a more sensible location
 * Improved unit test coverage and implementation in some cases
 * Started work on implementing an async device handler
 * Numerous docstring and code style improvements throughout
 * Fixed a bug where the timeout parameter was not passed down in until_state()
 * Fixed a bug where model updates were never sent by the inspecting client
   callback when sensor sync failed
 * Changed the __str__ method for InspectingClientStateType to be more compact
 * Added a random-exponential retry backoff process to the inspecting client,
   to ensure that the thundering herd does not continue thundering
 * Properly handle errors during inspection in inspecting client
 * Added support for continuous integration using a Jenkinsfile

30 August 2016 (0.6.1)
 * Increased the maximum message size that can be received up from 128k to 2M
 * Minor docstring fixes
 * Fixed testutils
 * Fixed inspecting client from losing units values
 * Avoid blocking python interpreter shutdown and avoid leaking file descriptors
 * Improved setup.py dependencies and removed pip-build-requirements.txt
 * Allow high level clients to preset KATCP flags
 * Simplify sensor wait() interface
 * Added a check that the sensor exists before removing it
 * Better handling of malformed log lines
 * Use katversion and git tags to determine package version on install

 15 June 2016 (0.6.0)
 * Added high level, simple to use, client API (KATCPResource) that connects to,
   inspects and presents a high level, hierarchical object oriented interface to
   multiple KATCP devices.
 * Added mid level client API (InspectingClient) that abstracts the inspection
   of a KATCP device.
 * Backwards-incompatible change in Sensor observer API
 * Sensor strategies now call back with raw Python datatype values rather than
   KATCP formatted values, using the same 'reading' API as Sensor
   observers' update methods.
 * Tornado ioloop / iostream based sensor sampling
 * Tornado ioloop / iostream based server
 * Sever message handlers can now return futures
 * Server supports fully async mode with Tornado futures.
 * Threaded message handle compatibility mode + new Async mode (performance
   penalty for threaded mode, but is default for now)
 * Tornado ioloop / iostream based client AsyncClient, single implementation for
   both BlockingClient and CallbackClient based on thin wrapper around
   AsyncClient.
 * Clients now stop much faster due to removal of 0.5s select timeout in
   previous implementation.
 * Backwards incompatible change in BlockingClient.blocking_request(): now
   returns a failed reply rather than raising an exception for timeouts (like
   CallbackClient.blocking_request() always did).
 * Threadsafe mode (default for BlockingClient and CallbackClient) available, but
   comes with performance overhead even async for usage.
 * Hundreds of test-suite reliablility improvements.

14 July 2014 (0.5.5)
 * Add utility code to katcp.version.py that other modules can use to parse svn
   or git revisions for use in build-state informs or sensors
 * Add katcp.client.request_check: a convenience function for making KATCP
   requests and raising an exception if there is an error.
 * Add katcp.testutils.handle_mock_req() to allow mocked testing of server request
   handlers and dispatch, removing the need to start() a DeviceServer for
   handler tests.
 * Make katcp.testutils.WaitingMock robust against race conditions
 * Explicitly list set of base KATCP requests in katcp.server.BASE_REQUESTS
 * Make method-name to KATCP-name function available outside KATCP server
   metaclass
 * Improvements to logging (i.a. prevents issues when using SocketHandler)
 * Docstring cleanups

10 June 2013 (0.5.4)
 * Change event-rate strategy to always send an update if the sensor has
   changed and shortest-period has passed
 * Add differential-rate strategy

31 August 2013 (0.5.3)
 * Add convert_seconds() method to katcp client classes that converts
   seconds into the device timestamp format.

24 April 2013 (0.5.2)
 * Fix memory leak in sample reactor when replacing sensor strategies
 * Minor error reporting improvements in sampling strategies
 * Fix 2012 -> 2013 in changelog :)

8 March 2013 (0.5.1)
 * Minor code comment cleanups
 * Minor documentation and docstring updates
 * Fix twisted katcp server's on-connect inform messages to be consistent
   with the selected katcp version
 * Improve error messages when an invalid sensor sampling strategy is set
 * Fix bug in event-rate sampling strategy where events were sent whenever a sensor
   object is updated, even if the value is unchanged. Also fixes same
   bug for the event sampling strategy, since it uses the event-rate
   code for its implementation
 * Work around potential CPU livelock when sending messages and the network
   buffers are full by using timeouts. Problem will be properly solved when the
   current networking code is replaced by twisted.
 * Fixed some buggy logger calls
 * Improve the handling of sensor removal from the sensor tree
 * made server main event loop robust against disconnect race-conditions when
   looking up the client connection object


29 January 2013 (0.5.0)
 * Minor documentation updates
 * Version 0.5.0 released

13 December 2012 (0.5.0a0)
 * Add support for protocol flags to katcp.core -- a standard
   data structure for storing KATCP versions and capabilities
 * Clean-up various bits of code, docstring fixes, etc.
 * katcp.Message constructors are now strict about unknown keyword arguments
 * Add clear_strategies() method to server class to clear all sampling
   strategies for a connection.
 * Support for serving either KATCP v4 or v5
   * sensor timestamp conversions as needed
   * log timestamp conversion as needed
   * kattypes decorators can be configured for the appropriate protocol versions
   * generate appropriate on-connect messages
 * Support for being a client of ether KATCP v4 or v5
   * Auto-detect server version using on-connect informs
   * Add ability for clients to wait until protocol information is received from
     server
 * Refactor use of raw 'sock' object as a connection identifier
   * Abstract sock to a transport-neutral connection object
   * Pass a request-bound connection object to request handlers, rather than a
     raw socket. Automatically use the right name and mid for informs and replies
   * Deprecate use of self.reply(sock, ....) and self.reply_inform(sock,
     ....). Rather use req.reply(...), req.reply_with_message(...),
     req.inform(...)
 * Renamed CallbackClient.request to callback_request to be more consistent with
   superclass API
 * Add sensor construction class methods, e.g. Sensor.integer(...), rather than
   Sensor(Sensor.INTEGER, ...)
 * Removed non-standard rate-limit extension from 'event' sample strategy
   implementation. KATCP v5 event-rate strategy covers that use case.
 * Fix race condition between timeout and reply handlers
 * Various improvements to tests and testutils to make tests more robust and
   (sometimes) faster
 * Add implementation of subset of new KATCP v5 features
   * Add Address sensor type
   * Implement ?version-list and #version-connect
   * Add 'unreachable' and 'inactive' sensor states
   * Make float and integer sensor ranges optional and informational.
   * Add Message Identifier (mid) support. Enabled by default in client
     for servers that support mids.
   * Use seconds instead of milliseconds for all timestamps and periods (except
     when explicitly configured to use an older KATCP, see multi-version support
     above)
 * Version 0.5.0a0 released

18 September 2012
 * PEP-8 cleanup of code.
 * Store sensor state as a tuple to allow 'atomic' read.
 * Use nose as the default test runner.
 * Fix thread leak in callback client when async requests are outstanding when
   the client is being shut down.
 * Fix server deadlock on sensor strategy lock at shutdown.
 * Add variable number of arguments support to kattypes decorators.
 * Version 0.3.5 released -- Should be last public release adhering to the KATCP
   v4 specifications. Next release should be based on the KATCP v5
   specifications.

01 June 2011:
 * Merge r9501,9706,9764,9769,9771,9776,9780:
   * Numerous improvements to katcp.tx.proxy to add hooks that proxy
     subclasses can connect functionality to (for example, arrival of
     devices) and improve the behaviour of proxied sensors.
 * Merge r9783,9784,9785:
   * Fix bug in sensortree that was triggered by sensor updates
     occurring while sensors were being added to the tree.
 * Merge r9791:
   * Fix katcp.tx build state and API version sending.
 * Version 0.3.4 released.

16 May 2011:
 * Merge r9447,9448,9491:
   * Add a better reconnecting interface to katcp.tx clients.
 * Version 0.3.3 released.

05 May 2011:
 * Merge r9321,9357,9370,9371,9373,9397 from trunk:
   * Fix kattypes decorator bug that caused it to break on request
     methods with no arguments.
   * Simplify disconnecting clients in katcp.tx.
   * Allow katcp.tx clients to wait to connect.
   * Report connections failures better inside katcp.tx.
   * Minor docstring fix.
 * Version 0.3.2 released.

09 Feb 2011:
 * Merge r7520,7522,7546,7578-7580,7588-7591,7635,7637-7638,7674-7678,
   7701-7702,7707,7726-7729 from trunk
   * Fix unbounded memory usage issue in katcp.tx.
   * Fix katcp.tx so it doesn't send sensor-list twice.
   * Fix katcp.tx to clear running strategies when a connection is lost.
   * Minor improvements to katcp.tx.
   * Benchmark suite improvements.
 * Merge r7806-7807,7812,7818,8081-8083,8085,8404,8420,8432-8434,8660
   from trunk
   * Refactor katcp.tx interface (breaks code that uses katcp.tx but Twisted
     support was experimental in 0.3.0).
   * Fix lack of build and api version reporting by katcp.tx server.
   * Fix katcp.tx bug in reporting methods with names containing
     underscores (affects ?help).
   * Fix katcp.tx bug which causes messages to be lost if socket buffer
     filled up.
   * Change katcp.tx demo client to only use official katcp.tx API.
 * Merge r8703 from trunk
   * Fix check in test (sensor_list should have been sensor-list).
 * Version 0.3.1 released.

14 Dec 2010:
 * Merge r8034,8234-8235,8238,8260,8265 from trunk
   * Cleanup of testutils.

25 Oct 2010:
 * Created 0.3.x branch.

22 Oct 2010:
 * Add a NullHandler for katcp library logging as recommended by Python logging
   documentation.

12 Oct 2010:
 * Add regular expression pattern support to ?sensor-list and ?sensor-value
   requests.

8 Oct 2010:
 * Add .wait_connected() method to KATCP client.

4 Oct 2010:
 * Start of KATCP benchmarking effort.

28 Sep 2010:
 * Refactor Twisted KATCP into katcp.tx sub-package.
 * Update and flesh out example KATCP server.

27 Sep 2010:
 * Implementation of Twisted KATCP proxy. A proxy mutliplexes queries and
   sensor sampling from a single client to multiple KATCP servers.

17 Sep 2010:
 * Completion of Twisted KATCP DeviceServer.

16 Aug 2010:
 * Implementation of __eq__ and __ne__ for katcp.Message.

30 Jul 2010
 * katcp.katcp renamed to katcp.core to avoid module name clashing with
   package name.
 * Allow Python types to be used in addition to katcp.Sensor object types
   when constructing sensors.

27 Jul 2010
 * Custom __repr__ for katcp.Message added.

22 Jul 2010
 * Initial sensor tree implementation. A sensor tree is a directed acyclic
   graph of sensor dependencies. The tree triggers updates of dependent
   sensors when dependee sensors change.

15 Jul 2010
 * Start of work on Twisted katcp client and server implementations.

13 Jul 2010
 * Major rework of katcp.testutils.

11 Mar 2010
 * Add check to client to bail out of re-trying sends if the client
   has disconnected.

02 Feb 2010
 * Allow adding and removing sensors on the fly.

20 Jan 2010
 * Allow logging using .warn(fmt, arg1, arg2, ...) format as used
   in Python logging (allows msg formatting to be delayed until it
   is known whether the message will actually be logged).

26 Oct 2009
 * Add include_msg option to request decorator to give decorated
   requests a means to access the original message and the message id.
 * Add support for msg ids to callback client.

23 Oct 2009
 * Add support for message ids to server and client request handling.
   * Server gained new .reply() and .reply_inform() methods that set
     message ids.
   * Server had .send_message() moved to ._send_message() since it is
     now rather important to use .reply() and .reply_inform() when
     replying. Old servers will need to upgrade. Calls to .send_message()
     should be replaced with calls to either .reply(), .reply_inform() or
     .inform(). Calls to .inform() should be changed to calls to
     .reply_inform() if they are part of the response to a request.
   * Client request handler handles message ids correctly.

22 Oct 2009
 * Add support for message ids to KATCP message and message parser classes.

21 Oct 2009
 * Add blocking_request implementation to callback client.
 * Reduce client sleep to 0.5s (from 1.0s) to make shutdown more responsive.
 * Add Message.reply_ok() method for checking that a message represents
   a successful reply.

07 Oct 2009
 * Fix bug in new callback client time outs.

05 Oct 2009
 * Support for commands timing out in callback client.

21 Sep 2009
 * Fix bug in client run loop where self._sock could become
   None while it was being used by select(..) and .recv(...).

02 Sep 2009
 * Implement TimestampOrNow kattype.

01 Sep 2009
 * Fix bug where AUTO strategy did not set updates on connect.

26 Aug 2009
 * Downgrade logging of successful request handling to DEBUG log level.

29 Jul 2009
 * Enable TCP_NODELAY on server and client sockets since katcp protocol messages
   are often small.

22 Jul 2009
 * Update version to 0.2.x.
 * Fix download url and license in setup.py.
 * Have Sphinx read the version number from katcp.VERSION and katcp.VERSION_STR.

29 Jun 2009
 * Start of CHANGELOG in preparation for 0.1.0 release.
