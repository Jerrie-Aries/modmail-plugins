from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from aiohttp.web import Server, ServerRunner, Response, TCPSite

from core.models import getLogger


if TYPE_CHECKING:
    from aiohttp.web_request import BaseRequest


logger = getLogger(__name__)
HOST = "0.0.0.0"
PORT = 8000
__html_content = '<div align="center"><img src="https://i.imgur.com/o558Qnq.png" align="center">'


async def handler(request: BaseRequest) -> Response:
    """
    A simple HTTP request handler to monitor the program on `Replit` with UptimeRobot.
    """
    if request.method == "HEAD":
        return Response(status=200, text="OK!")
    return Response(
        status=200,
        text=__html_content,
        content_type="text/html",
        charset="utf-8",
    )


class KeepAliveServer:
    """
    Repesents keep alive server.
    """

    def __init__(self, slug: str, owner: str):
        self.repl_slug: str = slug
        self.repl_owner: str = owner
        self.server: Optional[Server] = None
        self.runner: Optional[ServerRunner] = None
        self.site: Optional[TCPSite] = None
        self._running: bool = False

    @property
    def url(self) -> str:
        """
        Returns the web server URL.
        """
        return f"https://{self.repl_slug}.{self.repl_owner.lower()}.repl.co"

    async def start(self) -> None:
        """
        Creates and starts the HTTP server.
        """
        if self._running:
            raise RuntimeError("Keep alive server is already running.")
        if self.server is None:
            self.server = Server(handler)
        logger.info("Starting keep alive server.")
        self.runner = ServerRunner(self.server)
        await self.runner.setup()
        self.site = TCPSite(self.runner, HOST, PORT)
        await self.site.start()
        self._running = True

    async def stop(self) -> None:
        """
        Stops the `serve_forever` loop.
        """
        logger.warning(" - Shutting down keep alive server. - ")
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        self._running = False

    def is_running(self) -> bool:
        """
        Returns `True` if keep alive server is currently running.
        """
        return self._running
