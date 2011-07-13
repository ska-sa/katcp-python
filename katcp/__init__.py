# __init__.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""Root of katcp package.
   """

import logging


class NullHandler(logging.Handler):
    def emit(self, record):
        pass

logging.getLogger("katcp").addHandler(NullHandler())

del logging, NullHandler

from .core import Message, KatcpSyntaxError, MessageParser, \
                  DeviceMetaclass, ExcepthookThread, FailReply, \
                  AsyncReply, KatcpDeviceError, KatcpClientError, \
                  Sensor

from .server import DeviceServerBase, DeviceServer, DeviceLogger

from .client import DeviceClient, BlockingClient, CallbackClient

from .sensortree import GenericSensorTree, BooleanSensorTree, \
                        AggregateSensorTree

from .version import VERSION, VERSION_STR

__version__ = VERSION_STR
