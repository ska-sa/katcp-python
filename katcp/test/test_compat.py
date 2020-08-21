# test_compat.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2020 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

"""Tests for the server module."""
from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import unittest

from katcp.compat import ensure_native_str


class test_CompatClass(unittest.TestCase):
    def test_ensure_native_str_raises_error(self):
        """Test that ensure_native_str raises an error"""
        with self.assertRaises(TypeError):
            ensure_native_str(9)
        with self.assertRaises(TypeError):
            ensure_native_str(False)
