.. Core API

********
Core API
********

.. module:: katcp

Client
^^^^^^

Two different clients are provided: the :class:`BlockingClient <katcp.BlockingClient>`
for synchronous communication with a server and the :class:`CallbackClient <katcp.CallbackClient>`
for asynchronous communication. Both clients raise :exc:`KatcpClientError <katcp.KatcpClientError>`
when exceptions occur.

The :class:`DeviceClient <katcp.DeviceClient>` base class is provided as a foundation for
those wishing to implement their own clients.

BlockingClient
""""""""""""""

.. autoclass:: BlockingClient
    :members:
    :inherited-members:

CallbackClient
""""""""""""""

.. autoclass:: CallbackClient
    :members:
    :inherited-members:

AsyncClient
"""""""""""

.. autoclass:: AsyncClient
    :members:
    :inherited-members:


Base Classes
""""""""""""

.. autoclass:: DeviceClient
    :members:

Exceptions
""""""""""

.. autoclass:: KatcpClientError
    :members:

Server
^^^^^^


AsyncDeviceServer
"""""""""""""""""

.. autoclass:: AsyncDeviceServer
    :members:
    :inherited-members:


DeviceServer
""""""""""""

.. autoclass:: DeviceServer
    :members:
    :inherited-members:

DeviceServerBase
""""""""""""""""

.. autoclass:: DeviceServerBase
    :members:

DeviceLogger
""""""""""""

.. autoclass:: DeviceLogger
    :members:

Sensor
""""""

.. autoclass:: Sensor
    :members:

Exceptions
""""""""""

.. autoclass:: FailReply
    :members:

.. autoclass:: AsyncReply
    :members:

.. autoclass:: KatcpDeviceError
    :members:

High Level Clients
^^^^^^^^^^^^^^^^^^

KATCPClientResource
"""""""""""""""""""

.. autoclass:: KATCPClientResource
    :members:
    :inherited-members:

KATCPClientResourceContainer
""""""""""""""""""""""""""""

.. autoclass:: KATCPClientResourceContainer
    :members:
    :inherited-members:



Message Parsing
^^^^^^^^^^^^^^^

Message
"""""""

.. autoclass:: Message
    :members:

MessageParser
"""""""""""""

.. autoclass:: MessageParser
    :members:

Exceptions
""""""""""

.. autoclass:: KatcpSyntaxError
    :members:

Other
^^^^^

DeviceMetaclass
"""""""""""""""

.. autoclass:: DeviceMetaclass
    :members:


Version Information
^^^^^^^^^^^^^^^^^^^

.. data:: VERSION

Five-element tuple containing the version number. 

.. data:: VERSION_STR

String representing the version number.
