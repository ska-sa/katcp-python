# __init__.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the katcp package.
   """

import unittest
import test_katcp
import test_client
import test_kattypes
import test_sampling
import test_sensortree
import test_server
import test_txkatcp

try:
    # BNF tests reply on PLY (Python Lexx/Yacc)
    import ply
    import test_katcp_bnf
except ImportError:
    ply = None

def suite():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(test_katcp))
    suite.addTests(loader.loadTestsFromModule(test_client))
    suite.addTests(loader.loadTestsFromModule(test_kattypes))
    suite.addTests(loader.loadTestsFromModule(test_sampling))
    suite.addTests(loader.loadTestsFromModule(test_sensortree))
    suite.addTests(loader.loadTestsFromModule(test_server))
    suite.addTests(loader.loadTestsFromModule(test_txkatcp))
    if ply is not None:
        suite.addTests(loader.loadTestsFromModule(test_katcp_bnf))
    return suite

