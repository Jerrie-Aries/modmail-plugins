from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import discord

    from discord.ext import commands


def can_execute_giveaway(ctx: commands.Context, destination: discord.TextChannel) -> bool:
    ctx_perms = ctx.channel.permissions_for(ctx.me)
    attrs = [
        "send_messages",
        "read_message_history",
        "manage_messages",
        "embed_links",
        "add_reactions",
    ]
    all_perms = (getattr(ctx_perms, attr) for attr in attrs)
    if destination != ctx.channel:
        ch_perms = destination.permissions_for(ctx.me)
        all_perms = (*all_perms, *(getattr(ch_perms, attr) for attr in attrs))

    return all(all_perms)


def validate_message(ctx: commands.Context, message: discord.Message) -> bool:
    return ctx.author == message.author and ctx.channel == message.channel and (len(message.content) < 2048)


def is_cancelled(ctx: commands.Context, message: discord.Message) -> bool:
    return message.content.lower() in ("cancel", f"{ctx.prefix}cancel")
