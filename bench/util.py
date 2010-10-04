
from optparse import OptionParser

def standard_parser(default_port=1235):
    parser = OptionParser()
    parser.add_option('--port', dest='port', type=int, default=default_port)
    return parser
