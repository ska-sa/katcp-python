import threading
import logging

import tornado

from katcp import resource_client

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.DEBUG
)


ioloop = tornado.ioloop.IOLoop.current()

def run_ipy():
    try:
        import IPython
        IPython.embed()
    finally:
        ioloop.stop()

t = threading.Thread(target=run_ipy)
t.start()

ioloop.start()
