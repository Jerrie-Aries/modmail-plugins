from enum import Enum
from typing import Any, Dict, Union

import discord
from discord.utils import MISSING
from discord.ext import commands


def _color_converter(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(discord.Color.from_str(value))
    except ValueError:
        raise ValueError(f"`{value}` is unknown color format.")


class AnnouncementType(Enum):

    # only two are valid for now. may add more later.
    NORMAL = "normal"
    EMBED = "embed"
    INVALID = "invalid"

    @classmethod
    def from_value(cls, value: str) -> "AnnouncementType":
        """
        Instantiate this class from string that match the value of enum member.

        If the value does not match the value of any enum member, AnnouncementType.INVALID
        will be returned.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.INVALID

    @property
    def value(self) -> str:
        """The value of the Enum member."""
        return self._value_


class AnnouncementModel:
    def __init__(self, ctx: commands.Context, channel: discord.TextChannel):
        self.ctx: commands.Context = ctx
        self.channel: discord.TextChannel = channel
        self.ready: bool = False

        self.type: AnnouncementType = MISSING
        self.content: str = MISSING
        self.embed: discord.Embed = MISSING

    def create_embed(
        self,
        *,
        description: str = MISSING,
        color: Union[int, str] = MISSING,
        thumbnail_url: str = MISSING,
        image_url: str = MISSING,
    ) -> discord.Embed:
        color = _color_converter(color)
        embed = discord.Embed(description=description, color=color, timestamp=discord.utils.utcnow())
        author = self.ctx.author
        embed.set_author(name=str(author), icon_url=author.display_avatar)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text="Announcement", icon_url=self.channel.guild.icon)
        self.embed = embed
        return embed

    def send_params(self) -> Dict[str, Any]:
        params = {"embed": self.embed}
        if self.content:
            params["content"] = self.content
        return params

    async def post(self) -> None:
        await self.channel.send(**self.send_params())
