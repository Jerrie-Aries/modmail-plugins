from __future__ import annotations

import re
from typing import Any, Optional, Union, TYPE_CHECKING

import discord
from discord.ext import commands
from emoji import EMOJI_DATA


if TYPE_CHECKING:
    from bot import ModmailBot


__all__ = (
    "EmojiConverter",
    "convert_emoji",
    "convert_text_channel",
    "get_id_match",
)

EmojiT = Union[discord.Emoji, discord.PartialEmoji]
_ID_REGEX = re.compile(r"([0-9]{15,20})$")


def get_id_match(argument: str) -> Optional[re.Match]:
    """
    Checks whether the argument is a valid ID string.

    Returns
    -------
    Optional[re.Match]
        `re.Match` object if the argument is a valid ID string. Otherwise returns `None`.
    """
    return _ID_REGEX.match(argument)


def convert_emoji(bot: ModmailBot, name: str) -> EmojiT:
    """
    A function to convert the provided string to a :class:`discord.Emoji`, :class:`discord.PartialEmoji`.

    If the parsed emoji has an ID (a custom emoji) and cannot be found, or does not have an ID and
    cannot be found in :class:`EMOJI_DATA` dictionary keys, :class:`commands.EmojiNotFound`
    will be raised.

    Parameters
    -----------
    name : str
        The emoji string or a unicode emoji.

    Returns
    -------
    :class:`discord.Emoji` or :class:`discord.PartialEmoji`
        The converted emoji.
    """
    # remove trailing whitespace
    name = re.sub("\ufe0f", "", name)
    emoji = discord.PartialEmoji.from_str(name)
    if emoji.is_unicode_emoji():
        if emoji.name not in EMOJI_DATA:
            raise ValueError(f"{name} is not a valid unicode emoji.")
    else:
        # custom emoji
        emoji = bot.get_emoji(emoji.id)
        if emoji is None:
            raise commands.EmojiNotFound(name)
    return emoji


class EmojiConverter(commands.Converter):
    """
    Elegant way to resolve emoji conversions using external `emoji` library.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> EmojiT:
        try:
            return convert_emoji(ctx.bot, argument)
        except commands.BadArgument:
            raise commands.EmojiNotFound(argument)


# modified from discord.py's ext.commands.GuidChannelConverter._resolve_channel
def _resolve_channel(
    ctx: commands.Context, argument: str, attribute: str, channel_type: Any
) -> discord.abc.GuildChannel:
    bot = ctx.bot
    match = get_id_match(argument) or re.match(r"<#([0-9]{15,20})>$", argument)
    result = None
    guild = ctx.guild

    if match is None:
        # not a mention
        if guild:
            iterable = getattr(guild, attribute)
            result = discord.utils.get(iterable, name=argument)
        else:

            def check(c):
                return isinstance(c, channel_type) and c.name == argument

            result = discord.utils.find(check, bot.get_all_channels())  # type: ignore
    else:
        channel_id = int(match.group(1))
        if guild:
            result = guild.get_channel(channel_id)
        else:
            result = None
            for guild in bot.guilds:
                result = guild.get_channel(argument)
                if result:
                    break

    if not isinstance(result, channel_type):
        raise commands.ChannelNotFound(argument)

    return result


def convert_text_channel(ctx: commands.Context, argument: str) -> discord.TextChannel:
    """
    Converts a passed argument to a `discord.TextChannel`.

    All lookups are via the local guild. If in a DM context, then the lookup
    is done by the global cache.

    The lookup strategy is as follows (in order):

    1. Lookup by ID.
    2. Lookup by mention.
    3. Lookup by name.
    """
    return _resolve_channel(ctx, argument, "text_channels", discord.TextChannel)
