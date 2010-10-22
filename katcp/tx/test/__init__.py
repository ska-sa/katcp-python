# __init__.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2010 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the katcp.tx package.
   """

import unittest
import test_core
import test_proxy

def suite():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(test_core))
    suite.addTests(loader.loadTestsFromModule(test_proxy))
    return suite
