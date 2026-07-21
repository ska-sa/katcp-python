# -*- coding: utf-8 -*-
"""katcp Message parse/encode/copy/init micro-benchmark vs payload size.

Benchmarks the katcp checkout resolved by _repo.katcp_repo() (KATCP_REPO
env var, else the repo containing this bench/ directory).

Usage:
    python bench_katcp.py
    python bench_katcp.py --fast
"""
from __future__ import print_function
import sys
import timeit

from _repo import katcp_repo

sys.path.insert(0, katcp_repo())

if '--fast' in sys.argv:
    import fastkatcp  # noqa: F401  (optional monkeypatch, if on PYTHONPATH)
    print("[fastkatcp patch ACTIVE]")

from katcp.core import Message, MessageParser  # noqa: E402

PARSER = MessageParser()

CHANNEL_COUNTS = [1, 256, 1024, 4096, 32768]


def make_gain_value(i):
    """One complex gain as wire bytes, varied slightly per channel."""
    real = 0.9 + (i % 7) * 0.01
    imag = 0.05 + (i % 5) * 0.01
    return ("%.4f+%.4fj" % (real, imag)).encode('ascii')


def make_gain_line(nchans, mid=123):
    """Wire-format ?gain request: stream, input, then one complex gain per channel."""
    vals = [make_gain_value(i) for i in range(nchans)]
    args = [b"wide.antenna-channelised-voltage", b"m001h"] + vals
    line = b"?wide-gain[%d] " % mid + b" ".join(args)
    return line, args


def bench(fn, number=None):
    """Return best-of-3 seconds per call."""
    if number is None:
        # autoscale: aim for ~0.2s per repeat
        t = timeit.timeit(fn, number=1)
        number = max(1, int(0.2 / max(t, 1e-9)))
    best = min(timeit.repeat(fn, number=number, repeat=3))
    return best / number


def main():
    print("Python %s  repo %s" % (sys.version.split()[0], katcp_repo()))
    hdr = ("nchans", "line_kB", "parse_ms", "encode_ms", "init_ms", "copy_ms")
    print("%8s %8s %10s %10s %10s %10s" % hdr)
    for n in CHANNEL_COUNTS:
        line, args = make_gain_line(n)
        msg = PARSER.parse(line)

        parse_s = bench(lambda line=line: PARSER.parse(line))
        encode_s = bench(lambda msg=msg: msg.__bytes__())
        init_s = bench(lambda msg=msg: Message(Message.REQUEST, "wide-gain",
                                               msg.arguments, b"123"))
        copy_s = bench(lambda msg=msg: msg.copy())

        print("%8d %8.1f %10.4f %10.4f %10.4f %10.4f" % (
            n, len(line) / 1024.0,
            parse_s * 1e3, encode_s * 1e3, init_s * 1e3, copy_s * 1e3))

    # Baseline small message comparison
    small = b"!wide-gain[123] ok"
    small_s = bench(lambda: PARSER.parse(small))
    print("\nsmall reply '!wide-gain[123] ok' parse: %.1f us" % (small_s * 1e6))


if __name__ == '__main__':
    main()
