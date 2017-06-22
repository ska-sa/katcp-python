# testutils.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Test utils for katcp package tests."""

from __future__ import division, print_function, absolute_import

import logging
import re
import time
import Queue
import threading
import functools

import mock
import tornado.testing
import tornado.ioloop
import tornado.locks
import tornado.gen

from thread import get_ident

from tornado.concurrent import Future as tornado_Future
from concurrent.futures import Future, TimeoutError

from katcp import client


from .core import (Sensor,
                   Message,
                   AsyncReply,
                   AsyncEvent,
                   AttrDict,
                   steal_docstring_from,
                   ProtocolFlags)
from .server import DeviceServer, FailReply, ClientConnection
from .kattypes import (request,
                       return_reply,
                       Float, Str, Int,
                       concurrent_reply,
                       request_timeout_hint)
from .object_proxies import ObjectWrapper

logger = logging.getLogger(__name__)


def add_mid_to_msg_str(msg_str, mid):
    if mid:
        msg_parts = msg_str.split(' ')
        msg_parts[0] += '[%s]' % mid
        return ' '.join(msg_parts)
    else:
        return msg_str


class ClientConnectionTest(object):
    """A version of katcp.server.ClientConnection* suitable for testing.

    Records all messages.

    """
    def __init__(self):
        self.messages = []
        self.mass_informs = []

    def inform(self, msg):
        self.messages.append(msg)

    def mass_inform(self, msg):
        self.mass_informs.append(msg)

    def reply(self, msg, req_msg):
        self.messages.append(msg)

    @property
    def informs(self):
        return [m for m in self.messages if m.mtype == Message.INFORM]

    @property
    def replies(self):
        return [m for m in self.messages if m.mtype == Message.REPLY]


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


class MessageRecorder(object):
    """Message recorder.

    Parameters
    ----------
    msg_types : container supporting `in` operator
        Record only message of these types (eg. [Message.REQUEST])
    whitelist : container supporting `in` operator
        Record only messages matching these names.  If the list is empty,
        all received messages will be saved except any specified in the
        blacklist.
    regex_filter : str
        Record only messages that matches this regex. Applied to the
        unescaped message string. Messages are pre-filtered by the whitelist
        parameter, so only messages that match both this regex and have
        names listed in whitelist are recorded.
    blacklist : container supporting `in` operator
        Ignore messages matching these names.  If any names appear both in
        the whitelist and the blacklist, the whitelist will take
        precedence.

    """
    def __init__(self, msg_types, whitelist, regex_filter, blacklist):
        self.msgs = []
        self.msg_types = msg_types
        self.whitelist = whitelist
        self.blacklist = blacklist
        self.regex_filter = re.compile(regex_filter) if regex_filter else None
        self.msg_received = threading.Event()

    def get_msgs(self, min_number=0, timeout=1):
        """Return the messages recorded so far and delete them.

        Parameters
        ----------
        min_number : int, optional
            Minimum number of messages to return, else wait
        timeout : int or None, optional
            Don't wait longer than this in seconds

        """
        self.wait_number(min_number, timeout)
        msg_count = len(self.msgs)
        msgs_copy = self.msgs[:msg_count]
        del self.msgs[:msg_count]
        return msgs_copy

    __call__ = get_msgs           # For backwards compatibility

    def wait_number(self, number, timeout=1):
        """Wait until at least certain number of messages have been recorded.

        Parameters
        ----------
        number : int
            Number of messages to wait for
        timeout : int or None, optional
            Don't wait longer than this in seconds

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
            if self._record_predicate(msg):
                self.msgs.append(msg)
                self.msg_received.set()
        finally:
            self.msg_received.clear()

    def _record_predicate(self, msg):
        if self.whitelist and msg.name not in self.whitelist:
            return False
        if self.regex_filter:
            if self.regex_filter.match(str(msg)):
                return True
            else:
                return False
        if msg.name in self.blacklist:
            return False
        return True


class BlockingTestClient(client.BlockingClient):
    """Test blocking client."""
    def __init__(self, test, *args, **kwargs):
        """Takes a TestCase class as an additional parameter."""
        self.test = test
        self._message_recorders = {}
        super(BlockingTestClient, self).__init__(*args, **kwargs)

    @client.make_threadsafe
    def raw_send(self, chunk):
        """Send a raw chunk of data to the server."""
        self._stream.write(chunk)

    def _sensor_lag(self):
        """The expected lag before device changes are applied."""
        return getattr(self.test, "sensor_lag", 0)

    def handle_inform(self, msg):
        """Pass unhandled informs to message recorders."""
        for append_msg in self._message_recorders.values():
            append_msg(msg)
        return super(BlockingTestClient, self).handle_inform(msg)

    def handle_reply(self, msg):
        """Pass unhandled replies to message recorders."""
        for append_msg in self._message_recorders.values():
            append_msg(msg)
        return super(BlockingTestClient, self).handle_reply(msg)

    def message_recorder(self, whitelist=(), blacklist=(), regex_filter=None,
                         informs=True, replies=False):
        """Helper method for attaching a hook to selected received messages.

        Parameters
        ----------
        whitelist : list or tuple, optional
            Record only messages matching these names. If the list is empty,
            all received messages will be saved except any specified in the
            blacklist.
        blacklist : list or tuple, optional
            Ignore messages matching these names. If any names appear both in
            the whitelist and the blacklist, the whitelist will take
            precedence.
        regex_filter : str or None, optional
            Record only messages that matches this regex. Applied to the
            unescaped message string. Messages are pre-filtered by the whitelist
            parameter, so only messages that match both this regex and have
            names listed in whitelist are recorded.
        informs : bool, optional
            Whether to record informs.
        replies : bool, optional
            Whether to record replies.

        Returns
        -------
        get_msgs : :class:`MessageRecorder` object
            Callable instance of MessageRecorder. Returns a list of messages
            that have matched so far.  Each call returns the list of message
            since the previous call.

        """
        msg_types = set()
        if informs:
            msg_types.add(Message.INFORM)
        if replies:
            msg_types.add(Message.REPLY)
        whitelist = set(whitelist)
        blacklist = set(blacklist)

        mr = MessageRecorder(msg_types, whitelist, regex_filter, blacklist)
        self._message_recorders[id(mr)] = mr.append_msg

        return mr

    @staticmethod
    def expected_sensor_value_tuple(sensorname, value, sensortype=str,
                                    places=7):
        """Helper for completing optional values in expected sensor value tuple.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        value : obj
            The expected value of the sensor. Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value.
        places : int, optional
            The number of places to use in a float comparison. Has no effect
            if sensortype is not float.

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
            The type to use to convert the sensor value.

        """
        reply, informs = self.blocking_request(Message.request("sensor-value",
                                                               sensorname))
        msg = ("Could not retrieve sensor '%s': %s"
               % (sensorname, (reply.arguments[1]
                               if len(reply.arguments) >= 2 else "")))
        self.test.assertTrue(reply.reply_ok(), msg)

        self.test.assertEqual(len(informs), 1,
                              "Expected one sensor value inform for "
                              "sensor '%s', but received %d."
                              % (sensorname, len(informs)))

        inform = informs[0]

        self.test.assertEqual(len(inform.arguments), 5,
                              "Expected sensor value inform for sensor "
                              "'%s' to have five arguments, but received %d."
                              % (sensorname, len(inform.arguments)))

        timestamp, _num, _name, status, value = inform.arguments

        try:
            if sensortype == bool:
                typestr = "%r and then %r" % (int, bool)
                value = bool(int(value))
            else:
                typestr = "%r" % sensortype
                value = sensortype(value)
        except ValueError, e:
            self.test.fail("Could not convert value %r of sensor '%s' to type "
                           "%s: %s" % (value, sensorname, typestr, e))

        self.test.assertTrue(status in Sensor.STATUSES.values(),
                             "Got invalid status value %r for sensor '%s'."
                             % (status, sensorname))

        try:
            timestamp = float(timestamp)
        except ValueError, e:
            self.test.fail("Could not convert timestamp %r of sensor '%s' to "
                           "type %r: %s" % (timestamp, sensorname, float, e))

        return value, status, timestamp

    def get_sensor_value(self, sensorname, sensortype=str):
        """Retrieve the value of a sensor.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        sensortype : type, optional
            The type to use to convert the sensor value.

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
            The expected value of the sensor. Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value.
        msg : str, optional
            A custom message to print if the assertion fails. If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        places : int, optional
            The number of places to use in a float comparison. Has no effect
            if sensortype is not float.
        status : str or None, optional
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
            msg = ("Status of sensor '%s' is %r. Expected %r."
                   % (sensorname, got_status, status))
            self.test.assertEqual(got_status, status, msg)

    def assert_sensor_status_equals(self, sensorname, expected_status,
                                    msg=None):
        """Assert that a sensor's status is equal to the given status.

        Parameters
        ----------
        sensorname : str
            The name of the sensor.
        expected_status : str
            The expected status of the sensor.
        msg : str or None, optional
            A custom message to print if the assertion fails. If the string
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
            The expected value of the sensor. Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value.
        msg : str, optional
            A custom message to print if the assertion fails. If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.
        places : int, optional
            The number of places to use in a float comparison. Has no effect
            if sensortype is not float.

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

    def wait_until_sensors_equal(self, timeout, sensor_tuples, msg=None):
        """Wait up to timeout seconds for several sensors to reach the given values.
        Parameters
        ----------
        sensor_tuples : list of tuples
            A list of tuples specifying the sensor values to be checked. See
            :meth:`expected_sensor_value_tuple`.
        timeout : float seconds
           The overall timeout for all the sensors to reach their targets
        msg : str or None, optional
            A custom message to print if the assertion fails. If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.

        """
        sensor_tuples = [self.expected_sensor_value_tuple(*t)
                         for t in sensor_tuples]
        max_time = time.time() + timeout
        try:
            self.assert_sensors_equal(sensor_tuples, msg)
            return
        except AssertionError:
            pass
        while time.time() < max_time:
            try:
                self.assert_sensors_equal(sensor_tuples, msg)
                return
            except AssertionError:
                time.sleep(0.01)

        msg = (msg or '') + ': timed out after {}s'.format(timeout)
        self.assert_sensors_equal(sensor_tuples, msg)


    def assert_sensors_equal(self, sensor_tuples, msg=None):
        """Assert that the values of several sensors are equal to given values.

        Parameters
        ----------
        sensor_tuples : list of tuples
            A list of tuples specifying the sensor values to be checked. See
            :meth:`expected_sensor_value_tuple`.
        msg : str or None, optional
            A custom message to print if the assertion fails. If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.

        """
        sensor_tuples = [self.expected_sensor_value_tuple(*t)
                         for t in sensor_tuples]
        for sensorname, expected, sensortype, places in sensor_tuples:
            self.assert_sensor_equals(sensorname, expected, sensortype,
                                      msg=msg, places=places)

    def assert_sensors_not_equal(self, sensor_tuples, msg=None):
        """Assert that values of several sensors are not equal to given values.

        Parameters
        ----------
        sensor_tuples : list of tuples
            A list of tuples specifying the sensor values to be checked. See
            :meth:`expected_sensor_value_tuple`.
        msg : str or None, optional
            A custom message to print if the assertion fails. If the string
            contains %r, it will be replaced with the sensor's value. A default
            message is defined in this method.

        """
        sensor_tuples = [self.expected_sensor_value_tuple(*t)
                         for t in sensor_tuples]
        for sensorname, expected, sensortype, places in sensor_tuples:
            self.assert_sensor_not_equal(sensorname, expected, sensortype,
                                         msg=msg, places=places)

    def wait_condition(self, timeout, predicate, *args, **kwargs):
        """ Wait for the boolean function `predicate(*args, **kwargs)` to become True.
        Default polling period is 0.02s. A different period may be set by specifying
        'poll_period' as a named arg in the predicate."""
        t0 = time.time()
        poll_period = kwargs.pop('poll_period', 0.02)
        cond = predicate(*args, **kwargs)
        while not (cond or (time.time()-t0 >timeout)):
            time.sleep(poll_period)
            cond = predicate(*args, **kwargs)
        return (cond, '' if cond else 'Timed out after %s seconds' % timeout)

    def wait_until_sensor_equals(self, timeout, sensorname, value,
                                 sensortype=str, places=7, pollperiod=0.02):
        """Wait until a sensor's value equals the given value, or times out.

        Parameters
        ----------
        timeout : float
            How long to wait before timing out, in seconds.
        sensorname : str
            The name of the sensor.
        value : obj
            The expected value of the sensor. Type must match sensortype.
        sensortype : type, optional
            The type to use to convert the sensor value.
        places : int, optional
            The number of places to use in a float comparison. Has no effect
            if sensortype is not float.
        pollperiod : float, optional
            How frequently to poll for the sensor value.

        """
        # TODO Should be changed to use some variant of SensorTransitionWaiter

        stoptime = time.time() + timeout
        success = False

        if sensortype == float:
            cmpfun = lambda got, exp: abs(got - exp) < 10 ** -places
        else:
            cmpfun = lambda got, exp: got == exp

        lastval = self.get_sensor_value(sensorname, sensortype)
        while time.time() < stoptime:
            if cmpfun(lastval, value):
                success = True
                break
            time.sleep(pollperiod)
            lastval = self.get_sensor_value(sensorname, sensortype)

        if not success:
            self.test.fail("Timed out while waiting %ss for %s sensor to"
                           " become %s. Last value was %s." %
                           (timeout, sensorname, value, lastval))

    # REQUEST PARAMETERS

    def test_sensor_list(self, expected_sensors, ignore_descriptions=False):
        """Test that list of sensors on the device equals the provided list.

        Parameters
        ----------
        expected_sensors : list of tuples
            The list of expected sensors. Each tuple contains the arguments
            returned by each sensor-list inform, as unescaped strings.
        ignore_descriptions : boolean, optional
            If this is true, sensor descriptions will be ignored in the
            comparison.

        """
        def sensortuple(name, description, units, stype, *params):
            # ensure float params reduced to the same format
            if stype == "float":
                params = ["%g" % float(p) for p in params]
            return (name, description, units, stype) + tuple(params)

        reply, informs = self.blocking_request(Message.request("sensor-list"))

        msg = ("Could not retrieve sensor list: %s"
               % (reply.arguments[1] if len(reply.arguments) >= 2 else ""))
        self.test.assertTrue(reply.reply_ok(), msg)

        expected_sensors = [sensortuple(*t) for t in expected_sensors]
        got_sensors = [sensortuple(*m.arguments) for m in informs]

        # print ",\n".join([str(t) for t in got_sensors])

        if ignore_descriptions:
            expected_sensors = [s[:1] + s[2:] for s in expected_sensors]
            got_sensors = [s[:1] + s[2:] for s in got_sensors]

        expected_set = set(expected_sensors)
        got_set = set(got_sensors)

        msg = ("Sensor list differs from expected list.\n"
               "These sensors are missing:\n%s\n"
               "Found these unexpected sensors:\n%s"
               % ("\n".join(sorted([str(t) for t in expected_set - got_set])),
                  "\n".join(sorted([str(t) for t in got_set - expected_set]))))
        self.test.assertEqual(got_set, expected_set, msg)

    def test_help(self, expected_requests, full_descriptions=False,
                  exclude_defaults=True):
        """Test that list of requests on the device equals the provided list.

        Parameters
        ----------
        expected_requests : list of tuples or strings
            The list of expected requests. May be a list of request names as
            strings. May also be a list of tuples, each of which contains the
            request name and either the first line or the full text of the
            request description, as unescaped strings. If full_descriptions is
            true, full description text must be provided.
        full_descriptions : boolean, optional
            If this is true, the full text of descriptions is compared,
            otherwise only text up to the first newline is compared.  Has no
            effect if expected_requests is a list of strings.
        exclude_defaults : boolean, optional
            If this is true, exclude requests on the device which match
            requests found on :class:`katcp.DeviceServer` by name.

        """
        descriptions = True
        if not expected_requests or isinstance(expected_requests[0], str):
            descriptions = False

        reply, informs = self.blocking_request(Message.request("help"))
        msg = ("Could not retrieve help list: %s"
               % (reply.arguments[1] if len(reply.arguments) >= 2 else ""))
        self.test.assertTrue(reply.reply_ok(), msg)

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

        msg = ("Help list differs from expected list.\n"
               "These requests are missing:\n%s\n"
               "Found these unexpected requests:\n%s"
               % ("\n".join(sorted([str(t) for t in expected_set - got_set])),
                  "\n".join(sorted([str(t) for t in got_set - expected_set]))))
        self.test.assertEqual(got_set, expected_set, msg)

    def assert_request_succeeds(self, requestname, *params, **kwargs):
        """Assert that given request succeeds when called with given parameters.

        Optionally also checks the arguments.

        Parameters
        ----------
        requestname : str
            The name of the request.
        params : list of objects
            The parameters with which to call the request.
        args_echo : bool, optional
            Keyword parameter.  Assert that the reply arguments after 'ok'
            equal the request parameters.  Takes precedence over args_equal,
            args_in and args_length. Defaults to False.
        args_equal : None or list of strings, optional
            Keyword parameter.  Assert that the reply arguments after 'ok'
            equal this list.  Ignored if args_echo is present; takes precedence
            over args_echo, args_in and args_length.
        args_in : None or list of lists, optional
            Keyword parameter.  Assert that the reply arguments after 'ok'
            equal one of these tuples.  Ignored if args_equal or args_echo is
            present; takes precedence over args_length.
        args_length : None or int, optional
            Keyword parameter.  Assert that the length of the reply arguments
            after 'ok' matches this number. Ignored if args_equal, args_echo or
            args_in is present.
        informs_count : None or int, optional
            Keyword parameter.  Assert that the number of informs received
            matches this number, if not None.
        informs_args_equal : None or list of lists
            Keyword parameter.  Assert that the arguments of the informs
            received match this list. The sub-lists are the expected args for
            each inform. The order of received informs must match the order of
            the main list. Implicitly also checks the number of informs received.

        """
        reply, informs = self.blocking_request(Message.request(requestname,
                                                               *params))

        self.test.assertEqual(reply.name, requestname, "Reply to request '%s'"
                              " has name '%s'." % (requestname, reply.name))

        msg = ("Expected request '%s' called with parameters %r to succeed, "
               "but it failed %s."
               % (requestname, params, ("with error '%s'" % reply.arguments[1]
                                        if len(reply.arguments) >= 2 else
                                        "(with no error message)")))
        self.test.assertTrue(reply.reply_ok(), msg)

        args = reply.arguments[1:]

        args_echo = kwargs.get("args_echo", False)
        args_equal = kwargs.get("args_equal")
        args_in = kwargs.get("args_in")
        args_length = kwargs.get("args_length")
        informs_count = kwargs.get("informs_count")
        informs_args_equal = kwargs.get("informs_args_equal")

        if args_echo:
            args_equal = [str(p) for p in params]

        if args_equal is not None:
            msg = ("Expected reply to request '%s' called with parameters %r "
                   "to have arguments %s, but received %s."
                   % (requestname, params, args_equal, args))
            self.test.assertEqual(args, args_equal, msg)
        elif args_in is not None:
            msg = ("Expected reply to request '%s' called with parameters %r "
                   "to have arguments in %s, but received %s."
                   % (requestname, params, args_in, args))
            self.test.assertTrue(args in args_in, msg)
        elif args_length is not None:
            msg = ("Expected reply to request '%s' called with parameters %r "
                   "to have %d arguments, but received %d: %s."
                   % (requestname, params, args_length, len(args), args))
            self.test.assertEqual(len(args), args_length, msg)

        if informs_count is not None:
            msg = ("Expected %d informs in reply to request '%s' called with "
                   "parameters %r, but received %d."
                   % (informs_count, requestname, params, len(informs)))
            self.test.assertEqual(len(informs), informs_count, msg)

        if informs_args_equal is not None:
            expected_inform_args = list(informs_args_equal)
            actual_inform_args = [inform.arguments for inform in informs]
            msg = ("Expected inform arguments {expected_inform_args} in reply to request "
                   "{requestname!r} called with parameters {params!r}, "
                   "but received {actual_inform_args!r}.".format(**locals()))
            self.test.assertEqual(actual_inform_args, expected_inform_args, msg)

        return args

    def assert_request_fails(self, requestname, *params, **kwargs):
        """Assert that given request fails when called with given parameters.

        Parameters
        ----------
        requestname : str
            The name of the request.
        params : list of objects
            The parameters with which to call the request.
        status_equals : string, optional
            Keyword parameter. Assert that the reply status equals this
            string.
        error_equals : string, optional
            Keyword parameter. Assert that the error message equals this
            string.

        """
        reply, informs = self.blocking_request(Message.request(requestname,
                                                               *params))

        msg = "Reply to request '%s' has name '%s'." % (requestname, reply.name)
        self.test.assertEqual(reply.name, requestname, msg)

        msg = ("Expected request '%s' called with parameters %r to fail, "
               "but it was successful." % (requestname, params))
        self.test.assertFalse(reply.reply_ok(), msg)

        status = reply.arguments[0]
        error = reply.arguments[1] if len(reply.arguments) > 1 else None

        status_equals = kwargs.get("status_equals")
        error_equals = kwargs.get("error_equals")

        if status_equals is not None:
            msg = ("Expected request '%s' called with parameters %r to return "
                   "status %s, but the status was %r."
                   % (requestname, params, status_equals, status))
            self.test.assertTrue(status == status_equals, msg)

        if error_equals is not None:
            msg = ("Expected request '%s' called with parameters %r to fail "
                   "with error %s, but the error was %r."
                   % (requestname, params, error_equals, error))
            self.test.assertTrue(error is not None and error == error_equals, msg)

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
            The type to use to convert the sensor value.
        good : list of objects, optional
            A list of values to which this request can successfully set the
            sensor.  The object type should match sensortype.
        bad : list of objects, optional
            A list of values to which this request cannot successfully set the
            sensor.  The object type should match sensortype.
        places : int, optional
            The number of places to use in a float comparison. Has no effect
            if sensortype is not float.
        args_echo : boolean, optional
            Check that the value is echoed as an argument by the reply to the
            request.

        """
        for value in good:
            self.assert_request_succeeds(requestname, value,
                                         args_echo=args_echo)
            time.sleep(self._sensor_lag())

            msg = ("After request '%s' was called with parameter %r, "
                   "value of sensor '%s' is %%r. Expected %r%s."
                   % (requestname, value, sensorname, value,
                      (" (within %d decimal places)" % places
                       if sensortype == float else "")))
            self.assert_sensor_equals(sensorname, value, sensortype, msg, places)

        for value in bad:
            self.assert_request_fails(requestname, value)

    def test_multi_setter_request(self, requestname, good=(), bad=()):
        """Test a request which causes several sensor values to change.

        Parameters
        ----------
        requestname : str
            The name of the request.
        good : list of tuples, optional
            Each tuple contains a tuple of successful parameters, a tuple of
            expected sensor values (see :meth:`expected_sensor_value_tuple`),
            and optionally a dict of options. Permitted options are: "statuses"
            and a list of status tuples to check, or "delay" and a float in
            seconds specifying an additional delay before the sensors are
            expected to change.
        bad : list of tuples, optional
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
                msg = ("After request '%s' was called with parameters %r, "
                       "value of sensor '%s' is %%r. Expected %r%s."
                       % (requestname, params, sensorname, value,
                          (" (within %d decimal places)" % places
                           if sensortype == float else "")))
                self.assert_sensor_equals(sensorname, value, sensortype,
                                          msg, places)

            for sensorname, status in expected_statuses:
                msg = ("After request '%s' was called with parameters %r, "
                       "status of sensor '%s' is %%r. Expected %r."
                       % (requestname, params, sensorname, status))
                self.assert_sensor_status_equals(sensorname, status, msg)

        for params in bad:
            self.assert_request_fails(requestname, *params)


class DeviceTestServer(DeviceServer):
    """Test server."""

    def __init__(self, *args, **kwargs):
        super(DeviceTestServer, self).__init__(*args, **kwargs)
        # Make a copies so that test users can modify the available handlers without
        # breaking other tests
        self._request_handlers = dict(self._request_handlers)
        self._inform_handlers = dict(self._inform_handlers)
        self._reply_handlers = dict(self._reply_handlers)
        self.__msgs = []
        # Set to fail string if the sensor-list request should break
        self.break_sensor_list = False
        # Set to fail string if the help request should break
        self.break_help = False
        self.restart_queue = Queue.Queue()
        self.set_restart_queue(self.restart_queue)
        # Map of ClientConnection -> futures that can be resolved to cancel command
        self._slow_futures = {}
        self._cnt_futures = set()
        self.proceed_on_client_connect = AsyncEvent()
        """Do not send #version-connect on connection when True"""
        self.proceed_on_client_connect.set()

    @property
    def request_names(self):
        return self._request_handlers.keys()

    @property
    def sensor_names(self):
        return self._sensors.keys()

    @tornado.gen.coroutine
    def on_client_connect(self, client_conn):
        yield self.proceed_on_client_connect.until_set()
        rv = yield super(DeviceTestServer, self).on_client_connect(client_conn)
        raise tornado.gen.Return(rv)

    def setup_sensors(self):
        self.restarted = False
        self.add_sensor(DeviceTestSensor(
            Sensor.INTEGER, "an.int", "An Integer.", "count",
            [-5, 5],
            timestamp=12345, status=Sensor.NOMINAL, value=3))

    def request_new_command(self, req, msg):
        """A new command."""
        return Message.reply(msg.name, "ok", "param1", "param2")

    def request_raise_exception(self, req, msg):
        """A handler which raises an exception."""
        raise Exception("An exception occurred!")

    def request_raise_fail(self, req, msg):
        """A handler which raises a FailReply."""
        raise FailReply("There was a problem with your request.")

    @request_timeout_hint(99)
    def request_slow_command(self, req, msg):
        """A slow command, waits for msg.arguments[0] seconds.

        This is an async request that will allow another request to be handled
        before this one replies.

        Request can be cancelled using ?cancel-slow-command

        """
        if req.client_connection in self._slow_futures:
            raise FailReply(
                'A slow command is already running for this connection')
        t0 = time.time()
        wait_time = float(msg.arguments[0])
        fut = self._slow_futures[req.client_connection] = Future()
        @tornado.gen.coroutine
        def slow_timeout():
            try:
                yield tornado.gen.with_timeout(t0 + wait_time, fut, self.ioloop)
                req.reply("ok")
            except tornado.gen.TimeoutError:
                req.reply("ok")
            except Exception:
                self._logger.exception('Unable to complete ?slow-command request')
                req.reply('fail', 'Unhandled exception, see logs')
            finally:
                self._slow_futures.pop(req.client_connection, None)
        self.ioloop.add_callback(slow_timeout)
        raise AsyncReply

    def request_cancel_slow_command(self, req, msg):
        """Cancel slow command request, resulting in it replying immediately."""
        fut = self._slow_futures.pop(req.client_connection, None)
        if fut:
            fut.set_result(None)
        return req.make_reply('ok')

    def handle_message(self, req, msg):
        self.__msgs.append(msg)
        self._check_cnt_futures()
        return super(DeviceTestServer, self).handle_message(req, msg)

    @steal_docstring_from(DeviceServer.request_help)
    def request_help(self, req, msg):
        if self.break_help:
            return req.make_reply('fail', self.break_help)
        return super(DeviceTestServer, self).request_help(req, msg)

    @steal_docstring_from(DeviceServer.request_sensor_list)
    def request_sensor_list(self, req, msg):
        if self.break_sensor_list:
            return req.make_reply('fail', self.break_sensor_list)
        return super(DeviceTestServer, self).request_sensor_list(req, msg)

    @property
    def messages(self):
        return self.__msgs

    def until_messages(self, cnt):
        f = Future()
        self._cnt_futures.add((cnt, f))
        self.ioloop.add_callback(self._check_cnt_futures)
        return f

    def _check_cnt_futures(self):
        cnt = len(self.__msgs)
        for f_cnt, fut in list(self._cnt_futures):
            if cnt >= f_cnt:
                self._cnt_futures.remove((f_cnt, fut))
                fut.set_result(self.__msgs)

    def stop(self, *args, **kwargs):
        # Make sure a slow command with long timeout does not hold us up.
        for fut in self._slow_futures.values():
            if not fut.done:
                fut.set_result(None)
        super(DeviceTestServer, self).stop(*args, **kwargs)


class AsyncDeviceTestServer(DeviceTestServer):
    def __init__(self, *args, **kwargs):
        super(AsyncDeviceTestServer, self).__init__(*args, **kwargs)
        self.set_concurrency_options(thread_safe=False, handler_thread=False)

    @request(Float())
    @return_reply()
    @request_timeout_hint(99)
    @concurrent_reply
    @tornado.gen.coroutine
    def request_slow_command(self, req, wait_time):
        """A slow coroutine request, waits for msg.arguments[0] seconds.

        This is an async request that will allow another request to be handled
        before this one replies.

        Request can be cancelled using ?cancel-slow-command

        """
        if req.client_connection in self._slow_futures:
            raise FailReply(
                'A slow command is already running for this connection')

        t0 = time.time()
        fut = self._slow_futures[req.client_connection] = Future()
        try:
            yield tornado.gen.with_timeout(t0 + wait_time, fut)
        except tornado.gen.TimeoutError:
            pass
        finally:
            self._slow_futures.pop(req.client_connection, None)

        raise tornado.gen.Return(('ok', ))


class DeviceTestServerWithTimeoutHints(DeviceTestServer):
    PROTOCOL_INFO = ProtocolFlags(5, 1, set([
        ProtocolFlags.MULTI_CLIENT,
        ProtocolFlags.MESSAGE_IDS,
        ProtocolFlags.REQUEST_TIMEOUT_HINTS
        ]))

    def __init__(self, *args, **kwargs):
        super(DeviceTestServerWithTimeoutHints, self).__init__(*args, **kwargs)
        self.request_timeout_hints = {
            'slow-command': 10.5,
            'raise-fail': 2.3}

    @request(Str(optional=True))
    @return_reply(Int())
    def request_request_timeout_hint(self, req, name):
        """Return timeout hints for requests"""
        hints = ({name: self.request_timeout_hints.get(name, 0)} if name
                 else self.request_timeout_hints)
        for req_name, timeout_hint in hints.items():
            req.inform(req_name, timeout_hint)
        return ('ok', len(hints))




class SensorComparisonMixin(object):
    """Mixin to test that katcp.Sensor objects match specified criteria.

    Should be mixed in with unittest.TestCase subclasses.

    """

    # Mapping of description keys to functions that extract the
    # value of the appropriate key from a sensor
    key_fns = dict(
        name=lambda s: s.name,
        status=lambda s: s._status,
        type=lambda s: s.SENSOR_TYPE_LOOKUP[s.stype],
        value=lambda s: s.value(),
        timestamp=lambda s: s.read()[0],
        description=lambda s: s.description,
        units=lambda s: s.units,
        params=lambda s: s.params,)

    def _get_sensor_description(self, sensor, desired_keys=None, ignore_keys=()):
        """Return a dict description of a sensor and its values"""

        key_fns = self.key_fns
        if desired_keys == None:
            desired_keys = set(key_fns.keys())
        else:
            desired_keys = set(desired_keys)
        desired_keys = desired_keys - set(ignore_keys)

        def get_sensor_key(sensor, key):
            try: key_fn = key_fns[key]
            except KeyError, e: raise KeyError('Unknown sensor key: ' + e.message)
            return key_fn(sensor)

        sensor_description = {}
        for key in desired_keys:
            sensor_description[key] = get_sensor_key(sensor, key)
        return sensor_description

    def assert_sensor_equal(self, actual_sensor, desired_sensor, ignore_attributes=()):
        """Test that two sensor objects are equal, optionally ignoring some attributes

        Parameters
        ==========

        actual_sensor : katcp.Sensor
            The actual sensor to be checked
        desired_sensor : katcp.Sensor
            The model sensor
        ignore_attributes : seq of str, defaults to empty tuple
            Sensor attributes that should not taken into account for the
            comparison. Matches the keys as defined in assert_sensor_equal_description()'s
            docstring.
        """
        actual_description = self._get_sensor_description(
            actual_sensor, ignore_keys=ignore_attributes)
        desired_description = self._get_sensor_description(
            desired_sensor, ignore_keys=ignore_attributes)
        self.assertEqual(actual_description, desired_description)

    def assert_sensor_equal_description(self, actual_sensor, desired_description):
        """Test that a Sensor object (actual) matches a description (desired)

        The desired sensor is a dict with the parameters that should be
        tested

        Parameters
        ==========

        actual_sensor : katcp.Sensor
        desired_description : dict
           The following keys are tested:

            name : str (sensor name)
            status : int (sensor status enumeration, e.g. Sensor.NOMINAL)
            type : int (sensor type enumeration, e.g. Sensor.INTEGER)
            value : obj (sensor value)
            timestamp : float (value timestamp)
            description : str (sensor description)
            units : str (sensor units)
            params : list (sensor parameters as passsed to the constructor)

            If a key is left out of the description it is ignored, except for `name` which
            is required.
        """
        if 'name' not in desired_description:
            # The assert_sensors_equal_description() call doesn't work without a
            # 'name' key
            desired_description = desired_description.copy()
            desired_description['name'] = actual_sensor.name
        self.assert_sensors_equal_description(
            [actual_sensor], [desired_description])

    def assert_sensors_equal_description(self, actual_sensors, desired_description):
        """Test that a list of Sensor objects (actual) match a description (desired)

        Each desired sensor is a dict with the parameters that should be
        tested

        Parameters
        ==========

        actual sensors : seq of katcp.Sensor objects
        desired_description : seq of dict
            Each dict should have the format as described in
            assert_sensor_equal_description()

        The order of the actual or desired sensor sequence is not important, sensors are
        matched up on the basis of name.
        """
        actual_sensor_dict = dict((s.name, s) for s in actual_sensors)
        desired_description_dict = dict(
            (s['name'], s) for s in desired_description)
        # Check that the sensor names match
        self.assertEqual(sorted(actual_sensor_dict.keys()),
                         sorted(desired_description_dict.keys()))

        # Build description of the actual sensors in the same format
        # as desired_description
        actual_description_dict = {}
        for name, desired_info in desired_description_dict.items():
            actual_description_dict[name] = self._get_sensor_description(
                actual_sensor_dict[name], desired_info.keys())

        self.maxDiff = None     # Make unittest print differences even
                                # if they are large
        self.assertEqual(actual_description_dict, desired_description_dict)

    def assert_sensors_value_conditions(self, actual_sensors, value_tests):
        """Test that a list of Sensor objects (actual) match value_tests

        Parameters
        ----------
        actual_sensors -- List of sensor objects
        value_tests -- dict with
           value_tests['sensor_name'] : Callable value test. Sould raise
               AssertionError if the test fails
        """

        actual_sensor_dict = dict((s.name, s) for s in actual_sensors)
        # Check that all the requested sensors are present
        self.assertTrue(all(name in actual_sensor_dict
                             for name in value_tests.keys()))

        for name, test in value_tests.items():
            test(actual_sensor_dict[name].value())


class TestUtilMixin(object):
    """Mixin class for making assertions about lists of KATCP messages."""
    def _assert_msgs_length(self, actual_msgs, expected_number):
        """Assert that the number of messages is as expected."""
        num_msgs = len(actual_msgs)
        if num_msgs < expected_number:
            self.assertEqual(num_msgs, expected_number,
                             "Too few messages received.")
        elif num_msgs > expected_number:
            self.assertEqual(num_msgs, expected_number,
                             "Too many messages received.")

    def _assert_msgs_equal(self, actual_msgs, expected_msgs, mid=None):
        """Assert that the actual and expected messages are equal.

        Parameters
        ----------
        actual_msgs : list of message objects received
        expected_msgs : expected message strings
        mid : Add message identifier to message if not None

        """
        for msg, msg_str in zip(actual_msgs, expected_msgs):
            desired_msg_str = add_mid_to_msg_str(msg_str, mid)
            logger.debug('actual: %r, desired: %r' % (str(msg), msg_str))
            self.assertEqual(str(msg), desired_msg_str)
        self._assert_msgs_length(actual_msgs, len(expected_msgs))

    def _assert_msgs_match(self, actual_msgs, expected):
        """Assert that the actual messages match the expected regex patterns.

        Parameters
        ----------
        actual_msgs : list of message objects received
        expected : expected patterns

        """
        for msg, pattern in zip(actual_msgs, expected):
            self.assertTrue(re.match(pattern, str(msg)),
                            "Message did match pattern %r: %s" %
                            (pattern, msg))
        self._assert_msgs_length(actual_msgs, len(expected))

    def _assert_msgs_like(self, actual_msgs, expected, mid=None):
        """Assert that the actual messages start and end with expected strings.

        Parameters
        ----------
        actual_msgs : list of message objects received
        expected_msgs : tuples of (expected_prefix, expected_suffix)
        mid : Add message identifier to message if not None

        """
        for msg, (prefix, suffix) in zip(actual_msgs, expected):
            str_msg = str(msg)

            prefix = add_mid_to_msg_str(prefix, mid)

            if prefix and not str_msg.startswith(prefix):
                m = "Message '%s' does not start with '%s'." % (str_msg, prefix)
                self.assertEqual(str_msg, prefix, msg=m)

            if suffix and not str_msg.endswith(suffix):
                m = "Message '%s' does not end with '%s'." % (str_msg, suffix)
                self.assertEqual(str_msg, suffix, msg=m)

        self._assert_msgs_length(actual_msgs, len(expected))


def counting_callback(event=None, number_of_calls=1):
    """Decorate callback to set event once it's called certain number of times.

    Parameters
    ----------
    event: :class:`threading.Event` object or None, optional
        Will be set when enough calls have been made.
        If None, a new event will be created.
    number_of_calls: int, optional
        Number of calls before event.set() is called (must be > 0).

    Decorated Properties
    --------------------
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

        wrapped_callback.get_no_calls = lambda: calls[0]
        wrapped_callback.done = event
        wrapped_callback.wait = event.wait

        def assert_wait(timeout=1):
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
    """Generator that reads a Queue and suppresses runs of repeated values.

    The queue is consumed, and a value yielded whenever it differs from the
    previous value. If *read_time* is specified, stops iteration if the
    queue is empty after reading the queue for *read_time* seconds. If
    *read_time* is None, continue reading forever.

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
    """Wait for a given set of sensor transitions.

    Can be used to test sensor transitions independent of timing. If the
    SensorTransitionWaiter object is instantiated before the test is triggered
    the transitions are guaranteed (I hope) to be detected.

    Parameters
    ----------
    sensor : KATSensor object
        Sensor to watch
    value_sequence : list of sensor values or callable conditions or None
        Will check that this sequence of values occurs on the watched
        sensor. The first value is the starting value -- an error will be
        raised if that is not the initial value. If an object in the list
        is a callable, it will be called on the actual sensor value. If it
        returns True, the value will be considered as matched.
        If value_sequence is None, no checking will be done, and the .wait()
        method cannot be called. However, the received values can be
        retrieved using get_received_values().

    Notes
    -----
    If an exact value sequence is passed, the sensor is watched
    until its value changes. If the sensor does not take on the
    exact sequence of values, the transition is not matched. If
    expected values are repeated (e.g. [1, 2, 2, 3]) the repeated
    values (i.e. the second 2 in the example) are ignored.

    The following algorithm is used to test a sequence for validity
    when callable tests are used:

    * The initial value needs to satisfy at least the first test
    * While the current and not next test passes, continue reading
    * If neither current nor next passes, signal failure
    * If next passes, move test sequence along

    This can be used to check a sequence of floating point values::

      waiter = WaitForSensorTransition(sensor, value_sequence=(
          lambda x: x < 0.7,
          lambda x: x >= 0.7,
          lambda x: x >= 1,
          lambda x: x < 1,
          lambda x: x < 0.3)
      waiter.wait(timeout=1)

    The above can be used to check that a sensor starts at a value smaller
    than 0.7, then grows to a value larger than 1 and then shrinks to a value
    smaller than 0.3. Note that the x >= 0.7 test is required in case the
    sensor value attains exactly 0.7.

    For tests like this it is important to specify a timeout.

    """
    def __init__(self, sensor, value_sequence=None):
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
        # Do necessary sensor configuration. Assumes sensor is stored as
        # self.sensor
        # Attach our observer to the katcp sensor
        class observer(object):
            pass
        observer.update = self._sensor_callback
        self._observer = observer
        self.sensor.attach(self._observer)

    def _teardown_sensor(self):
        # Perform teardown cleanup actions on self.sensor
        self.sensor.detach(self._observer)

    def _sensor_callback(self, sensor, reading):
        assert(sensor is self.sensor)
        self._value_queue.put(reading[2])

    def _test_value(self, value, value_test):
        """Test value against value_test.

        The *value_test* can be a callable or a simple value. If it is a simple
        value it is compared for equality, otherwise value_test(value)
        is called and the result returned.

        """
        if callable(value_test):
            return value_test(value)
        else:
            return value == value_test

    def wait(self, timeout=5):
        """Wait until the specified transition occurs.

        Parameters
        ----------
        timeout : float or None, optional
            Time to wait for the transition in seconds. Wait forever if None.

        Returns
        -------
        Returns True if the sequence is matched within the
        timeout. Returns False if the sequence does not match or if
        the timeout expired. The actual received values is available
        as the `received_values` member of the object. Sets the member
        `timed_out` to True if a timeout occurred.

        """
        if self._done:
            raise RuntimeError('Transition already triggered. Instantiate '
                               'a new SensorTransitionWaiter object')
        if self._torn_down:
            raise RuntimeError('This object has been torn down. Instantiate '
                               'a new SensorTransitionWaiter object')
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
                    continue  # Next test already satisfied by current value
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
        stop : bool, optional
            Stop listening, tear down and unsubscribe from sensor
        reset: bool, optional
            Reset the recieved values to an empty list

        Notes
        -----
        Once this method has been called, wait() can no longer work.

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
        """Clean up: restore sensor strategy and deregister sensor callback."""
        if self._torn_down:
            return
        self._teardown_sensor()
        self._torn_down = True


class SensorTransitionStatusWaiter(SensorTransitionWaiter):
    """Also check for sensor status transitions, not just value."""
    def _get_current_sensor_value(self):
        # Return (status, value) tuple
        return self.sensor.read()[1:]

    def _sensor_callback(self, sensor, reading):
        assert(sensor is self.sensor)
        self._value_queue.put(reading[1:])


def wait_sensor(sensor, value, status=None, timeout=5):
    """Wait for a katcp sensor to attain a certain value.

    Temporarily attaches to the sensor to get sensor updates.
    It is assumed that the sensor is getting updated by another thread.

    Returns True if the value is attained matched within the timeout, else False
    """
    test_val = value if status is None else (status, value)
    tests = (lambda x: True, test_val)
    if status is None:
        waiter = SensorTransitionWaiter(sensor, tests)
    else:
        waiter = SensorTransitionStatusWaiter(sensor, tests)
    return waiter.wait(timeout=timeout)


def wait_sensor_async(sensor, value, status=None, ioloop=None):
    """Stop-gap async sensor-wait until SensorTransitionWaiter can be made async compatible

    Returns a tornado future that resolves when the value is matched

    """
    ioloop = ioloop or tornado.ioloop.IOLoop.current()
    f = tornado_Future()
    class Observer(object):
        def update(self, sensor, reading):
            val_matched = reading.value == value
            status_matched = reading.status == status or status is None
            if val_matched and status_matched:
                sensor.detach(self)
                ioloop.add_callback(f.set_result, True)

    observer = Observer()
    sensor.attach(observer)
    ioloop.add_callback(observer.update, sensor, sensor.read())
    return f

def start_thread_with_cleanup(test_instance, thread_object, timeout=1,
                              start_timeout=None):
    """Start thread_object and add cleanup functions to test_instance.

    thread_object.start() is called to start the thread, or
    thread_object.start(timeout=start_timeout) if the start_timeout parameter
    is not None.

    thread_object.join(timeout=timeout) and thread_object.stop() is added to
    the test instance cleanup.

    """
    test_instance.addCleanup(thread_object.join, timeout=timeout)
    test_instance.addCleanup(thread_object.stop)
    if start_timeout is not None:
        thread_object.start(timeout=start_timeout)
    else:
        thread_object.start()


class AtomicIaddCallback(ObjectWrapper):
    # Attributes must be in the class definition, or else they will be
    # proxied to __subject__
    _mutex = None
    _callback = None

    def __init__(self, ob, callback=lambda x: x, mutex=None):
        self._callback = callback
        self._mutex = mutex if mutex else threading.RLock()
        super(AtomicIaddCallback, self).__init__(ob)

    def __iadd__(self, other):
        with self._mutex:
            self.__subject__ += other
            val = self.__subject__
        self._callback(val)
        return self


class WaitingMock(mock.Mock):
    def __init__(self, *args, **kwargs):
        super(WaitingMock, self).__init__(*args, **kwargs)
        self._counted_queue = Queue.Queue(maxsize=1)
        # Replace the underlying value for self.call_count with a proxied int
        # that uses a threading.RLock to allow atomic incrementation in case
        # multiple threads are calling the mock, and does a callback as soon as
        # the value is updated. This is needed in case the side_effect function
        # is blocking. The original mock.CallableMixin._mock_call() updates the
        # call_count before calling side_effect(). This means we can notify
        # someone waiting on self.assert_wait_call_count in another thread as
        # soon as the call is made, even if side_effect blocks.
        self.call_count = AtomicIaddCallback(0, callback=self._call_count_callback)

    def _call_count_callback(self, call_count):
        try:
            self._counted_queue.put_nowait(call_count)
        except Queue.Full:
            pass

    def reset_mock(self, visited=None):
        # Re-set call_count as an AtomicIaddCallback instance since
        # the reset_mock() super-method does self.call_count=0
        super(WaitingMock, self).reset_mock(visited)
        self.call_count = AtomicIaddCallback(
            self.call_count, callback=self._call_count_callback)

    def assert_wait_call_count(self, count, timeout=1.):
        """Wait for mock to be called >= *count* times within *timeout* seconds.

        Raises AssertionError if the call count is not reached.

        """
        t0 = time.time()
        to_wait = timeout
        if self.call_count >= count and len(self.call_args_list) >= count:
            return True
        while to_wait >= 0 and self.call_count < count:
            try:
                self._counted_queue.get(timeout=to_wait)
            except Queue.Empty:
                pass
            to_wait = timeout - (time.time() - t0)

        assert(self.call_count >= count)
        # The _mock_call method increments call_count before adding the call to
        # call_args or call_args_list, so it is possible that we return from
        # assert_wait_call_count() before the results are available. Try sleep
        # some more until this stops being the case.
        quantum = 0.001
        while to_wait >= 0 and len(self.call_args_list) < count:
            time.sleep(quantum)
            to_wait = timeout - (time.time() - t0)

        # If the call_args_list still hasn't been updated after the loop above
        # then the test using this function may fail if it looks at the
        # call_args_list.  Raise a RuntimeError to let the test author know
        # something went wrong.  The timeout parameter used by the test
        # should be increased.
        if self.call_count >= count and len(self.call_args_list) < count:
            raise RuntimeError("call_args_list not updated within timeout.")

class AsyncWaitingMock(mock.Mock):

    def __init__(self, *args, **kwargs):
        super(AsyncWaitingMock, self).__init__(*args, **kwargs)
        self._call_event = tornado.locks.Event()

    def _mock_call(self, *args, **kwargs):
        ret = super(AsyncWaitingMock, self)._mock_call(*args, **kwargs)
        self._call_event.set()
        return ret

    @tornado.gen.coroutine
    def assert_wait_call_count(self, count, timeout=1.):
        """Wait for mock to be called >= *count* times within *timeout* seconds.

        Raises AssertionError if the call count is not reached.

        """
        t0 = time.time()
        if self.call_count >= count:
            raise tornado.gen.Return(True)
        to_wait = timeout

        while to_wait >= 0 and self.call_count < count:
            try:
                yield self._call_event.wait(to_wait)
                self._call_event.clear()
            except tornado.gen.TimeoutError:
                pass
            to_wait = timeout - (time.time() - t0)

        assert(self.call_count >= count)


def mock_req(req_name, *args, **kwargs):
    """Create a mock ClientRequestConnection object.

    Parameters
    ----------
    req_name : str
        Name of the request
    *args : list of objects
        Arguments for the request, used to construct a request Message object

    Optional Keyword Arguments
    --------------------------
    server : obj
       Used as the server instance when constructing a client_connection.
       Cannot be specified together with client_conn.
    client_conn : obj
       Used as the client_connection object when constructing mock
       ClientRequestConnection object. Cannot be specified together with sock.
    sock : obj
       Used as the socket object when constructing server and client_connection

    If server but not sock is specified, a mock sock and real ClientConnection
    instances are used. If client_conn is not specified, a WaitingMock instance
    is used instead.

    Returns
    -------
    req : :class:`WaitingMock` object
        The `client_connection` and `msg` attributes are set. The
        :meth:`make_reply()` method returns the appropriate request Message
        object as a side effect.

    """
    server = kwargs.get('server')
    client_conn = kwargs.get('client_conn')
    sock = kwargs.get('sock')
    if server and client_conn:
        raise TypeError('Specify either server or client_conn, not both')
    if client_conn and sock:
        raise TypeError('Specify either client_conn or sock, not both')
    if not sock:
        sock = WaitingMock()
    if server:
        client_conn = ClientConnection(server._server, sock)
    if not client_conn:
        client_conn = WaitingMock()

    # TODO Consider making the mocks autospecced from the real classes to test
    # somewhat more strictly.

    # TODO Consider making .reply_msg and .inform_msgs attributes containing
    # the actual reply/inform Message objects that would have been sent.
    # May make certain kinds of test comparisons easier.

    req = WaitingMock()
    req.reply_msg = None
    req.inform_msgs = []
    req.client_connection = client_conn
    req.msg = Message.request(req_name, *args)
    req.make_reply.side_effect = lambda *args: Message.reply_to_request(
        req.msg, *args)
    f = req.reply_and_inform_msgs_future = tornado_Future()
    def reply_side_effect(*args):
        req.reply_msg = Message.reply_to_request(req.msg, *args)
        req.reply_and_inform_msgs_future.set_result((req.reply_msg, req.inform_msgs))
    req.reply.side_effect = reply_side_effect
    req.inform.side_effect = lambda *args : req.inform_msgs.append(
        Message.reply_inform(req.msg, *args))
    return req


def handle_mock_req(dev, req):
    """Instrument a real unstarted server.DeviceServer to handle a mock request.

    Parameters
    ----------
    dev : :class:`katcp.server.DeviceServer` object
         A device server that has not necessarily been started
    req : :class:`WaitingMock` object
        A mock request created with katcp.testutils.mock_req()

    Return Value
    ------------

    reply_and_inform_msgs_future : tornado Future instance
        Resolves with (reply_msg, inform_msgs)

    Example
    -------
    dev = katcp.server.DeviceServer(...)
    req = katcp.testutils.mock_request('help')
    handle_mock_req(dev, req)
    # All replies / informs can now be asserted on the mock request
    req.reply.assert_called_once_with('ok')

    # If using an async device from a tornado.test.gen_test test (or any other tornado
    # coroutine)

    reply_msg, inform_msgs = yield handle_mock_req(dev, req)
    self.assertTrue(reply_msg.reply_ok())

    """
    client_connection = req.client_connection
    # Hook up client_connection reply so that it logs reply on mock request obj
    client_connection.reply.side_effect = \
        lambda rep_msg, _: req.reply(*rep_msg.arguments)
    with mock.patch('katcp.server.ClientRequestConnection') as CRC:
        CRC.return_value = req
        dev.handle_request(req.client_connection, req.msg)
    return req.reply_and_inform_msgs_future


def call_in_ioloop(ioloop, fn, *args, **kwargs):
    """Run fn in ioloop and block until the result is available.

    Should raise exceptions with proper tracebacks.

    The *ioloop_timeout* kwarg is used as the maximum time to wait for a
    result, which defaults to 5 seconds. This kwarg is not sent to fn.
    Raises concurrent.futures.TimeoutError if the result times out.

    """
    ioloop_timeout = kwargs.pop('ioloop_timeout', 5)
    f = Future()
    tf = tornado_Future()                 # for nice tracebacks
    ioloop.add_callback(tornado.gen.chain_future, tf, f)
    @tornado.gen.coroutine
    def cb():
        return fn(*args, **kwargs)
    ioloop.add_callback(lambda: tornado.gen.chain_future(cb(), tf))
    try:
        f.result(timeout=ioloop_timeout)
    except TimeoutError:
        raise
    except Exception:
        pass
    return tf.result()


class TimewarpAsyncTestCase(tornado.testing.AsyncTestCase):
    """Tornado AsyncTestCase that supports timewarping.

    The io_loop.time() method is replaced by a mock-timer, that only progresses
    when moved along using set_ioloop_time().

    Note: subclasses must call their super setUp() methods.

    """
    def get_new_ioloop(self):
        ioloop = super(TimewarpAsyncTestCase, self).get_new_ioloop()
        self.ioloop_time = 0
        ioloop.time = lambda: self.ioloop_time
        def set_ioloop_thread_id():
            self.ioloop_thread_id = get_ident()
        ioloop.add_callback(set_ioloop_thread_id)
        return ioloop

    def wake_ioloop(self):
        f = tornado.concurrent.Future()
        self.io_loop.add_callback(lambda: f.set_result(None))
        return f

    def set_ioloop_time(self, new_time, wake_ioloop=True):
        logger.debug('setting ioloop time: {0}'.format(new_time))
        assert new_time > self.ioloop_time
        self.ioloop_time = new_time
        if wake_ioloop:
            return self.wake_ioloop()

class TimewarpAsyncTestCaseTimeAdvancer(threading.Thread):
    def __init__(self, test_instance, quantum=0.05, rate=1000.):
        self.test_instance = test_instance
        self.quantum = quantum
        self.rate = rate
        self._running = threading.Event()
        super(TimewarpAsyncTestCaseTimeAdvancer, self).__init__()

    def start(self):
        self.test_instance.addCleanup(self.stop)
        super(TimewarpAsyncTestCaseTimeAdvancer, self).start()

    def stop(self, timeout=None):
        if timeout:
            self._running.wait(timeout)
        self._running.clear()
        self._f.set_result(None)

    def run(self):
        self._f = f = Future()
        self._running.set()
        while self._running.is_set():
            try:
                self.test_instance.io_loop.add_callback(self._advance, f)
            except RuntimeError:
                # Probably means that the ioloop is closed, so let's just quit
                break
            f.result()
            self._f = f = Future()
            time.sleep(1./self.rate)

    @tornado.gen.coroutine
    def _advance(self, future):
        yield self.test_instance.set_ioloop_time(
            self.test_instance.ioloop_time + self.quantum)
        future.set_result(None)
