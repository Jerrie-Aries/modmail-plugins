from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import discord

from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel

from .core.models import AnnouncementModel
from .core.views import AnnouncementView


if TYPE_CHECKING:
    from bot import ModmailBot


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


logger = getLogger(__name__)


class Announcement(commands.Cog):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce(self, ctx: commands.Context):
        """
        Base command to create announcements.
        """
        await ctx.send_help(ctx.command)

    @announce.command(name="start", aliases=["create"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce_start(self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None):
        """
        Post an announcement in a channel specified.

        This will initiate a announcement creation panel where you can choose and customise the output of the announcement.

        `channel` if specified may be a channel ID, mention, or name. Otherwise, fallbacks to current channel.
        """
        if channel is None:
            channel = ctx.channel
        announcement = AnnouncementModel(ctx, channel)
        view = AnnouncementView(ctx, announcement)
        embed = discord.Embed(title="Announcement Creation Panel")
        embed.description = (
            "Choose a type of announcement using the dropdown menu below.\n\n"
            "__**Available types:**__\n"
            "`Normal` - Plain text announcement.\n"
            "`Embed` - Embedded announcement. Image and thumbnail image are also supported."
        )
        embed.set_footer(text="This panel will timeout after 10 minutes.")
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()

    @announce.command(name="quick")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce_quick(self, ctx: commands.Context, channel: discord.TextChannel, *, content: str):
        """
        Create a quick plain text announcement.

        `channel` may be a channel ID, mention, or name.
        """
        await channel.send(content)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Announcement(bot))
