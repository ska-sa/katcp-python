from tornado.util import ObjectDict
from tornado.concurrent import Future as tornado_Future
from katcp import Sensor

from katcp.testutils import DeviceTestServer
from katcp.client import *

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.DEBUG
)

def cb(*args):
    print args

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
