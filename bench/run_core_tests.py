# -*- coding: utf-8 -*-
"""Run katcp core + BNF test suites.

Usage:
    python run_core_tests.py
"""
from __future__ import print_function
import sys
import unittest

from _repo import katcp_repo
sys.path.insert(0, katcp_repo())

loader = unittest.TestLoader()
suite = unittest.TestSuite()
for mod in ('katcp.test.test_core', 'katcp.test.test_katcp_bnf'):
    __import__(mod)
    suite.addTests(loader.loadTestsFromModule(sys.modules[mod]))

result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
