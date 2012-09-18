# version.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""katcp version information.
   """

VERSION = (0, 3, 5, 'final', 0)

BASE_VERSION_STR = '.'.join([str(x) for x in VERSION[:3]])
VERSION_STR = {
    'final': BASE_VERSION_STR,
    'alpha': BASE_VERSION_STR + 'a' + str(VERSION[4]),
    'rc': BASE_VERSION_STR + 'rc' + str(VERSION[4]),
}[VERSION[3]]
