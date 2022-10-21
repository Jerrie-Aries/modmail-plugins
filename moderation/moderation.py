from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shlex
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import (
    Any,
    Dict,
    Optional,
    Union,
    List,
    TYPE_CHECKING,
)

import discord
from discord.ext import commands
from discord.utils import MISSING

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession
from core.time import UserFriendlyTime
from core.utils import strtobool


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)

logger = getLogger(__name__)

# <!-- Developer -->
try:
    from discord.ext.modmail_utils import ConfirmView, human_timedelta, plural
except ImportError as exc:
    required = __plugin_info__["cogs_required"][0]
    raise RuntimeError(
        f"`modmail_utils` package is required for {__plugin_name__} plugin to function.\n"
        f"Install {required} plugin to resolve this issue."
    ) from exc

from .core.config import ModConfig
from .core.converters import Arguments, ActionReason, BannedMember
from .core.errors import BanEntryNotFound
from .core.logging import ModerationLogging
from .core.utils import get_audit_reason, parse_delete_message_days


# <!-- ----- -->


def can_execute_action(ctx: commands.Context, user: discord.Member, target: discord.Member) -> bool:
    return user.id in ctx.bot.bot_owner_ids or user == ctx.guild.owner or user.top_role > target.top_role


class Moderation(commands.Cog):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.blurple: discord.Color = discord.Color.blurple()
        self.db: AsyncIOMotorCollection = MISSING  # implemented in `initialize()`
        self.config_cache: Dict[str, Any] = {}
        self.logging: ModerationLogging = ModerationLogging(self)
        self.massban_enabled: bool = strtobool(os.environ.get("MODERATION_MASSBAN_ENABLE", False))

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self.initialize())

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """
        Checks errors. Overridden from "discord.py" library.
        A special method that is called whenever an error
        is dispatched inside this cog.

        This is similar to :func:`.on_command_error` except only applying
        to the commands inside this cog.

        However please note that the :meth:`on_command_error` in :class:`ModmailBot` will still be executed.

        This **must** be a coroutine.

        Parameters
        -----------
        ctx: commands.Context
            The invocation context where the error happened.
        error: commands.CommandError
            The error that happened.
        """
        error = getattr(error, "original", error)
        handled = False
        if isinstance(error, BanEntryNotFound):
            handled = True
            embed = discord.Embed(color=self.bot.error_color, description=str(error))
            await ctx.send(embed=embed)

        elif isinstance(error, commands.CheckFailure):
            handled = True
            logger.warning("CheckFailure: %s", error)
            for check in ctx.command.checks:
                if asyncio.iscoroutinefunction(check):
                    checked = await check(ctx)
                else:
                    checked = check(ctx)
                embed = discord.Embed(color=self.bot.error_color)
                if not checked and hasattr(check, "permission_level"):
                    correct_permission_level = self.bot.command_perm(ctx.command.qualified_name)
                    if correct_permission_level == PermissionLevel.OWNER:
                        continue  # skip if level OWNER
                    embed.description = (
                        f"You need permission level `{correct_permission_level.name}` to run this command!"
                    )
                    return await ctx.send(embed=embed)
                elif hasattr(check, "fail_msg"):
                    embed.description = check.fail_msg
                    return await ctx.send(embed=embed)

        # else let the handler in 'bot.py' deal with it
        if not handled:
            await self.bot.on_command_error(ctx, error, unhandled_by_cog=True)

    async def initialize(self) -> None:
        """
        Initial tasks when loading the cog.
        """
        await self.bot.wait_for_connected()

        if self.db is MISSING:
            self.db = self.bot.api.get_plugin_partition(self)

        await self.populate_cache()

    async def populate_cache(self) -> None:
        """
        Sets up database and populates the config cache with the data from the database.
        """
        from_db = await self.db.find_one({"_id": "config"})
        if from_db is None:
            from_db = {}  # empty dict so we can use `.get` without error

        for guild in self.bot.guilds:
            db_config = from_db.get(str(guild.id))
            if db_config:
                config = ModConfig(self, self.db, guild, data=db_config)
            else:
                config = ModConfig(self, self.db, guild, data={})

            self.config_cache[str(guild.id)] = config

    def guild_config(self, guild_id: str) -> ModConfig:
        config = self.config_cache.get(guild_id)
        if config is None:
            guild = self.bot.get_guild(int(guild_id))
            default = ModConfig(self.bot, self.db, guild, data={})
            self.config_cache[guild_id] = default
            config = default
        return config

    # Logging
    @commands.group(name="logging", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logging_group(self, ctx: commands.Context):
        """
        Logging feature for Moderation actions.

        __**Support actions:**__
        - `ban`/`unban`
        - `kick`
        - Timeout, `mute`/`unmute`
        - Member roles update, `add`/`remove`
        - Nickname changes, `set`/`update`/`remove`
        - Channels, `created`/`deleted`

        For initial setup, set the logging channel and enable the logging.
        Use commands:
        - `{prefix}logging config channel #channel`
        - `{prefix}logging config enable true`
        """
        await ctx.send_help(ctx.command)

    @logging_group.group(name="config", usage="<command> [argument]", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logging_config(self, ctx: commands.Context):
        """
        Moderation logging configuration.

        Run this command without argument to see the current set configurations.
        """
        config = self.guild_config(str(ctx.guild.id))
        embed = discord.Embed(
            title="Logging Config",
            color=self.bot.main_color,
        )
        for key, value in config.items():
            embed.add_field(name=key.replace("_", " ").capitalize(), value=f"`{value}`")
        await ctx.send(embed=embed)

    @logging_config.command(name="channel")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logging_channel(self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None):
        """
        Sets the logging channel.

        `channel` may be a channel ID, mention, or name.

        Leave `channel` empty to see the current set channel.
        """
        config = self.guild_config(str(ctx.guild.id))
        if channel is None:
            channel = self.bot.get_channel(int(config.get("log_channel")))
            if channel:
                description = f"Current moderation logging channel is {channel.mention}."
            else:
                description = "Moderation logging channel is not set."
        else:
            config.set("log_channel", str(channel.id))
            config.remove("webhook")
            config.webhook = MISSING
            description = f"Log channel is now set to {channel.mention}."
            await config.update()

        embed = discord.Embed(description=description, color=self.bot.main_color)
        await ctx.send(embed=embed)

    @logging_config.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logging_enable(self, ctx: commands.Context, *, mode: Optional[bool] = None):
        """
        Enable or disable moderation logging feature.

        `mode` is a boolean value, may be `True` or `False` (case insensitive).

        Leave `mode` empty to see the current set value.
        """
        config = self.guild_config(str(ctx.guild.id))
        if mode is None:
            mode = config.get("logging")
            description = "Logging feature is currently " + ("`enabled`" if mode else "`disabled`") + "."
        else:
            config.set("logging", mode)
            description = ("Enabled " if mode else "Disabled ") + "the logging for moderation actions."
            await config.update()

        embed = discord.Embed(description=description, color=self.bot.main_color)
        await ctx.send(embed=embed)

    @logging_config.command(name="clear", aliases=["reset"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logging_clear(self, ctx: commands.Context):
        """
        Reset the moderation logging configurations to default.
        """
        config = self.guild_config(str(ctx.guild.id))
        for key in config.keys():
            config.remove(key)
        config.webhook = MISSING
        await config.update()

        embed = discord.Embed(
            color=self.bot.main_color, description="Moderation logging configurations are now cleared."
        )
        await ctx.send(embed=embed)

    # Mute commands
    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def mute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: UserFriendlyTime,
        *,
        reason: ActionReason = None,
    ):
        """
        Mutes the specified member.

        `member` may be a member ID, mention, or name.
        `duration` may be a simple "human-readable" time text and without space.

        Examples:
        - `2m` or `2minutes` = 2 minutes
        - `6h` or `6hours` = 6 hours
        - `12h30m` = 12 hours 30 minutes

        `reason` is optional.
        """
        if not can_execute_action(ctx, ctx.author, member):
            raise commands.BadArgument("You cannot do this action on this user due to role hierarchy.")
        if not ctx.me.top_role > member.top_role:
            raise commands.BadArgument("This user is higher than me in role hierarchy.")
        dur_ts = duration.dt.timestamp() - duration.now.timestamp()
        if dur_ts <= 0:
            raise commands.BadArgument("Unable to parse the `duration` properly. Please try again.")
        if dur_ts >= (3600 * 24 * 28):
            raise commands.BadArgument("Duration must be less than 28 days.")

        if member.is_timed_out():
            raise commands.BadArgument(
                f"Member is already muted and will be unmuted in {human_timedelta(member.timed_out_until)}."
            )

        if reason is None:
            reason = "No reason was provided."

        human_delta = human_timedelta(duration.dt)

        await member.timeout(duration.dt, reason=get_audit_reason(ctx.author, reason))

        await ctx.send(
            embed=discord.Embed(
                title="Success",
                description=f"`{member}` is now muted for **{human_delta}**.",
                color=self.bot.main_color,
            ).add_field(name="Reason", value=reason)
        )

        await self.logging.send_log(
            guild=ctx.guild,
            action=ctx.command.name,
            duration=human_delta,
            target=member,
            moderator=ctx.author,
            reason=reason,
            description=f"`{member}` has been muted for **{human_delta}**.",
        )

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def unmute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: ActionReason = None,
    ):
        """
        Unmutes the specified member.

        `member` may be a member ID, mention, or name.
        `reason` is optional.
        """
        if not can_execute_action(ctx, ctx.author, member):
            raise commands.BadArgument("You cannot do this action on this user due to role hierarchy.")
        if not ctx.me.top_role > member.top_role:
            raise commands.BadArgument("This user is higher than me in role hierarchy.")
        if not member.is_timed_out():
            raise commands.BadArgument(f"{member} is not muted.")

        if reason is None:
            reason = "No reason was provided."

        await member.timeout(None, reason=get_audit_reason(ctx.author, reason))

        await ctx.send(
            embed=discord.Embed(
                title="Success",
                description=f"`{member}` is now unmuted.",
                color=self.bot.main_color,
            )
        )
        await self.logging.send_log(
            guild=ctx.guild,
            action=ctx.command.name,
            target=member,
            moderator=ctx.author,
            reason=reason,
            description=f"`{member}` is now unmuted.",
        )

    # Warn command
    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def warn(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: ActionReason = None,
    ):
        """
        Warns the specified member.

        `member` may be a member ID, mention, or name.
        `reason` is optional.

        **Notes:**
        - A warn message will be sent to the member through DM's as well.
        """
        if not can_execute_action(ctx, ctx.author, member):
            raise commands.BadArgument("You cannot do this action on this user due to role hierarchy.")

        if reason is None:
            reason = "No reason was provided."

        dm_embed = discord.Embed(
            title="Warn",
            description=f"You have been warned by a Moderator.",
            color=self.bot.error_color,
            timestamp=discord.utils.utcnow(),
        )
        dm_embed.set_thumbnail(
            url="https://raw.githubusercontent.com/Jerrie-Aries/extras/master/icons/warn.png"
        )
        dm_embed.add_field(name="Reason", value=reason)

        dm_embed.set_footer(text=f"Server: {ctx.guild}", icon_url=ctx.guild.icon)

        try:
            await member.send(embed=dm_embed)
        except discord.errors.Forbidden:
            raise commands.BadArgument(f"I couldn't warn `{member}` on DM's since they have it disabled.")

        embed = discord.Embed(
            title="Success", color=self.bot.main_color, description=f"`{member}` has been warned in DM's."
        )
        embed.add_field(name="Reason", value=reason)
        embed.set_footer(text=f"User ID: {member.id}")
        await ctx.send(embed=embed)

        await self.logging.send_log(
            guild=ctx.guild,
            action=ctx.command.name,
            target=member,
            moderator=ctx.author,
            reason=reason,
            description=f"`{member}` has been warned.",
        )

    # Purge commands
    @commands.group(aliases=["clear"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def purge(self, ctx: commands.Context, amount: int):
        """
        Purge messages in the current channel.

        `amount` must be an integer between `1` to `100`.
        Max `amount` is `100`.

        In order for this to work, the bot must have `Manage Messages` and `Read Message History` permissions.
        These commands cannot be used in a private message.

        When the command is done doing its work, you will get a message detailing which users got removed and how many messages got removed.

        **Notes:**
        - Pinned messages will be ignored. However, if you purge using any of this command's sub-commands pinned messages also will be purged.
        - To purge messages including the pinned messages, use command `{prefix}purge all <amount>` instead.
        """
        await self.do_removal(ctx, amount, lambda e: e.pinned is False)

    @staticmethod
    async def do_removal(
        ctx: commands.Context,
        limit: int,
        predicate: Any,
        *,
        before: int = None,
        after: int = None,
    ):
        """
        A handy method to do the removal process.
        Bot permissions check also will be done in here.
        """
        error_embed = discord.Embed(color=discord.Color.red(), description="")
        perms = ctx.channel.permissions_for(ctx.me)
        if not perms.manage_messages or not perms.read_message_history:
            error_embed.description = "Need `MANAGE_MESSAGES` and `READ_MESSAGE_HISTORY` permissions."
            return await ctx.send(embed=error_embed)

        min_amount, max_amount = 1, 100
        if limit < min_amount:
            error_embed.description = f"You must purge more then {limit} message!"
            return await ctx.send(embed=error_embed)
        if limit > max_amount:
            error_embed.description = f"Too many messages to search given ({limit}/100)."
            return await ctx.send(embed=error_embed)

        if before is None:
            before = ctx.message
        else:
            before = discord.Object(id=before)

        if after is not None:
            after = discord.Object(id=after)

        # Start deleting.
        await ctx.message.delete()
        try:
            deleted = await ctx.channel.purge(limit=limit, before=before, after=after, check=predicate)
        except discord.Forbidden:
            error_embed.description = "I do not have the required permissions to delete messages."
            return await ctx.send(embed=error_embed)
        except discord.HTTPException as e:
            error_embed.description = f"Error: {e} (try a smaller search?)"
            return await ctx.send(embed=error_embed)

        spammers = Counter(m.author.display_name for m in deleted)
        deleted = len(deleted)
        messages = [f'{deleted} message{" was" if deleted == 1 else "s were"} removed.']
        if deleted:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"**{name}**: {count}" for name, count in spammers)

        to_send = "\n".join(messages)
        done_embed = discord.Embed(title="Purge", color=discord.Color.blurple())
        if len(to_send) > 2000:
            done_embed.description = f"Successfully removed {deleted} messages."
            await ctx.send(ctx.author.mention, embed=done_embed, delete_after=10)
        else:
            done_embed.description = to_send
            await ctx.send(ctx.author.mention, embed=done_embed, delete_after=10)

    @purge.command(name="all")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_all(self, ctx: commands.Context, amount: int):
        """
        Removes all types of messages.

        `amount` must be an integer between `1` to `100`.
        Max `amount` is `100`.
        """
        await self.do_removal(ctx, amount, lambda e: True)

    @purge.command(name="embeds")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_embeds(self, ctx: commands.Context, search: int = 10):
        """Removes messages that have embeds in them."""
        await self.do_removal(ctx, search, lambda e: len(e.embeds))

    @purge.command(name="files")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_files(self, ctx: commands.Context, search: int = 10):
        """Removes messages that have attachments in them."""
        await self.do_removal(ctx, search, lambda e: len(e.attachments))

    @purge.command(name="images")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_images(self, ctx: commands.Context, search: int = 10):
        """Removes messages that have embeds or attachments."""
        await self.do_removal(ctx, search, lambda e: len(e.embeds) or len(e.attachments))

    @purge.command(name="user")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_user(self, ctx: commands.Context, member: discord.Member, search: int = 10):
        """Removes all messages by the member."""
        await self.do_removal(ctx, search, lambda e: e.author == member)

    @purge.command(name="contains")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_contains(self, ctx: commands.Context, *, substr: str):
        """Removes all messages containing a substring.

        The substring must be at least 3 characters long.
        """
        if len(substr) < 3:
            raise commands.BadArgument("The substring length must be at least 3 characters.")
        else:
            await self.do_removal(ctx, 100, lambda e: substr in e.content)

    @purge.command(name="bot", aliases=["bots"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_bot(self, ctx: commands.Context, prefix: str = None, search: int = 10):
        """Removes a bot user's messages and messages with their optional prefix."""

        def predicate(m):
            return (m.webhook_id is None and m.author.bot) or (prefix and m.content.startswith(prefix))

        await self.do_removal(ctx, search, predicate)

    @purge.command(name="emoji", aliases=["emojis"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_emoji(self, ctx: commands.Context, search: int = 10):
        """Removes all messages containing custom emoji."""
        custom_emoji = re.compile(r"<a?:[a-zA-Z0-9_]+:([0-9]+)>")

        def predicate(m):
            return custom_emoji.search(m.content)

        await self.do_removal(ctx, search, predicate)

    @purge.command(name="reactions")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_reactions(self, ctx: commands.Context, search: int = 10):
        """Removes all reactions from messages that have them."""

        if search > 100:
            raise commands.BadArgument(f"Too many messages to search for ({search}/100)")

        total_reactions = 0
        async for message in ctx.history(limit=search, before=ctx.message):
            if len(message.reactions):
                total_reactions += sum(r.count for r in message.reactions)
                await message.clear_reactions()

        embed = discord.Embed(
            color=self.blurple,
            description=f"Successfully removed {total_reactions} reactions.",
        )
        await ctx.send(embed=embed)

    @purge.command(name="custom")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _remove_custom(self, ctx: commands.Context, *, args: str):
        """
        A more advanced purge command.

        This command uses a powerful "command line" syntax.
        Most options support multiple values to indicate 'any' match.
        If the value has spaces it must be quoted.

        The messages are only deleted if all options are met unless the `--or` flag is passed, in which case only if any is met.

        **The following options are valid:**
        `--user`: A mention or name of the user to remove.
        `--contains`: A substring to search for in the message.
        `--starts`: A substring to search if the message starts with.
        `--ends`: A substring to search if the message ends with.
        `--search`: How many messages to search. Default 10. Max 100.
        `--after`: Messages must come after this message ID.
        `--before`: Messages must come before this message ID.

        **Flag options (no arguments):**
        `--bot`: Check if it's a bot user.
        `--embeds`: Check if the message has embeds.
        `--files`: Check if the message has attachments.
        `--emoji`: Check if the message has custom emoji.
        `--reactions`: Check if the message has reactions
        `--or`: Use logical OR for all options.
        `--not`: Use logical NOT for all options.
        """
        parser = Arguments(add_help=False, allow_abbrev=False)
        parser.add_argument("--user", nargs="+")
        parser.add_argument("--contains", nargs="+")
        parser.add_argument("--starts", nargs="+")
        parser.add_argument("--ends", nargs="+")
        parser.add_argument("--or", action="store_true", dest="_or")
        parser.add_argument("--not", action="store_true", dest="_not")
        parser.add_argument("--emoji", action="store_true")
        parser.add_argument("--bot", action="store_const", const=lambda m: m.author.bot)
        parser.add_argument("--embeds", action="store_const", const=lambda m: len(m.embeds))
        parser.add_argument("--files", action="store_const", const=lambda m: len(m.attachments))
        parser.add_argument("--reactions", action="store_const", const=lambda m: len(m.reactions))
        parser.add_argument("--search", type=int)
        parser.add_argument("--after", type=int)
        parser.add_argument("--before", type=int)

        try:
            args = parser.parse_args(shlex.split(args))
        except Exception as e:
            raise commands.BadArgument(str(e))

        predicates = []
        if args.bot:
            predicates.append(args.bot)

        if args.embeds:
            predicates.append(args.embeds)

        if args.files:
            predicates.append(args.files)

        if args.reactions:
            predicates.append(args.reactions)

        if args.emoji:
            custom_emoji = re.compile(r"<:(\w+):(\d+)>")
            predicates.append(lambda m: custom_emoji.search(m.content))

        if args.user:
            users = []
            converter = commands.MemberConverter()
            for u in args.user:
                try:
                    user = await converter.convert(ctx, u)
                    users.append(user)
                except Exception as e:
                    raise commands.BadArgument(str(e))

            predicates.append(lambda m: m.author in users)

        if args.contains:
            predicates.append(lambda m: any(sub in m.content for sub in args.contains))

        if args.starts:
            predicates.append(lambda m: any(m.content.startswith(s) for s in args.starts))

        if args.ends:
            predicates.append(lambda m: any(m.content.endswith(s) for s in args.ends))

        # noinspection PyProtectedMember
        op = all if not args._or else any

        def predicate(m):
            r = op(p(m) for p in predicates)
            # noinspection PyProtectedMember
            if args._not:
                return not r
            return r

        if args.after:
            if args.search is None:
                args.search = 100

        if args.search is None:
            args.search = 10

        args.search = max(0, min(100, args.search))  # clamp from 0-100
        await self.do_removal(ctx, args.search, predicate, before=args.before, after=args.after)

    # Kick command
    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def kick(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: ActionReason = None,
    ):
        """
        Kicks a user from the server.

        `member` may be a member ID, mention, or name.
        `reason` is optional.

        In order for this to work, the bot must have `Kick Members` permission.
        """
        if not ctx.me.guild_permissions.kick_members:
            raise commands.BadArgument("Need `KICK_MEMBERS` permission.")
        if not can_execute_action(ctx, ctx.author, member):
            raise commands.BadArgument("You cannot do this action on this user due to role hierarchy.")

        if member.id == ctx.message.author.id:
            raise commands.BadArgument("You can't kick yourself.")

        if reason is None:
            reason = "No reason was provided."
        try:
            await member.kick(reason=get_audit_reason(ctx.author, reason))
        except discord.Forbidden:
            raise commands.BadArgument("I don't have enough permissions to kick this user.")

        embed = discord.Embed(
            title="Kick",
            description=f"{member.mention} has been kicked.",
            color=self.bot.main_color,
        )
        embed.add_field(name="Reason", value=reason)
        await ctx.send(embed=embed)
        await self.logging.send_log(
            guild=ctx.guild,
            action=ctx.command.name,
            target=member,
            moderator=ctx.author,
            reason=reason,
            description=f"`{member}` has been kicked.",
        )

    # Ban command
    @commands.group(usage="<user> [reason] [message_days]", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def ban(
        self,
        ctx: commands.Context,
        user: Union[discord.Member, discord.User, int],
        *,
        reason: ActionReason = None,
    ):
        """
        Bans a user from the server.

        `user` may be a user ID, mention, or name.
        `reason` is optional.
        `message_days` is the number of days worth of messages to delete from the user in the guild. If not specified, defaults to `0`. Max is `7`.

        In order for this to work, the bot must have `Ban Members` permission.

        **Examples:**
        - `{prefix}ban @User`
        - `{prefix}ban @User Posting server invites in DMs.`
        - `{prefix}ban @User --2`
        - `{prefix}ban @User Posting server invites in DMs. --2`

        **Notes:**
        - `message_days` if specified, must start with `--` (e.g. `--1`) for the bot to recognize the syntax.
        """
        if not ctx.me.guild_permissions.ban_members:
            raise commands.BadArgument("Need `BAN_MEMBERS` permission.")

        if not isinstance(user, (discord.Member, discord.User)) and isinstance(user, int):
            user_id = user
            try:
                user = await self.bot.get_or_fetch_user(user_id)
            except (discord.NotFound, Exception):
                raise commands.BadArgument('"{}" not found. Invalid user ID.'.format(user_id))
        elif isinstance(user, discord.Member) and not can_execute_action(ctx, ctx.author, user):
            raise commands.BadArgument("You cannot do this action on this user due to role hierarchy.")

        user: discord.User = user
        if user.id == ctx.message.author.id:
            raise commands.BadArgument("You can't ban yourself.")

        message_days = 0
        if reason is not None:
            reason, message_days = parse_delete_message_days(str(reason))

        if reason is None:
            reason = "No reason was provided."
        try:
            await ctx.guild.ban(
                user,
                reason=get_audit_reason(ctx.author, reason),
                delete_message_days=message_days,
            )
        except discord.Forbidden:
            raise commands.BadArgument("I don't have enough permissions to ban this user.")

        embed = discord.Embed(
            title="Ban",
            description=f"`{user}` has been banned.",
            color=self.bot.main_color,
        )
        embed.add_field(name="Reason", value=reason)
        await ctx.send(embed=embed)
        await self.logging.send_log(
            guild=ctx.guild,
            action=ctx.command.name,
            target=user,
            moderator=ctx.author,
            reason=reason,
            description=f"`{user}` has been banned.",
        )

    @ban.command(name="custom", aliases=["--massban"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def massban(self, ctx: commands.Context, *, args: str):
        """
        Mass bans multiple members from the server using custom syntax.

        This command has a powerful "command line" syntax.
        In order for this to work, the bot must have `Ban Members` permission.

        **Every option is optional.**

        Users are only banned **if and only if** all conditions are met.

        **The following options are valid:**
        `--channel` or `-c`: Channel to search for message history.
        `--reason` or `-r`: The reason for the ban.
        `--regex`: Regex that usernames must match.
        `--created`: Matches users whose accounts were created less than specified minutes ago.
        `--joined`: Matches users that joined less than specified minutes ago.
        `--joined-before`: Matches users who joined before the member ID given.
        `--joined-after`: Matches users who joined after the member ID given.
        `--no-avatar`: Matches users who have no avatar. (no arguments)
        `--no-roles`: Matches users that have no role. (no arguments)
        `--show`: Show members instead of banning them (no arguments).

        **Message history filters (Requires `--channel`):**
        `--contains`: A substring to search for in the message.
        `--starts`: A substring to search if the message starts with.
        `--ends`: A substring to search if the message ends with.
        `--match`: A regex to match the message content to.
        `--search`: How many messages to search. Default 100. Max 2000.
        `--after`: Messages must come after this message ID.
        `--before`: Messages must come before this message ID.
        `--files`: Checks if the message has attachments (no arguments).
        `--embeds`: Checks if the message has embeds (no arguments).

        **Notes:**
        - By default, this command is disabled due to too powerful outcome. **It will not actually ban the users.**
        It is put here only for educational purpose for you to familiarize yourself with the custom syntax.
        However if you want to enable it, set the envinronment config variable `MODERATION_MASSBAN_ENABLE` to `True`.
        **Use it at your own risk.**
        """

        # For some reason there are cases due to caching that ctx.author
        # can be a User even in a guild only context
        # Rather than trying to work out the kink with it
        # Just upgrade the member itself.
        if not isinstance(ctx.author, discord.Member):
            try:
                author = await ctx.guild.fetch_member(ctx.author.id)
            except discord.HTTPException:
                raise commands.BadArgument("Somehow, Discord does not seem to think you are in this server.")
        else:
            author = ctx.author

        parser = Arguments(add_help=False, allow_abbrev=False)
        parser.add_argument("--channel", "-c")
        parser.add_argument("--reason", "-r")
        parser.add_argument("--search", type=int, default=100)
        parser.add_argument("--regex")
        parser.add_argument("--no-avatar", action="store_true")
        parser.add_argument("--no-roles", action="store_true")
        parser.add_argument("--created", type=int)
        parser.add_argument("--joined", type=int)
        parser.add_argument("--joined-before", type=int)
        parser.add_argument("--joined-after", type=int)
        parser.add_argument("--contains")
        parser.add_argument("--starts")
        parser.add_argument("--ends")
        parser.add_argument("--match")
        parser.add_argument("--show", action="store_true")
        parser.add_argument("--embeds", action="store_const", const=lambda m: len(m.embeds))
        parser.add_argument("--files", action="store_const", const=lambda m: len(m.attachments))
        parser.add_argument("--after", type=int)
        parser.add_argument("--before", type=int)

        try:
            args = parser.parse_args(shlex.split(args))
        except Exception as e:
            raise commands.BadArgument(str(e))

        members = []

        if args.channel:
            channel = await commands.TextChannelConverter().convert(ctx, args.channel)
            before = args.before and discord.Object(id=args.before)
            after = args.after and discord.Object(id=args.after)
            predicates = []
            if args.contains:
                predicates.append(lambda m: args.contains in m.content)
            if args.starts:
                predicates.append(lambda m: m.content.startswith(args.starts))
            if args.ends:
                predicates.append(lambda m: m.content.endswith(args.ends))
            if args.match:
                try:
                    _match = re.compile(args.match)
                except re.error as e:
                    raise commands.BadArgument(f"Invalid regex passed to `--match`: {e}")
                else:
                    predicates.append(lambda m, x=_match: x.match(m.content))
            if args.embeds:
                predicates.append(args.embeds)
            if args.files:
                predicates.append(args.files)

            async for message in channel.history(
                limit=min(max(1, args.search), 2000), before=before, after=after
            ):
                if all(p(message) for p in predicates):
                    members.append(message.author)
        else:
            if ctx.guild.chunked:
                members = ctx.guild.members
            else:
                async with ctx.typing():
                    await ctx.guild.chunk(cache=True)
                members = ctx.guild.members

        # member filters
        predicates = [
            lambda m: isinstance(m, discord.Member)
            and can_execute_action(ctx, author, m),  # Only if applicable
            lambda m: not m.bot,  # No bots
            lambda m: m.discriminator != "0000",  # No deleted users
        ]

        converter = commands.MemberConverter()

        if args.regex:
            try:
                _regex = re.compile(args.regex)
            except re.error as e:
                raise commands.BadArgument(f"Invalid regex passed to `--regex`: {e}")
            else:
                predicates.append(lambda m, x=_regex: x.match(m.name))

        if args.no_avatar:
            predicates.append(lambda m: m.avatar is None)
        if args.no_roles:
            predicates.append(lambda m: len(getattr(m, "roles", [])) <= 1)

        now = discord.utils.utcnow()
        if args.created:

            def created(member, *, offset=now - timedelta(minutes=args.created)):
                return member.created_at > offset

            predicates.append(created)
        if args.joined:

            def joined(member, *, offset=now - timedelta(minutes=args.joined)):
                if isinstance(member, discord.User):
                    # If the member is a user then they left already
                    return True
                return member.joined_at and member.joined_at > offset

            predicates.append(joined)
        if args.joined_after:
            _joined_after_member = await converter.convert(ctx, str(args.joined_after))

            def joined_after(member, *, _other=_joined_after_member):
                return member.joined_at and _other.joined_at and member.joined_at > _other.joined_at

            predicates.append(joined_after)
        if args.joined_before:
            _joined_before_member = await converter.convert(ctx, str(args.joined_before))

            def joined_before(member, *, _other=_joined_before_member):
                return member.joined_at and _other.joined_at and member.joined_at < _other.joined_at

            predicates.append(joined_before)

        members = {m for m in members if all(p(m) for p in predicates)}
        if len(members) == 0:
            raise commands.BadArgument("No members found matching criteria.")

        if args.show:
            members = sorted(members, key=lambda m: m.joined_at or now)
            fmt = "\n".join(f"{m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\t{m}" for m in members)
            content = f"Current Time: {datetime.utcnow()}\nTotal members: {len(members)}\n{fmt}"
            file = discord.File(io.BytesIO(content.encode("utf-8")), filename="members.txt")
            return await ctx.send(file=file)

        if args.reason is None:
            raise commands.BadArgument("`--reason` flag is required.")
        else:
            reason = await ActionReason().convert(ctx, args.reason)

        view = ConfirmView(bot=self.bot, user=ctx.author)
        view.message = await ctx.send(
            f"This will ban **{plural(len(members)):member}**. Are you sure?", view=view
        )

        await view.wait()

        if not view.value:
            return

        count = 0
        if not self.massban_enabled:
            logger.info(
                "`massban` feature is disabled. To enable it set the environment config variable `MODERATION_MASSBAN_ENABLE` to `True`."
            )

        async with ctx.typing():
            for member in members:
                try:
                    if member and reason:
                        if not self.massban_enabled:
                            continue
                        await ctx.guild.ban(member, reason=get_audit_reason(ctx.author, reason))
                except discord.HTTPException:
                    pass
                else:
                    count += 1
                await asyncio.sleep(0.5)

        await ctx.send(f"Banned {count}/{len(members)}")

    @ban.command(name="list")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def ban_list(self, ctx: commands.Context):
        """
        Shows the list of all banned users.

        To fetch the list, the bot must have `Ban Members` permission.

        **Notes:**
        - Depends on the quantity of banned members in the server, due to Discord limitation, this operation can be incredibly slow.
        """
        if not ctx.me.guild_permissions.ban_members:
            raise commands.BadArgument("Need `BAN_MEMBERS` permission.")

        banned_users = [entry async for entry in ctx.guild.bans(limit=None)]

        def base_embed(continued=False, description=None):
            embed = discord.Embed(color=discord.Color.dark_theme())
            embed.description = description if description is not None else ""
            embed.title = "Banned users"
            if continued:
                embed.title += " (Continued)"
            embed.set_footer(text=f"Found {plural(len(banned_users)):entry|entries}")
            return embed

        embeds = [base_embed()]
        entries = 0

        if banned_users:
            embed = embeds[0]

            for ban_entry in sorted(banned_users, key=lambda entry: entry.user.name):
                user = ban_entry.user
                line = f"{user.name}#{user.discriminator} = `{user.id}`\n"
                if entries == 25:
                    embed = base_embed(continued=True, description=line)
                    embeds.append(embed)
                    entries = 1
                else:
                    embed.description += line
                    entries += 1
        else:
            embeds[0].description = "Currently there is no banned user."

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def multiban(
        self,
        ctx: commands.Context,
        members: commands.Greedy[Union[discord.Member, discord.User]],
        *,
        reason: ActionReason = None,
    ):
        """
        Bans multiple members from the server.

        This only works through banning via ID.

        In order for this to work, the bot must have `Ban Members` permission.

        **Examples:**
        - `{prefix}multiban 204255221017214 159985870458322 Involved in server raid.`

        **Notes:**
        - To prevent from being rate limited with Discord API, you can only ban up to 10 members with single command.
        """
        if len(members) > 10:
            raise commands.BadArgument(f"Too many members to ban given ({len(members)}/10).")
        if reason is None:
            reason = "No reason was provided."

        total_members = len(members)
        if total_members == 0:
            raise commands.BadArgument("Missing members to ban.")

        view = ConfirmView(bot=self.bot, user=ctx.author)
        view.message = await ctx.send(
            embed=discord.Embed(
                color=self.bot.main_color,
                description=f"This will ban **{plural(total_members):member}**. Are you sure?",
            ),
            view=view,
        )

        await view.wait()

        if not view.value:
            return

        success = []
        failed = []
        done_embed = discord.Embed(title="Multiban", color=self.bot.main_color)
        async with ctx.typing():
            for member in members:
                try:
                    await ctx.guild.ban(
                        member,
                        reason=get_audit_reason(ctx.author, reason),
                        delete_message_days=0,
                    )
                except discord.HTTPException:
                    failed.append(member)
                else:
                    success.append(member)
                await asyncio.sleep(0.5)
        done_embed.description = f"Banned **{len(success)}/{total_members}** " + (
            "member." if len(success) == 1 else "members."
        )
        done_embed.add_field(
            name="Success",
            value="\n".join(str(m) for m in success) if success else "None",
            inline=False,
        )
        done_embed.add_field(
            name="Failed",
            value="\n".join(str(m) for m in failed) if failed else "None",
            inline=False,
        )
        done_embed.add_field(name="Reason", value=reason, inline=False)
        await ctx.send(embed=done_embed)

        if not success:
            return

        await self.logging.send_log(
            guild=ctx.guild,
            action="multiban",
            target=success,
            moderator=ctx.author,
            reason=reason,
            description=f"Banned **{len(success)}/{total_members}** "
            + ("member." if len(success) == 1 else "members."),
        )

    @commands.command(usage="<member> [reason] [message_days]")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def softban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: ActionReason = None,
    ):
        """
        Soft bans a member from the server.

        `member` may be a member ID, mention, or name.
        `reason` is optional.
        `message_days` is the number of days worth of messages to delete from the user in the guild. If not specified, defaults to `1`. Max is `7`.

        A softban is basically banning the member from the server but then unbanning the member as well.
        This allows you to essentially kick the member while removing their messages.

        In order for this to work, the bot must have `Ban Members` permission.

        **Examples:**
        - `{prefix}softban @User`
        - `{prefix}softban @User Posting server invites in DMs.`
        - `{prefix}softban @User --2`
        - `{prefix}softban @User Posting server invites in DMs. --2`

        **Notes:**
        - `message_days` if specified, must start with `--` (e.g. `--1`) for the bot to recognize the syntax.
        """
        message_days = 1
        if reason is not None:
            reason, message_days = parse_delete_message_days(str(reason))
            if message_days == 0:
                message_days = 1

        if reason is None:
            reason = "No reason was provided."
        await ctx.guild.ban(
            member,
            reason=get_audit_reason(ctx.author, reason),
            delete_message_days=message_days,
        )
        await ctx.guild.unban(member, reason=get_audit_reason(ctx.author, reason))

        embed = discord.Embed(
            title="Ban",
            description=f"`{member}` has been soft banned.",
            color=self.bot.main_color,
        )
        embed.add_field(name="Reason", value=reason)
        await ctx.send(embed=embed)

        await self.logging.send_log(
            guild=ctx.guild,
            action=ctx.command.name,
            target=member,
            moderator=ctx.author,
            reason=reason,
            description=f"`{member}` has been soft banned.",
        )

    # Unban command
    @commands.command(usage="<user> [reason]")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def unban(
        self,
        ctx: commands.Context,
        ban_entry: BannedMember,
        *,
        reason: ActionReason = None,
    ):
        """
        Unbans a user from the server.

        `ban_entry` may be the ID (recommended) or the format of `Name#Discriminator` combination (e.g. `User#1234`) of the banned user.
        Typically the ID is the easiest and recommended to use.

        `reason` is optional.

        In order for this to work, the bot must have `Ban Members` permission.
        """
        if not ctx.me.guild_permissions.ban_members:
            raise commands.BadArgument("Need `BAN_MEMBERS` permission.")

        if reason is None:
            reason = "No reason was provided."

        await ban_entry.unban(reason=get_audit_reason(ctx.author, reason))

        embed = discord.Embed(
            title="Unban",
            description=f"`{ban_entry.user}` is now unbanned.",
            color=self.blurple,
        )
        embed.add_field(name="Reason", value=reason)
        await ctx.send(embed=embed)
        await self.logging.send_log(
            guild=ctx.guild,
            action=ctx.command.name,
            target=ban_entry.user,
            moderator=ctx.author,
            reason=reason,
            ban_reason=ban_entry.ban_reason,
            description=f"`{ban_entry.user}` is now unbanned.",
        )

    @commands.command(aliases=["nick"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def nickname(self, ctx: commands.Context, user: discord.Member, *, new_nickname: str = None):
        """
        Change nickname of a specified user.

        `user` may be a user ID, mention, or name.

        In order for this to work, the bot must have `Manage Nicknames` permission.

        **Notes:**
        - To remove nickname from user, just leave the `new_nickname`'s parameter empty.
        """
        if not ctx.me.guild_permissions.manage_nicknames:
            raise commands.BadArgument("Need `MANAGE_NICKNAMES` permission.")

        if new_nickname == user.name:
            raise commands.BadArgument("Nickname must be different than username.")
        try:
            await user.edit(nick=new_nickname)
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)}"
            raise commands.BadArgument(
                "Error renaming {name}#{discrim}.\n```Haskell\n{error}\n```".format(
                    name=user.name, discrim=user.discriminator, error=err
                )
            )
        else:
            embed = discord.Embed(
                description=f"`{user}`'s nickname is now changed to `{new_nickname}`."
                if new_nickname is not None
                else f"Removed the nickname from `{user}`.",
                color=self.blurple,
            )
            await ctx.send(embed=embed)

    @commands.group(name="role", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _role(self, ctx: commands.Context, member: discord.Member, *, args: str):
        """
        Adds/Removes one or more roles to/from a member.

        `member` may be a member ID, mention, or name.
        `args` is a type of `option/operation` you want to execute, and `role(s)`.

        **Valid `options/operations`:**
        `+`: Adds role to member.
        `-`: Removes role from member.
        Multiple options must be separated with comma `,`.

        `role` may be a role ID, mention, or name.

        In order for this to work, the bot must have `Manage Roles` permission.

        **Examples:**
        - `{prefix}role @User +678788597817540, -684463102057906` (separated by comma `,`)
        - `{prefix}role @User +Role_one, -Role_two` (separated by comma `,`)
        """
        if not ctx.me.guild_permissions.manage_roles:
            raise commands.BadArgument("Need `MANAGE_ROLES` permission.")

        parse_args = [v.strip() for v in args.split(",")]
        to_add = []
        to_remove = []
        converter = commands.RoleConverter()
        member_roles = [role for role in reversed(member.roles) if role is not ctx.guild.default_role]
        for id in parse_args:
            add, remove = False, False
            if not id.startswith("+") and not id.startswith("-"):
                raise commands.BadArgument(f'Missing `+` or `-` symbol for argument "{id}".')
            elif id.startswith("+"):
                id = id.strip("+")
                add = True
            else:
                id = id.strip("-")
                remove = True
            try:
                role: discord.Role = await converter.convert(ctx, id)
            except commands.RoleNotFound:
                raise commands.BadArgument(f'Role "{id}" not found.')
            if (
                role.is_premium_subscriber()
                or role.is_bot_managed()
                or role.is_integration()
                or role > ctx.me.top_role
            ):
                continue
            if add and role not in member_roles and ctx.me.top_role > role:
                to_add.append(role)
            elif remove and role in member_roles and ctx.me.top_role > role:
                to_remove.append(role)

        if not to_add and not to_remove:
            raise commands.BadArgument("No changes were made.")

        embed = discord.Embed(title="Role", color=self.bot.main_color)
        embed.set_footer(text=f"User ID: {member.id}")
        try:
            if to_add:
                await member.add_roles(*to_add)
                embed.add_field(
                    name="Added",
                    value="\n".join(role.mention for role in to_add),
                    inline=False,
                )
            if to_remove:
                await member.remove_roles(*to_remove)
                embed.add_field(
                    name="Removed",
                    value="\n".join(role.mention for role in to_remove),
                    inline=False,
                )
        except Exception as e:
            raise commands.BadArgument(f"**Error:**\n```py\n{str(e)}\n```")
        embed.set_footer(text=f"User ID: {member.id}")
        embed.description = f"Updated **{plural(len(to_add) + len(to_remove)):role}** for {member.mention}."
        await ctx.send(embed=embed)

    @_role.command(name="add")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_add(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """
        Adds a role to member.

        `member` may be a member ID, mention, or name.
        `role` may be a role ID, mention, or name.

        In order for this to work, the bot must have `Manage Roles` permission.
        """
        if not ctx.me.guild_permissions.manage_roles:
            raise commands.BadArgument("Need `MANAGE_ROLES` permission.")

        if role.is_premium_subscriber() or role.is_bot_managed() or role.is_integration():
            raise commands.BadArgument(
                "The specified role is automatically managed by Discord for "
                "Server Boosting or by an Integration (Bot)."
            )

        member_roles = [role for role in reversed(member.roles) if role is not ctx.guild.default_role]
        if role in member_roles or role > ctx.me.top_role:
            raise commands.BadArgument("No changes were made.")

        await member.add_roles(role)
        await ctx.send(
            embed=discord.Embed(
                description=f"Updated roles for {member.mention}, added {role.mention}.",
                color=self.bot.main_color,
            )
        )

    @_role.command(name="remove")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_remove(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """
        Removes a role from member.

        `member` may be a member ID, mention, or name.
        `role` may be a role ID, mention, or name.

        In order for this to work, the bot must have `Manage Roles` permission.
        """
        if not ctx.me.guild_permissions.manage_roles:
            raise commands.BadArgument("Need `MANAGE_ROLES` permission.")

        if role.is_premium_subscriber() or role.is_bot_managed() or role.is_integration():
            raise commands.BadArgument(
                "The specified role is automatically managed by Discord for "
                "Server Boosting or by an Integration (Bot)."
            )

        member_roles = [role for role in reversed(member.roles) if role is not ctx.guild.default_role]
        if role not in member_roles or role > ctx.me.top_role:
            raise commands.BadArgument("No changes were made.")

        await member.remove_roles(role)
        await ctx.send(
            embed=discord.Embed(
                description=f"Updated roles for {member.mention}, removed {role.mention}.",
                color=self.bot.main_color,
            )
        )

    @commands.Cog.listener()
    async def on_member_update(self, *args, **kwargs) -> None:
        await self.logging.on_member_update(*args, **kwargs)

    @commands.Cog.listener()
    async def on_member_remove(self, *args, **kwargs) -> None:
        await self.logging.on_member_remove(*args, **kwargs)

    @commands.Cog.listener()
    async def on_member_ban(self, *args, **kwargs) -> None:
        await self.logging.on_member_ban(*args, **kwargs)

    @commands.Cog.listener()
    async def on_member_unban(self, *args, **kwargs) -> None:
        await self.logging.on_member_unban(*args, **kwargs)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, *args, **kwargs) -> None:
        await self.logging.on_guild_channel_create(*args, **kwargs)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, *args, **kwargs) -> None:
        await self.logging.on_guild_channel_delete(*args, **kwargs)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Moderation(bot))
