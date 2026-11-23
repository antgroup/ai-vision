"""Threaded HTTP fixtures on an ephemeral IPv4 loopback port."""

from http.server import ThreadingHTTPServer
from socketserver import TCPServer


class LoopbackHTTPServer(ThreadingHTTPServer):
    def __init__(self, handler):
        super().__init__(("127.0.0.1", 0), handler)

    def server_bind(self):
        # HTTPServer resolves its host name even for a numeric loopback address.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address
