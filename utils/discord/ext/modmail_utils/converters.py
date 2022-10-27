from __future__ import annotations

import re
from typing import Union, TYPE_CHECKING

import discord
from discord.ext import commands
from emoji import EMOJI_DATA


if TYPE_CHECKING:
    from bot import ModmailBot


EmojiT = Union[discord.Emoji, discord.PartialEmoji]

__all__ = ("EmojiConverter",)


class EmojiConverter(commands.Converter):
    """
    Elegant way to resolve emoji conversions using external `emoji` library.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> EmojiT:
        try:
            return self._convert_emoji(ctx.bot, argument)
        except commands.BadArgument:
            raise commands.EmojiNotFound(argument)

    @staticmethod
    def _convert_emoji(bot: ModmailBot, name: str) -> EmojiT:
        """
        A method to convert the provided string to a :class:`discord.Emoji`, :class:`discord.PartialEmoji`.

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
