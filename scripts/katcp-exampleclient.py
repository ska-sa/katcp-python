#!/usr/bin/env python
# Copyright 2008 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

"""KATCP command line client example.

   @author Simon Cross <simon.cross@ska.ac.za>
   @date 2008-10-29
"""
from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import logging
import sys
import traceback

from builtins import input
from future.utils import PY3
from optparse import OptionParser

import katcp


logging.basicConfig(level=logging.INFO,
                    stream=sys.stderr,
                    format="%(asctime)s - %(name)s - %(filename)s:%(lineno)s - %(levelname)s - %(message)s")


class DeviceExampleClient(katcp.DeviceClient):

    def handle_reply(self, msg):
        """Called when a reply message arrives."""
        print(msg)

    def handle_inform(self, msg):
        """Called when an inform message arrives."""
        print(msg)


if __name__ == "__main__":

    usage = "usage: %prog [options]"
    parser = OptionParser(usage=usage)
    parser.add_option('-a', '--host', dest='host', type="string", default="", metavar='HOST',
                      help='attach to server HOST (default="" - localhost)')
    parser.add_option('-p', '--port', dest='port', type=int, default=1235, metavar='N',
                      help='attach to server port N (default=1235)')
    (opts, args) = parser.parse_args()

    katcp_parser = katcp.MessageParser()

    print("Client connecting to port %s:%d, Ctrl-C to terminate." % (opts.host, opts.port))
    client = DeviceExampleClient(opts.host, opts.port)

    client.start()
    try:
        while True:
            s = input("> ")
            try:
                if PY3:
                    s = s.encode('utf-8')
                msg = katcp_parser.parse(s)
                client.ioloop.add_callback(client.send_message, msg)
            except Exception:
                e_type, e_value, trace = sys.exc_info()
                reason = "\n".join(traceback.format_exception(
                    e_type, e_value, trace, 20
                ))
                print(reason)
    finally:
        client.stop()
        client.join()
