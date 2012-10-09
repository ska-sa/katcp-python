# testutils.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Test utils for katcp package tests.
   """

import client
import logging
import re
import time
import Queue
import threading
import functools
from .core import Sensor, Message
from .server import DeviceServer, FailReply


class TestLogHandler(logging.Handler):
    """A logger for KATCP tests."""

    def __init__(self):
        """Create a TestLogHandler."""
        logging.Handler.__init__(self)
        self._records = []

    def emit(self, record):
        """Handle the arrival of a log message."""
        self._records.append(record)

    def clear(self):
        """Clear the list of remembered logs."""
        self._records = []


class DeviceTestSensor(Sensor):
    """Test sensor."""

    def __init__(self, sensor_type, name, description, units, params,
                 timestamp, status, value):
        super(DeviceTestSensor, self).__init__(
            sensor_type, name, description, units, params)
        self.set(timestamp, status, value)


class BlockingTestClient(client.BlockingClient):
    """Test blocking client."""

    def __init__(self, test, *args, **kwargs):
        """Takes a TestCase class as an additional parameter."""
        self.test = test
        self._message_recorders = {}
        super(BlockingTestClient, self).__init__(*args, **kwargs)

    def raw_send(self, chunk):
        """Send a raw chunk of data to the server."""
        self._sock.send(chunk)

    def _sensor_lag(self):
        """The expected lag before device changes are applied."""
        return getattr(self.test, "sensor_lag", 0)

    def handle_inform(self, msg):
        """Pass unhandled informs to message recorders."""
        for append_msg in self._message_recorders.itervalues():
            append_msg(msg)
        return super(BlockingTestClient, self).handle_inform(msg)

    def handle_reply(self, msg):
        """Pass unhandled replies to message recorders."""
        for append_msg in self._message_recorders.itervalues():
            append_msg(msg)
        return super(BlockingTestClient, self).handle_reply(msg)

    def message_recorder(self, whitelist=(), blacklist=(), informs=True,
                         replies=False):
        """Helper method for attaching a hook to selected received messages.

        Parameters
        ----------
        whitelist : list or tuple
            Record only messages matching these names.  If the list is empty,
            all received messages will be saved except any specified in the
            blacklist.
        blacklist : list or tuple
            Ignore messages matching these names.  If any names appear both in
            the whitelist and the blacklist, the whitelist will take
            precedence.
        informs : boolean
            Whether to record informs.  Default: True.
        replies : boolean
            Whether to record replies.  Default: False.

        Return
        ------
        get_msgs : function
            Function that returns a list of messages that have matched so far.
            Each call returns the list of message since the previous call.
        """
        msg_types = set()
        if informs:
            msg_types.add(Message.INFORM)
        if replies:
            msg_types.add(Message.REPLY)
        whitelist = set(whitelist)
        blacklist = set(blacklist)


        class MessageRecorder(object):
            def __init__(self, msg_types, whitelist, blacklist):
                self.msgs = []
                self.msg_types = msg_types
                self.whitelist = whitelist
                self.blacklist = blacklist
                self.msg_received = threading.Event()

            def get_msgs(self, min_number=0, timeout=1):
                """Return the messages recorded so far. and delete them

                Parameters
                ==========
                number -- Minumum number of messages to return, else wait
                timeout: int seconds or None -- Don't wait longer than this
                """
                self.wait_number(min_number, timeout)
                msg_count = len(self.msgs)
                msgs_copy = self.msgs[:msg_count]
                del self.msgs[:msg_count]
                return msgs_copy

            __call__ = get_msgs           # For backwards compatibility

            def wait_number(self, number, timeout=1):
                """Wait until at least certain number of messages have been recorded

                Parameters
                ==========
                number -- Number of messages to wait for
                timeout: int seconds or None -- Don't wait longer than this
                """
                start_time = time.time()
                while True:
                    if len(self.msgs) >= number:
                        return True
                    elapsed_time = time.time() - start_time
                    if elapsed_time >= timeout:
                        return None
                    self.msg_received.wait(timeout=timeout-elapsed_time)

            def append_msg(self, msg):
                """Append a message if it matches the criteria."""
                if msg.mtype not in self.msg_types:
                    return
                try:
                    if self.whitelist:
                        if msg.name in whitelist:
                            self.msgs.append(msg)
                            self.msg_received.set()
                    else:
                        if msg.name not in self.blacklist:
                            self.msgs.append(msg)
                            self.msg_received.set()
                finally:
                    self.msg_received.clear()

        mr = MessageRecorder(msg_types, whitelist, blacklist)

        self._message_recorders[id(mr)] = mr.append_msg

        return mr

    @staticmethod
    def expected_sensor_value_tuple(sensorname, value, sensortype=str,
                                    places=7):
        """Helper method for completing optional values in expected sensor
        value tuples.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        value : obj
            The expected value of the sensor.  Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        places : int, optional
            The number of places to use in a float comparison.  Has no effect
            if sensortype is not float. Default: 7.
        """

        return (sensorname, value, sensortype, places)

    # SENSOR VALUES

    def get_sensor(self, sensorname, sensortype=str):
        """Retrieve the value, status and timestamp of a sensor.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        """

        reply, informs = self.blocking_request(Message.request("sensor-value",
                                                               sensorname))
        self.test.assertTrue(reply.reply_ok(),
            "Could not retrieve sensor '%s': %s"
            % (sensorname, (reply.arguments[1] if len(reply.arguments) >= 2
                            else "")))

        self.test.assertEqual(len(informs), 1,
            "Expected one sensor value inform for sensor '%s', but"
            " received %d." % (sensorname, len(informs)))

        inform = informs[0]

        self.test.assertEqual(len(inform.arguments), 5,
            "Expected sensor value inform for sensor '%s' to have five"
            " arguments, but received %d." %
            (sensorname, len(inform.arguments)))

        timestamp, _num, _name, status, value = inform.arguments

        try:
            if sensortype == bool:
                typestr = "%r and then %r" % (int, bool)
                value = bool(int(value))
            else:
                typestr = "%r" % sensortype
                value = sensortype(value)
        except ValueError, e:
            self.test.fail("Could not convert value %r of sensor '%s' to type"
                           " %s: %s" % (value, sensorname, typestr, e))

        self.test.assertTrue(status in Sensor.STATUSES.values(),
                "Got invalid status value %r for sensor '%s'." %
                (status, sensorname))

        try:
            timestamp = int(timestamp)
        except ValueError, e:
            self.test.fail("Could not convert timestamp %r of sensor '%s' to"
                           " type %r: %s" % (timestamp, sensorname, int, e))

        return value, status, timestamp

    def get_sensor_value(self, sensorname, sensortype=str):
        """Retrieve the value of a sensor.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        """

        value, _, _ = self.get_sensor(sensorname, sensortype)
        return value

    def get_sensor_status(self, sensorname):
        """Retrieve the status of a sensor.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        """

        _, status, _ = self.get_sensor(sensorname)
        return status

    def get_sensor_timestamp(self, sensorname):
        """Retrieve the timestamp of a sensor.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        """

        _, _, timestamp = self.get_sensor(sensorname)
        return timestamp

    def assert_sensor_equals(self, sensorname, expected, sensortype=str,
                             msg=None, places=7, status=None):
        """Assert that a sensor's value is equal to the given value.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        expected : obj
            The expected value of the sensor.  Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        msg : str, optional
            A custom message to print if the assertion fails.  If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        places : int, optional
            The number of places to use in a float comparison.  Has no effect
            if sensortype is not float. Default: 7.
        status : str, optional
            The expected status of the sensor.
        """

        if msg is None:
            places_msg = " (within %d decimal places)" % \
                         places if sensortype == float else ""
            msg = "Value of sensor '%s' is %%r. Expected %r%s." % \
                  (sensorname, expected, places_msg)

        got, got_status, _timestamp = self.get_sensor(sensorname, sensortype)
        if '%r' in msg:
            msg = msg % got

        if sensortype == float:
            self.test.assertAlmostEqual(got, expected, places, msg)
        else:
            self.test.assertEqual(got, expected, msg)

        if status is not None:
            self.test.assertEqual(got_status, status, "Status of sensor '%s'"
                                  " is %r. Expected %r." % (sensorname,
                                                            got_status,
                                                            status))

    def assert_sensor_status_equals(self, sensorname, expected_status,
                                    msg=None):
        """Assert that a sensor's status is equal to the given status.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        expected_status : str
            The expected status of the sensor.
        msg : str, optional
            A custom message to print if the assertion fails.  If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        """

        if msg is None:
            msg = "Status of sensor '%s' is %%r. Expected %r." % \
                  (sensorname, expected_status)

        got_status = self.get_sensor_status(sensorname)

        if '%r' in msg:
            msg = msg % got_status

        self.test.assertEqual(got_status, expected_status, msg)

    def assert_sensor_not_equal(self, sensorname, expected, sensortype=str,
                                msg=None, places=7):
        """Assert that a sensor's value is not equal to the given value.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        expected : obj
            The expected value of the sensor.  Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        msg : str, optional
            A custom message to print if the assertion fails.  If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        places : int, optional
            The number of places to use in a float comparison.  Has no effect
            if sensortype is not float. Default: 7.
        """

        if msg is None:
            places_msg = " (within %d decimal places)" % \
                         places if sensortype == float else ""
            msg = "Value of sensor '%s' is %%r. Expected a different" \
                  " value%s." % (sensorname, places_msg)

        got = self.get_sensor_value(sensorname, sensortype)
        if '%r' in msg:
            msg = msg % got

        if sensortype == float:
            self.test.assertNotAlmostEqual(got, expected, places, msg)
        else:
            self.test.assertNotEqual(got, expected, msg)

    def assert_sensors_equal(self, sensor_tuples, msg=None):
        """Assert that the values of several sensors are equal to the given
        values.

        Parameters
        ----------
        sensor_tuples : list of tuples
            A list of tuples specifying the sensor values to be checked.  See
            :meth:`expected_sensor_value_tuple`.
        msg : str, optional
            A custom message to print if the assertion fails.  If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        """

        sensor_tuples = [self.expected_sensor_value_tuple(*t)
                         for t in sensor_tuples]
        for sensorname, expected, sensortype, places in sensor_tuples:
            self.assert_sensor_equals(sensorname, expected, sensortype,
                                      msg=msg, places=places)

    def assert_sensors_not_equal(self, sensor_tuples, msg=None):
        """Assert that the values of several sensors are not equal to the given
        values.

        Parameters
        ----------
        sensor_tuples : list of tuples
            A list of tuples specifying the sensor values to be checked.  See
            :meth:`expected_sensor_value_tuple`.
        msg : str, optional
            A custom message to print if the assertion fails.  If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        """

        sensor_tuples = [self.expected_sensor_value_tuple(*t)
                         for t in sensor_tuples]
        for sensorname, expected, sensortype, places in sensor_tuples:
            self.assert_sensor_not_equal(sensorname, expected, sensortype,
                                         msg=msg, places=places)

    def wait_until_sensor_equals(self, timeout, sensorname, value,
                                 sensortype=str, places=7, pollfreq=0.1):
        """Wait until a sensor's value is equal to the given value, or time
        out.

        Parameters
        ----------
        timeout : float
            How long to wait before timing out, in seconds.
        sensorname : str
            The name of the sensor.
        value : obj
            The expected value of the sensor.  Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        places : int, optional
            The number of places to use in a float comparison.  Has no effect
            if sensortype is not float. Default: 7.
        pollfreq : float, optional
            How frequently to poll for the sensor value. Default: 0.1.
        """

        stoptime = time.time() + timeout
        success = False

        if sensortype == float:
            cmpfun = lambda got, exp: abs(got - exp) < 10 ** -places
        else:
            cmpfun = lambda got, exp: got == exp

        lastval = None
        while time.time() < stoptime:
            lastval = self.get_sensor_value(sensorname, sensortype)
            if cmpfun(lastval, value):
                success = True
                break
            time.sleep(pollfreq)

        if not success:
            self.test.fail("Timed out while waiting %ss for %s sensor to"
                           " become %s. Last value was %s." %
                           (timeout, sensorname, value, lastval))

    # REQUEST PARAMETERS

    def test_sensor_list(self, expected_sensors, ignore_descriptions=False):
        """Test that the list of sensors on the device equals the provided
        list.

        Parameters
        ----------
        expected_sensors : list of tuples
            The list of expected sensors.  Each tuple contains the arguments
            returned by each sensor-list inform, as unescaped strings.
        ignore_descriptions : boolean, optional
            If this is true, sensor descriptions will be ignored in the
            comparison. Default: False.
        """

        def sensortuple(name, description, units, stype, *params):
            # ensure float params reduced to the same format
            if stype == "float":
                params = ["%g" % float(p) for p in params]
            return (name, description, units, stype) + tuple(params)

        reply, informs = self.blocking_request(Message.request("sensor-list"))

        self.test.assertTrue(reply.reply_ok(),
            "Could not retrieve sensor list: %s"
            % (reply.arguments[1] if len(reply.arguments) >= 2 else ""))

        expected_sensors = [sensortuple(*t) for t in expected_sensors]
        got_sensors = [sensortuple(*m.arguments) for m in informs]

        #print ",\n".join([str(t) for t in got_sensors])

        if ignore_descriptions:
            expected_sensors = [s[:1] + s[2:] for s in expected_sensors]
            got_sensors = [s[:1] + s[2:] for s in got_sensors]

        expected_set = set(expected_sensors)
        got_set = set(got_sensors)

        self.test.assertEqual(got_set, expected_set,
            "Sensor list differs from expected list.\nThese sensors are"
            " missing:\n%s\nFound these unexpected sensors:\n%s"
            % ("\n".join(sorted([str(t) for t in expected_set - got_set])),
               "\n".join(sorted([str(t) for t in got_set - expected_set]))))

    def test_help(self, expected_requests, full_descriptions=False,
                  exclude_defaults=True):
        """Test that the list of requests on the device equals the provided
        list.

        Parameters
        ----------
        expected_requests : list of tuples or strings
            The list of expected requests.  May be a list of request names as
            strings.  May also be a list of tuples, each of which contains the
            request name and either the first line or the full text of the
            request description, as unescaped strings.  If full_descriptions is
            true, full description text must be provided.
        full_descriptions : boolean, optional
            If this is true, the full text of descriptions is compared,
            otherwise only text up to the first newline is compared.  Has no
            effect if expected_requests is a list of strings.  Default: False.
        exclude_defaults : boolean, optional
            If this is true, exclude requests on the device which match
            requests found on :class:`katcp.DeviceServer` by name.
            Default: True.
        """

        descriptions = True
        if not expected_requests or isinstance(expected_requests[0], str):
            descriptions = False

        reply, informs = self.blocking_request(Message.request("help"))
        self.test.assertTrue(reply.reply_ok(),
            "Could not retrieve help list: %s"
            % (reply.arguments[1] if len(reply.arguments) >= 2 else ""))

        got_requests = [tuple(m.arguments) for m in informs]

        if descriptions:
            if not full_descriptions:
                got_requests = [(name, desc.split('\n')[0])
                                for (name, desc) in got_requests]
                expected_requests = [(name, desc.split('\n')[0])
                                     for (name, desc) in expected_requests]
        else:
            got_requests = [name for (name, desc) in got_requests]

        got_set = set(got_requests)
        expected_set = set(expected_requests)

        if exclude_defaults:
            default_request_names = set(DeviceServer._request_handlers.keys())

            if not descriptions:
                got_set = got_set - default_request_names
            else:
                got_set = set([(name, desc) for (name, desc) in got_set
                               if name not in default_request_names])

        self.test.assertEqual(got_set, expected_set,
            "Help list differs from expected list.\nThese requests are"
            " missing:\n%s\nFound these unexpected requests:\n%s"
            % ("\n".join(sorted([str(t) for t in expected_set - got_set])),
               "\n".join(sorted([str(t) for t in got_set - expected_set]))))

    def assert_request_succeeds(self, requestname, *params, **kwargs):
        """Assert that the given request completes successfully when called
        with the given parameters, and optionally check the arguments.

        Parameters
        ----------
        requestname : str
            The name of the request.
        params : list of objects
            The parameters with which to call the request.
        args_echo : boolean, optional
            Keyword parameter.  Assert that the reply arguments after 'ok'
            equal the request parameters.  Takes precedence over args_equal,
            args_in and args_length. Default False.
        args_equal : list of strings, optional
            Keyword parameter.  Assert that the reply arguments after 'ok'
            equal this list.  Ignored if args_echo is present; takes precedence
            over args_echo, args_in and args_length.
        args_in : list of lists, optional
            Keyword parameter.  Assert that the reply arguments after 'ok'
            equal one of these tuples.  Ignored if args_equal or args_echo is
            present; takes precedence over args_length.
        args_length : int, optional
            Keyword parameter.  Assert that the length of the reply arguments
            after 'ok' matches this number. Ignored if args_equal, args_echo or
            args_in is present.
        informs_count : int, optional
            Keyword parameter.  Assert that the number of informs received
            matches this number.
        """

        reply, informs = self.blocking_request(Message.request(requestname,
                                                               *params))

        self.test.assertEqual(reply.name, requestname, "Reply to request '%s'"
                              " has name '%s'." % (requestname, reply.name))

        self.test.assertTrue(reply.reply_ok(),
            "Expected request '%s' called with parameters %r to succeed, but"
            " it failed %s." %
            (requestname, params, ("with error '%s'" % reply.arguments[1]
                                   if len(reply.arguments) >= 2
                                   else "(with no error message)")))

        args = reply.arguments[1:]

        args_echo = kwargs.get("args_echo", False)
        args_equal = kwargs.get("args_equal")
        args_in = kwargs.get("args_in")
        args_length = kwargs.get("args_length")
        informs_count = kwargs.get("informs_count")

        if args_echo:
            args_equal = [str(p) for p in params]

        if args_equal is not None:
            self.test.assertEqual(args, args_equal,
                "Expected reply to request '%s' called with parameters %r to"
                " have arguments %s, but received %s."
                % (requestname, params, args_equal, args))
        elif args_in is not None:
            self.test.assertTrue(args in args_in,
                "Expected reply to request '%s' called with parameters %r to"
                " have arguments in %s, but received %s."
                % (requestname, params, args_in, args))
        elif args_length is not None:
            self.test.assertEqual(len(args), args_length,
                "Expected reply to request '%s' called with parameters %r to"
                " have %d arguments, but received %d: %s."
                % (requestname, params, args_length, len(args), args))

        if informs_count is not None:
            self.test.assertEqual(len(informs), informs_count,
                "Expected %d informs in reply to request '%s' called with"
                " parameters %r, but received %d."
                % (informs_count, requestname, params, len(informs)))

        return args

    def assert_request_fails(self, requestname, *params, **kwargs):
        """Assert that the given request fails when called with the given
        parameters.

        Parameters
        ----------
        requestname : str
            The name of the request.
        params : list of objects
            The parameters with which to call the request.
        status_equals : string, optional
            Keyword parameter.  Assert that the reply status equals this
            string.
        error_equals : string, optional
            Keyword parameter.  Assert that the error message equals this
            string.
        """

        reply, informs = self.blocking_request(Message.request(requestname,
                                                               *params))

        self.test.assertEqual(reply.name, requestname,
                              "Reply to request '%s' has name '%s'." %
                              (requestname, reply.name))

        self.test.assertFalse(reply.reply_ok(), "Expected request '%s' called"
                              " with parameters %r to fail, but it was"
                              " successful." % (requestname, params))

        status = reply.arguments[0]
        error = reply.arguments[1] if len(reply.arguments) > 1 else None

        status_equals = kwargs.get("status_equals")
        error_equals = kwargs.get("error_equals")

        if status_equals is not None:
            self.test.assertTrue(status == status_equals,
                "Expected request '%s' called with parameters %r to return"
                " status %s, but the status was %r."
                % (requestname, params, status_equals, status))

        if error_equals is not None:
            self.test.assertTrue(error is not None and error == error_equals,
                "Expected request '%s' called with parameters %r to fail with"
                " error %s, but the error was %r."
                % (requestname, params, error_equals, error))

        return status, error

    def test_setter_request(self, requestname, sensorname, sensortype=str,
                            good=(), bad=(), places=7, args_echo=False):
        """Test a request which simply sets the value of a sensor.

        Parameters
        ----------
        requestname : str
            The name of the request.
        sensorname : str
            The name of the sensor.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        good : list of objects
            A list of values to which this request can successfully set the
            sensor.  The object type should match sensortype.
        bad : list of objects
            A list of values to which this request cannot successfully set the
            sensor.  The object type should match sensortype.
        places : int, optional
            The number of places to use in a float comparison.  Has no effect
            if sensortype is not float. Default: 7.
        args_echo : boolean, optional
            Check that the value is echoed as an argument by the reply to the
            request.  Default: False.
        """

        for value in good:
            self.assert_request_succeeds(requestname, value,
                                         args_echo=args_echo)
            time.sleep(self._sensor_lag())

            self.assert_sensor_equals(sensorname, value, sensortype,
                "After request '%s' was called with parameter %r, value of"
                " sensor '%s' is %%r. Expected %r%s."
                % (requestname, value, sensorname, value,
                   (" (within %d decimal places)" % places
                    if sensortype == float else "")), places)

        for value in bad:
            self.assert_request_fails(requestname, value)

    def test_multi_setter_request(self, requestname, good=(), bad=()):
        """Test a request which causes several sensor values to change.

        Parameters
        ----------
        requestname : str
            The name of the request.
        good : list of tuples
            Each tuple contains a tuple of successful parameters, a tuple of
            expected sensor values (see :meth:`expected_sensor_value_tuple`),
            and optionally a dict of options. Permitted options are: "statuses"
            and a list of status tuples to check, or "delay" and a float in
            seconds specifying an additional delay before the sensors are
            expected to change.
        bad : list of tuples
            Each tuple is set of parameters which should cause the request to
            fail.
        """

        def testtuple(params, expected_values, options={}):
            return (params, expected_values, options)

        good = [testtuple(*t) for t in good]

        for params, expected_values, options in good:
            delay = options.get("delay", 0)
            expected_statuses = options.get("statuses", ())

            self.assert_request_succeeds(requestname, *params)
            time.sleep(self._sensor_lag() + delay)

            expected_values = [self.expected_sensor_value_tuple(*t)
                               for t in expected_values]

            for sensorname, value, sensortype, places in expected_values:
                self.assert_sensor_equals(sensorname, value, sensortype,
                    "After request '%s' was called with parameters %r, value"
                    " of sensor '%s' is %%r. Expected %r%s."
                    % (requestname, params, sensorname, value,
                       (" (within %d decimal places)" % places
                        if sensortype == float else "")), places)

            for sensorname, status in expected_statuses:
                self.assert_sensor_status_equals(sensorname, status,
                    "After request '%s' was called with parameters %r, status"
                    " of sensor '%s' is %%r. Expected %r."
                    % (requestname, params, sensorname, status))

        for params in bad:
            self.assert_request_fails(requestname, *params)


class DeviceTestServer(DeviceServer):
    """Test server."""

    def __init__(self, *args, **kwargs):
        super(DeviceTestServer, self).__init__(*args, **kwargs)
        self.__msgs = []
        self.restart_queue = Queue.Queue()
        self.set_restart_queue(self.restart_queue)
        self._cancel_slow_command = threading.Event()
        self.slow_waiting = False

    def setup_sensors(self):
        self.restarted = False
        self.add_sensor(DeviceTestSensor(
            Sensor.INTEGER, "an.int", "An Integer.", "count",
            [-5, 5],
            timestamp=12345, status=Sensor.NOMINAL, value=3))

    def request_new_command(self, sock, msg):
        """A new command."""
        return Message.reply(msg.name, "ok", "param1", "param2")

    def request_raise_exception(self, sock, msg):
        """A handler which raises an exception."""
        raise Exception("An exception occurred!")

    def request_raise_fail(self, sock, msg):
        """A handler which raises a FailReply."""
        raise FailReply("There was a problem with your request.")

    def request_slow_command(self, sock, msg):
        """A slow command, waits for msg.arguments[0] seconds"""
        self.slow_waiting = True
        self._cancel_slow_command.wait(float(msg.arguments[0]))
        self.slow_waiting = False
        return Message.reply(msg.name, "ok", msgid=msg.mid)

    def request_cancel_slow_command(self, sock, msg):
        """Cancel slow command request, resulting in it replying immedietely"""
        self._cancel_slow_command.set()
        return Message.reply(msg.name, "ok", msgid=msg.mid)

    def handle_message(self, sock, msg):
        self.__msgs.append(msg)
        super(DeviceTestServer, self).handle_message(sock, msg)

    def messages(self):
        return self.__msgs

    def stop(self, *args, **kwargs):
        # Make sure a slow command with long timeout does not hold us
        # up.
        self._cancel_slow_command.set()
        super(DeviceTestServer, self).stop(*args, **kwargs)


class TestUtilMixin(object):
    """Mixin class implementing test helper methods for making
       assertions about lists of KATCP messages.
       """

    def _assert_msgs_length(self, actual_msgs, expected_number):
        """Assert that the number of messages is as expected."""
        num_msgs = len(actual_msgs)
        if num_msgs < expected_number:
            self.assertEqual(num_msgs, expected_number,
                             "Too few messages received.")
        elif num_msgs > expected_number:
            self.assertEqual(num_msgs, expected_number,
                             "Too many messages received.")

    def _assert_msgs_equal(self, actual_msgs, expected_msgs):
        """Assert that the actual and expected messages are equal.

           actual_msgs: list of message objects received
           expected_msgs: expected message strings
           """
        for msg, msg_str in zip(actual_msgs, expected_msgs):
            self.assertEqual(str(msg), msg_str)
        self._assert_msgs_length(actual_msgs, len(expected_msgs))

    def _assert_msgs_match(self, actual_msgs, expected):
        """Assert that the actual messages match the expected regular
           expression patterns.

           actual_msgs: list of message objects received
           expected: expected patterns
           """
        for msg, pattern in zip(actual_msgs, expected):
            self.assertTrue(re.match(pattern, str(msg)),
                            "Message did match pattern %r: %s" %
                            (pattern, msg))
        self._assert_msgs_length(actual_msgs, len(expected))

    def _assert_msgs_like(self, actual_msgs, expected):
        """Assert that the actual messages start and end with
           the expected strings.

           actual_msgs: list of message objects received
           expected_msgs: tuples of (expected_prefix, expected_suffix)
           """
        for msg, (prefix, suffix) in zip(actual_msgs, expected):
            str_msg = str(msg)

            if prefix and not str_msg.startswith(prefix):
                self.assertEqual(str_msg, prefix,
                    msg="Message '%s' does not start with '%s'."
                    % (str_msg, prefix))

            if suffix and not str_msg.endswith(suffix):
                self.assertEqual(str_msg, suffix,
                    msg="Message '%s' does not end with '%s'."
                    % (str_msg, suffix))
        self._assert_msgs_length(actual_msgs, len(expected))

def counting_callback(event=None, number_of_calls=1):
    """Decorate a callback to set an event once it has been called a certain number of times

    Parameters
    ==========
    event: threading.Event() -- will be set when enough calls have been made.
        If None, a new event will be created
    number_of_calls: int > 0 -- Number of calls before event.set() is called

    Decorated Properties
    ====================

    done -- The event object
    get_no_calls() -- Returns current number of calls
    wait(timeout=None) -- Wait for number_of_calls to be made
    assert_wait(timeout=None) -- Wait for number_of_calls and raises
        AssertionError if they are not made before the timeout.
    reset() -- Set call count back to zero
    """
    if event is None:
        event = threading.Event()

    def decorator(original_callback):
        assert number_of_calls > 0
        calls = [0]

        @functools.wraps(original_callback)
        def wrapped_callback(*args, **kwargs):
            retval = original_callback(*args, **kwargs)
            calls[0] += 1
            if calls[0] >= number_of_calls:
                event.set()
            return retval

        wrapped_callback.get_no_calls = lambda : calls[0]
        wrapped_callback.done = event
        wrapped_callback.wait = event.wait

        def assert_wait(timeout=None):
            done = event.wait(timeout)
            assert event.isSet()
            return done
        wrapped_callback.assert_wait = assert_wait

        def reset():
            calls[0] = 0
            event.clear()
        wrapped_callback.reset = reset

        return wrapped_callback
    return decorator


def suppress_queue_repeats(queue, initial_value, read_time=None):
    """Generator that reads a Queue.Queue() and suppresses runs of repeated values

    The queue is consumed, and a value yielded whenever it differs from the
    previous value. If read_time is specified, stops iteration if the
    queue is empty after reading the queue for read_time seconds. read_time=None
    continues reading forever.
    """
    start_time = time.time()
    cur_value = initial_value
    next_wait = read_time

    while True:
        if next_wait:
            next_wait = read_time - (time.time() - start_time)
            next_wait = max(0, next_wait)
        try:
            next_value = queue.get(timeout=next_wait)
        except Queue.Empty:
            break
        if next_value != cur_value:
            yield next_value
            cur_value = next_value

class SensorTransitionWaiter(object):
    """
    Wait for a given set of sensor transitions

    Can be used to test sensor transitions indepedent of timing. If the
    SensorTransitionWaiter object is instantiated before the test is triggered
    the transitions are guaranteed (I hope) to be detected.
    """
    def __init__(self, sensor, value_sequence=None):
        """
        Parameters
        ----------
        sensor : KATSensor object to watch
        value_sequence : list of sensor values or callable conditions or None

            Will check that this sequence of values occurs on the watched
            sensor. The first value is the starting value -- an error will be
            raised if that is not the initial value.  If an object in the list
            is a callable, it will be called on the actual sensor value. If it
            returns True, the value will be considered as matched. If
            value_sequence is None, no checking will be done, and the .wait()
            method cannot be called. However, the received values can be
            retrieved using get_received_values().

        If an exact value sequence is passed, the sensor is watched
        until its value changes. If the sensor does not take on the
        exact sequence of values, the transition is not matched. If
        expected values are repeated (e.g. [1, 2, 2, 3]) the repeated
        values (i.e. the second 2 in the example) are ignored.


        The following algorithm is used to test a For a sequence for validity
        when callable tests are used:

        * The initial value needs to satisfy at least the first test
        * While the current and not next test passes, continue reading
        * If neither current nor next passes, signal failure
        * If next passes, move test sequence along

        This can be used to check a sequence of floating point values:

        waiter = WaitForSensorTransition(sensor, value_sequence=(
            lambda x: x < 0.7,
            lambda x: x >= 0.7,
            lambda x: x >= 1,
            lambda x: x < 1,
            lambda x: x < 0.3)
        waiter.wait(timeout=1)

        can be used to check that a sensor starts at a value smaller than 0.7,
        then grows to a value larger than 1 and then shrinks to a value smaller
        than 0.3. Note that the x >= 0.7 test is required in case the sensor
        value attains exactly 0.7.

        For tests like this it is important to specify a timeout.
        """

        self.sensor = sensor
        self.desired_value_sequence = value_sequence
        self._torn_down = False
        self._done = False
        self._value_queue = Queue.Queue()
        self.timed_out = False
        current_value = self._get_current_sensor_value()
        if value_sequence:
            if not self._test_value(current_value, value_sequence[0]):
                raise ValueError('Sensor value does not satisfy initial condition')
        self.received_values = [current_value]
        self._configure_sensor()

    def _get_current_sensor_value(self):
        return self.sensor.value()

    def _configure_sensor(self):
        # Do neccesary sensor configuration. Assumes sensor is stored as
        # self.sensor

        # Attach our observer to the katcp sensor
        class observer(object): pass
        observer.update = self._sensor_callback
        self._observer = observer
        self.sensor.attach(self._observer)

    def _teardown_sensor(self):
        # Perform teardown cleanup actions on self.sensor
        self.sensor.detach(self._observer)

    def _sensor_callback(self, sensor):
        assert(sensor is self.sensor)
        self._value_queue.put(sensor.value())

    def _test_value(self, value, value_test):
        """
        Test value against value_test

        value_test can be a callable or a simple value. If it is a simple
        value it is compared for equality, otherwise value_test(value)
        is called and the result returned
        """
        if callable(value_test):
            return value_test(value)
        else:
            return value == value_test

    def wait(self, timeout=5):
        """
        Wait until the specified transition occurs

        Parameters
        ----------
        timeout : float seconds or None
            Time to wait for the transition. Wait forever if None

        Return Value
        ------------

        Returns True if the sequence is matched within the
        timeout. Returns False if the sequence does not match or if
        the timeout expired. The actual received values is available
        as the `received_values` member of the object. Sets the member
        `timed_out` to True if a timeout occured.
        """
        if self._done:
            raise RuntimeError('Transition already triggered. Instantiate a new '
                               'SensorTransitionWaiter object')
        if self._torn_down:
            raise RuntimeError('This object has been torn down. Instantiate a new '
                               'SensorTransitionWaiter object')
        nonrepeat_sensor_values = suppress_queue_repeats(
            self._value_queue, self.received_values[-1], timeout)

        try:
            # While current and not next test passes, continue reading
            # If neither current nor next passes, signal failure
            # If next passes, move test sequence along
            for current_test, next_test in zip(
                    self.desired_value_sequence[:-1],
                    self.desired_value_sequence[1:]):
                if self._test_value(self.received_values[-1], next_test):
                    continue # Next test already satisfied by current value
                while True:
                    # Read values from the queue until either the timeout
                    # expires or a value different from the last is found
                    next_value = nonrepeat_sensor_values.next()
                    self.received_values.append(next_value)
                    current_pass = self._test_value(next_value, current_test)
                    next_pass = self._test_value(next_value, next_test)
                    if next_pass:
                        break         # Matches, move on to the next test
                    if not current_pass:
                        # Matches neither the current test nor the
                        # next. Indicates an invalid sequence.
                        return False
            # We have passed all test conditions, hence the desired
            # transition has occured
            return True
        except StopIteration:
            self.timed_out = True
            return False
        finally:
            self._done = True
            self.teardown()

    def get_received_values(self, stop=True, reset=True):
        """Return list of values received to date.

        Parameters
        ----------

        stop -- Stop listening, tear down and unsubscribe from sensor
        reset -- Reset the recieved values to an empty list

        Note, once this method has been called, wait() can no longer work
        """
        try:
            while True:
                self.received_values.append(self._value_queue.get_nowait())
        except Queue.Empty:
            pass
        received_values = self.received_values
        if reset:
            self.received_values = []
        if stop:
            self.teardown()
        return received_values

    def teardown(self):
        """Clean up, restoring sensor strategy and deregistering the sensor callback"""
        if self._torn_down:
            return
        self._teardown_sensor()
        self._torn_down = True

def wait_sensor(sensor, value, timeout=5):
    """Wait for a katcp sensor to attain a certain value

    Temporarily attaches to the sensor to get sensor updates. It is assumed that
    the sensor is getting updated by another thread.

    """
    tests = (lambda x: True, value)
    waiter = SensorTransitionWaiter(sensor, tests)
    return waiter.wait(timeout=timeout)


