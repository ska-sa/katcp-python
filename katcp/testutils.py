"""Test utils for katcp package tests."""

import katcp
import client
import logging

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


class DeviceTestSensor(katcp.Sensor):
    """Test sensor."""

    def __init__(self, sensor_type, name, description, units, params,
                 timestamp, status, value):
        super(DeviceTestSensor, self).__init__(
            sensor_type, name, description, units, params)
        self.set(timestamp, status, value)
        self.__sampling_changes = []

    def _apply_sampling_change(self, strategy, params):
        self.__sampling_changes.append((strategy, params))

    def get_changes(self):
        return self.__sampling_changes


class TestClientMetaclass(katcp.DeviceMetaclass):
    """Metaclass for test client classes.

       Adds a raw send method and methods for collecting all inform and
       reply messages received by the client.
       """
    def __init__(mcs, name, bases, dct):
        """Constructor for TestClientMetaclass.  Should not be used
           directly.

           @param mcs The metaclass instance
           @param name The metaclass name
           @param bases List of base classes
           @param dct Class dict
        """
        super(TestClientMetaclass, mcs).__init__(name, bases, dct)

        orig_init = mcs.__init__
        orig_handle_reply = mcs.handle_reply
        orig_handle_inform = mcs.handle_inform

        def __init__(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            self.clear_messages()

        def handle_reply(self, msg):
            self._replies.append(msg)
            self._msgs.append(msg)
            return orig_handle_reply(self, msg)

        def handle_inform(self, msg):
            self._informs.append(msg)
            self._msgs.append(msg)
            return orig_handle_inform(self, msg)

        def raw_send(self, chunk):
            """Send a raw chunk of data to the server."""
            self._sock.send(chunk)

        def replies_and_informs(self):
            return self._replies, self._informs

        def messages(self):
            return self._msgs

        def clear_messages(self):
            self._replies = []
            self._informs = []
            self._msgs = []

        mcs.__init__ = __init__
        mcs.handle_reply = handle_reply
        mcs.handle_inform = handle_inform
        mcs.raw_send = raw_send
        mcs.replies_and_informs = replies_and_informs
        mcs.messages = messages
        mcs.clear_messages = clear_messages


class DeviceTestClient(client.DeviceClient):
    """Test client."""
    __metaclass__ = TestClientMetaclass


class CallbackTestClient(client.CallbackClient):
    """Test callback client."""
    __metaclass__ = TestClientMetaclass


class BlockingTestClient(client.BlockingClient):
    """Test blocking client."""
    __metaclass__ = TestClientMetaclass

    def get_sensor_value(self, sensorname):
        reply, informs = self.blocking_request(katcp.Message.request("sensor-value", sensorname))
        del(self._replies[-1:])
        del(self._informs[-len(informs):])
        del(self._msgs[-(len(informs)+1):])

        if str(reply) == "!sensor-value ok 1":
            value = str(informs[0]).split(" ")[5]
        else:
            raise ValueError(str(reply))
        return value


class DeviceTestServer(katcp.DeviceServer):
    """Test server."""

    def __init__(self, *args, **kwargs):
        super(DeviceTestServer, self).__init__(*args, **kwargs)
        self.__msgs = []

    def setup_sensors(self):
        self.restarted = False
        self.add_sensor(DeviceTestSensor(
            katcp.Sensor.INTEGER, "an.int", "An Integer.", "count",
            [-5, 5],
            timestamp=12345, status=katcp.Sensor.NOMINAL, value=3
        ))

    def schedule_restart(self):
        self.restarted = True

    def request_new_command(self, sock, msg):
        """A new command."""
        return katcp.Message.reply(msg.name, "ok", "param1", "param2")

    def request_raise_exception(self, sock, msg):
        """A handler which raises an exception."""
        raise Exception("An exception occurred!")

    def request_raise_fail(self, sock, msg):
        """A handler which raises a FailReply."""
        raise katcp.FailReply("There was a problem with your request.")

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

    def _check_request_params(self, request, returns=None, raises=None):
        sock = ""
        requestname = request.__name__[8:].replace("_", "-")
        if returns is None:
            returns = []
        if raises is None:
            raises = []

        returned_msgs = [(request(sock, katcp.Message.request(requestname, *tuple(params))), expected) for (params, expected) in returns]

        msgs_equal = [(msg, expected) for (msg, expected) in returned_msgs if not hasattr(expected, "__iter__")]
        msgs_like = [(msg, expected) for (msg, expected) in returned_msgs if hasattr(expected, "__iter__")]

        if msgs_equal:
            self._assert_msgs_equal(*zip(*msgs_equal))
        if msgs_like:
            self._assert_msgs_like(*zip(*msgs_like))

        for params in raises:
            self.assertRaises(katcp.FailReply, request, sock, katcp.Message.request(requestname, *tuple(params)))

    def _assert_sensors_equal(self, get_sensor_method, sensor_tuples):
        for sensorname, sensortype, expected in sensor_tuples:
            if sensortype == float:
                self.assertAlmostEqual(sensortype(get_sensor_method(sensorname)), expected)
            else:
                self.assertEqual(sensortype(get_sensor_method(sensorname)), expected)


def device_wrapper(device):
    outgoing_informs = []

    def inform(sock, msg):
        outgoing_informs.append(msg)

    def mass_inform(msg):
        outgoing_informs.append(msg)

    def informs():
        return outgoing_informs

    def clear_informs():
        del outgoing_informs[:]

    device.inform = inform
    device.mass_inform =mass_inform
    device.informs = informs
    device.clear_informs = clear_informs

    return device
