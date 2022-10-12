from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import discord


if TYPE_CHECKING:
    from bot import ModmailBot


def guild_roughly_chunked(guild: discord.Guild) -> bool:
    return len(guild.members) / guild.member_count > 0.9


def error_embed(bot: ModmailBot, *, description: str) -> discord.Embed:
    return discord.Embed(
        title="__Errors__",
        color=bot.error_color,
        description=description,
    )


def bind_string_format(emoji: Optional[str], label: Optional[str], role_id: str) -> str:
    """
    Returns a string representation of emoji, label and role.

    Parameters
    -----------
    emoji : Optional[str]
        Emoji string.
    label : Optional[str]
        Button label.
    role_id : str
        The role ID.
    """

    if not emoji:
        emoji = ""
    if not label:
        label = ""
    if emoji and label:
        sep = "  "
    else:
        sep = ""
    return f"**{emoji}{sep}{label}** : <@&{role_id}>"


def get_audit_reason(moderator: discord.Member, reason: Optional[str] = None) -> str:
    """
    Returns a string representation of action reason for audit logs.
    """
    ret = f"Moderator: {moderator}\n"
    if reason:
        ret += f"Reason: {reason}"
    return ret
