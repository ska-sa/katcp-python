# -*- coding: utf-8 -*-
"""Run katcp core + BNF test suites.

Usage:
    python run_core_tests.py
    python run_core_tests.py --fast
"""
from __future__ import print_function
import os
import sys
import unittest

from _repo import katcp_repo
sys.path.insert(0, katcp_repo())

if '--fast' in sys.argv:
    sys.argv.remove('--fast')
    import fastkatcp  # noqa: F401
    print("[fastkatcp patch ACTIVE]")

loader = unittest.TestLoader()
suite = unittest.TestSuite()
for mod in ('katcp.test.test_core', 'katcp.test.test_katcp_bnf'):
    __import__(mod)
    suite.addTests(loader.loadTestsFromModule(sys.modules[mod]))

result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)