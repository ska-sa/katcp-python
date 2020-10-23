# katcp.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2019 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

"""Utilities for dealing with Python 2 and 3 compatibility."""

from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import builtins
import future


if future.utils.PY2:
    def ensure_native_str(value):
        """Coerce unicode string or bytes to native string type (UTF-8 encoding)."""
        if isinstance(value, str):
            return value
        elif isinstance(value, unicode):
            return value.encode('utf-8')
        else:
            raise TypeError(
                "Invalid type for string conversion: {}".format(type(value)))
else:
    def ensure_native_str(value):
        """Coerce unicode string or bytes to native string type (UTF-8 encoding)."""
        if isinstance(value, str):
            return value
        elif isinstance(value, bytes):
            return value.decode('utf-8')
        else:
            raise TypeError(
                "Invalid type for string conversion: {}".format(type(value)))


def byte_chars(byte_string):
    """Return list of characters from a byte string (PY3-compatible).

    In PY2, `list(byte_string)` works fine, but in PY3, this returns
    each element as an int instead of single character byte string.
    Slicing is used instead to get the individual characters.

    Parameters
    ----------
    byte_string : bytes
        Byte string to be split into characters.

    Returns
    -------
    chars : list
        The individual characters, each as a byte string.
    """
    return [byte_string[i:i+1] for i in range(len(byte_string))]


def is_bytes(value):
    """Indicate if object is bytes-like.

    future.utils.isbytes is deprecated, so re-implementing, as per their
    recommendation.
    """
    return isinstance(value, builtins.bytes)


def is_text(value):
    """Indicate if object is text-like.

    future.utils.istext is deprecated, so re-implementing, as per their
    recommendation.
    """
    return isinstance(value, builtins.str)
