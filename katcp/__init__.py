# __init__.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2008 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

"""Root of katcp package."""
from __future__ import absolute_import, division, print_function

from .client import AsyncClient, BlockingClient, CallbackClient, DeviceClient
from .core import (AsyncReply, AttrDict, DeviceMetaclass, FailReply,
                   KatcpClientError, KatcpDeviceError, KatcpSyntaxError,
                   Message, MessageParser, ProtocolFlags, Sensor)
from .resource_client import KATCPClientResource, KATCPClientResourceContainer
from .sensortree import (AggregateSensorTree, BooleanSensorTree,
                         GenericSensorTree)
from .server import (AsyncDeviceServer, DeviceLogger, DeviceServer,
                     DeviceServerBase)

# BEGIN VERSION CHECK
# Get package version when locally imported from repo or via -e develop install
try:
    import katversion as _katversion
except ImportError:
    import time as _time
    __version__ = "0.0+unknown.{}".format(_time.strftime('%Y%m%d%H%M'))
else:
    __version__ = _katversion.get_version(__path__[0])
# END VERSION CHECK
