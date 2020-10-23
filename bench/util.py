# Copyright 2010 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

from __future__ import absolute_import, division, print_function

from optparse import OptionParser


def standard_parser(default_port=1235):
    parser = OptionParser()
    parser.add_option('--port', dest='port', type=int, default=default_port)
    return parser
