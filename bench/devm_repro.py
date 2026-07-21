# -*- coding: utf-8 -*-
"""MT-7273 devbox CBF+ proxy reproduction script.

Queries existing per-input gains and re-applies those same values, keeping state
neutral while exercising large (~60 KiB) message latency against the proxy.

Stdlib only (raw sockets) to ensure client-side overhead is negligible.

The proxy's katcp port is required. Find it from the proxy's command line,
e.g.:  ps aux | grep data-cbfplus-proxy   ->  ... --addr :5056

Usage:
    python devm_repro.py --port <proxy-port>              # 880 msgs, serial
    python devm_repro.py --port <proxy-port> --smoke      # quick 3-pass run
    python devm_repro.py --port <proxy-port> --pipelined  # all msgs in flight
"""
from __future__ import print_function
import argparse
import socket
import time

STREAM_CANDIDATES = [
    b"wide-antenna-channelised-voltage",
    b"wide.antenna-channelised-voltage",
    b"antenna-channelised-voltage",
]


class Katcp(object):
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=30)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
        time.sleep(0.5)
        self.drain()
        self.mid = 0

    def drain(self):
        self.sock.setblocking(False)
        try:
            while True:
                d = self.sock.recv(65536)
                if not d:
                    break
                self.buf += d
        except Exception:
            pass
        self.sock.setblocking(True)
        self.sock.settimeout(30)
        self.buf = b""

    def readline(self):
        while True:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                line = self.buf[:nl]
                self.buf = self.buf[nl + 1:]
                return line.replace(b"\r", b"")
            d = self.sock.recv(65536)
            if not d:
                raise RuntimeError("connection closed")
            self.buf += d

    def send(self, name, args):
        """Send ?name[mid] args...; return the mid used."""
        self.mid += 1
        line = b"?%s[%d]" % (name, self.mid)
        if args:
            line += b" " + b" ".join(args)
        self.sock.sendall(line + b"\n")
        return self.mid

    def wait_reply(self, name, mid):
        """Collect informs until the !name[mid] reply; return (reply, informs)."""
        informs = []
        want_r = b"!%s[%d]" % (name, mid)
        want_i = b"#%s[%d]" % (name, mid)
        while True:
            line = self.readline()
            if line.startswith(want_r):
                return line, informs
            if line.startswith(want_i):
                informs.append(line)

    def request(self, name, args=()):
        mid = self.send(name, list(args))
        return self.wait_reply(name, mid)


def detect_stream(kc, an_input):
    """Find the stream name the proxy's wide-gain request accepts."""
    for stream in STREAM_CANDIDATES:
        reply, _ = kc.request(b"wide-gain", [stream, an_input])
        if b" ok" in reply or reply.split(b" ")[1:2] == [b"ok"]:
            return stream, reply
    raise RuntimeError("no stream candidate accepted; last reply: %r" % reply)


def query_gains(kc, stream, inp, nchans):
    """Query current gains for an input; expand a uniform single value."""
    reply, _ = kc.request(b"wide-gain", [stream, inp])
    parts = reply.split(b" ")
    assert parts[1] == b"ok", "gain query failed: %r" % reply
    vals = parts[2:]
    if len(vals) == 1:
        vals = vals * nchans
    assert len(vals) == nchans, "expected %d gains, got %d" % (nchans, len(vals))
    return vals


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='localhost')
    ap.add_argument('--port', type=int, required=True,
                    help='katcp port of the CBF+ proxy under test')
    ap.add_argument('--passes', type=int, default=110)
    ap.add_argument('--nchans', type=int, default=4096)
    ap.add_argument('--pipelined', action='store_true',
                    help='send everything, then collect all replies')
    ap.add_argument('--smoke', action='store_true', help='3 passes only')
    opts = ap.parse_args()
    if opts.smoke:
        opts.passes = 3

    kc = Katcp(opts.host, opts.port)

    reply, informs = kc.request(b"sensor-value", [b"input-labels"])
    assert b" ok" in reply, "sensor-value input-labels failed: %r" % reply
    inputs = informs[0].split(b" ")[-1].split(b",")
    print("inputs: %s" % b",".join(inputs).decode())

    stream, _ = detect_stream(kc, inputs[0])
    print("stream accepted by wide-gain: %s" % stream.decode())

    print("Querying current gains...")
    t0 = time.time()
    gains = {}
    for inp in inputs:
        gains[inp] = query_gains(kc, stream, inp, opts.nchans)
    print("  %d queries took %.2fs" % (len(inputs), time.time() - t0))

    total = opts.passes * len(inputs)
    line_len = len(b" ".join([stream, inputs[0]] + gains[inputs[0]]))
    print("Running %d passes x %d inputs = %d set-gain messages (~%.0f KiB each), %s..."
          % (opts.passes, len(inputs), total, line_len / 1024.0,
             "pipelined" if opts.pipelined else "serial"))

    lat = []
    t0 = time.time()
    if opts.pipelined:
        mids = []
        for p in range(opts.passes):
            for inp in inputs:
                mids.append(kc.send(b"wide-gain", [stream, inp] + gains[inp]))
        for mid in mids:
            reply, _ = kc.wait_reply(b"wide-gain", mid)
            assert b" ok" in reply, reply[:200]
    else:
        for p in range(opts.passes):
            for inp in inputs:
                t1 = time.time()
                mid = kc.send(b"wide-gain", [stream, inp] + gains[inp])
                reply, _ = kc.wait_reply(b"wide-gain", mid)
                lat.append(time.time() - t1)
                assert b" ok" in reply, reply[:200]
    dt = time.time() - t0

    print("Total: %.2fs for %d messages -> %.1f ms/msg, %.1f msg/s"
          % (dt, total, dt / total * 1e3, total / dt))
    if lat:
        lat.sort()
        print("Per-message latency: p50 %.1f ms  p95 %.1f ms  max %.1f ms"
              % (pct(lat, 0.50) * 1e3, pct(lat, 0.95) * 1e3, lat[-1] * 1e3))


if __name__ == '__main__':
    main()
