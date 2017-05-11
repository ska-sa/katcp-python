from __future__ import division, print_function, absolute_import

import sys
import time
import logging
import threading
import textwrap

import tornado.ioloop

from functools import wraps
from thread import get_ident as get_thread_ident

from concurrent.futures import Future, TimeoutError
from tornado.concurrent import Future as tornado_Future
from tornado import gen

from katcp.object_proxies import ObjectWrapper


log = logging.getLogger(__name__)


def with_relative_timeout(timeout, future, io_loop=None):
    return gen.with_timeout(timeout + time.time(), future, io_loop)


class IOLoopManager(object):
    """Manages an IOLoop instance, optionally in a separate thread."""

    def __init__(self, managed_default=True, logger=log):
        # True if we manage the ioloop. Will be updated by self.set_ioloop()
        self._ioloop_managed = managed_default
        self._logger = logger
        # Thread object that a managed ioloop is running in
        self._ioloop_thread = None
        self._ioloop = None
        # Event that indicates that the ioloop is running.
        self._running = threading.Event()
        self._start_lock = threading.Lock()
        self._daemonize = False

    @property
    def managed(self):
        return self._ioloop_managed

    def get_ioloop(self):
        if not self._ioloop:
            if self._ioloop_managed:
                self.set_ioloop(tornado.ioloop.IOLoop())
            else:
                self.set_ioloop(tornado.ioloop.IOLoop.current())
        return self._ioloop

    def set_ioloop(self, ioloop, managed=None):
        if managed is not None:
            self._ioloop_managed = managed
        if self._ioloop:
            raise RuntimeError('IOLoop instance already set')
        self._ioloop = ioloop

    def _run_managed_ioloop(self):
        assert self._ioloop_managed

        def run_ioloop():
            try:
                self._ioloop.start()
                self._ioloop.close(all_fds=True)
            except Exception:
                self._logger.error('Error running tornado IOloop: ',
                                   exc_info=True)
            finally:
                ioloop = self._ioloop
                self._ioloop = None
                self._running.clear()
                self._logger.info('Managed tornado IOloop {0} stopped'
                                  .format(ioloop))

        with self._start_lock:
            t = threading.Thread(target=run_ioloop)
            t.setDaemon(self._daemonize)
            try:
                if self._ioloop_thread.isAlive():
                    raise RuntimeError('Seems that managed ioloop has already been '
                                       'started, can only restart after stop()')
            except AttributeError:
                pass
            self._ioloop_thread = t
            self._ioloop_thread.start()

    def setDaemon(self, daemonic):
        """Set daemonic state of the managed ioloop thread to True / False

        Calling this method for a non-managed ioloop has no effect. Must be called before
        start(), or it will also have no effect
        """
        self._daemonize = bool(daemonic)

    def start(self, timeout=None):
        """Start managed ioloop thread, or do nothing if not managed.

        If a timeout is passed, it will block until the the event loop is alive
        (or the timeout expires) even if the ioloop is not managed.

        """
        if not self._ioloop:
            raise RuntimeError('Call get_ioloop() or set_ioloop() first')

        self._ioloop.add_callback(self._running.set)

        if self._ioloop_managed:
            self._run_managed_ioloop()
        else:            #  TODO this seems inconsistent with what the docstring describes
            self._running.set()

        if timeout:
            return self._running.wait(timeout)

    def stop(self, timeout=None, callback=None):
        """Stop ioloop (if managed) and call callback in ioloop before close.

        Parameters
        ----------
        timeout : float or None
            Seconds to wait for ioloop to have *started*.

        Returns
        -------
        stopped : thread-safe Future
            Resolves when the callback() is done

        """
        if timeout:
            self._running.wait(timeout)

        stopped_future = Future()

        @gen.coroutine
        def _stop():
            if callback:
                try:
                    yield gen.maybe_future(callback())
                except Exception:
                    self._logger.exception('Unhandled exception calling stop callback')
            if self._ioloop_managed:
                self._logger.info('Stopping ioloop {0!r}'.format(self._ioloop))
                # Allow ioloop to run once before stopping so that callbacks
                # scheduled by callback() above get a chance to run.
                yield gen.moment
                self._ioloop.stop()
            self._running.clear()

        try:
            self._ioloop.add_callback(
                lambda: gen.chain_future(_stop(), stopped_future))
        except AttributeError:
            # Probably we have been shut-down already
            pass

        return stopped_future

    def join(self, timeout=None):
        """Join managed ioloop thread, or do nothing if not managed."""
        if not self._ioloop_managed:
            # Do nothing if the loop is not managed
            return
        try:
            self._ioloop_thread.join(timeout)
        except AttributeError:
            raise RuntimeError('Cannot join if not started')

class IOLoopThreadWrapper(object):
    default_timeout = None

    def __init__(self, ioloop=None):
        self.ioloop = ioloop = ioloop or tornado.ioloop.IOLoop.current()
        self._thread_id = None
        ioloop.add_callback(self._install)

    def call_in_ioloop(self, fn, args, kwargs, timeout=None):
        timeout = timeout or self.default_timeout
        if get_thread_ident() == self._thread_id:
            raise RuntimeError("Cannot call a thread-wrapped object from the ioloop")
        future, tornado_future = Future(), tornado_Future()
        self.ioloop.add_callback(
            self._ioloop_call, future, tornado_future, fn, args, kwargs)
        try:
            # Use the threadsafe future to block
            return future.result(timeout)
        except TimeoutError:
            raise
        except Exception:
            # If we have an exception use the tornado future instead since it
            # will print a nicer traceback.
            tornado_future.result()
            # Should never get here since the tornado future should raise
            assert False, 'Tornado Future should have raised'

    def decorate_callable(self, callable_):
        """Decorate a callable to use call_in_ioloop"""
        @wraps(callable_)
        def decorated(*args, **kwargs):
            # Extract timeout from request itself or use default for ioloop wrapper
            timeout = kwargs.get('timeout')
            return self.call_in_ioloop(callable_, args, kwargs, timeout)

        decorated.__doc__ = '\n\n'.join((
"""Wrapped async call. Will call in ioloop.

This call will block until the original callable has finished running on the ioloop, and
will pass on the return value. If the original callable returns a future, this call will
wait for the future to resolve and return the value or raise the exception that the future
resolves with.

Original Callable Docstring
---------------------------
""",
            textwrap.dedent(decorated.__doc__ or '')))

        return decorated

    def _install(self):
        self._thread_id = get_thread_ident()

    def _ioloop_call(self, future, tornado_future, fn, args, kwargs):
        gen.chain_future(tornado_future, future)
        try:
            result_future = gen.maybe_future(fn(*args, **kwargs))
            gen.chain_future(result_future, tornado_future)
        except Exception:
            tornado_future.set_exc_info(sys.exc_info())

class ThreadSafeMethodAttrWrapper(ObjectWrapper):
    # Attributes must be in the class definition, or else they will be
    # proxied to __subject__
    _ioloop_wrapper = None

    def __init__(self, subject, ioloop_wrapper):
        self._ioloop_wrapper = ioloop_wrapper
        super(ThreadSafeMethodAttrWrapper, self).__init__(subject)

    def __getattr__(self, attr):
        val = super(ThreadSafeMethodAttrWrapper, self).__getattr__(attr)
        if callable(val):
            return self._ioloop_wrapper.decorate_callable(val)
        else:
            return val

    def _getattr(self, attr):
        return self._ioloop_wrapper.call_in_ioloop(getattr, (self.__subject__,
            attr), {})
