from __future__ import annotations

import os
import re

from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import aiohttp
import jinja2

from aiohttp import web
from aiohttp.web import Application, Request as AIOHTTPRequest, Response as AIOHTTPResponse
from discord.utils import MISSING
from jinja2 import Environment, FileSystemLoader

from core.models import getLogger

from .handlers import aiohttp_error_handler, AIOHTTPMethodHandler
from .models import LogEntry


if TYPE_CHECKING:
    from jinja2 import Template
    from bot import ModmailBot

    from .typing_ext import RawPayload


logger = getLogger(__name__)

# Set path for static
parent_dir = Path(__file__).parent.parent.resolve()
static_path = parent_dir / "static"

# Set path for templates
templates_path = parent_dir / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(templates_path),
    enable_async=True,
)


class Config:
    """
    Base class for storing configurations from `.env` (environment variables).
    """

    def __init__(self):
        self.log_prefix = os.getenv("URL_PREFIX", "/logs")
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = os.getenv("PORT", 8000)


class LogviewerServer:
    """
    Parent class for Log viewer server. This class should not be instantiated directly instead
    use one of its subclasses.
    """

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self.config: Config = Config()

        self.app: Application = MISSING
        self._hooked: bool = False
        self._running: bool = False

    def init_hook(self) -> None:
        """
        Hooks everything necessary before starting up the server.

        This method should be overridden by subclasses.
        """
        raise NotImplementedError

    async def _setup(self) -> None:
        """
        Setup the server. This method should be overridden by subclasses.
        """
        raise NotImplementedError

    async def start(self) -> None:
        """
        Starts the log viewer server.

        Internally this will call the `._setup()` method to setup the server, so it should be overridden
        by subclasses.
        """
        if self._running:
            raise RuntimeError("Log viewer server is already running.")
        if not self._hooked:
            self.init_hook()
        logger.info("Starting log viewer server.")
        await self._setup()
        favicon_path = static_path / "favicon.webp"
        if not favicon_path.exists():
            asset = self.bot.user.display_avatar.replace(size=32, format="webp")
            await asset.save(favicon_path)
        self._running = True

    async def stop(self) -> None:
        """
        Stops the log viewer server.

        Internally this will call the `._stop()` method, so it should be overridden by subclasses.
        """
        logger.warning(" - Shutting down web server. - ")
        await self._stop()
        self._running = False

    async def _stop(self) -> None:
        raise NotImplementedError

    def is_running(self) -> bool:
        return self._running

    def info(self) -> None:
        raise NotImplementedError

    async def process_logs(self, request: AIOHTTPRequest, *, path: str, key: str) -> AIOHTTPResponse:
        """
        Matches the request path with regex before rendering the logs template to user.
        """
        PATH_RE = re.compile(rf"^{self.config.log_prefix}/(?:(?P<raw>raw)/)?(?P<key>([a-zA-Z]|[0-9])+)")
        match = PATH_RE.match(path)
        if match is None:
            return await self.raise_error("not_found", message=f"Invalid path, '{path}'.")
        data = match.groupdict()
        raw = data["raw"]
        if not raw:
            return await self.render_logs(request, key)
        else:
            return await self.render_raw_logs(request, key)

    async def render_logs(
        self,
        request: AIOHTTPRequest,
        key: str,
    ) -> AIOHTTPResponse:
        """Returns the html rendered log entry"""
        logs = self.bot.api.logs
        document: RawPayload = await logs.find_one({"key": key})
        if not document:
            return await self.raise_error("not_found", message=f"Log entry '{key}' not found.")
        log_entry = LogEntry(document)
        return await self.render_template("logbase", request, log_entry=log_entry)

    async def render_raw_logs(self, request, key) -> Any:
        """
        Returns the plain text rendered log entry.

        This method should be overridden by subclass.
        """
        raise NotImplementedError

    @staticmethod
    async def raise_error(error_type: str, *, message: Optional[str] = None, **kwargs) -> Any:
        """
        Raises error. This method should be overridden by subclass.
        """
        raise NotImplementedError

    async def render_template(
        self,
        name: str,
        request: AIOHTTPRequest,
        *args: Any,
        **kwargs: Any,
    ) -> AIOHTTPResponse:

        kwargs["app"] = request.app
        kwargs["config"] = self.config

        template = jinja_env.get_template(name + ".html")
        template = await template.render_async(*args, **kwargs)
        return await self.send_template(template, **kwargs)


class AIOHTTPServer(LogviewerServer):
    def __init__(self, bot: ModmailBot):
        super().__init__(bot)
        self.site: web.TCPSite = MISSING
        self.runner: web.AppRunner = MISSING

    def init_hook(self) -> None:
        self.app: Application = Application()
        self.app.router.add_static("/static", static_path)
        self.app["server"] = self

        self._add_routes()

        # middlewares
        self.app.middlewares.append(aiohttp_error_handler)

        self._hooked = True

    def _add_routes(self) -> None:
        prefix = self.config.log_prefix
        self.app.router.add_route("HEAD", "/", AIOHTTPMethodHandler)
        for path in ("/", prefix + "/{key}", prefix + "/raw/{key}"):
            self.app.router.add_route("GET", path, AIOHTTPMethodHandler)

    async def _setup(self) -> None:
        self.runner = web.AppRunner(self.app, handle_signals=True)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.config.host, self.config.port)
        await self.site.start()

    async def _stop(self) -> None:
        """
        Stops the logviewer server.
        """
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    def info(self) -> str:
        main_deps = (
            f"Web application: aiohttp v{aiohttp.__version__}\n"
            f"Template renderer: jinja2 v{jinja2.__version__}\n"
        )

        return main_deps

    async def render_raw_logs(self, request: AIOHTTPRequest, key: str) -> AIOHTTPResponse:
        """Returns the plain text rendered log entry"""
        logs = self.bot.api.logs
        document: RawPayload = await logs.find_one({"key": key})
        if not document:
            return await self.raise_error("not_found", message=f"Log entry '{key}' not found.")

        log_entry = LogEntry(document)
        return AIOHTTPResponse(
            status=200,
            text=log_entry.plain_text(),
            content_type="text/plain",
            charset="utf-8",
        )

    @staticmethod
    async def send_template(template: Template, status: int = 200, **kwargs) -> AIOHTTPResponse:
        response = AIOHTTPResponse(
            status=status,
            content_type="text/html",
            charset="utf-8",
        )
        response.text = template

        return response

    @staticmethod
    async def raise_error(error_type: str, *, message: Optional[str] = None, **kwargs) -> None:
        exc_mapping = {
            "not_found": web.HTTPNotFound,
            "error": web.HTTPInternalServerError,
        }
        try:
            ret = exc_mapping[error_type]
        except KeyError:
            ret = web.HTTPInternalServerError
        if "status_code" in kwargs:
            status = kwargs.pop("status_code")
            kwargs["status"] = status

        if message is None:
            message = "No error message."
        raise ret(reason=message, **kwargs)
