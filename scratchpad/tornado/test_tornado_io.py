from tornado_io import *

from tornado.util import ObjectDict
from katcp import Sensor

import mock

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.INFO
)

def handle_message(client_conn, msg):
    logging.info('received {0} from client {1}'.format(str(msg), client_conn.address))

def on_client_disconnect(client_conn, reason, connection_valid):
    logging.info('client {0} disconnect: {1}, valid: {2}'.format(
        client_conn.address, reason, connection_valid))
    client_conn.inform(Message.inform('disconnect', reason))

def on_client_connect(client_conn):
    logging.info('client {0} connected'.format(client_conn.address))

# device = ObjectDict(
#     handle_message=handle_message,
#     on_client_connect=on_client_connect,
#     on_client_disconnect=on_client_disconnect)

# KS = KATCPServerTornado(device, '', 5000)

class D(DeviceServer):
    def setup_sensors(self):
        self.add_sensor(Sensor.boolean('asens'))

with mock.patch('katcp.server.KATCPServer') as mK:
    mK.side_effect = KATCPServerTornado
    DS = D('', 5000)
try:
    DS.start()
    time.sleep(1000000)
    # import IPython ; IPython.embed()
finally:
    DS.stop()
