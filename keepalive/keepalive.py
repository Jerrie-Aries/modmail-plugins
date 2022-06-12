from __future__ import annotations

import asyncio
import os
import signal

from typing import Optional, TYPE_CHECKING

import discord
from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel

from .client import UptimeRobotAPIClient
from .web_server import KeepAliveServer


if TYPE_CHECKING:
    from bot import ModmailBot


logger = getLogger(__name__)


class KeepAlive(commands.Cog, name="Keep Alive"):
    """
    A tool to help Modmail bot stays alive when hosting on `Replit`.

    This plugin will create a simple HTTP web server on `Replit` to handle HTTP requests.

    __**Note:**__
    - You must also set up a monitor on [UptimeRobot](https://uptimerobot.com/) to send HTTP request to the web server created by this plugin.
    Read the [Keep Alive plugin wiki](https://github.com/Jerrie-Aries/modmail-plugins/wiki/Keep-Alive-plugin-guide) for more info.
    """

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        -----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.keep_alive: Optional[KeepAliveServer] = None
        self.uptimerobot_client: Optional[UptimeRobotAPIClient] = None

        slug = os.environ.get("REPL_SLUG")
        self.using_replit: bool = slug is not None
        if self.using_replit:
            self.keep_alive = KeepAliveServer(slug, os.environ.get("REPL_OWNER"))
            self.keep_alive.run()
            self._set_signal_handlers()

            api_key = os.environ.get("UPTIMEROBOT_API_KEY")
            if api_key:
                self.uptimerobot_client = UptimeRobotAPIClient(self, api_key=api_key)
                asyncio.create_task(self.uptimerobot_client.check_monitor())
            else:
                logger.error("UPTIMEROBOT_API_KEY is not set.")

    def cog_unload(self) -> None:
        self._shutdown_keep_alive()

    def _set_signal_handlers(self) -> None:
        """
        An internal method to set the signal handlers to terminate the bot and web server.
        """

        logger.debug("Setting up signal handlers.")

        def stop_callback(*_args):
            self._shutdown_keep_alive()

            if not self.bot.is_closed():
                raise SystemExit

        for attr in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, attr, None)
            if sig is None:
                continue
            signal.signal(sig, stop_callback)

    def _shutdown_keep_alive(self) -> None:
        if self.keep_alive:
            self.keep_alive.shutdown()
        # reset the attribute
        self.keep_alive = None

    @commands.group(name="keepalive", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def keepalive_group(self, ctx: commands.Context):
        """
        Keep alive server tools.
        """
        await ctx.send_help(ctx.command)

    @keepalive_group.command(name="info")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def keepalive_info(self, ctx: commands.Context):
        """
        Shows keep alive information.
        """
        embed = discord.Embed(title="Keep alive status")
        if not self.using_replit:
            raise commands.BadArgument(
                "Not running since this bot is not hosted on `Replit`."
            )

        status = "Running" if self.keep_alive is not None else "Not running"
        embed.color = self.bot.main_color
        embed.description = status
        embed.add_field(name="URL", value=self.keep_alive.url)
        http_server = self.keep_alive.http_server
        embed.add_field(name="Raw name", value=http_server.server_name)
        embed.add_field(name="Port", value=http_server.server_port)
        await ctx.send(embed=embed)

    @commands.group(name="uptimerobot", aliases=["uprob"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def uptimerobot_group(self, ctx: commands.Context):
        """
        UptimeRobot tools.
        """
        await ctx.send_help(ctx.command)

    @uptimerobot_group.command(name="info")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def uptimerobot_info(
        self, ctx: commands.Context, option: Optional[str] = None
    ):
        """
        Shows the UptimeRobot monitor information.

        `option` can be `refresh` or `fetch` of you want to update the information from API.
        """
        if self.uptimerobot_client is None:
            raise commands.BadArgument(
                "UptimeRobot service is not set due to missing API key."
            )
        monitor = self.uptimerobot_client.monitor
        if monitor is None:
            raise commands.BadArgument("UptimeRobot monitor is not set.")

        if option and option.lower() in ("fetch", "refresh"):
            await monitor.refresh()

        embed = discord.Embed(title="UptimeRobot", color=self.bot.main_color)
        embed.add_field(name="ID", value=str(monitor.id))
        embed.add_field(name="Name", value=monitor.friendly_name)
        embed.add_field(name="URL", value=monitor.url)
        embed.add_field(name="Type", value=monitor.type)
        embed.add_field(name="Interval", value=str(monitor.interval))
        embed.add_field(name="Status", value=monitor.status)

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(KeepAlive(bot))
