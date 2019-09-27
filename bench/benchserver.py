# Copyright 2010 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

from __future__ import absolute_import, division, print_function

import sys

from katcp import DeviceServer, Message, Sensor
from util import standard_parser


class BenchmarkServer(DeviceServer):
    # an ugly hack
    def _bind(self, *args):
        res = DeviceServer._bind(self, *args)
        print("RUNNING")
        sys.stdout.flush()
        return res

    def setup_sensors(self):
        pass

    def request_add_sensor(self, sock, msg):
        """ add a sensor
        """
        self.add_sensor(Sensor(int, 'int_sensor%d' % len(self._sensors),
                               'descr', 'unit', params=[-10, 10]))
        return Message.reply('add-sensor', 'ok')

def main():
    parser = standard_parser(1236)
    options, args = parser.parse_args()
    server = BenchmarkServer('localhost', options.port)
    server.run()

if __name__ == '__main__':
    main()
