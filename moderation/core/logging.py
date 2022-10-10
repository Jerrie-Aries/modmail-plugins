from __future__ import annotations

from typing import List, Union, TYPE_CHECKING

import discord

from discord.utils import MISSING

from core.models import getLogger


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..moderation import Moderation


logger = getLogger(__name__)

action_colors = {
    "normal": discord.Color.blue(),
    "ban": discord.Color.red(),
    "multiban": discord.Color.red(),
}


class ModerationLogging:
    def __init__(self, cog: Moderation):
        self.cog: Moderation = cog
        self.bot: ModmailBot = cog.bot

    async def send_log(
        self,
        guild: discord.Guild,
        *,
        action: str,
        target: Union[discord.Member, discord.User, List[discord.Member]],
        moderator: discord.Member,
        reason: str,
        description: str,
        **kwargs,
    ) -> None:
        """
        Sends logs to the log channel.

        Parameters
        ----------
        guild: discord.Guild
            Guild object. This is to fetch the guild config.
        action: str
            The moderation action.
        target: discord.Member or discord.User or List
            Target that was executed from this moderation action.
            Could be a list of "Member" or "User" especially if the action is "multiban".
        moderator: discord.Member
            Moderator that executed this moderation action.
        reason: str
            Reason for this moderation action.
        description: str
            A message to be put in the Embed description.
        """
        config = self.cog.guild_config(str(guild.id))

        if not config.get("logging"):
            return
        channel = config.log_channel
        if channel is None:
            return

        # Parsing args and kwargs, and sending embed.
        color = action_colors.get(action, action_colors["normal"])
        embed = discord.Embed(
            title=action.title(),
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )

        if isinstance(target, (discord.Member, discord.User)):
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="User", value=target.mention)
            embed.set_footer(text=f"User ID: {target.id}")
        elif isinstance(target, List):
            embed.add_field(
                name="User" if len(target) == 1 else "Users",
                value="\n".join(str(m) for m in target),
            )
        else:
            raise TypeError("Invalid type of parameter `target`. Expected type: `Member`, `User`, or `List`.")

        embed.add_field(name="Reason", value=reason or "No reason as provided.")

        # extra info
        for key, value in kwargs.items():
            name = key.replace("_", " ").capitalize()
            embed.add_field(name=name, value=value)

        embed.add_field(name="Moderator", value=moderator.mention, inline=False)
        return await channel.send(embed=embed)

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        """
        An internal method to retrieve an existing webhook from the channel if any, otherwise a new one
        will be created.

        Parameters
        -----------
        channel : discord.TextChannel
            The channel to get or create the webhook from.
        """
        # check bot permissions first
        bot_me = channel.guild.get_member(self.bot.user.id)
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
                    reason="Webhook for invite logs.",
                )
            except Exception as e:
                logger.error(f"{type(e).__name__}: {str(e)}")
                wh = None

        return wh

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        pass

    async def on_member_remove(self, member: discord.Member) -> None:
        audit_logs = member.guild.audit_logs(limit=10, action=discord.AuditLogAction.kick)
        async for entry in audit_logs:
            if int(entry.target.id) == member.id:
                break
        else:
            return

        mod = entry.user
        if mod == self.bot:
            return

        if entry.created_at.timestamp() < member.joined_at.timestamp():
            return

        await self.send_log(
            member.guild,
            action="kick",
            target=member,
            moderator=mod,
            reason=entry.reason,
            description=f"{member} has been kicked.",
        )

    async def on_member_ban(self, guild: disocrd.Guild, user: Union[discord.User, discord.Member]) -> None:
        audit_logs = guild.audit_logs(limit=10, action=discord.AuditLogAction.ban)
        async for entry in audit_logs:
            if int(entry.target.id) == user.id:
                break
        else:
            logger.debug("Cannot find the audit log entry for user ban of %d, guild %s.", user, guild)
            return

        mod = entry.user
        if mod == self.bot:
            return

        if isinstance(user, discord.Member):
            if not user.joined_at or entry.created_at.timestamp() < user.joined_at.timestamp():
                return

        await self.send_log(
            guild,
            action="ban",
            target=user,
            moderator=mod,
            reason=entry.reason,
            description=f"{user} has been banned.",
        )

    async def on_member_unban(self, guild: disocrd.Guild, user: discord.User) -> None:
        audit_logs = guild.audit_logs(limit=10, action=discord.AuditLogAction.unban)
        async for entry in audit_logs:
            if int(entry.target.id) == user.id:
                break
        else:
            logger.debug("Cannot find the audit log entry for user unban of %d, guild %s.", user, guild)
            return

        mod = entry.user
        if mod == self.bot:
            return

        await self.send_log(
            guild,
            action="unban",
            target=user,
            moderator=mod,
            reason=entry.reason,
            description=f"{user} is now unbanned.",
        )
