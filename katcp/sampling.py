# sampling.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Strategies for sampling sensor values."""

from __future__ import division, print_function, absolute_import

import logging
import os

import tornado.ioloop

from thread import get_ident as get_thread_ident
from functools import wraps

from .core import Message, Sensor


log = logging.getLogger("katcp.sampling")

AGE_OF_UNIVERSE = 4.32329886e17  # approximate age of the universe


# pylint: disable-msg=W0142

def format_inform_v4(sensor, *reading):
    timestamp, status, value = sensor.format_reading(reading, 4)
    return Message.inform(
        "sensor-status", timestamp, "1", sensor.name, status, value)


def format_inform_v5(sensor, *reading):
    timestamp, status, value = sensor.format_reading(reading, 5)
    return Message.inform(
        "sensor-status", timestamp, "1", sensor.name, status, value)


def update_in_ioloop(update):
    """Decorator that ensures an update() method is run in the tornado ioloop.

    Does this by checking the thread identity. Requires that the object to
    which the method is bound has the attributes :attr:`_ioloop_thread_id`
    (the result of thread.get_ident() in the ioloop thread) and :attr:`ioloop`
    (the ioloop instance in use). Also assumes the signature
    `update(self, sensor, reading)` for the method.


    """
    @wraps(update)
    def wrapped_update(self, sensor, reading):
        if get_thread_ident() == self._ioloop_thread_id:
            update(self, sensor, reading)
        else:
            self.ioloop.add_callback(update, self, sensor, reading)

    return wrapped_update


class SampleStrategy(object):
    """Base class for strategies for sampling sensors.

    Parameters
    ----------
    inform_callback : callable, signature inform_callback(sensor_obj, reading)
        Callback to receive inform messages.
    sensor : Sensor object
        Sensor to sample.
    params : list of objects
        Custom sampling parameters.

    """

    # Sampling strategy constants
    NONE, AUTO, PERIOD, EVENT, DIFFERENTIAL, EVENT_RATE, DIFFERENTIAL_RATE = range(7)

    ## @brief Mapping from strategy constant to strategy name.
    SAMPLING_LOOKUP = {
        NONE: "none",
        AUTO: "auto",
        PERIOD: "period",
        EVENT: "event",
        DIFFERENTIAL: "differential",
        EVENT_RATE: "event-rate",
        DIFFERENTIAL_RATE: "differential-rate",
    }

    # SAMPLING_LOOKUP not found by pylint
    #
    # pylint: disable-msg = E0602

    ## @brief Mapping from strategy name to strategy constant.
    SAMPLING_LOOKUP_REV = dict((v, k) for k, v in SAMPLING_LOOKUP.items())

    # pylint: enable-msg = E0602

    OBSERVE_UPDATES = False
    "True if a strategy must be attached to its sensor as an observer"

    def __init__(self, inform_callback, sensor, *params, **kwargs):
        self.ioloop = kwargs.get('ioloop') or tornado.ioloop.IOLoop.current()
        self._inform_callback = inform_callback
        self._sensor = sensor
        self._params = params

    @classmethod
    def get_strategy(cls, strategyName, inform_callback, sensor,
                     *params, **kwargs):
        """Factory method to create a strategy object.

        Parameters
        ----------
        strategyName : str
            Name of strategy.
        inform_callback : callable, signature inform_callback(sensor, reading)
            Callback to receive inform messages.
        sensor : Sensor object
            Sensor to sample.
        params : list of objects
            Custom sampling parameters for specified strategy.

        Keyword Arguments
        -----------------
        ioloop : tornado.ioloop.IOLoop instance, optional
            Tornado ioloop to use, otherwise tornado.ioloop.IOLoop.current()

        Returns
        -------
        strategy : :class:`SampleStrategy` object
            The created sampling strategy.

        """
        if strategyName not in cls.SAMPLING_LOOKUP_REV:
            raise ValueError("Unknown sampling strategy '%s'. "
                             "Known strategies are %s."
                             % (strategyName, cls.SAMPLING_LOOKUP.values()))

        strategyType = cls.SAMPLING_LOOKUP_REV[strategyName]
        if strategyType == cls.NONE:
            return SampleNone(inform_callback, sensor, *params, **kwargs)
        elif strategyType == cls.AUTO:
            return SampleAuto(inform_callback, sensor, *params, **kwargs)
        elif strategyType == cls.EVENT:
            return SampleEvent(inform_callback, sensor, *params, **kwargs)
        elif strategyType == cls.DIFFERENTIAL:
            return SampleDifferential(inform_callback, sensor,
                                      *params, **kwargs)
        elif strategyType == cls.PERIOD:
            return SamplePeriod(inform_callback, sensor, *params, **kwargs)
        elif strategyType == cls.EVENT_RATE:
            return SampleEventRate(inform_callback, sensor, *params, **kwargs)
        elif strategyType == cls.DIFFERENTIAL_RATE:
            return SampleDifferentialRate(inform_callback, sensor,
                                          *params, **kwargs)

    def update(self, sensor, reading):
        """Callback used by the sensor's notify() method.

        This update method is called whenever the sensor value is set
        so sensor will contain the right info. Note that the strategy
        does not really need to be passed a sensor because it already
        has a handle to it, but receives it due to the generic observer
        mechanism.

        Sub-classes should override this method or :meth:`start` to provide
        the necessary sampling strategy. Sub-classes should also ensure that
        :meth:`update` is thread-safe; an easy way to do this is by using
        the @update_in_ioloop decorator.

        Parameters
        ----------
        sensor : Sensor object
            The sensor which was just updated.
        reading : (timestamp, status, value) tuple
            Sensor reading as would be returned by sensor.read()

        """
        pass

    def get_sampling(self):
        """The Strategy constant for this sampling strategy.

        Sub-classes should implement this method and return the
        appropriate constant.

        Returns
        -------
        strategy : Strategy constant
            The strategy type constant for this strategy.

        """
        raise NotImplementedError

    def get_sampling_formatted(self):
        """The current sampling strategy and parameters.

        The strategy is returned as a string and the values
        in the parameter list are formatted as strings using
        the formatter for this sensor type.

        Returns
        -------
        strategy_name : string
            KATCP name for the strategy.
        params : list of strings
            KATCP formatted parameters for the strategy.

        """
        strategy = self.get_sampling()
        strategy = self.SAMPLING_LOOKUP[strategy]
        params = [str(p) for p in self._params]
        return strategy, params

    def attach(self):
        """Attach strategy to its sensor and send initial update."""
        s = self._sensor
        self.update(s, s.read())
        self._sensor.attach(self)

    def detach(self):
        """Detach strategy from its sensor."""
        self._sensor.detach(self)

    def cancel(self):
        """Detach strategy from its sensor and cancel ioloop callbacks."""
        if self.OBSERVE_UPDATES:
            self.detach()
        self.ioloop.add_callback(self.cancel_timeouts)

    def start(self):
        """Start operating the strategy.

        Subclasses that override start() should call the super method before
        it does anything that uses the ioloop. This will attach to the sensor
        as an observer if :attr:`OBSERVE_UPDATES` is True, and sets
        :attr:`_ioloop_thread_id` using `thread.get_ident()`.

        """
        def first_run():
            self._ioloop_thread_id = get_thread_ident()
            if self.OBSERVE_UPDATES:
                self.attach()
        self.ioloop.add_callback(first_run)

    def inform(self, reading):
        """Inform strategy creator of the sensor status."""
        try:
            self._inform_callback(self._sensor, reading)
        except Exception:
            log.exception('Unhandled exception trying to send {!r} '
                          'for sensor {!r} of type {!r}'
                          .format(reading, self._sensor.name, self._sensor.type))

    def cancel_timeouts(self):
        """Override this method to cancel any outstanding ioloop timeouts."""
        pass


class SampleAuto(SampleStrategy):
    """Strategy which sends updates whenever the sensor itself is updated."""

    OBSERVE_UPDATES = True

    def __init__(self, inform_callback, sensor, *params, **kwargs):
        SampleStrategy.__init__(self, inform_callback, sensor, *params, **kwargs)
        if params:
            raise ValueError("The 'auto' strategy takes no parameters.")

    @update_in_ioloop
    def update(self, sensor, reading):
        self.inform(reading)

    def get_sampling(self):
        return SampleStrategy.AUTO


class SampleNone(SampleStrategy):
    """Sampling strategy which never sends any updates."""

    def __init__(self, inform_callback, sensor, *params, **kwargs):
        SampleStrategy.__init__(self, inform_callback, sensor, *params, **kwargs)
        if params:
            raise ValueError("The 'none' strategy takes no parameters.")

    def start(self):
        # Do nothing, since we don't need set anything up
        pass

    def get_sampling(self):
        return SampleStrategy.NONE


class SampleDifferential(SampleStrategy):
    """Differential sampling strategy for integer and float sensors.

    Sends updates only when the value has changed by more than some
    specified threshold, or the status changes.

    """

    OBSERVE_UPDATES = True

    def __init__(self, inform_callback, sensor, *params, **kwargs):
        SampleStrategy.__init__(self, inform_callback, sensor, *params, **kwargs)
        if len(params) != 1:
            raise ValueError("The 'differential' strategy takes "
                             "one parameter.")
        if sensor._sensor_type not in (Sensor.INTEGER, Sensor.FLOAT,
                                       Sensor.TIMESTAMP):
            raise ValueError("The 'differential' strategy is only valid for "
                             "float, integer and timestamp sensors.")
        if sensor._sensor_type == Sensor.INTEGER:
            self._threshold = int(params[0])
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive integer.")
        elif sensor._sensor_type == Sensor.FLOAT:
            self._threshold = float(params[0])
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive float.")
        else:
            # _sensor_type must be Sensor.TIMESTAMP

            # There is a potential snafu here if katcpv4 server is used, since
            # the timestamp sensor type should be in milliseconds. For now, just
            # ignore this eventuality, and fix if anyone actually needs this
            self._threshold = float(params[0])
            if self._threshold <= 0:
                raise ValueError("The diff amount must be a positive number "
                                 "of seconds.")
        self._lastStatus = None
        self._lastValue = None

    @update_in_ioloop
    def update(self, sensor, reading):
        _timestamp, status, value = reading
        if (status != self._lastStatus or
                abs(value - self._lastValue) > self._threshold):
            self._lastStatus = status
            self._lastValue = value
            self.inform(reading)

    def get_sampling(self):
        return SampleStrategy.DIFFERENTIAL


class SamplePeriod(SampleStrategy):
    """Periodic sampling strategy."""

    def __init__(self, inform_callback, sensor, *params, **kwargs):
        SampleStrategy.__init__(self, inform_callback, sensor, *params, **kwargs)
        if len(params) != 1:
            raise ValueError("The 'period' strategy takes one parameter. "
                             "Parameters passed: %r, in pid : %s"
                             % (params, os.getpid()))
        period = float(params[0])
        if period <= 0:
            raise ValueError("The period must be a positive float in seconds. "
                             "Parameters passed: %r, in pid : %s"
                             % (params, os.getpid()))
        self._period = period

    def start(self):
        super(SamplePeriod, self).start()
        def start_periodic_sampling():
            self.next_time = self.ioloop.time()
            self._run_once()
        self.ioloop.add_callback(start_periodic_sampling)

    def _run_once(self):
        assert get_thread_ident() == self._ioloop_thread_id
        now = self.ioloop.time()
        self.inform(self._sensor.read())
        self.next_time += self._period
        if self.next_time < now:
            # Catch up if we have fallen far behind
            self.next_time = now + self._period
        self.next_timeout_handle = self.ioloop.call_at(self.next_time,
                                                       self._run_once)

    def get_sampling(self):
        return SampleStrategy.PERIOD

    def cancel_timeouts(self):
        self.ioloop.remove_timeout(self.next_timeout_handle)


class SampleEventRate(SampleStrategy):
    """Event rate sampling strategy.

    Report the sensor value whenever it changes or if more than
    *longest_period* seconds have passed since the last reported
    update. However, do not report the value if less than
    *shortest_period* seconds have passed since the last reported
    update.

    """

    OBSERVE_UPDATES = True

    def __init__(self, inform_callback, sensor, *params, **kwargs):
        SampleStrategy.__init__(self, inform_callback, sensor, *params, **kwargs)
        if len(params) != 2:
            raise ValueError("The 'event-rate' strategy takes two parameters.")
        shortest_period = float(params[0])
        longest_period = float(params[1])
        self._next_periodic = 1e99

        if not 0 <= shortest_period <= longest_period:
            raise ValueError("The shortest and longest periods must "
                             "satisfy 0 <= shortest_period <= longest_period")
        self._shortest_period = shortest_period
        self._short_timeout_handle = None
        self._longest_period = longest_period
        # don't send updates until timestamp _not_before
        self._not_before = 0
        self._last_reading_sent = (None, None, None)

    def inform(self, reading):
        SampleStrategy.inform(self, reading)
        now = self.ioloop.time()
        self._not_before = now + self._shortest_period
        self._not_after = now + self._longest_period
        self._last_reading_sent = reading

    def start(self):
        super(SampleEventRate, self).start()
        self.ioloop.add_callback(self._periodic_sampling)

    def _periodic_sampling(self):
        if self._not_after >= AGE_OF_UNIVERSE:
            # Ignore stupidly long periods
            self._periodic_timeout_handle = None
            return
        if self.ioloop.time() >= self._not_after:
            self.inform(self._sensor.read())
        # We depend on self.inform() having updated self._not_after
        self._periodic_timeout_handle = self.ioloop.call_at(
            self._not_after, self._periodic_sampling)

    def _short_timeout_handler(self):
        self._short_timeout_handle = None
        if (self.ioloop.time() >= self._not_before):
            self.inform(self._sensor.read())

    def _sensor_changed(self, reading):
        _, status, value = reading
        _, last_s, last_v = self._last_reading_sent
        return status != last_s or value != last_v

    @update_in_ioloop
    def update(self, sensor, reading):
        sensor_changed = self._sensor_changed(reading)
        if not sensor_changed:
            # Ignore update if sensor value/status is unchanged
            return
        now = self.ioloop.time()
        if now < self._not_before:
            # Too soon to send an update again
            if not self._short_timeout_handle:
                # Make sure we schedule a callback to send the sensor value as
                # soon as the minimum period since the last update has expired
                self._short_timeout_handle = self.ioloop.call_at(
                    self._not_before, self._short_timeout_handler)
        else:
            # Send the sensor value, updating self._not_before and
            # self._last_reading_sent in the process
            self.inform(reading)

    def get_sampling(self):
        return SampleStrategy.EVENT_RATE

    def cancel_timeouts(self):
        if self._periodic_timeout_handle:
            self.ioloop.remove_timeout(self._periodic_timeout_handle)
        if self._short_timeout_handle:
            self.ioloop.remove_timeout(self._short_timeout_handle)


class SampleEvent(SampleEventRate):
    """Strategy which sends updates when the sensor value or status changes.

    Since SampleEvent is just a special case of SampleEventRate, we use
    SampleEventRate with the appropriate default values to implement
    SampleEvent.

    """
    def __init__(self, inform_callback, sensor, *params, **kwargs):
        if len(params) > 0:
            raise ValueError("The 'event' strategy takes no parameters.")
        super(SampleEvent, self).__init__(inform_callback, sensor, 0, 1e99, **kwargs)
        # Fix up the parameters so we don't see the extra parameters that were
        # passed to SampleEventRate
        self._params = params

    def get_sampling(self):
        return SampleStrategy.EVENT


class SampleDifferentialRate(SampleEventRate):
    """Differential rate sampling strategy.

    Report the value whenever it changes by more than *difference*
    from the last reported value or if more than *longest_period*
    seconds have passed since the last reported update. However, do
    not report the value until *shortest_period* seconds have passed
    since the last reported update. The behaviour if *shortest_period*
    is greater than *longest_period* is undefined. May only be
    implemented for float and integer sensors.

    """
    def __init__(self, inform_callback, sensor, *params, **kwargs):
        # Remove the 'difference' parameter from params for the super call
        if len(params) != 3:
            raise ValueError("The 'differential-rate' strategy takes three parameters.")
        super(SampleDifferentialRate, self).__init__(inform_callback, sensor,
                                                     *params[1:], **kwargs)
        difference = params[0]
        if sensor.stype == 'integer':
            difference = int(difference)
        elif sensor.stype == 'float':
            difference = float(difference)
        else:
            raise ValueError('The differential-rate strategy can only be '
                             'defined for integer or float sensors')
        self.difference = difference
        # Initial value that should not cause errors in _sensor_changed(),
        # but should let it return True
        self._last_reading_sent = (None, None, 1e99)

    def _sensor_changed(self, reading):
        _, status, value = reading
        _, last_s, last_v = self._last_reading_sent
        return (abs(value - last_v) > self.difference or status != last_s)
