from tornado_io import *

from tornado.util import ObjectDict
from katcp import Sensor


logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.INFO
)

class D(DeviceServer):
    def setup_sensors(self):
        self.add_sensor(Sensor.boolean('asens'))

    def request_stupid(self, req, msg):
        "blah"
        req.inform('Doing stupid sleep')
        time.sleep(10)
        req.inform('Stupid sleep done')
        return Message.reply('stupid', 'ok')

    def start(self, timeout=None, daemon=None, excepthook=None):
        """Start the server in a new thread.

        Parameters
        ----------
        timeout : float in seconds
            Time to wait for server thread to start.
        daemon : boolean
            If not None, the thread's setDaemon method is called with this
            parameter before the thread is started.
        excepthook : function
            Function to call if the client throws an exception. Signature
            is as for sys.excepthook.
        """
        super(D, self).start(timeout, daemon, excepthook)
        s = self.get_sensor('asens')
        def updat():
            s.set_value(not s.value())
            self.ioloop.call_later(0.9, updat)
        self.ioloop.add_callback(updat)

restart_queue = Queue.Queue()
DS = D('', 5000)
DS.set_restart_queue(restart_queue)
try:
    DS.start()
    # import IPython ; IPython.embed()
    while True:
        try:
            restart_device = restart_queue.get(timeout=100000)
        except Queue.Empty:
            continue
        logging.info('Stopping')
        restart_device.stop()
        restart_device.join()
        logging.info("Restarting ...")
        restart_device.start()
        logging.info("Started.")

except Exception:
    logging.exception("error")
finally:
    DS.stop()
