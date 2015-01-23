###############################################################################
# SKA South Africa (http://ska.ac.za/)                                        #
# Author: cam@ska.ac.za                                                       #
# Copyright @ 2013 SKA SA. All rights reserved.                               #
#                                                                             #
# THIS SOFTWARE MAY NOT BE COPIED OR DISTRIBUTED IN ANY FORM WITHOUT THE      #
# WRITTEN PERMISSION OF SKA SA.                                               #
###############################################################################

import unittest2 as unittest
import logging

from katcp import resource

class test_escape_name(unittest.TestCase):
    def test_escape_name(self):
        desired_mappings = {
            'blah-bal' : 'blah_bal',
            'bief_bof.ba32f-blief' : 'bief_bof_ba32f_blief',
            'already_escape_name' : 'already_escape_name'}
        for input, expected_output in desired_mappings.items():
            self.assertEqual(resource.escape_name(input), expected_output)
