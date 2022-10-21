from __future__ import annotations

import asyncio
import json
import os
import sys

from pathlib import Path
from site import USER_SITE
from subprocess import PIPE
from typing import Any, Dict, TYPE_CHECKING

import discord
from discord.ext import commands

try:
    from discord.ext import modmail_utils
except ImportError:
    modmail_utils = None

from core import checks
from core.models import getLogger, PermissionLevel


if TYPE_CHECKING:
    from bot import ModmailBot


logger = getLogger(__name__)

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)
__requirements__ = __plugin_info__["requirements"]


class ExtendedUtils(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot

    async def cog_load(self) -> None:
        if modmail_utils is None:
            logger.debug("Downloading requirements for %s.", __plugin_name__)
            await self.install_packages()

    async def cog_unload(self) -> None:
        pass

    async def install_packages(self) -> None:
        for req in __requirements__:
            venv = hasattr(sys, "real_prefix") or hasattr(sys, "base_prefix")  # in a virtual env
            user_install = " --user" if not venv else ""
            proc = await asyncio.create_subprocess_shell(
                f'"{sys.executable}" -m pip install --upgrade{user_install} {req} -q -q',
                stderr=PIPE,
                stdout=PIPE,
            )

            logger.debug("Downloading `%s`.", req)

            stdout, stderr = await proc.communicate()

            if stdout:
                logger.debug("[stdout]\n%s.", stdout.decode())

            if stderr:
                logger.debug("[stderr]\n%s.", stderr.decode())
                logger.error(
                    "Failed to download `%s`.",
                    req,
                    exc_info=True,
                )
                raise RuntimeError(f"Unable to download requirements: ```\n{stderr.decode()}\n```")

            if os.path.exists(USER_SITE):
                sys.path.insert(0, USER_SITE)

    @commands.command(name="ext-utils", hidden=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ext_utils(self, ctx: commands.Context):
        """Extended utils."""
        embed = discord.Embed(title="Utils", color=self.bot.main_color)
        elems = ["chat_formatting", "config", "timeutils", "views"]
        description = ""
        for elem in elems:
            attr = getattr(self, elem)
            description += f"__**{elem.replace('_', ' ').capitalize()}:**__\n"
            description += "\n".join(f"`{e}`" for e in attr)
            description += "\n\n"
        embed.description = description
        embed.set_footer(text=f"Version: {__version__}")
        await ctx.send(embed=embed)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(ExtendedUtils(bot))
