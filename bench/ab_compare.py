# -*- coding: utf-8 -*-
"""MT-7273 A/B runner: benchmark baseline ref vs working tree.

Creates a temporary git worktree for the baseline ref (default: master),
runs the benchmark against both trees, and prints a side-by-side comparison
table with speedup ratios.

Usage:
    python ab_compare.py               # micro-benchmark (parse/encode/copy)
    python ab_compare.py --e2e         # include end-to-end proxy benchmark
    python ab_compare.py --ref <sha>   # custom baseline ref
    python ab_compare.py --py <python> # python interpreter path
"""

from __future__ import print_function
import argparse
import os
import re
import subprocess
import sys
import tempfile

from _repo import katcp_repo

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = katcp_repo()
DEFAULT_PY = sys.executable

ROW_RE = re.compile(r'^\s*(\d+)\s')


def run_bench(py, script, katcp_path, extra=()):
    env = dict(os.environ, KATCP_REPO=katcp_path)
    out = subprocess.check_output([py, os.path.join(HERE, script)] + list(extra),
                                  env=env, stderr=subprocess.STDOUT)
    return out.decode('utf-8', 'replace')


def parse_micro(out):
    """-> {nchans: (parse_ms, encode_ms, init_ms, copy_ms)}"""
    rows = {}
    for ln in out.splitlines():
        m = ROW_RE.match(ln)
        parts = ln.split()
        if m and len(parts) == 6:
            rows[int(parts[0])] = tuple(float(x) for x in parts[2:])
    return rows


def parse_e2e(out):
    """-> {(nchans, mode): per_msg_ms}"""
    rows = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 5 and ROW_RE.match(ln) and parts[1] in ('serial', 'pipelined'):
            rows[(int(parts[0]), parts[1])] = float(parts[3])
    return rows


def ratio(a, b):
    return ('%.1fx' % (a / b)) if b else '-'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default='master', help='baseline git ref')
    ap.add_argument('--py', default=DEFAULT_PY)
    ap.add_argument('--e2e', action='store_true')
    ap.add_argument('--e2e-n', type=int, default=100)
    opts = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix='mt7273-ab-')
    baseline_tree = os.path.join(tmpdir, 'baseline')
    subprocess.check_call(['git', '-C', REPO, 'worktree', 'add', '--detach',
                           baseline_tree, opts.ref])
    try:
        head = subprocess.check_output(
            ['git', '-C', REPO, 'rev-parse', '--short', 'HEAD']).decode().strip()
        base = subprocess.check_output(
            ['git', '-C', REPO, 'rev-parse', '--short', opts.ref]).decode().strip()
        print('baseline: %s (%s)   fix: checkout HEAD (%s)\n'
              % (opts.ref, base, head))

        print('== micro-benchmark (ms) ==')
        a = parse_micro(run_bench(opts.py, 'bench_katcp.py', baseline_tree))
        b = parse_micro(run_bench(opts.py, 'bench_katcp.py', REPO))
        print('%8s | %22s | %22s | %22s' % ('nchans', 'parse (base/fix/x)',
                                            'encode (base/fix/x)', 'copy (base/fix/x)'))
        for n in sorted(set(a) & set(b)):
            pa, ea, _, ca = a[n]
            pb, eb, _, cb = b[n]
            print('%8d | %9.4f %7.4f %4s | %9.4f %7.4f %4s | %9.4f %7.4f %4s'
                  % (n, pa, pb, ratio(pa, pb), ea, eb, ratio(ea, eb),
                     ca, cb, ratio(ca, cb)))

        if opts.e2e:
            print('\n== end-to-end proxied round trip (ms/msg) ==')
            extra = ['-n', str(opts.e2e_n)]
            ea_ = parse_e2e(run_bench(opts.py, 'e2e_proxy_bench.py',
                                      baseline_tree, extra))
            eb_ = parse_e2e(run_bench(opts.py, 'e2e_proxy_bench.py',
                                      REPO, extra))
            print('%8s %10s | %10s %10s %6s' % ('nchans', 'mode', 'baseline',
                                                'fix', 'x'))
            for key in sorted(set(ea_) & set(eb_)):
                print('%8d %10s | %10.2f %10.2f %6s'
                      % (key[0], key[1], ea_[key], eb_[key],
                         ratio(ea_[key], eb_[key])))
    finally:
        subprocess.call(['git', '-C', REPO, 'worktree', 'remove', '--force',
                         baseline_tree])


if __name__ == '__main__':
    main()
