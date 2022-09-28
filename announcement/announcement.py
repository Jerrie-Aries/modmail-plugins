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

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce(self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None):
        """
        Post an announcement in channel specified.
        """
        if channel is None:
            channel = ctx.channel
        announcement = AnnouncementModel(ctx, channel)
        view = AnnouncementView(ctx, announcement)
        view.message = await ctx.send("Announcement creation panel.", view=view)
        await view.wait()


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Announcement(bot))
