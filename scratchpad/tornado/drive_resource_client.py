import time
import threading
import logging

import tornado
import IPython

from katcp.testutils import DeviceTestServer

from katcp import resource_client


logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.DEBUG
)

ioloop = tornado.ioloop.IOLoop.current()

d = DeviceTestServer('', 0)
d.set_concurrency_options(False, False)
d.set_ioloop(ioloop)
ioloop.add_callback(d.start)

def setup_resource_client():
    global rc
    print d.bind_address
    rc = resource_client.KATCPResourceClient(dict(
        name='thething',
        address=d.bind_address,
        controlled=True
    ))
    rc.start()

ioloop.add_callback(setup_resource_client)

stop = threading.Event()

def run_ipy():
    try:
        IPython.embed()
        # stop.wait(10000)
    finally:
        ioloop.stop()

t = threading.Thread(target=run_ipy)
t.start()

try:
    ioloop.start()
except KeyboardInterrupt:
    stop.set()
