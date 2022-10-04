from __future__ import annotations

from typing import TYPE_CHECKING

import discord


if TYPE_CHECKING:
    from bot import ModmailBot


async def delete_quietly(message: discord.Message) -> None:
    if message.channel.permissions_for(message.guild.me).manage_messages:
        try:
            await message.delete()
        except discord.HTTPException:
            pass


def guild_roughly_chunked(guild: discord.Guild) -> bool:
    return len(guild.members) / guild.member_count > 0.9


def error_embed(bot: ModmailBot, *, description: str) -> discord.Embed:
    return discord.Embed(
        title="__Errors__",
        color=bot.error_color,
        description=description,
    )
