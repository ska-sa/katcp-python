#!/usr/bin/env python

"""KATCP server example.

   copyright (c) 2008 SKA/KAT. All Rights Reserved.
   @author Robert Crida <robert.crida@ska.ac.za>
   @date 2008-10-10
   """

import logging
import sys
import Queue
from optparse import OptionParser
import katcp
from katcp.kattypes import request, return_reply, Float, Int, Str


logging.basicConfig(level=logging.INFO,
                    stream=sys.stderr,
                    format="%(asctime)s - %(name)s - %(filename)s:%(lineno)s - %(levelname)s - %(message)s")


class DeviceExampleServer(katcp.DeviceServer):

    ## Interface version information.
    VERSION_INFO = ("example-server", 0, 1)

    ## Device server build / instance information.
    BUILD_INFO = ("my-example-server", 0, 1, "rc1")

    #pylint: disable-msg=R0904
    def setup_sensors(self):
        pass

    def request_echo(self, sock, msg):
        """Echo the arguments of the message sent."""
        return katcp.Message.reply(msg.name, "ok", *msg.arguments)

    @request(Str(), Int())
    @return_reply(Str())
    def request_repeat(self, sock, txt, n):
        """Repeat txt n times."""
        return ("ok", txt*n)

    @request(Float(), Float())
    @return_reply(Float())
    def request_add(self, sock, x, y):
        """Add x and y."""
        return ("ok", x+y)

    @request(Int(), Int())
    @return_reply(Int())
    def request_intdiv(self, sock, x, y):
        """Perform integer division of x and y."""
        return ("ok", x / y)


if __name__ == "__main__":

    usage = "usage: %prog [options]"
    parser = OptionParser(usage=usage)
    parser.add_option('-a', '--host', dest='host', type="string", default="", metavar='HOST',
                      help='listen to HOST (default="" - all hosts)')
    parser.add_option('-p', '--port', dest='port', type=long, default=1235, metavar='N',
                      help='attach to port N (default=1235)')
    (opts, args) = parser.parse_args()

    print "Server listening on port %d, Ctrl-C to terminate server" % opts.port
    restart_queue = Queue.Queue()
    server = DeviceExampleServer(opts.host, opts.port)
    server.set_restart_queue(restart_queue)

    server.start()
    print "Started."

    try:
        while True:
            try:
                device = restart_queue.get(timeout=0.5)
            except Queue.Empty:
                device = None
            if device is not None:
                print "Stopping ..."
                device.stop()
                device.join()
                print "Restarting ..."
                device.start()
                print "Started."
    except KeyboardInterrupt:
        print "Shutting down ..."
        server.stop()
        server.join()
