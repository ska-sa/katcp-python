# Copyright 2014 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

from __future__ import absolute_import, division, print_function

from tornado.concurrent import Future as tornado_Future
from tornado.util import ObjectDict

from katcp import Sensor
from katcp.client import *
from katcp.testutils import DeviceTestServer

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.DEBUG
)

def cb(*args):
    print(args)

try:
    d = DeviceTestServer('', 0)
    d.start(timeout=1)
    logging.info('Server started at port {0}'.format(d.bind_address[1]))
    c = AsyncClient('127.0.0.1', d.bind_address[1])
    c.enable_thread_safety()
    c.start(timeout=1)

#     rm = Message.request

#     #time.sleep(10000000)
    import IPython ; IPython.embed()
finally:
    d.stop()
    c.stop()
