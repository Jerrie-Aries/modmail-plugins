import asyncio

from enum import Enum
from typing import Any, Dict

import discord
from discord.utils import MISSING
from discord.ext import commands


__all__ = ("AnnouncementType", "AnnouncementModel")


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
    PLAIN = "plain"
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
    """
    Represents an instance to manage announcement creation.
    """

    def __init__(
        self,
        ctx: commands.Context,
        *,
        type: AnnouncementType = MISSING,
        channel: discord.TextChannel = MISSING,
        content: str = MISSING,
        embed: discord.Embed = MISSING,
    ):
        self.ctx: commands.Context = ctx
        self.type: AnnouncementType = type
        self.channel: discord.TextChannel = channel
        self.content: str = content
        self.embed: discord.Embed = embed

        self.message: discord.Message = MISSING
        self.event: asyncio.Event = asyncio.Event()
        self.ready: bool = False
        self.task: asyncio.Task = MISSING

    def is_ready(self) -> bool:
        """
        Returns whether the announcement is ready to be posted.
        """
        return self.ready and self.event.is_set()

    def cancel(self) -> None:
        """Cancel the announcement."""
        self.ready = False
        if self.task is not MISSING:
            self.task.cancel()
        self.event.clear()

    async def wait(self) -> None:
        """
        Wait until the announcement is ready to be posted or cancelled.
        """
        if self.task is MISSING:
            self.task = self.ctx.bot.loop.create_task(self.event.wait())
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    def create_embed(
        self,
        *,
        description: str = MISSING,
        color: str = MISSING,
        thumbnail_url: str = MISSING,
        image_url: str = MISSING,
    ) -> discord.Embed:
        """
        Create the announcement embed.
        """
        if not color:
            color = self.ctx.bot.main_color
        else:
            color = _color_converter(color)
        embed = discord.Embed(description=description, color=color, timestamp=discord.utils.utcnow())
        author = self.ctx.author
        embed.set_author(name=str(author), icon_url=author.display_avatar)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text="Announcement", icon_url=self.ctx.guild.icon)
        self.embed = embed
        return embed

    def send_params(self) -> Dict[str, Any]:
        params = {"embed": self.embed}
        if self.content:
            params["content"] = self.content
        return params

    async def send(self) -> None:
        """
        Send the announcement message.
        """
        if not self.channel:
            self.channel = self.ctx.channel
        self.message = await self.channel.send(**self.send_params())

    async def publish(self) -> None:
        """
        Publish the announcement. This will only work if the channel type is a news channel
        and if the announcement has never been published yet.
        """
        await self.message.publish()
