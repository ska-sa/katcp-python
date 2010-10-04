
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
