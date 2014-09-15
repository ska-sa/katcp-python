from tornado.util import ObjectDict
from tornado.concurrent import Future as tornado_Future
from katcp import Sensor

from katcp.testutils import DeviceTestServer
from katcp.client import *

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.DEBUG
)

def waitable_flag(getter):
    waiting_futures = set()
    var_name = '_' + getter.func_name

    def getter(self):
        return getattr(self, var_name)

    def setter(self, val):
        setattr(self, var_name, val)
        if val:
            notify(val)

    def notify(val):
        while waiting_futures:
            f = waiting_futures.pop()
            f.set_result(val)

    return property(getter, setter, doc=getter.__doc__)


class C(object):
    def __init__(self):
        self.x = AsyncFuture()

c = C()

import IPython ; IPython.embed()

# try:
#     d = DeviceTestServer('', 0)
#     d.start(timeout=1)

#     c = DeviceClient('localhost', d.bind_address[1])
#     c.start(timeout=1)

#     rm = Message.request

#     #time.sleep(10000000)
#     import IPython ; IPython.embed()
# finally:
#     d.stop()
#     c.stop()
