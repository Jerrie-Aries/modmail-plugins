from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sys

from pathlib import Path
from site import USER_SITE
from subprocess import PIPE
from typing import Optional, Tuple, TYPE_CHECKING

import discord
from discord.ext import commands

try:
    from discord.ext import modmail_utils
except ImportError:
    # resolve when loading the cog
    modmail_utils = None

from core import checks
from core.models import getLogger, PermissionLevel


if TYPE_CHECKING:
    from bot import ModmailBot


logger = getLogger(__name__)

current_dir = Path(__file__).parent.resolve()
info_json = current_dir / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


def version_tuple(version_string: str) -> Tuple[int]:
    return tuple(int(i) for i in version_string.split("."))


def _additional_tasks() -> None:
    # additional tasks to run when debugging
    pass


class ExtendedUtils(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self.package_path: Path = current_dir
        self.package_name: str = "modmail-utils"

    async def cog_load(self) -> None:
        global modmail_utils

        mode = os.environ.get("UTILS_PACKAGE_MODE", "production")
        if mode.lower() == "development":
            # for developers usage
            # make sure the package was installed before running the script
            return

        if modmail_utils is None or not self._is_latest():
            operation = "Installing" if modmail_utils is None else "Updating"
            logger.debug("%s requirements for %s.", operation, __plugin_name__)
            await self.install_packages()

            do_reload = modmail_utils is not None

            from discord.ext import modmail_utils

            if do_reload:
                importlib.reload(modmail_utils)

            _additional_tasks()

    def _is_latest(self) -> bool:
        current = version_tuple(modmail_utils.__version__)
        latest = version_tuple(self.version_from_source_dir())
        if latest > current:
            return False
        return True

    async def install_packages(self) -> None:
        """
        Install additional packages. Currently we only use `modmail-utils` custom package.
        This method was adapted from cogs/plugins.py.
        """
        req = self.package_path
        venv = hasattr(sys, "real_prefix") or hasattr(sys, "base_prefix")  # in a virtual env
        user_install = " --user" if not venv else ""
        proc = await asyncio.create_subprocess_shell(
            f'"{sys.executable}" -m pip install --upgrade{user_install} {req} -q -q',
            stderr=PIPE,
            stdout=PIPE,
        )

        logger.debug("Installing `%s`.", req)

        stdout, stderr = await proc.communicate()

        if stdout:
            logger.debug("[stdout]\n%s.", stdout.decode())

        if stderr:
            logger.debug("[stderr]\n%s.", stderr.decode())
            logger.error(
                "Failed to install `%s`.",
                req,
                exc_info=True,
            )
            raise RuntimeError(f"Unable to install requirements: ```\n{stderr.decode()}\n```")

        if os.path.exists(USER_SITE):
            sys.path.insert(0, USER_SITE)

    def version_from_source_dir(self) -> Optional[str]:
        """
        Get latest version string from the source directory.
        """
        file_path = self.package_path / "discord/ext/modmail_utils/__init__.py"
        with open(file_path) as f:
            text = f.read()
        return re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', text, re.MULTILINE).group(1)

    @commands.group(name="ext-utils", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ext_utils(self, ctx: commands.Context):
        """
        Extended Utils base command.
        """
        await ctx.send_help(ctx.command)

    @ext_utils.command(name="info")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def utils_info(self, ctx: commands.Context):
        """
        Information and version of current additional packages used.
        """
        embed = discord.Embed(title="Utils", color=self.bot.main_color)
        description = "__**Additional packages:**__\n"
        if modmail_utils is not None:
            description += f"- `{self.package_name}`: `v{modmail_utils.__version__}`\n"
        else:
            description += f"- `{self.package_name}`: Not installed.\n"
        latest = self.version_from_source_dir()
        if latest is None:
            description += "Failed to parse latest version.\n"
        else:
            description += f"Latest from source: `v{latest}`"
        embed.description = description
        embed.set_footer(text=f"{__plugin_name__}: v{__version__}")
        await ctx.send(embed=embed)

    @ext_utils.command(name="update")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def utils_update(self, ctx: commands.Context):
        """
        Update the `modmail-utils` package.
        """
        current = version_tuple(modmail_utils.__version__)
        latest = self.version_from_source_dir()
        if latest is None:
            raise commands.BadArgument("Failed to parse latest version.")
        latest = version_tuple(latest)
        if current >= latest:
            raise commands.BadArgument(
                f"`{self.package_name}` is up to date with latest version: `v{'.'.join(str(i) for i in current)}`."
            )
        embed = discord.Embed(color=self.bot.main_color)
        embed.description = f"Updating `{self.package_name}`..."
        msg = await ctx.send(embed=embed)

        async with ctx.typing():
            try:
                await self.install_packages()
            except Exception as exc:
                description = f"{type(exc).__name__}: Failed to install. Check console for error."
            else:
                description = (
                    f"Successfully update `{self.package_name}` to `v{'.'.join(str(i) for i in latest)}`."
                )
        embed.description = description
        await msg.edit(embed=embed)

    @ext_utils.command(name="reorder", hidden=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def utils_reorder(self, ctx: commands.Context):
        """
        Reorder the plugins loading order.
        Generally no need to run this command, but put here just in case.
        This is just to make sure the plugins that require this plugin will load last or after this plugin is loaded.
        """
        plugins_cog = self.bot.get_cog("Plugins")
        ordered = []
        for plugin in plugins_cog.loaded_plugins:
            try:
                extension = self.bot.extensions[plugin.ext_string]
                if not hasattr(extension, "__plugin_info__"):
                    continue
                cogs_required = (
                    getattr(extension, "__cogs_required__", None)
                    or extension.__plugin_info__["cogs_required"]
                )
            except (AttributeError, KeyError):
                continue

            if self.qualified_name not in cogs_required:
                continue

            if str(plugin) in self.bot.config["plugins"]:
                # just remove and append it back
                self.bot.config["plugins"].remove(str(plugin))
                self.bot.config["plugins"].append(str(plugin))
                ordered.append(str(plugin))

        embed = discord.Embed(color=self.bot.main_color)
        if ordered:
            await self.bot.config.update()
            description = "Reordered the plugins.\n"
            description += "```\n"
            description += "\n".join(ordered)
            description += "\n```"
        else:
            description = "Nothing changed."
        embed.description = description
        await ctx.send(embed=embed)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(ExtendedUtils(bot))
