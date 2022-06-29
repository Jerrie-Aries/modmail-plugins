from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional

from core.models import getLogger


logger = getLogger(__name__)
HOST = "0.0.0.0"
PORT = 8000


class HTTPRequestHandler(BaseHTTPRequestHandler):
    """
    A simple HTTP request handler to monitor the program on `Replit` with UptimeRobot.

    This class inherits from `http.server.BaseHTTPRequestHandler`. Instead of inheriting
    the `http.server.SimpleHTTPRequestHandler` class, we inherit from the base class and manually
    construct it.
    """

    __html_content = '<div align="center"><img src="https://i.imgur.com/o558Qnq.png" align="center">'

    def do_GET(self) -> None:
        """Serve a GET request."""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes(self.__html_content, "utf8"))

    def do_HEAD(self) -> None:
        """Serve a HEAD request."""
        self.send_response(200, "OK!")


class KeepAliveServer:
    """
    Repesents keep alive server.
    """

    def __init__(self, slug: str, owner: str):
        self.slug: str = slug
        self.owner: str = owner
        self.http_server: Optional[HTTPServer] = None  # implemented in `run()`

    @property
    def url(self) -> str:
        """
        Returns the web server URL.
        """
        return f"https://{self.slug}.{self.owner.lower()}.repl.co"

    def create_http_server(self) -> None:
        """
        Creates the HTTP server.
        """
        with HTTPServer((HOST, PORT), HTTPRequestHandler) as server:
            self.http_server = server
            logger.info(f"Web server started at '{self.url}'")
            self.http_server.serve_forever()

    def shutdown(self) -> None:
        """
        Stops the `serve_forever` loop.
        """
        if self.http_server is not None:
            logger.warning(" - Shutting down web server. - ")
            self.http_server.shutdown()
            self.http_server.server_close()

    def run(self) -> HTTPServer:
        """
        Runs the HTTP server inside a new thread.

        Once this is executed, the value for the `http_server` attribute will be replaced with
        the actual :class:`HTTPServer` instance.

        Returns
        -------
        HTTPServer
            The instance of HTTPServer created.
        """
        thread = Thread(target=self.create_http_server)
        thread.start()
        return self.http_server
