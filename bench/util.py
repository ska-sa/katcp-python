from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from __future__ import unicode_literals
# Copyright 2009 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

from future import standard_library
standard_library.install_aliases()
from builtins import *
from optparse import OptionParser

def standard_parser(default_port=1235):
    parser = OptionParser()
    parser.add_option('--port', dest='port', type=int, default=default_port)
    return parser
