# -*- coding: utf-8 -*-
"""MT-7273 end-to-end proxy benchmark script.

Chain: test client -> mini-proxy -> device server

Simulates KATCP proxied request flow (copy -> rename -> callback -> reply)
with immediate device responses to measure proxy stack overhead.

Usage:
    python e2e_proxy_bench.py [-n N] [--chans C1,C2,...]
"""
from __future__ import print_function
import argparse
import socket
import sys
import time

from _repo import katcp_repo
sys.path.insert(0, katcp_repo())

parser = argparse.ArgumentParser()
parser.add_argument('-n', type=int, default=200, help='messages per run')
parser.add_argument('--chans', default='1,4096')
cli_args = parser.parse_args()

from katcp import AsyncReply, CallbackClient, DeviceServer  # noqa: E402


class Device(DeviceServer):
    VERSION_INFO = ("mt7273-device", 0, 1)
    BUILD_INFO = ("mt7273-device", 0, 1, "")

    def setup_sensors(self):
        pass

    def request_wide_gain(self, req, msg):
        """Accept gains and reply ok immediately."""
        return req.make_reply("ok")


class MiniProxy(DeviceServer):
    """Proxy handler mimicking katcore DeviceHandler request flow."""

    VERSION_INFO = ("mt7273-proxy", 0, 1)
    BUILD_INFO = ("mt7273-proxy", 0, 1, "")

    def __init__(self, device_addr, *a, **kw):
        super(MiniProxy, self).__init__(*a, **kw)
        self._dev_client = CallbackClient(device_addr[0], device_addr[1])

    def setup_sensors(self):
        pass

    def start(self, *a, **kw):
        self._dev_client.start()
        self._dev_client.wait_protocol(timeout=5)
        super(MiniProxy, self).start(*a, **kw)

    def stop(self, *a, **kw):
        super(MiniProxy, self).stop(*a, **kw)
        self._dev_client.stop()

    def request_wide_gain(self, req, msg):
        devmsg = msg.copy()
        devmsg.name = "wide-gain"

        def reply_cb(devreply, orig_msg, orig_req):
            m = devreply.copy()
            m.name = orig_msg.name
            orig_req.reply_with_message(m)

        self._dev_client.callback_request(
            devmsg,
            reply_cb=lambda r: reply_cb(r, msg, req),
            timeout=30,
        )
        raise AsyncReply()


def make_gain_value(i):
    """One complex gain as wire bytes, varied slightly per channel."""
    real = 0.9 + (i % 7) * 0.01
    imag = 0.05 + (i % 5) * 0.01
    return ("%.4f+%.4fj" % (real, imag)).encode('ascii')


def make_line(nchans, mid):
    vals = [make_gain_value(i) for i in range(nchans)]
    return (b"?wide-gain[%d] wide.antenna-channelised-voltage m001h " % mid
            + b" ".join(vals) + b"\n")


class RawClient(object):
    def __init__(self, addr):
        self.sock = socket.create_connection(addr)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""

    def read_reply(self):
        """Read lines until a !wide-gain reply arrives."""
        while True:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                line, self.buf = self.buf[:nl], self.buf[nl + 1:]
                if line.startswith(b"!wide-gain"):
                    return line
                continue
            data = self.sock.recv(65536)
            if not data:
                raise RuntimeError("connection closed")
            self.buf += data

    def close(self):
        self.sock.close()


def run_serial(addr, nchans, n):
    c = RawClient(addr)
    t0 = time.time()
    for i in range(n):
        c.sock.sendall(make_line(nchans, i + 1))
        c.read_reply()
    dt = time.time() - t0
    c.close()
    return dt


def run_pipelined(addr, nchans, n):
    c = RawClient(addr)
    lines = [make_line(nchans, i + 1) for i in range(n)]
    t0 = time.time()
    c.sock.sendall(b"".join(lines))
    for _ in range(n):
        c.read_reply()
    dt = time.time() - t0
    c.close()
    return dt


def main():
    n = cli_args.n
    chan_list = [int(x) for x in cli_args.chans.split(',')]

    device = Device("127.0.0.1", 0)
    device.start(timeout=5)
    dev_addr = device.bind_address

    proxy = MiniProxy(dev_addr, "127.0.0.1", 0)
    proxy.start(timeout=5)
    proxy_addr = proxy.bind_address
    time.sleep(0.5)

    print("python %s  n=%d" % (sys.version.split()[0], n))
    print("%8s %10s %14s %14s %16s" % (
        "nchans", "mode", "total_s", "per_msg_ms", "msgs_per_s"))
    try:
        for nchans in chan_list:
            # Warmup run
            run_serial(proxy_addr, nchans, 5)
            dt = run_serial(proxy_addr, nchans, n)
            print("%8d %10s %14.2f %14.2f %16.1f" % (
                nchans, "serial", dt, dt / n * 1e3, n / dt))
            dt = run_pipelined(proxy_addr, nchans, n)
            print("%8d %10s %14.2f %14.2f %16.1f" % (
                nchans, "pipelined", dt, dt / n * 1e3, n / dt))
    finally:
        proxy.stop()
        device.stop()


if __name__ == '__main__':
    main()
