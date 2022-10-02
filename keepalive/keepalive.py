from __future__ import annotations

import json
import os

from pathlib import Path
from typing import Optional, TYPE_CHECKING

import discord
from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel

from .core.ur_client import UptimeRobotAPIClient
from .core.web_server import KeepAliveServer


if TYPE_CHECKING:
    from bot import ModmailBot

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__plugin_info__["wiki"], __version__)

logger = getLogger(__name__)


class KeepAlive(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

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
        self.repl_slug: Optional[str] = os.environ.get("REPL_SLUG")
        self.repl_owner: Optional[str] = os.environ.get("REPL_OWNER")
        self.using_replit: bool = self.repl_slug is not None

    async def cog_load(self) -> None:
        if self.using_replit:
            self.keep_alive = KeepAliveServer(self.repl_slug, self.repl_owner)
            await self.keep_alive.start()

            api_key = os.environ.get("UPTIMEROBOT_API_KEY")
            if api_key:
                self.uptimerobot_client = UptimeRobotAPIClient(self, api_key=api_key)
                await self.uptimerobot_client.check_monitor()
            else:
                logger.error("UPTIMEROBOT_API_KEY is not set.")

    async def cog_unload(self) -> None:
        await self._shutdown_keep_alive()

    async def _shutdown_keep_alive(self) -> None:
        if self.keep_alive.is_running():
            await self.keep_alive.stop()

    @commands.group(name="keepalive", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def keepalive_group(self, ctx: commands.Context):
        """
        Keep alive server tools.
        """
        await ctx.send_help(ctx.command)

    @keepalive_group.command(name="start")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ka_start(self, ctx: commands.Context):
        """
        Starts the keep alive server.
        """
        if not self.using_replit:
            raise commands.BadArgument("Keep alive server can only be ran on Replit.")
        if self.keep_alive.is_running():
            raise commands.BadArgument("Keep alive server is already running.")

        await self.keep_alive.start()
        embed = discord.Embed(
            title="Start",
            color=self.bot.main_color,
            description="Keep alive server is now running.",
        )
        await ctx.send(embed=embed)

    @keepalive_group.command(name="stop")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ka_stop(self, ctx: commands.Context):
        """
        Stops the keep alive server.
        """
        if not self.keep_alive.is_running():
            raise commands.BadArgument("Keep alive server is not running.")
        await self._shutdown_keep_alive()
        embed = discord.Embed(
            title="Stop", color=self.bot.main_color, description="Keep alive server is now stopped."
        )
        await ctx.send(embed=embed)

    @keepalive_group.command(name="info")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def keepalive_info(self, ctx: commands.Context):
        """
        Shows keep alive information.
        """
        embed = discord.Embed(title="Keep alive status")
        if not self.using_replit:
            raise commands.BadArgument("Not running since this bot is not hosted on `Replit`.")

        status = "Running" if self.keep_alive is not None else "Not running"
        embed.color = self.bot.main_color
        embed.description = status
        embed.add_field(name="URL", value=self.keep_alive.url)
        server = self.keep_alive.server
        embed.add_field(name="Server class", value=f"`{str(type(server)).strip('<>')}`")
        embed.set_footer(text=f"Version: v{__version__}")
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
    async def uptimerobot_info(self, ctx: commands.Context, option: Optional[str] = None):
        """
        Shows the UptimeRobot monitor information.

        `option` can be `refresh` or `fetch`, this will fetch the latest monitor information from API.
        """
        if self.uptimerobot_client is None:
            raise commands.BadArgument("UptimeRobot service is not set due to missing API key.")
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


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(KeepAlive(bot))
