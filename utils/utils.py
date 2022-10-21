from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from pathlib import Path
from site import USER_SITE
from subprocess import PIPE
from typing import Any, Dict, Optional, TYPE_CHECKING

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


def version_tuple(version_string: str) -> Tuple[int]:
    return tuple(int(i) for i in version_string.split("."))


class ExtendedUtils(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        # TODO: Update to master
        self.branch: str = "master"
        self.raw_version_url: str = "https://raw.githubusercontent.com/Jerrie-Aries/modmail-plugins/{}/discord/ext/modmail_utils/__init__.py"

    async def cog_load(self) -> None:
        global modmail_utils
        if not await self._is_latest():
            logger.debug("Downloading requirements for %s.", __plugin_name__)
            await self.install_packages()

            from discord.ext import modmail_utils

    async def cog_unload(self) -> None:
        pass

    async def _is_latest(self) -> bool:
        if modmail_utils is None:
            return False
        current = version_tuple(modmail_utils.__version__)
        latest = version_tuple(await self.fetch_latest_version_string())
        if latest > current:
            return False
        return True

    async def install_packages(self, branch: Optional[str] = None) -> None:
        """
        Install additonal packages.
        This method was adapted from cogs/plugins.py.
        """
        req = __requirements__[0]
        req += f"@{branch}" if branch is not None else f"@{self.branch}"
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

    async def fetch_latest_version_string(self, branch: Optional[str] = None) -> Optional[str]:
        """
        Fetch latest version string from Github.
        """
        url = self.raw_version_url.format(branch if branch else self.branch)
        try:
            text = await self.bot.api.request(url)
        except Exception as exc:
            logger.error(f"{type(exc).__name__}: {str(exc)}")
            return None
        return re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', text, re.MULTILINE).group(1)

    @commands.group(name="ext-utils", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ext_utils(self, ctx: commands.Context):
        """
        Extended utils. Show information of current additional packages used.
        """
        embed = discord.Embed(title="Utils", color=self.bot.main_color)
        description = "__**Additional packages:**__\n"
        if modmail_utils is not None:
            description += f"- `modmail-utils`: `v{modmail_utils.__version__}`\n"
        else:
            description += "- `modmail-utils`: Not installed.\n"
        latest = await self.fetch_latest_version_string()
        if latest is None:
            description += "Failed to fetch latest version.\n"
        else:
            description += f"Latest version: `v{latest}`"
        embed.description = description
        embed.set_footer(text=f"Version: {__version__}")
        await ctx.send(embed=embed)

    @ext_utils.group(name="package", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def utils_package(self, ctx: commands.Context):
        """
        Base package manager.
        """
        await ctx.send_help(ctx.command)

    @utils_package.command(name="update")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def package_update(self, ctx: commands.Context, *, branch: Optional[str] = None):
        """
        Update the `modmail-utils` package.
        """
        current = version_tuple(modmail_utils.__version__)
        latest = await self.fetch_latest_version_string(branch)
        if latest is None:
            raise commands.BadArgument("Failed to fetch latest version.")
        latest = version_tuple(latest)
        if current >= latest:
            raise commands.BadArgument(
                f"`modmail-utils` is up to date with latest version: `v{'.'.join(str(i) for i in current)}`"
            )
        embed = discord.Embed(color=self.bot.main_color)
        embed.description = "Updating `modmail-utils`"
        msg = await ctx.send(embed=embed)
        try:
            await self.install_packages(branch)
        except Exception as exc:
            description = "Failed to download. Check console for error."
        else:
            description = f"Successfully update `modmail-utils` to `v{'.'.join(str(i) for i in latest)}`"
        embed.description = description
        await msg.edit(embed=embed)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(ExtendedUtils(bot))
