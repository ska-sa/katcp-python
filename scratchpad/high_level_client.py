import tornado

from tornado.ioloop import IOLoop
from katcp import resource_client

ioloop = IOLoop.current()

client = resource_client.KATCPClientResource(dict(
    name='demo-client',
    address=('localhost', 5000),
    controlled=True))

@tornado.gen.coroutine
def demo():
    # Wait until the client has finished inspecting the device
    yield client.until_synced()
    help_response = yield client.req.help()
    print "device help:\n ", help_response
    add_response = yield client.req.add(3, 6)
    print "3 + 6 response:\n", add_response
    # By not yielding we are not waiting for the response
    pick_response_future = client.req.pick_fruit()
    # Instead we wait for the fruit.result sensor status to change to
    # nominal. Before we can wait on a sensor, a strategy must be set:
    client.sensor.fruit_result.set_strategy('event')
    # If the condition does not occur within the timeout (default 5s), we will
    # get a TimeoutException
    yield client.sensor.fruit_result.wait(
        lambda reading: reading.status == 'nominal')
    fruit = yield client.sensor.fruit_result.get_value()
    print 'Fruit picked: ', fruit
    # And see how the ?pick-fruit request responded by yielding on its future
    pick_response = yield pick_response_future
    print 'pick response: \n', pick_response

    ioloop.stop()

# Note, katcp.resource_client.ThreadSafeKATCPClientResourceWrapper can be used to
# turn the client into a 'blocking' client for use in e.g. ipython. It will turn
# all functions that return tornado futures into blocking calls, and will bounce
# all method calls through the ioloop. In this case the ioloop must be started
# in a separate thread. katcp.ioloop_manager.IOLoopManager can be used to manage
# the ioloop thread.

ioloop.add_callback(client.start)
ioloop.add_callback(demo)
ioloop.start()
