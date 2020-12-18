# Copyright 2017 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details
from __future__ import absolute_import, division, print_function
from future import standard_library
standard_library.install_aliases()  # noqa: E402

import sys
import unittest

from builtins import object
from concurrent.futures import Future

from _thread import get_ident as get_thread_ident

# Module under test
from katcp import ioloop_manager
from katcp.testutils import start_thread_with_cleanup


class test_ThreadsafeMethodAttrWrapper(unittest.TestCase):
    def setUp(self):
        self.ioloop_manager = ioloop_manager.IOLoopManager(managed_default=True)
        self.ioloop = self.ioloop_manager.get_ioloop()
        self.ioloop_thread_wrapper = ioloop_manager.IOLoopThreadWrapper(self.ioloop)
        start_thread_with_cleanup(self, self.ioloop_manager, start_timeout=1)

    def test_wrapping(self):
        test_inst = self
        class Wrappee(object):
            def __init__(self, ioloop_thread_id):
                self.thread_id = ioloop_thread_id

            def a_callable(self, arg, kwarg='abc'):
                test_inst.assertEqual(get_thread_ident(), self.thread_id)
                return (arg * 2, kwarg * 3)

            @property
            def not_in_ioloop(self):
                test_inst.assertNotEqual(get_thread_ident(), self.thread_id)
                return 'not_in'

            @property
            def only_in_ioloop(self):
                test_inst.assertEqual(get_thread_ident(), self.thread_id)
                return 'only_in'

        class TestWrapper(ioloop_manager.ThreadSafeMethodAttrWrapper):
            @property
            def only_in_ioloop(self):
                return self._getattr('only_in_ioloop')


        id_future = Future()
        self.ioloop.add_callback(lambda : id_future.set_result(get_thread_ident()))
        wrappee = Wrappee(id_future.result(timeout=1))
        wrapped = TestWrapper(wrappee, self.ioloop_thread_wrapper)
        # First test our assumptions about Wrappee
        with self.assertRaises(AssertionError):
            wrappee.a_callable(3, 'a')
        with self.assertRaises(AssertionError):
            wrappee.only_in_ioloop
        self.assertEqual(wrappee.not_in_ioloop, 'not_in')

        # Now test the wrapped version
        self.assertEqual(wrapped.a_callable(5, kwarg='bcd'), (10, 'bcd'*3))
        self.assertEqual(wrapped.only_in_ioloop, 'only_in')
        self.assertEqual(wrapped.not_in_ioloop, 'not_in')


class test_IOLoopManager(unittest.TestCase):
    def setUp(self):
        self.ioloop_manager = ioloop_manager.IOLoopManager(managed_default=True)
        self.ioloop = self.ioloop_manager.get_ioloop()
        start_thread_with_cleanup(self, self.ioloop_manager, start_timeout=1)

    def test_managed_io_loop_is_asyncio_in_python3(self):
        if sys.version_info[0] == 3:
            self.assertTrue(hasattr(self.ioloop, "asyncio_loop"))
        else:
            self.assertFalse(hasattr(self.ioloop, "asyncio_loop"))
