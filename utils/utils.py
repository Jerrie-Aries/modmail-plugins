from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

import discord

from core import checks

# <!-- Developer -->
from discord.ext import commands
from core.models import PermissionLevel
from .core.chat_formatting import (
    bold,
    code_block,
    cleanup_code,
    days,
    escape,
    escape_code_block,
    escape_mentions,
    human_join,
    humanize_roles,
    humanize_members,
    inline,
    normalize_smartquotes,
    paginate,
    plural,
    text_to_file,
)
from .core.timeutils import datetime_formatter, human_timedelta, humanize_timedelta
from .core.views import ConfirmView

# <!-- ----- -->


if TYPE_CHECKING:
    from bot import ModmailBot

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


class ExtendedUtils(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot

        # store these in dictionaries
        # TODO: do research about more elegant way to deal with these
        self.chat_formatting: Dict[str, Any] = {
            "bold": bold,
            "code_block": code_block,
            "cleanup_code": cleanup_code,
            "days": days,
            "escape": escape,
            "escape_code_block": escape_code_block,
            "escape_mentions": escape_mentions,
            "human_join": human_join,
            "humanize_roles": humanize_roles,
            "humanize_members": humanize_members,
            "inline": inline,
            "normalize_smartquotes": normalize_smartquotes,
            "paginate": paginate,
            "plural": plural,
            "text_to_file": text_to_file,
        }
        self.timeutils: Dict[str, Any] = {
            "datetime_formatter": datetime_formatter,
            "human_timedelta": human_timedelta,
            "humanize_timedelta": humanize_timedelta,
        }
        self.views: Dict[str, Any] = {
            "ConfirmView": ConfirmView,
        }

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
    await bot.add_cog(ExtendedUtils(bot))
