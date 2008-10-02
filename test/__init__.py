import unittest
import katcp # for module dependencies
#import test_katcp_bnf
import test_katcp

def suite():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    #suite.addTests(loader.loadTestsFromModule(test_katcp_bnf))
    suite.addTests(loader.loadTestsFromModule(test_katcp))
    return suite

