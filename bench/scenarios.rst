
Testing scenario 1
==================

We measure the raw throughput of sensor sampling (without waiting). This is
done by adding sensors and requesting sampling strategy until the server
falls behind with sampling. The measured number is a number of requests
per second averaged over time.

Note: as learned on #twisted there is a producer API which is designed for
high throughput data (there is no caching in between but instead it's designed
for writing when there is more data available). I don't think sensor-sampling
is a good example, but should be considered.

Testing scenario 2
==================

We measure the throughput of sensor-value requests (or something larger),
provided that a new request can only be issued once previous request is
completed. This is a latency measurment (how much time it'll take
for one request to complete). Measurements shall be taken with growing
number of participating clients within four to ten clients.

Testing scenario 3
==================

We measure the throughput of requests as a function of message size using
an echo request that returns its arguments, e.g.::

  ?echo hello world
  !echo ok hello world

The purpose of this measurement is two fold:

  * Firstly, to determine the optimal message size for transmission of
    large data sets. This might be useful when transferring large data
    blocks from the correlator for debugging or to pack multiple sensor
    values together if sensor sampling becomes a bottleneck.
    
  * Secondly, to determine whether the message parser has any
    undesirable performance characteristics for large messages. For
    example, (poorly constructed) regular expression matches may scale
    badly with message size.
