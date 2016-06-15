import signal
import threading
import time
import random

import tornado


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
        self._add_result.set_value(0, Sensor.UNREACHABLE)

        self._time_result = Sensor.timestamp("time.result",
            "Last ?time result.", "")
        self._time_result.set_value(0, Sensor.INACTIVE)

        self._eval_result = Sensor.string("eval.result",
            "Last ?eval result.", "")
        self._eval_result.set_value('', Sensor.UNKNOWN)

        self._fruit_result = Sensor.discrete("fruit.result",
            "Last ?pick-fruit result.", "", self.FRUIT)
        self._fruit_result.set_value('apple', Sensor.ERROR)

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

    @request(Str())
    @return_reply()
    def request_set_sensor_inactive(self, req, sensor_name):
        """Set sensor status to inactive"""
        sensor = self.get_sensor(sensor_name)
        ts, status, value = sensor.read()
        sensor.set_value(value, sensor.INACTIVE, ts)
        return('ok',)

    @request(Str())
    @return_reply()
    def request_set_sensor_unreachable(self, req, sensor_name):
        """Set sensor status to unreachable"""
        sensor = self.get_sensor(sensor_name)
        ts, status, value = sensor.read()
        sensor.set_value(value, sensor.UNREACHABLE, ts)
        return('ok',)

    def request_raw_reverse(self, req, msg):
        """
        A raw request handler to demonstrate the calling convention if
        @request decoraters are not used. Reverses the message arguments.
        """
        # msg is a katcp.Message.request object
        reversed_args = msg.arguments[::-1]
        # req.make_reply() makes a katcp.Message.reply using the correct request
        # name and message ID
        return req.make_reply(*reversed_args)


@tornado.gen.coroutine
def on_shutdown(ioloop, server):
    print('Shutting down')
    yield server.stop()
    ioloop.stop()

if __name__ == "__main__":
    ioloop = tornado.ioloop.IOLoop.current()
    server = MyServer(server_host, server_port)
    server.set_concurrency_options(thread_safe=False, handler_thread=False)
    server.set_ioloop(ioloop)
    signal.signal(signal.SIGINT, lambda sig, frame: ioloop.add_callback_from_signal(
        on_shutdown, ioloop, server))
    ioloop.add_callback(server.start)
    ioloop.start()
