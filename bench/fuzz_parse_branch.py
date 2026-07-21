# -*- coding: utf-8 -*-
"""Differential fuzz test: baseline MessageParser vs updated parser.

Extracts baseline katcp modules directly from git into a temporary package
to compare outputs (including exception types and messages) against current HEAD.

Usage:
    python fuzz_parse_branch.py
    python fuzz_parse_branch.py --fastkatcp
"""
from __future__ import print_function
import os
import random
import shutil
import subprocess
import sys
import tempfile

from _repo import katcp_repo

KATCP_REPO = katcp_repo()
BASELINE_REF = os.environ.get('KATCP_BASELINE_REF', 'master')
sys.path.insert(0, KATCP_REPO)

# Extract baseline katcp modules into temporary pristinekatcp package
tmpdir = tempfile.mkdtemp(prefix='mt7273-pristine-')
pkg = os.path.join(tmpdir, 'pristinekatcp')
os.mkdir(pkg)
open(os.path.join(pkg, '__init__.py'), 'w').close()

for fname in ('core.py', 'compat.py', 'kattypes.py', 'sampling.py'):
    src = subprocess.check_output(
        ['git', 'show', '%s:katcp/%s' % (BASELINE_REF, fname)], cwd=KATCP_REPO)
    with open(os.path.join(pkg, fname), 'wb') as f:
        f.write(src)
sys.path.insert(0, tmpdir)

from katcp.core import MessageParser  # noqa: E402

if '--fastkatcp' in sys.argv:
    import fastkatcp  # noqa: F401
    print("[testing fastkatcp monkeypatch]")

from pristinekatcp.core import MessageParser as OrigParser  # noqa: E402

p_orig = OrigParser()
p_fast = MessageParser()

random.seed(1234)

CHARS = [b"a", b"z", b"0", b"9", b"-", b"+", b".", b"j", b"_",
         b"\\_", b"\\\\", b"\\0", b"\\n", b"\\r", b"\\e", b"\\t", b"\\@",
         b"\\", b"\\x", b"\0", b"\x1b", b"\t", b" "]

NAMES = [b"wide-gain", b"gain", b"a", b"9bad", b"x-1", b"ba\0d", b"b\\_d", b""]


def rand_arg():
    n = random.randint(0, 12)
    return b"".join(random.choice(CHARS) for _ in range(n))


def rand_line():
    t = random.choice([b"?", b"!", b"#", b"%", b""])
    name = random.choice(NAMES)
    mid = random.choice([b"", b"[123]", b"[0]", b"[x]", b"[]"])
    nargs = random.randint(0, 6)
    args = b" ".join(rand_arg() for _ in range(nargs))
    line = t + name + mid
    if args:
        line += b" " + args
    return line


def outcome(parser, line):
    try:
        m = parser.parse(line)
        return ("ok", m.mtype, m.name, m.mid, m.arguments)
    except Exception as e:
        return ("err", type(e).__name__, str(e))


mismatches = 0
N = 300000
try:
    for _i in range(N):
        line = rand_line()
        a = outcome(p_orig, line)
        b = outcome(p_fast, line)
        if a != b:
            mismatches += 1
            print("MISMATCH on %r:\n  orig: %r\n  new:  %r" % (line, a, b))
            if mismatches > 10:
                break
    print("done: %d lines, %d mismatches" % (N, mismatches))
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

sys.exit(1 if mismatches else 0)
