
from katcp.txclient import KatCP
from katcp.test.testserver import run_subprocess, PORT
from twisted.trial.unittest import TestCase
from twisted.internet import reactor
from twisted.internet.defer import Deferred

import time
import sys, os, re

class TestKatCP(TestCase):
    """ A tesited test case, run with trial testing

    Also note - don't forget to open a log file:
    tail -F --max-unchanged-stats=0 _trial_temp/test.log
    
    """
    def test_server_infrastructure(self):
        def connected(protocol):
            protocol.do_halt()

        d, process = run_subprocess(connected, KatCP)
        return d

    def test_version_check(self):
        class TestKatCP(KatCP):
            def inform_build_state(self, args):
                KatCP.inform_build_state(self, args)
                # check that version is already set
                assert self.version == 'device_stub-0.1'
                self.do_halt()

        d, process = run_subprocess(None, TestKatCP)
        return d

    def test_help(self):
        def received_help(args, protocol):
            assert len(args) == 9
            protocol.do_halt()
            
        def connected(protocol):
            d = protocol.do_help()
            d.addCallback(received_help, protocol)

        d, process = run_subprocess(connected, KatCP)
        return d
