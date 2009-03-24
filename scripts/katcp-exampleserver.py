#!/usr/bin/env python

"""KATCP server example.

   copyright (c) 2008 SKA/KAT. All Rights Reserved.
   @author Robert Crida <robert.crida@ska.ac.za>
   @date 2008-10-10
   """

import logging
import sys
from optparse import OptionParser
import katcp

logging.basicConfig(level=logging.INFO,
                    stream=sys.stderr,
                    format="%(asctime)s - %(name)s - %(filename)s:%(lineno)s - %(levelname)s - %(message)s")

class DeviceExampleServer(katcp.DeviceServer):
    #pylint: disable-msg=R0904
    def setup_sensors(self):
        pass

    def schedule_restart(self):
        #pylint: disable-msg=W0201
        self.restarted = True

if __name__ == "__main__":

    usage = "usage: %prog [options]"
    parser = OptionParser(usage=usage)
    parser.add_option('-a', '--host', dest='host', type="string", default="", metavar='HOST',
                      help='listen to HOST (default="" - all hosts)')
    parser.add_option('-p', '--port', dest='port', type=long, default=1235, metavar='N',
                      help='attach to port N (default=1235)')
    (opts, args) = parser.parse_args()

    print "Server listening on port %d, Ctrl-C to terminate server" % opts.port
    server = DeviceExampleServer(opts.host, opts.port)
    server.run()
