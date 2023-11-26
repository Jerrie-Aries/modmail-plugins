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
from typing import List, Optional, Tuple, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

try:
    from discord.ext import modmail_utils
except ImportError:
    # resolve when loading the cog
    modmail_utils = None

from core import checks
from core.models import getLogger, PermissionLevel, UnseenFormatter
from core.paginator import EmbedPaginatorSession
from core.utils import normalize_alias

from .core.config import UtilsConfig


if TYPE_CHECKING:
    from .motor.motor_asyncio import AsyncIOMotorCollection
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
        self.db: AsyncIOMotorCollection = bot.api.get_plugin_partition(self)
        self.config: UtilsConfig = UtilsConfig(self, self.db)

        self.package_path: Path = current_dir
        self.package_name: str = "modmail-utils"

    async def cog_load(self) -> None:
        await self._resolve_package()
        self.bot.loop.create_task(self.initialize())

    async def _resolve_package(self) -> None:
        """
        Update `modmail_utils` package from this plugin's directory.
        """
        global modmail_utils

        valids = ("production", "development")
        mode = os.environ.get("UTILS_PACKAGE_MODE", valids[0]).lower()
        if mode == valids[1]:
            # for developers usage
            # make sure the package was installed before running the script
            # install command: pip install -e path
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

    async def initialize(self) -> None:
        await self.bot.wait_for_connected()
        await self.config.fetch()

    def _is_latest(self) -> bool:
        current = version_tuple(modmail_utils.__version__)
        latest = version_tuple(self.version_from_source_dir())
        if latest > current:
            return False
        return True

    async def install_packages(self) -> None:
        """
        Currently we only use `modmail-utils` custom package.
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
        with open(file_path, encoding="utf-8") as f:
            text = f.read()
        return re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', text, re.MULTILINE).group(1)

    @commands.group(aliases=["extutils"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def eutils(self, ctx: commands.Context):
        """
        Extended Utils base command.
        """
        await ctx.send_help(ctx.command)

    @eutils.command(name="info")
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

    @eutils.command(name="update")
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

    @eutils.command(name="reorder", hidden=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def utils_reorder(self, ctx: commands.Context):
        """
        Reorder the plugins loading order.
        Generally there is no need to run this command, but it is put here just in case.
        This is just to make sure the plugins that require this plugin will load last or after this plugin is loaded.
        """
        plugins_cog = self.bot.get_cog("Plugins")
        ordered = []
        utils_pos = False
        for plugin in plugins_cog.loaded_plugins:
            if plugin.name == "utils":
                utils_pos = True
                continue
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

            if not utils_pos and str(plugin) in self.bot.config["plugins"]:
                # just remove and append it back
                self.bot.config["plugins"].remove(str(plugin))
                self.bot.config["plugins"].append(str(plugin))
                ordered.append(str(plugin))

        embed = discord.Embed(color=self.bot.main_color)
        if ordered:
            await self.bot.config.update()
            description = "__**Reordered:**__\n"
            description += "```\n" + "\n".join(ordered) + "\n```"
            description += (
                "\n\n__**Note:**__\nYou may need to restart the bot to reload the reordered plugins."
            )
        else:
            embed.color = self.bot.error_color
            description = "The plugins are already properly ordered."
        embed.description = description
        await ctx.send(embed=embed)

    # these were adapted from cogs/utility.py
    @eutils.group(name="config", usage="[subcommand]", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def utils_config(self, ctx: commands.Context):
        """
        Modify changeable configuration.

        To set a configuration:
        - `{prefix}eutils config set config-name value`

        To get a configuration value:
        - `{prefix}eutils config get config-name`

        To remove a configuration:
        - `{prefix}eutils config remove config-name`

        To show all configurations and their informations:
        - `{prefix}eutils config help`
        """
        await ctx.send_help(ctx.command)

    @utils_config.command(name="set", aliases=["add"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def config_set(self, ctx: commands.Context, key: str.lower, *, value: str):
        """
        Set a configuration variable and its value.
        """
        if key in self.config.defaults:
            try:
                value = await self.config.resolve_conversion(ctx, key, value)
                self.config.set(key, value)
                await self.config.update()
                embed = discord.Embed(
                    title="Success",
                    color=self.bot.main_color,
                    description=f"Set `{key}` to `{self.config[key]}`.",
                )
            except commands.BadArgument as exc:
                raise commands.BadArgument(str(exc))
        else:
            embed = discord.Embed(
                title="Error",
                color=self.bot.error_color,
                description=f"`{key}` is an invalid key.",
            )
        return await ctx.send(embed=embed)

    @utils_config.command(name="get")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def config_get(self, ctx: commands.Context, *, key: str.lower = None):
        """
        Show the configuration variables that are currently set.

        Leave `key` empty to show all currently set configuration variables.
        """
        if key:
            if key in self.config.defaults:
                desc = f"`{key}` is set to `{self.config[key]}`"
                embed = discord.Embed(color=self.bot.main_color, description=desc)
                embed.set_author(name="Config variable", icon_url=self.bot.user.display_avatar.url)

            else:
                embed = discord.Embed(
                    title="Error",
                    color=self.bot.error_color,
                    description=f"`{key}` is an invalid key.",
                )
        else:
            embed = discord.Embed(
                color=self.bot.main_color,
                description="Here is a list of currently set configurations.",
            )
            embed.set_author(name="Current config:", icon_url=self.bot.user.display_avatar.url)

            for name, value in self.config.items():
                embed.add_field(name=name, value=f"`{value}`", inline=False)

        return await ctx.send(embed=embed)

    @utils_config.command(name="remove", aliases=["del", "delete"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def config_remove(self, ctx: commands.Context, *, key: str.lower):
        """
        Delete a set configuration variable.
        """
        if key in self.config.defaults:
            self.config.remove(key, restore_default=True)
            await self.config.update()
            embed = discord.Embed(
                title="Success",
                color=self.bot.main_color,
                description=f"`{key}` is now reset to default.",
            )
        else:
            embed = discord.Embed(
                title="Error",
                color=self.bot.error_color,
                description=f"`{key}` is an invalid key.",
            )
        return await ctx.send(embed=embed)

    @utils_config.command(name="help", aliases=["info"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def config_help(self, ctx: commands.Context, key: str.lower = None):
        """
        Show information on a specified configuration.

        Leave `key` unspecified to show all available config options and informations.
        """
        if key is not None and key not in self.config.defaults:
            raise commands.BadArgument(f"`{key}` is an invalid key.")

        config_info = self.config.config_info
        if key is not None and key not in config_info:
            raise commands.BadArgument(f"No help details found for `{key}`.")

        index = 0
        embeds = []

        def fmt(val: str) -> str:
            return UnseenFormatter().format(
                val,
                prefix=self.bot.prefix,
                config_set=f"{ctx.command.parent.qualified_name} set",
                ctx=ctx,
                key=current_key,
            )

        for i, (current_key, info) in enumerate(config_info.items()):
            if current_key == key:
                index = i
            embed = discord.Embed(title=f"{current_key}", color=self.bot.main_color)
            embed.add_field(name="Information:", value=info["description"], inline=False)
            if info.get("examples", []):
                example_text = ""
                for example in info["examples"]:
                    example_text += f"- {fmt(example)}\n"
                embed.add_field(name="Examples:", value=example_text, inline=False)
            # use .__get__ to retrieve raw value
            embed.add_field(name="Current value", value=f"{self.config[current_key]}")
            embeds += [embed]

        paginator = EmbedPaginatorSession(ctx, *embeds)
        paginator.current = index
        await paginator.run()

    async def get_contexts(
        self, message: discord.Message, *, cls: commands.Context = commands.Context
    ) -> List[commands.Context]:
        """
        Manually construct the context.

        Instances constructed from this will be partial and just to invoke the commands or aliases if any.
        Some attributes may not available (e.g. `.thread`). Snippets also will not be resolved.
        """
        view = StringView(message.content)
        ctx = cls(view=view, bot=self.bot, message=message)
        ctx.thread = None

        if message.author.id == self.bot.user.id:
            return [ctx]

        invoker = view.get_word().lower()

        # Check if there is any aliases being called.
        alias = self.bot.aliases.get(invoker)
        if alias is not None:
            aliases = normalize_alias(alias, message.content[len(f"{invoker}") :])
            if not aliases:
                logger.warning("Alias %s is invalid.", invoker)
                return [ctx]

            ctxs = []
            for alias in aliases:
                view = StringView(alias)
                ctx = cls(view=view, bot=self.bot, message=message)
                ctx.thread = None
                ctx.invoked_with = view.get_word().lower()
                ctx.command = self.bot.all_commands.get(ctx.invoked_with)
                ctxs += [ctx]
            return ctxs

        ctx.command = self.bot.all_commands.get(invoker)
        ctx.invoked_with = invoker
        return [ctx]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        channel_id = self.config.get("developer_channel")
        if not channel_id:
            return

        checks = [
            message.type in (discord.MessageType.default, discord.MessageType.reply),
            message.author.id in self.bot.bot_owner_ids,
            str(message.channel.id) == channel_id,
        ]
        if not all(checks):
            return
        if message.content.startswith(tuple(await self.bot.get_prefix())):
            return
        ctxs = await self.get_contexts(message)
        for ctx in ctxs:
            if ctx.command:
                await self.bot.invoke(ctx)
                continue


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(ExtendedUtils(bot))
