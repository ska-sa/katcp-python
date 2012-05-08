# __init__.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2010 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Tests for the katcp.tx package.
   """
### Horrendious monkey patch hack to make twisted.trial.unittest
### support skipping in python 2.6
import unittest, unittest2
oldTestCase = unittest.TestCase
unittest.TestCase = unittest2.TestCase

from twisted.trial import unittest as tx_unit
unittest.TestCase = oldTestCase

def addSkip(self, test, reason):
    self.original.addSkip(test, reason)

tx_unit.PyUnitResultAdapter.addSkip = addSkip
### End horrendious hack

def suite():
    import unittest
    import test_core
    import test_proxy
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(test_core))
    suite.addTests(loader.loadTestsFromModule(test_proxy))
    return suite
