import time

from old_io import *



class S(DeviceServerBase):

    ## @brief Protocol versions and flags. Default to version 5, subclasses
    ## should override PROTOCOL_INFO
    PROTOCOL_INFO = ProtocolFlags(DEFAULT_KATCP_MAJOR, 0, set([
        ProtocolFlags.MULTI_CLIENT,
        ProtocolFlags.MESSAGE_IDS,
        ]))

    ## @brief Interface version information.
    VERSION_INFO = ("device_stub", 0, 1)

    ## @brief Device server build / instance information.
    BUILD_INFO = ("name", 0, 1, "")

    def build_state(self):
        """Return a build state string in the form
        name-major.minor[(a|b|rc)n]
        """
        return "%s-%s.%s%s" % self.BUILD_INFO

    def version(self):
        """Return a version string of the form type-major.minor."""
        return "%s-%s.%s" % self.VERSION_INFO


    def request_help(self, req, msg):
        """Return help on the available requests.

        Return a description of the available requests using a seqeunce of
        #help informs.

        Parameters
        ----------
        request : str, optional
            The name of the request to return help for (the default is to
            return help for all requests).

        Informs
        -------
        request : str
            The name of a request.
        description : str
            Documentation for the named request.

        Returns
        -------
        success : {'ok', 'fail'}
            Whether sending the help succeeded.
        informs : int
            Number of #help inform messages sent.

        Examples
        --------
        ::

            ?help
            #help halt ...description...
            #help help ...description...
            ...
            !help ok 5

            ?help halt
            #help halt ...description...
            !help ok 1
        """
        if not msg.arguments:
            for name, method in sorted(self._request_handlers.items()):
                doc = method.__doc__
                req.inform(name, doc)
            num_methods = len(self._request_handlers)
            return req.make_reply("ok", str(num_methods))
        else:
            name = msg.arguments[0]
            if name in self._request_handlers:
                method = self._request_handlers[name]
                doc = method.__doc__.strip()
                req.inform(name, doc)
                return req.make_reply("ok", "1")
            return req.make_reply("fail", "Unknown request method.")

    def on_client_connect(self, client_conn):
        """Inform client of build state and version on connect.

        Parameters
        ----------
        client_conn : ClientConnectionTCP object
            The client connection that has been successfully established.
        """
        katcp_version = self.PROTOCOL_INFO.major
        if katcp_version >= VERSION_CONNECT_KATCP_MAJOR:
            client_conn.inform(Message.inform(
                "version-connect", "katcp-protocol", self.PROTOCOL_INFO))
            client_conn.inform(Message.inform(
                "version-connect", "katcp-library",
                "katcp-python-%s" % VERSION_STR))
            client_conn.inform(Message.inform(
                "version-connect", "katcp-device",
                self.version(), self.build_state() ))
        else:
            client_conn.inform(Message.inform("version", self.version()))
            client_conn.inform(Message.inform("build-state", self.build_state()))

s = S('0.0.0.0', 5000)
s.start()
try:
    time.sleep(1000000000)
finally:
    s.stop()
