.. Twisted KatCP implementation

****************************
Twisted KatCP implementation
****************************

.. module:: katcp.tx

Twisted
"""""""

Twisted KatCP is an alternative implementation of KatCP protocol using
`Twisted`_ framework. Providing alternative implementation using a well known
networking library has multiple advantages. Among the most important is
an alternative to threads concurrency model, which work better for some
cases and robustness that come from years of testing.

.. _`Twisted`: http://twistedmatrix.com

Using TxKatCP, clients
""""""""""""""""""""""

.. note:: general information how to `write clients using Twisted`_.

The main client interface is a :class:`ClientKatCP <katcp.tx.ClientKatCP>`,
similar in purpose to the :class:`CallbackClient <katcp.CallbackClient>`.

Interface is built around two ways of communicating. Asynchronous informs, which
are not associated with any requests will be handled by overloaded
``inform_xyz`` methods, where ``xyz`` is a name of a method. Requests are sent
via ``send_request`` method on the client class, which will return a deferred.
Deferred will be called with a tuple ``((informs, reply))`` where informs
is a list of katcp :class:`Message <katcp.Message>` and reply is one message
object.

.. note:: an example client is in ``scripts/demotxclient.py``.

.. _`write clients using Twisted`: http://twistedmatrix.com/documents/current/core/howto/clients.html

Using TxKatCP, device servers
"""""""""""""""""""""""""""""

.. note:: general information how to `write servers using Twisted`_.

The :class:`DeviceServer <katcp.tx.DeviceServer>` is very similar to
:class:`DeviceServer <katcp.DeviceServer>`. You overload ``request_xxx`` methods
where ``xxx`` is a name of katcp request. A request method either returns
a :class:`Message <katcp.Message>` reply instance, possibly sending informs
along the way (using ``self.send_message`` interface). Device server must
overload ``setup_sensors`` method, which should register sensors by calling
``self.add_sensor``.

.. note:: an example server is in ``scripts/demotxserver.py``.

.. _`write servers using Twisted`: http://twistedmatrix.com/documents/current/core/howto/servers.html
