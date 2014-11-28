import logging
logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.DEBUG
)

import time
import threading
import signal

import tornado
import IPython

from katcp.testutils import DeviceTestServer

from katcp import resource_client, inspecting_client


log = logging.getLogger(__name__)

ioloop = tornado.ioloop.IOLoop.current()

d = DeviceTestServer('', 0)
d.set_concurrency_options(False, False)
d.set_ioloop(ioloop)
ioloop.add_callback(d.start)

def setup_resource_client():
    global rc
    print d.bind_address
    rc = resource_client.KATCPClientResource(dict(
        name='thething',
        address=d.bind_address,
        controlled=True
    ))
    rc.start()

def printy(*args):
    print args

@tornado.gen.coroutine
def setup_inspecting_client():
    global ic
    try:
        print d.bind_address
        host, port = d.bind_address
        ic = inspecting_client.InspectingClientAsync(host, port, ioloop=ioloop)
        ic.set_state_callback(printy)
        yield ic.connect()
    except Exception:
        log.exception('whups')

#ioloop.add_callback(setup_inspecting_client)
ioloop.add_callback(setup_resource_client)

stop = threading.Event()

@tornado.gen.coroutine
def doreq(req, *args, **kwargs):
    print 'hi'
    try:
        rep = yield req(*args, **kwargs)
        print rep
    except Exception:
        print 'logging'
        log.exception('oops')
    finally:
        print 'blah'

def run_ipy():
    try:
        iotw = resource_client.IOLoopThreadWrapper(ioloop)
        time.sleep(0.24)
        s = rc.sensor.an_int
        ws = resource_client.ThreadsafeKATCPSensorWrapper(s, iotw)
        IPython.embed()
        # stop.wait(10000)
    finally:
        ioloop.stop()

t = threading.Thread(target=run_ipy)
t.start()

ioloop.set_blocking_log_threshold(0.1)
def ignore_signal(sig, frame):
    pass
# Disable sigint, since it stops the ioloop but not ipython shell ;)
signal.signal(signal.SIGINT, ignore_signal)
try:
    ioloop.start()
except KeyboardInterrupt:
    print 'Keyboard interrupt'
    stop.set()

