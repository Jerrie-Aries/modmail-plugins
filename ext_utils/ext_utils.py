from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel

# <!-- Developer -->
from .core.chat_formatting import (
    bold,
    code_block,
    cleanup_code,
    days,
    escape,
    escape_code_block,
    human_join,
    normalize_smartquotes,
    plural,
    text_to_file,
)
from .core.timeutils import human_timedelta
from .core.views import ConfirmView

# <!-- ----- -->


if TYPE_CHECKING:
    from bot import ModmailBot

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    info = json.loads(f.read())

__plugin_name__ = info["name"]
__version__ = info["version"]
__description__ = "\n".join(info["description"]).format(__version__)


class Utils(commands.Cog):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self.bold = bold
        self.code_block = code_block
        self.cleanup_code = cleanup_code
        self.days = days
        self.escape = escape
        self.escape_code_block = escape_code_block
        self.human_join = human_join
        self.normalize_smartquotes = normalize_smartquotes
        self.plural = plural
        self.text_to_file = text_to_file
        self.human_timedelta = human_timedelta
        self.confirmview = ConfirmView

    async def cog_load(self) -> None:
        pass

    async def cog_unload(self) -> None:
        pass

    @commands.command()
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ext_utils(self, ctx: commands.Context):
        """Extended utils."""
        embed = discord.Embed(color=self.bot.main_color)
        embed.description(f"Version: `{__version__}`")
        await ctx.send(embed=embed)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Utils(bot))
