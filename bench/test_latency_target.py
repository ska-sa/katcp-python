#!/usr/bin/env python3
"""Gain-set latency/throughput against a CBF+ proxy or a CBF product directly.

Derived from Bruce Merry's original aiokatcp latency script, corrected for use
against a live product and parameterised so the identical client code, message
construction and timing can be pointed at either end of the chain:

  proxy    ?wide-gain on the CBF+ proxy          (katcp-python)
  product  ?gain      on the product controller  (aiokatcp)

The product's ?gain takes the same arguments as the proxy's ?wide-gain, so only
the request name and the address differ. Running both and subtracting isolates
what the proxy itself costs.

Corrections to the original, all of which matter against a live product:
  - real input labels, discovered from the proxy's input-labels sensor rather
    than assumed (the original's inp0..inpN are rejected by a real product);
  - gains formatted with an explicit '+' before non-negative imaginary parts,
    without which roughly half of them are not valid complex values;
  - reply status is checked, so failed requests cannot be timed as if they had
    succeeded;
  - existing gains are saved and restored, leaving product state as found.

Requires aiokatcp, numpy and uvloop (all present in the katsdpcontroller venv).

Usage:
    # via the proxy (input labels discovered automatically)
    python test_latency_target.py --port 5056

    # direct to the product controller; labels must be given explicitly
    python test_latency_target.py --host <cmc-host> --port <product-port> \
        --request gain --inputs m011h,m011v,m022h,m022v

    # per-request latency instead of pipelined throughput
    python test_latency_target.py --port 5056 --serial
"""

import argparse
import asyncio
import logging
import signal

import aiokatcp
import numpy as np
import uvloop

DEFAULT_STREAM = "wide-antenna-channelised-voltage"


async def read_reply(reader, name):
    """Read lines until the reply to `name` arrives, discarding informs."""
    want = b"!" + name.encode()
    while True:
        line = await reader.readuntil()
        if line.startswith(want):
            return line


async def discover_inputs(reader, writer):
    """Read the input labels from the proxy's input-labels sensor.

    Only works against the proxy; the product controller does not carry this
    sensor, so --inputs must be given explicitly for that target.
    """
    writer.write(bytes(aiokatcp.Message.request("sensor-value", "input-labels")))
    labels = None
    while True:
        line = await reader.readuntil()
        if line.startswith(b"#sensor-value"):
            labels = line.strip().split(b" ")[-1]
        elif line.startswith(b"!sensor-value"):
            break
    if not labels or b"," not in labels:
        raise RuntimeError(
            "could not discover input labels (got %r); pass --inputs explicitly, "
            "which is required when targeting the product controller" % labels)
    return [label.decode() for label in labels.split(b",")]


async def query_gains(reader, writer, request, stream, label):
    """Return the current gains for one input, as wire-format byte strings."""
    writer.write(bytes(aiokatcp.Message.request(request, stream, label)))
    line = await read_reply(reader, request)
    parts = line.strip().split(b" ")
    if parts[1] != b"ok":
        raise RuntimeError("gain query for %s failed: %r" % (label, line[:200]))
    return parts[2:]


async def set_gains(reader, writer, request, stream, label, vals):
    """Write gains back for one input."""
    writer.write(bytes(aiokatcp.Message.request(request, stream, label, *vals)))
    line = await read_reply(reader, request)
    if b" ok" not in line:
        raise RuntimeError("gain restore for %s failed: %r" % (label, line[:200]))


def make_gains(n_inputs, channels):
    """Random complex64 gains, formatted as the wire expects."""
    shape = (n_inputs, channels)
    rng = np.random.default_rng(seed=2)
    mag = rng.uniform(0.5, 2.0, size=shape)
    phase = rng.uniform(-np.pi, np.pi, size=shape)
    gains = (mag * np.exp(1j * phase)).astype(np.complex64)
    return [[f"{g.real}{g.imag:+}j".encode() for g in row] for row in gains]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, required=True,
                    help="katcp port of the proxy, or of the product controller")
    ap.add_argument("--request", default="wide-gain",
                    help="wide-gain for the proxy, gain for the product")
    ap.add_argument("--stream", default=DEFAULT_STREAM)
    ap.add_argument("--inputs", default=None,
                    help="comma-separated input labels; discovered from the "
                         "proxy if omitted, required for the product")
    ap.add_argument("--passes", type=int, default=110,
                    help="passes over all inputs; 110 x 8 inputs = 880 messages")
    ap.add_argument("--channels", type=int, default=4096)
    ap.add_argument("--serial", action="store_true",
                    help="one request at a time (latency) instead of "
                         "pipelined bursts (throughput)")
    ap.add_argument("--tag", default="")
    opts = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    asyncio.get_event_loop().add_signal_handler(signal.SIGINT, asyncio.current_task().cancel)

    loop = asyncio.get_running_loop()
    reader, writer = await asyncio.open_connection(opts.host, opts.port, limit=2 ** 22)

    labels = opts.inputs.split(",") if opts.inputs else await discover_inputs(reader, writer)
    gains_text = make_gains(len(labels), opts.channels)
    messages = [
        bytes(aiokatcp.Message.request(opts.request, opts.stream, label, *row))
        for row, label in zip(gains_text, labels)
    ]
    msg_bytes = sum(len(m) for m in messages)

    saved = {}
    for label in labels:
        saved[label] = await query_gains(reader, writer, opts.request, opts.stream, label)

    ok = fail = 0
    first_fail = None
    latencies = []

    async def drain_one():
        nonlocal ok, fail, first_fail
        while True:
            line = await reader.readuntil()
            if line[:1] == b"!":
                if b" ok" in line:
                    ok += 1
                else:
                    fail += 1
                    if first_fail is None:
                        first_fail = line[:200]
                return

    async def once():
        if opts.serial:
            for message in messages:
                t0 = loop.time()
                writer.write(message)
                await drain_one()
                latencies.append((loop.time() - t0) * 1e3)
        else:
            for message in messages:
                writer.write(message)
            for _ in messages:
                await drain_one()

    try:
        await once()  # warmup pass, not timed
        ok = fail = 0
        latencies.clear()
        start = loop.time()
        for _ in range(opts.passes):
            await once()
        elapsed = loop.time() - start
    finally:
        for label, vals in saved.items():
            await set_gains(reader, writer, opts.request, opts.stream, label, vals)

    n = opts.passes * len(messages)
    size = opts.passes * msg_bytes
    mode = "serial" if opts.serial else "pipelined"
    print("%-10s %-10s %7.2f s  %8.2f ms/msg  %7.1f msg/s  %6.1f MB/s  ok=%d fail=%d%s"
          % (opts.tag, mode, elapsed, elapsed / n * 1e3, n / elapsed,
             size / elapsed / 1e6, ok, fail,
             "  first_fail=%r" % first_fail if first_fail else ""))
    if latencies:
        latencies.sort()

        def pct(q):
            return latencies[min(len(latencies) - 1, int(len(latencies) * q))]

        print("%-10s per-request latency: p50 %.2f ms  p95 %.2f ms  max %.2f ms"
              % ("", pct(0.50), pct(0.95), latencies[-1]))
    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        asyncio.run(main())
    except asyncio.CancelledError:
        pass
