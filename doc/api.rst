.. API

***
API
***

.. module:: katcp

Client
^^^^^^

.. autoclass:: DeviceClient
    :members:

.. autoclass:: BlockingClient
    :members:

.. autoclass:: CallbackClient
    :members:

.. autoclass:: KatcpClientError
    :members:

Server
^^^^^^

.. autoclass:: DeviceServerBase
    :members:

.. autoclass:: DeviceServer
    :members:

.. autoclass:: DeviceLogger
    :members:

.. autoclass:: Sensor
    :members:

.. autoclass:: FailReply
    :members:

.. autoclass:: AsyncReply
    :members:

.. autoclass:: KatcpDeviceError
    :members:

Message Parsing
^^^^^^^^^^^^^^^

.. autoclass:: Message
    :members:

.. autoclass:: MessageParser
    :members:

.. autoclass:: KatcpSyntaxError
    :members:

Other
^^^^^

.. autoclass:: DeviceMetaclass
    :members:

.. autoclass:: ExcepthookThread
    :members:

Version Information
^^^^^^^^^^^^^^^^^^^

.. data:: VERSION

Five-element tuple containing the version number. 

.. data:: VERSION_STR

String representing the version number.
