from __future__ import annotations

import io

from typing import Any, Dict, Optional, Union, TYPE_CHECKING

import discord
from discord.utils import MISSING
from discord.ext.modmail_utils import Limit, plural

from core.models import getLogger
from core.utils import truncate


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..moderation import Moderation
    from .config import GuildConfig


logger = getLogger(__name__)

action_colors = {
    "normal": discord.Color.blue(),
    "ban": discord.Color.red(),
    "multiban": discord.Color.red(),
    "message edited": discord.Color.gold(),
    "message deleted": discord.Color.red(),
    "bulk message deleted": discord.Color.red(),
    "mute": discord.Color.dark_grey(),
}


class ModerationLogging:
    """
    Instance to handle and manage the logging for guild.
    """

    def __init__(self, guild: discord.Guild, cog: Moderation):
        self.guild: discord.Guild = guild
        self.cog: Moderation = cog
        self.bot: ModmailBot = cog.bot
        self._config: GuildConfig = MISSING
        self._channel: discord.TextChannel = MISSING
        self._webhook: discord.Webhook = MISSING

    @property
    def config(self) -> GuildConfig:
        if self._config is MISSING:
            self._config = self.cog.config.get_config(self.guild)
        return self._config

    @property
    def channel(self) -> discord.TextChannel:
        channel_id = self.config.log_channel_id
        if not channel_id:
            if self._channel:
                self._channel = MISSING
        elif not self._channel or self._channel.id != channel_id:
            channel = self.guild.get_channel(channel_id)
            self._channel = channel if channel else MISSING
        return self._channel

    @channel.setter
    def channel(self, item: discord.TextChannel) -> None:
        if item is MISSING:
            self.config.remove("log_channel")
        elif isinstance(item, discord.TextChannel):
            self.config["log_channel"] = str(item.id)
        else:
            raise TypeError(
                f"Invalid type of item. Expected TextChannel or MISSING, got {type(item).__name__} instead."
            )
        self.config.remove("webhook")
        self._channel = item

    @property
    def webhook(self) -> discord.Webhook:
        wh = self._webhook
        if wh and wh.url != self.config.webhook_url:
            self._webhook = wh = MISSING
        return wh

    @webhook.setter
    def webhook(self, item: discord.Webhook) -> None:
        self._webhook = item

    def is_enabled(self) -> bool:
        """
        Returns `True` if logging is enabled for the specified guild.
        """
        return self.config.get("logging", False)

    def is_whitelisted(self, channel: discord.TextChannel) -> bool:
        """
        Returns `True` if channel or its category is whitelisted.
        """
        whitelist_ids = self.config.get("channel_whitelist", [])
        if str(channel.id) in whitelist_ids:
            return True
        category = channel.category
        if category and str(category.id) in whitelist_ids:
            return True
        return False

    async def send_log(
        self,
        *,
        action: str,
        target: Optional[Any] = None,
        description: Optional[str] = None,
        moderator: Optional[discord.Member] = None,
        reason: Optional[str] = None,
        send_params: Optional[Dict[str, Any]] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """
        Sends logs to the log channel.

        Parameters
        ----------
        action: str
            The moderation action.
        target: Optional[Any]
            Target that was executed from this moderation action.
            This also could be a list of "Member" or "User" especially if the action is "multiban".
        description: Optional[str]
            A message to be put in the Embed description.
        moderator: Optional[discord.Member]
            Moderator that executed this moderation action.
        reason: Optional[str]
            Reason for this moderation action.
        send_params: Optional[Dict[str, Any]]
            Additional parameter to use when sending the log message.
        """
        channel = self.channel
        if not channel:
            return

        if send_params is None:
            send_params = {}

        webhook = self.webhook or await self._get_or_create_webhook(channel)
        if webhook:
            send_params["username"] = self.bot.user.name
            send_params["avatar_url"] = str(self.bot.user.display_avatar)
            send_method = webhook.send
        else:
            send_method = channel.send

        # In some events (e.g. message updates) the embed is already provided.
        embed = kwargs.pop("embed", None)
        if embed is None:
            color = action_colors.get(action, action_colors["normal"])
            embed = discord.Embed(
                description=description,
                color=color,
                timestamp=discord.utils.utcnow(),
            )

        # Parsing args and kwargs, and sending embed.
        embed.title = action.title()

        if target is not None:
            if isinstance(target, (discord.Member, discord.User)):
                embed.set_thumbnail(url=target.display_avatar.url)
                embed.add_field(name="User", value=target.mention)
                embed.set_footer(text=f"User ID: {target.id}")
            elif isinstance(target, list) and isinstance(target[0], (discord.Member, discord.User)):
                # multiban
                embed.add_field(
                    name="User" if len(target) == 1 else "Users",
                    value="\n".join(str(m) for m in target),
                )
            elif isinstance(target, discord.abc.GuildChannel):
                embed.add_field(name="Channel", value=f"# {target.name}")
                embed.set_footer(text=f"Channel ID: {target.id}")
            else:
                raise TypeError(
                    f"Invalid type of target. Expected Member, User, GuildChannel, List, or None. Got {type(target).__name__} instead."
                )

        if reason is not None:
            embed.add_field(name="Reason", value=reason)

        # extra info
        for key, value in kwargs.items():
            name = key.replace("_", " ").capitalize()
            embed.add_field(name=name, value=value)

        if moderator is not None:
            embed.add_field(name="Moderator", value=moderator.mention, inline=False)

        send_params["embed"] = embed
        return await send_method(**send_params)

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        """
        An internal method to retrieve an existing webhook from the channel if any, otherwise a new one
        will be created.

        Parameters
        -----------
        channel : discord.TextChannel
            The channel to get or create the webhook from.
        """
        wh_url = self.config.webhook_url
        update = False
        if wh_url:
            wh = discord.Webhook.from_url(
                wh_url,
                session=self.bot.session,
                bot_token=self.bot.token,
            )
            wh = await wh.fetch()
            if wh.channel == channel:
                self.webhook = wh
                return wh
            self.config.remove("webhook")
            update = True

        # check bot permissions first
        bot_me = channel.guild.me
        if not bot_me or not channel.permissions_for(bot_me).manage_webhooks:
            return None

        wh = None
        webhooks = await channel.webhooks()
        if webhooks:
            # find any webhook that has token which means that belongs to the client
            wh = discord.utils.find(lambda x: x.token is not None, webhooks)

        if not wh:
            avatar = await self.bot.user.display_avatar.read()
            try:
                wh = await channel.create_webhook(
                    name=self.bot.user.name,
                    avatar=avatar,
                    reason="Webhook for Moderation logs.",
                )
            except Exception as e:
                logger.error(f"{type(e).__name__}: {str(e)}")
                wh = None

        if wh:
            self.config.set("webhook", wh.url)
            update = True
            self.webhook = wh
        if update:
            await self.config.update()

        return wh

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """
        General member update events will be caught from here.
        As of now, we only look for these events:
        - Guild avatar update
        - Nickname changes
        - Timed out changes
        - Role updates
        """
        if before.guild_avatar != after.guild_avatar:
            return await self._on_member_guild_avatar_update(before, after)

        audit_logs = after.guild.audit_logs(limit=10)
        found = False
        async for entry in audit_logs:
            if int(entry.target.id) == after.id:
                action = entry.action
                if action == discord.AuditLogAction.member_update:
                    if hasattr(entry.after, "nick"):
                        found = True
                        await self._on_member_nick_update(before, after, entry.user, reason=entry.reason)
                    elif hasattr(entry.after, "timed_out_until"):
                        found = True
                        await self._on_member_timed_out_update(before, after, entry.user, reason=entry.reason)
                elif action == discord.AuditLogAction.member_role_update:
                    found = True
                    await self._on_member_role_update(before, after, entry.user, reason=entry.reason)
                if found:
                    return

    async def _on_member_guild_avatar_update(self, before: discord.Member, after: discord.Member) -> None:
        action = "updated" if after.guild_avatar is not None else "removed"
        description = f"`{after}` {action} their guild avatar."
        await self.send_log(
            action="avatar update",
            target=after,
            description=description,
        )

    async def _on_member_nick_update(
        self,
        before: discord.Member,
        after: discord.Member,
        moderator: discord.Member,
        *,
        reason: Optional[str] = None,
    ) -> None:
        action = "set" if before.nick is None else "removed" if after.nick is None else "updated"
        description = f"`{after}`'s nickname was {action}"
        description += "." if after.nick is None else f" to `{after.nick}`."
        await self.send_log(
            action="nickname",
            target=after,
            moderator=moderator if moderator != after else None,
            reason=reason if reason else "None",
            description=description,
            before=f"`{str(before.nick)}`",
            after=f"`{str(after.nick)}`",
        )

    async def _on_member_role_update(
        self,
        before: discord.Member,
        after: discord.Member,
        moderator: discord.Member,
        *,
        reason: Optional[str] = None,
    ) -> None:
        description = f"`{after}`'s roles were updated."
        added = [role for role in after.roles if role not in before.roles]
        removed = [role for role in before.roles if role not in after.roles]

        # bug? this happens in community guild if it has the built-in reaction roles in welcome screen
        # not really sure what was the issue, but this fires twice. so to prevent from hitting rate limits,
        # we just return
        if not added and not removed:
            return

        kwargs = {}
        if added:
            kwargs["added"] = "\n".join(r.mention for r in added)
        if removed:
            kwargs["removed"] = "\n".join(r.mention for r in removed)

        await self.send_log(
            action="role update",
            target=after,
            moderator=moderator if moderator != after else None,
            reason=reason if reason else "None",
            description=description,
            **kwargs,
        )

    async def _on_member_timed_out_update(
        self,
        before: discord.Member,
        after: discord.Member,
        moderator: discord.Member,
        *,
        reason: Optional[str] = None,
    ) -> None:
        if moderator == self.bot.user:
            # handled in mute/unmute command
            return

        kwargs = {}
        description = f"`{after}`"
        if after.timed_out_until is None:
            action = "unmute"
            description += " has been unmuted."
        elif before.timed_out_until is None:
            action = "mute"
            description += " has been muted."
            kwargs["expires"] = discord.utils.format_dt(after.timed_out_until, "R")
        else:
            action = "mute update"
            description += "'s mute time out has been updated."
            kwargs["before"] = discord.utils.format_dt(before.timed_out_until, "F")
            kwargs["after"] = discord.utils.format_dt(after.timed_out_until, "F")

        await self.send_log(
            action=action,
            target=after,
            description=description,
            moderator=moderator,
            reason=reason,
            **kwargs,
        )

    async def on_member_remove(self, member: discord.Member) -> None:
        """
        Currently this listener is to search for kicked members.
        For some reason Discord and discord.py do not dispatch or have a specific event when a guild member
        was kicked, so we have to do it manually here.
        """
        audit_logs = self.guild.audit_logs(limit=10, action=discord.AuditLogAction.kick)
        async for entry in audit_logs:
            if int(entry.target.id) == member.id:
                break
        else:
            return

        mod = entry.user
        if mod == self.bot.user:
            return

        if entry.created_at.timestamp() < member.joined_at.timestamp():
            return

        await self.send_log(
            action="kick",
            target=member,
            moderator=mod,
            reason=entry.reason,
            description=f"`{member}` has been kicked.",
        )

    async def on_member_ban(self, user: Union[discord.User, discord.Member]) -> None:
        audit_logs = self.guild.audit_logs(limit=10, action=discord.AuditLogAction.ban)
        async for entry in audit_logs:
            if int(entry.target.id) == user.id:
                break
        else:
            logger.error("Cannot find the audit log entry for user ban of %d, guild %s.", user, self.guild)
            return

        mod = entry.user
        if mod == self.bot.user:
            return

        if isinstance(user, discord.Member):
            if not user.joined_at or entry.created_at.timestamp() < user.joined_at.timestamp():
                return

        await self.send_log(
            action="ban",
            target=user,
            moderator=mod,
            reason=entry.reason,
            description=f"`{user}` has been banned.",
        )

    async def on_member_unban(self, user: discord.User) -> None:
        audit_logs = self.guild.audit_logs(limit=10, action=discord.AuditLogAction.unban)
        async for entry in audit_logs:
            if int(entry.target.id) == user.id:
                break
        else:
            logger.error("Cannot find the audit log entry for user unban of %d, guild %s.", user, self.guild)
            return

        mod = entry.user
        if mod == self.bot.user:
            return

        await self.send_log(
            action="unban",
            target=user,
            moderator=mod,
            reason=entry.reason,
            description=f"`{user}` is now unbanned.",
        )

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        audit_logs = self.guild.audit_logs(limit=10, action=discord.AuditLogAction.channel_create)
        async for entry in audit_logs:
            if int(entry.target.id) == channel.id:
                break
        else:
            logger.error(
                "Cannot find the audit log entry for channel creation of %d, guild %s.", channel, self.guild
            )
            return

        mod = entry.user

        kwargs = {}
        if channel.category:
            kwargs["category"] = channel.category.name

        await self.send_log(
            action="channel created",
            target=channel,
            moderator=mod,
            description=f"Channel {channel.mention} was created.",
            reason=entry.reason,
            **kwargs,
        )

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        audit_logs = self.guild.audit_logs(limit=10, action=discord.AuditLogAction.channel_delete)
        async for entry in audit_logs:
            if int(entry.target.id) == channel.id:
                break
        else:
            logger.error(
                "Cannot find the audit log entry for channel deletion of %d, guild %s.", channel, self.guild
            )
            return

        mod = entry.user

        kwargs = {}
        if channel.category:
            kwargs["category"] = channel.category.name

        await self.send_log(
            action="channel deleted",
            target=channel,
            moderator=mod,
            description=f"Channel `# {channel.name}` was deleted.",
            reason=entry.reason,
            **kwargs,
        )

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        channel = self.guild.get_channel(payload.channel_id)
        if channel is None or self.is_whitelisted(channel):
            return

        message = payload.cached_message
        if message and message.author.bot:
            return

        action = "message deleted"
        embed = discord.Embed(
            color=action_colors.get(action, action_colors["normal"]),
            timestamp=discord.utils.utcnow(),
        )
        if message:
            content = message.content
            info = (
                f"Sent by: {message.author.mention}\n"
                f"Message sent on: {discord.utils.format_dt(message.created_at)}\n"
            )
            embed.add_field(name="Message info", value=info)
            footer_text = f"Message ID: {message.id}\nChannel ID: {message.channel.id}"
        else:
            content = None
            footer_text = f"Message ID: {payload.message_id}\nChannel ID: {payload.channel_id}"

        embed.description = f"**A message was deleted in {channel.mention}.**\n"
        if content:
            embed.description += truncate(content, Limit.embed_description - len(embed.description))
        else:
            footer_text = f"The message content cannot be retrieved.\n{footer_text}"
        embed.set_footer(text=footer_text)

        await self.send_log(
            action=action,
            embed=embed,
        )

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        channel = self.guild.get_channel(payload.channel_id)
        if channel is None or self.is_whitelisted(channel):
            return

        messages = sorted(payload.cached_messages, key=lambda msg: msg.created_at)
        message_ids = payload.message_ids
        upload_text = "Deleted messages:\n\n"

        if not messages:
            upload_text += "There are no known messages.\n"
            upload_text += "Message IDs: " + ", ".join(map(str, message_ids)) + "."
        else:
            known_message_ids = set()
            for message in messages:
                known_message_ids.add(message.id)
                try:
                    time = message.created_at.strftime("%b %-d, %Y at %-I:%M %p")
                except ValueError:
                    time = message.created_at.strftime("%b %d, %Y at %I:%M %p")
                upload_text += (
                    f"{time} • {message.author} ({message.author.id})\n"
                    f"Message ID: {message.id}\n{message.content}\n\n"
                )
            unknown_message_ids = message_ids ^ known_message_ids
            if unknown_message_ids:
                upload_text += "Unknown message IDs: " + ", ".join(map(str, unknown_message_ids)) + "."

        action = "bulk message deleted"
        embed = discord.Embed(
            color=action_colors.get(action, action_colors["normal"]),
            timestamp=discord.utils.utcnow(),
        )
        embed.description = f"**{plural(len(message_ids)):message} deleted from {channel.mention}.**"
        embed.set_footer(text=f"Channel ID: {payload.channel_id}")
        fp = io.BytesIO(bytes(upload_text, "utf-8"))
        send_params = {"file": discord.File(fp, "Messages.txt")}

        await self.send_log(
            action=action,
            embed=embed,
            send_params=send_params,
        )

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        channel = self.guild.get_channel(payload.channel_id)
        if channel is None or self.is_whitelisted(channel):
            return

        message_id = payload.message_id

        new_content = payload.data.get("content", "")
        old_message = payload.cached_message

        if not new_content or (old_message and new_content == old_message.content):
            # Currently does not support Embed edits
            return

        action = "message edited"
        embed = discord.Embed(
            color=action_colors.get(action, action_colors["normal"]),
            timestamp=discord.utils.utcnow(),
        )
        embed.description = f"**A message was updated in {channel.mention}.**\n"
        footer_text = f"Message ID: {payload.message_id}\nChannel ID: {payload.channel_id}"

        info = None
        if old_message:
            # always ignore bot's message
            if old_message.author.bot:
                return

            embed.add_field(
                name="Before", value=truncate(old_message.content, Limit.embed_field_value) or "No Content"
            )
            info = (
                f"Sent by: {old_message.author.mention}\n"
                f"Message sent on: {discord.utils.format_dt(old_message.created_at)}\n"
            )
        else:
            try:
                message = await channel.fetch_message(message_id)
                if message.author.bot:
                    return
                info = (
                    f"Sent by: {message.author.mention}\n"
                    f"Message sent on: {discord.utils.format_dt(message.created_at)}\n"
                )
            except discord.NotFound:
                pass
            footer_text = f"The former message content cannot be found.\n{footer_text}"
        embed.add_field(name="After", value=truncate(new_content, Limit.embed_field_value) or "No Content")
        if info is not None:
            embed.add_field(name="Message info", value=info)
        embed.set_footer(text=footer_text)

        await self.send_log(
            action=action,
            embed=embed,
        )
