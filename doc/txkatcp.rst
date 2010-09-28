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

ClientKatCP
"""""""""""
.. autoclass:: ClientKatCP
    :members:
