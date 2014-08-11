from tornado_io import *

from tornado.util import ObjectDict

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(funcName)s(%(filename)s:%(lineno)d)%(message)s",
    level=logging.INFO
)

def handle_message(client_conn, msg):
    logging.info('received {0} from client {1}'.format(str(msg), client_conn))

def on_client_disconnect(client_conn, reason, connection_valid):
    logging.info('client {0} disconnect: {1}, valid: {2}'.format(
        client_conn, reason, connection_valid))
    client_conn.inform(Message.inform('disconnect', reason))

def on_client_connect(client_conn):
    logging.info('client {0} connected'.format(client_conn))

def send_msg(client_conn, msg):
    KS._ioloop.add_callback(client_conn._send_message(msg))

device = ObjectDict(
    handle_message=handle_message,
    on_client_connect=on_client_connect,
    on_client_disconnect=on_client_disconnect)

KS = KATCPServerTornado(device, '', 5000)
try:
    KS.start()
    time.sleep(1000000)
    # import IPython ; IPython.embed()
finally:
    KS.stop()
