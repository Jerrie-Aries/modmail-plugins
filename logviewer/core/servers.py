from __future__ import annotations

import os
import re

from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import aiohttp
import discord
import jinja2

from aiohttp import web
from aiohttp.web import Application, Request, Response
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
        self.port = int(os.getenv("PORT", 8000))


class LogviewerServer:
    """
    Main class to handle the log viewer server.
    """

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self.config: Config = Config()

        self.app: Application = MISSING
        self.site: web.TCPSite = MISSING
        self.runner: web.AppRunner = MISSING
        self._hooked: bool = False
        self._running: bool = False

    def init_hook(self) -> None:
        """
        Initial setup to start the server.
        """
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

    async def start(self) -> None:
        """
        Starts the log viewer server.
        """
        if self._running:
            raise RuntimeError("Log viewer server is already running.")
        if not self._hooked:
            self.init_hook()
        logger.info("Starting log viewer server.")
        self.runner = web.AppRunner(self.app, handle_signals=True)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.config.host, self.config.port)
        await self.site.start()
        favicon_path = static_path / "favicon.webp"
        if not favicon_path.exists():
            asset = self.bot.user.display_avatar.replace(size=32, format="webp")
            try:
                await asset.save(favicon_path)
            except discord.NotFound as exc:
                logger.error("Unable to set 'favicon.webp' due to download failure.")
                logger.error(f"{type(exc).__name__}: {str(exc)}")
        self._running = True

    async def stop(self) -> None:
        """
        Stops the log viewer server.
        """
        logger.warning(" - Shutting down web server. - ")
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        self._running = False

    def is_running(self) -> bool:
        """Returns `True` if the server is currently running."""
        return self._running

    def info(self) -> str:
        """Returns modules used to run the web server."""
        main_deps = (
            f"Web application: aiohttp v{aiohttp.__version__}\n"
            f"Template renderer: jinja2 v{jinja2.__version__}\n"
        )

        return main_deps

    async def process_logs(self, request: Request, *, path: str, key: str) -> Response:
        """
        Matches the request path with regex before rendering the logs template to user.
        """
        path_re = re.compile(rf"^{self.config.log_prefix}/(?:(?P<raw>raw)/)?(?P<key>([a-zA-Z]|[0-9])+)")
        match = path_re.match(path)
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
        request: Request,
        key: str,
    ) -> Response:
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
        """
        logs = self.bot.api.logs
        document: RawPayload = await logs.find_one({"key": key})
        if not document:
            return await self.raise_error("not_found", message=f"Log entry '{key}' not found.")

        log_entry = LogEntry(document)
        return Response(
            status=200,
            text=log_entry.plain_text(),
            content_type="text/plain",
            charset="utf-8",
        )

    @staticmethod
    async def raise_error(error_type: str, *, message: Optional[str] = None, **kwargs) -> Any:
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

    async def render_template(
        self,
        name: str,
        request: Request,
        *args: Any,
        **kwargs: Any,
    ) -> Response:

        kwargs["app"] = request.app
        kwargs["config"] = self.config

        template = jinja_env.get_template(name + ".html")
        template = await template.render_async(*args, **kwargs)
        response = Response(
            status=200,
            content_type="text/html",
            charset="utf-8",
        )
        response.text = template
        return response
