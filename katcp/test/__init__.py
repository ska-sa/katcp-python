import unittest
import test_katcp
import test_client
import test_kattypes
import test_sampling

try:
    # BNF tests reply on PLY (Python Lexx/Yacc)
    import ply
    import test_katcp_bnf
except ImportError:
    ply = None

def suite():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(test_katcp))
    suite.addTests(loader.loadTestsFromModule(test_client))
    suite.addTests(loader.loadTestsFromModule(test_kattypes))
    suite.addTests(loader.loadTestsFromModule(test_sampling))
    if ply is not None:
        suite.addTests(loader.loadTestsFromModule(test_katcp_bnf))
    return suite

