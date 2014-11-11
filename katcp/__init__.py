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

del NullHandler

try:

    from .core import (Message, KatcpSyntaxError, MessageParser,
                       DeviceMetaclass, FailReply,
                       AsyncReply, KatcpDeviceError, KatcpClientError,
                       Sensor, ProtocolFlags, AttrDict)

    from .server import DeviceServerBase, DeviceServer, DeviceLogger

    from .client import (DeviceClient, AsyncClient, CallbackClient,
                         BlockingClient)

    from .sensortree import (GenericSensorTree, BooleanSensorTree,
                             AggregateSensorTree)

except ImportError:
    # Ignore this error to prevent import errors during setup.py when
    # katcp.version is imported. Things should break soon enough afterwards :)
    import warnings
    warnings.warn('Could not import some modules, fine during setup, '
                  'but will prevent the library from working if you see '
                  'this after installation')
    logging.exception('Error importing:')

del logging

from .version import VERSION, VERSION_STR

__version__ = VERSION_STR
