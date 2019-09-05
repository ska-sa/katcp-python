# __init__.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

"""Root of katcp package."""
from __future__ import division, print_function, absolute_import


from future import standard_library
standard_library.install_aliases()
from builtins import *
from .core import (Message, KatcpSyntaxError, MessageParser,
                   DeviceMetaclass, FailReply,
                   AsyncReply, KatcpDeviceError, KatcpClientError,
                   Sensor, ProtocolFlags, AttrDict)

from .server import (DeviceServerBase, DeviceServer, AsyncDeviceServer,
                     DeviceLogger)

from .client import (DeviceClient, AsyncClient, CallbackClient,
                     BlockingClient)

from .resource_client import (KATCPClientResource, KATCPClientResourceContainer)

from .sensortree import (GenericSensorTree, BooleanSensorTree,
                         AggregateSensorTree)

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
