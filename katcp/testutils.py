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
import types
from .core import Sensor, Message, DeviceMetaclass
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
    #__metaclass__ = TestClientMetaclass

    def __init__(self, test, *args, **kwargs):
        """Takes a TestCase class as an additional parameter."""
        self.test = test
        super(BlockingTestClient, self).__init__(*args, **kwargs)

    def raw_send(self, chunk):
        """Send a raw chunk of data to the server."""
        self._sock.send(chunk)

    def _sensor_lag(self):
        """The expected lag before device changes are applied."""
        return getattr(self.test, "sensor_lag", 0)

    def record_messages(self, whitelist=(), blacklist=(), informs=True, replies=False):
        """Helper method for attaching a hook to selected received messages.

        Parameters
        ----------
        whitelist : list or tuple
            Record only messages matching these names.  If the list is empty,
            all received messages will be saved except any specified in the
            blacklist.
        blacklist : list or tuple
            Ignore messages matching these names.  If any names appear both in
            the whitelist and the blacklist, the whitelist will take precedence.
        informs : boolean
            Whether to record informs.  Default: True.
        replies : boolean
            Whether to record replies.  Default: False.
        """
        msg_types = []
        if informs:
            msg_types.append("inform")
        if replies:
            msg_types.append("reply")

        msgs = []

        for msg_type in msg_types:
            if whitelist:
                def handle_msg(client, msg):
                    msgs.append(msg)

                handler_dict = getattr(self, "_%s_handlers" % msg_type)

                for name in whitelist:
                    handler_dict[name] = handle_msg

            else:
                handler = getattr(self, "handle_%s" % msg_type)

                def handle_msg(client, msg):
                    if msg.name not in blacklist:
                        msgs.append(msg)
                    return handler(msg)

                newhandler = types.MethodType(handle_msg, self, self.__class__)

                setattr(self, "handle_%s" % msg_type, newhandler)

        return msgs

    @staticmethod
    def expected_sensor_value_tuple(sensorname, value, sensortype=str, places=7):
        """Helper method for completing optional values in expected sensor value tuples.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        value : obj
            The expected value of the sensor.  Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value. Default: str.
        places : int, optional
            The number of places to use in a float comparison.  Has no effect if
            sensortype is not float. Default: 7.
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

        reply, informs = self.blocking_request(Message.request("sensor-value", sensorname))
        self.test.assertTrue(reply.reply_ok(),
            "Could not retrieve sensor '%s': %s"
            % (sensorname, (reply.arguments[1] if len(reply.arguments) >= 2 else ""))
        )

        self.test.assertEqual(len(informs), 1,
            "Expected one sensor value inform for sensor '%s', but received %d."
            % (sensorname, len(informs))
        )

        inform = informs[0]

        self.test.assertEqual(len(inform.arguments), 5,
            "Expected sensor value inform for sensor '%s' to have five arguments, but received %d."
            % (sensorname, len(inform.arguments))
        )

        timestamp, _num, _name, status, value = inform.arguments

        try:
            if sensortype == bool:
                typestr = "%r and then %r" % (int, bool)
                value =  bool(int(value))
            else:
                typestr = "%r" % sensortype
                value = sensortype(value)
        except ValueError, e:
            self.test.fail("Could not convert value %r of sensor '%s' to type %s: %s"
                % (value, sensorname, typestr, e)
            )

        self.test.assertTrue(status in Sensor.STATUSES.values(),
            "Got invalid status value %r for sensor '%s'." % (status, sensorname)
        )

        try:
            timestamp =  int(timestamp)
        except ValueError, e:
            self.test.fail("Could not convert timestamp %r of sensor '%s' to type %r: %s"
                % (timestamp, sensorname, int, e)
            )

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

    def assert_sensor_equals(self, sensorname, expected, sensortype=str, msg=None, places=7, status=None):
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
            The number of places to use in a float comparison.  Has no effect if
            sensortype is not float. Default: 7.
        status : str, optional
            The expected status of the sensor.
        """

        if msg is None:
            places_msg = " (within %d decimal places)" % places if sensortype == float else ""
            msg = "Value of sensor '%s' is %%r. Expected %r%s." % (sensorname, expected, places_msg)

        got, got_status, _timestamp = self.get_sensor(sensorname, sensortype)
        if '%r' in msg:
            msg = msg % got

        if sensortype == float:
            self.test.assertAlmostEqual(got, expected, places, msg)
        else:
            self.test.assertEqual(got, expected, msg)

        if status is not None:
            self.test.assertEqual(got_status, status, "Status of sensor '%s' is %r. Expected %r." % (sensorname, got_status, status))

    def assert_sensor_status_equals(self, sensorname, expected_status, msg=None):
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
            msg = "Status of sensor '%s' is %%r. Expected %r." % (sensorname, expected_status)

        got_status = self.get_sensor_status(sensorname)

        if '%r' in msg:
            msg = msg % got_status

        self.test.assertEqual(got_status, expected_status, msg)

    def assert_sensor_not_equal(self, sensorname, expected, sensortype=str, msg=None, places=7):
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
            The number of places to use in a float comparison.  Has no effect if
            sensortype is not float. Default: 7.
        """

        if msg is None:
            places_msg = " (within %d decimal places)" % places if sensortype == float else ""
            msg = "Value of sensor '%s' is %%r. Expected a different value%s." % (sensorname, places_msg)

        got = self.get_sensor_value(sensorname, sensortype)
        if '%r' in msg:
            msg = msg % got

        if sensortype == float:
            self.test.assertNotAlmostEqual(got, expected, places, msg)
        else:
            self.test.assertNotEqual(got, expected, msg)

    def assert_sensors_equal(self, sensor_tuples, msg=None):
        """Assert that the values of several sensors are equal to the given values.

        Parameters
        ----------
        sensor_tuples : list of tuples
            A list of tuples specifying the sensor values to be checked.  See :meth:`expected_sensor_value_tuple`.
        msg : str, optional
            A custom message to print if the assertion fails.  If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        """

        sensor_tuples = [self.expected_sensor_value_tuple(*t) for t in sensor_tuples]
        for sensorname, expected, sensortype, places in sensor_tuples:
            self.assert_sensor_equals(sensorname, expected, sensortype, msg=msg, places=places)

    def assert_sensors_not_equal(self, sensor_tuples, msg=None):
        """Assert that the values of several sensors are not equal to the given values.

        Parameters
        ----------
        sensor_tuples : list of tuples
            A list of tuples specifying the sensor values to be checked.  See :meth:`expected_sensor_value_tuple`.
        msg : str, optional
            A custom message to print if the assertion fails.  If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        """

        sensor_tuples = [self.expected_sensor_value_tuple(*t) for t in sensor_tuples]
        for sensorname, expected, sensortype, places in sensor_tuples:
            self.assert_sensor_not_equal(sensorname, expected, sensortype, msg=msg, places=places)

    def wait_until_sensor_equals(self, timeout, sensorname, value, sensortype=str, places=7, pollfreq=0.1):
        """Wait until a sensor's value is equal to the given value, or time out.

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
            The number of places to use in a float comparison.  Has no effect if
            sensortype is not float. Default: 7.
        pollfreq : float, optional
            How frequently to poll for the sensor value. Default: 0.1.
        """

        stoptime = time.time() + timeout
        success = False

        if sensortype == float:
            cmpfun = lambda got, exp: abs(got-exp) < 10**-places
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
            self.test.fail("Timed out while waiting %ss for %s sensor to become %s. Last value was %s." % (timeout, sensorname, value, lastval))

    # REQUEST PARAMETERS

    def test_sensor_list(self, expected_sensors, ignore_descriptions=False):
        """Test that the list of sensors on the device equals the provided list.

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
            % (reply.arguments[1] if len(reply.arguments) >= 2 else "")
        )

        expected_sensors = [sensortuple(*t) for t in expected_sensors]
        got_sensors = [sensortuple(*m.arguments) for m in informs]

        #print ",\n".join([str(t) for t in got_sensors])

        if ignore_descriptions:
            expected_sensors = [s[:1]+s[2:] for s in expected_sensors]
            got_sensors = [s[:1]+s[2:] for s in got_sensors]

        expected_set = set(expected_sensors)
        got_set = set(got_sensors)

        self.test.assertEqual(got_set, expected_set,
            "Sensor list differs from expected list.\nThese sensors are missing:\n%s\nFound these unexpected sensors:\n%s"
            % ("\n".join(sorted([str(t) for t in expected_set - got_set])), "\n".join(sorted([str(t) for t in got_set - expected_set])))
        )

    def test_help(self, expected_requests, full_descriptions=False, exclude_defaults=True):
        """Test that the list of requests on the device equals the provided list.

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
            If this is true, exclude requests on the device which match requests
            found on :class:`katcp.DeviceServer` by name.  Default: True.
        """

        descriptions = True
        if not expected_requests or isinstance(expected_requests[0], str):
            descriptions = False

        reply, informs = self.blocking_request(Message.request("help"))
        self.test.assertTrue(reply.reply_ok(),
            "Could not retrieve help list: %s"
            % (reply.arguments[1] if len(reply.arguments) >= 2 else "")
        )

        got_requests = [tuple(m.arguments) for m in informs]

        if descriptions:
            if not full_descriptions:
                got_requests = [(name, desc.split('\n')[0]) for (name, desc) in got_requests]
                expected_requests = [(name, desc.split('\n')[0]) for (name, desc) in expected_requests]
        else:
            got_requests = [name for (name, desc) in got_requests]

        got_set = set(got_requests)
        expected_set = set(expected_requests)

        if exclude_defaults:
            default_request_names = set(DeviceServer._request_handlers.keys())

            if not descriptions:
                got_set = got_set - default_request_names
            else:
                got_set = set([(name, desc) for (name, desc) in got_set if name not in default_request_names])

        self.test.assertEqual(got_set, expected_set,
            "Help list differs from expected list.\nThese requests are missing:\n%s\nFound these unexpected requests:\n%s"
            % ("\n".join(sorted([str(t) for t in expected_set - got_set])), "\n".join(sorted([str(t) for t in got_set - expected_set])))
        )

    def assert_request_succeeds(self, requestname, *params, **kwargs):
        """Assert that the given request completes successfully when called with the given parameters, and optionally check the arguments.

        Parameters
        ----------
        requestname : str
            The name of the request.
        params : list of objects
            The parameters with which to call the request.
        args_echo : boolean, optional
            Keyword parameter.  Assert that the reply arguments after 'ok' equal
            the request parameters.  Takes precedence over args_equal, args_in
            and args_length. Default False.
        args_equal : list of strings, optional
            Keyword parameter.  Assert that the reply arguments after 'ok' equal
            this list.  Ignored if args_echo is present; takes precedence over
            args_echo, args_in and args_length.
        args_in : list of lists, optional
            Keyword parameter.  Assert that the reply arguments after 'ok' equal
            one of these tuples.  Ignored if args_equal or args_echo is present;
            takes precedence over args_length.
        args_length : int, optional
            Keyword parameter.  Assert that the length of the reply arguments
            after 'ok' matches this number. Ignored if args_equal, args_echo or
            args_in is present.
        informs_count : int, optional
            Keyword parameter.  Assert that the number of informs received
            matches this number.
        """

        reply, informs = self.blocking_request(Message.request(requestname, *params))

        self.test.assertEqual(reply.name, requestname, "Reply to request '%s' has name '%s'." % (requestname, reply.name))

        self.test.assertTrue(reply.reply_ok(),
            "Expected request '%s' called with parameters %r to succeed, but it failed %s."
            % (requestname, params, ("with error '%s'" % reply.arguments[1] if len(reply.arguments) >= 2 else "(with no error message)"))
        )

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
                "Expected reply to request '%s' called with parameters %r to have arguments %s, but received %s."
                % (requestname, params, args_equal, args)
            )
        elif args_in is not None:
            self.test.assertTrue(args in args_in,
                "Expected reply to request '%s' called with parameters %r to have arguments in %s, but received %s."
                % (requestname, params, args_in, args)
            )
        elif args_length is not None:
            self.test.assertEqual(len(args), args_length,
                "Expected reply to request '%s' called with parameters %r to have %d arguments, but received %d: %s."
                % (requestname, params, args_length, len(args), args)
            )

        if informs_count is not None:
            self.test.assertEqual(len(informs), informs_count,
                "Expected %d informs in reply to request '%s' called with parameters %r, but received %d."
                % (informs_count, requestname, params, len(informs))
            )

        return args

    def assert_request_fails(self, requestname, *params, **kwargs):
        """Assert that the given request fails when called with the given parameters.

        Parameters
        ----------
        requestname : str
            The name of the request.
        params : list of objects
            The parameters with which to call the request.
        status_equals : string, optional
            Keyword parameter.  Assert that the reply status equals this string.
        error_equals : string, optional
            Keyword parameter.  Assert that the error message equals this
            string.
        """

        reply, informs = self.blocking_request(Message.request(requestname, *params))

        self.test.assertEqual(reply.name, requestname, "Reply to request '%s' has name '%s'." % (requestname, reply.name))

        self.test.assertFalse(reply.reply_ok(),
            "Expected request '%s' called with parameters %r to fail, but it was successful."
            % (requestname, params)
        )

        status = reply.arguments[0]
        error = reply.arguments[1] if len(reply.arguments) > 1 else None

        status_equals = kwargs.get("status_equals")
        error_equals = kwargs.get("error_equals")

        if status_equals is not None:
            self.test.assertTrue(status == status_equals,
                "Expected request '%s' called with parameters %r to return status %s, but the status was %r."
                % (requestname, params, status_equals, status)
            )

        if error_equals is not None:
            self.test.assertTrue(error is not None and error == error_equals,
                "Expected request '%s' called with parameters %r to fail with error %s, but the error was %r."
                % (requestname, params, error_equals, error)
            )

        return status, error

    def test_setter_request(self, requestname, sensorname, sensortype=str, good=(), bad=(), places=7, args_echo=False):
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
            The number of places to use in a float comparison.  Has no effect if
            sensortype is not float. Default: 7.
        args_echo : boolean, optional
            Check that the value is echoed as an argument by the reply to the
            request.  Default: False.
        """

        for value in good:
            self.assert_request_succeeds(requestname, value, args_echo=args_echo)
            time.sleep(self._sensor_lag())

            self.assert_sensor_equals(
                sensorname, value, sensortype,
                "After request '%s' was called with parameter %r, value of sensor '%s' is %%r. Expected %r%s."
                % (requestname, value, sensorname, value, (" (within %d decimal places)" % places if sensortype == float else "")),
                places
            )

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
            expected sensor values (see :meth:`expected_sensor_value_tuple`), and
            optionally a dict of options. Permitted options are: "statuses" and
            a list of status tuples to check, or "delay" and a float in seconds
            specifying an additional delay before the sensors are expected to
            change.
        bad : list of tuples
            Each tuple is set of parameters which should cause the request to fail.
        """

        def testtuple(params, expected_values, options={}):
            return (params, expected_values, options)

        good = [testtuple(*t) for t in good]

        for params, expected_values, options in good:
            delay = options.get("delay", 0)
            expected_statuses = options.get("statuses", ())

            self.assert_request_succeeds(requestname, *params)
            time.sleep(self._sensor_lag() + delay)

            expected_values = [self.expected_sensor_value_tuple(*t) for t in expected_values]

            for sensorname, value, sensortype, places in expected_values:
                self.assert_sensor_equals(
                    sensorname, value, sensortype,
                    "After request '%s' was called with parameters %r, value of sensor '%s' is %%r. Expected %r%s."
                    % (requestname, params, sensorname, value, (" (within %d decimal places)" % places if sensortype == float else "")),
                    places
                )

            for sensorname, status in expected_statuses:
                self.assert_sensor_status_equals(
                    sensorname, status,
                    "After request '%s' was called with parameters %r, status of sensor '%s' is %%r. Expected %r."
                    % (requestname, params, sensorname, status)
                )

        for params in bad:
            self.assert_request_fails(requestname, *params)


class DeviceTestServer(DeviceServer):
    """Test server."""

    def __init__(self, *args, **kwargs):
        super(DeviceTestServer, self).__init__(*args, **kwargs)
        self.__msgs = []
        self.restart_queue = Queue.Queue()
        self.set_restart_queue(self.restart_queue)

    def setup_sensors(self):
        self.restarted = False
        self.add_sensor(DeviceTestSensor(
            Sensor.INTEGER, "an.int", "An Integer.", "count",
            [-5, 5],
            timestamp=12345, status=Sensor.NOMINAL, value=3
        ))

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
        """A slow command, sleeps for msg.arguments[0]"""
        time.sleep(float(msg.arguments[0]))
        return Message.reply(msg.name, "ok", msgid=msg.mid)

    def handle_message(self, sock, msg):
        self.__msgs.append(msg)
        super(DeviceTestServer, self).handle_message(sock, msg)

    def messages(self):
        return self.__msgs


class TestUtilMixin(object):
    """Mixin class implementing test helper methods for making
       assertions about lists of KATCP messages.
       """

    def _assert_msgs_length(self, actual_msgs, expected_number):
        """Assert that the number of messages is that expected."""
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
            self.assertTrue(re.match(pattern, str(msg)), "Message did match pattern %r: %s" % (pattern, msg))
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
                    % (str_msg, prefix)
                )

            if suffix and not str_msg.endswith(suffix):
                self.assertEqual(str_msg, suffix,
                    msg="Message '%s' does not end with '%s'."
                    % (str_msg, suffix)
                )
        self._assert_msgs_length(actual_msgs, len(expected))
