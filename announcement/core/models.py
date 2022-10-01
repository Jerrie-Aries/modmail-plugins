import asyncio

from enum import Enum
from typing import Any, Dict, Optional

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
        self.event: asyncio.Event = asyncio.Event()
        self.ready: bool = False

        self.type: AnnouncementType = MISSING
        self.message: discord.Message = MISSING
        self.content: str = MISSING
        self.embed: discord.Embed = MISSING

    @property
    def posted(self) -> bool:
        return self.event.is_set()

    @posted.setter
    def posted(self, flag: bool) -> None:
        if flag:
            self.event.set()
        else:
            self.event.clear()

    async def wait(self) -> None:
        try:
            await self.event.wait()
        except asyncio.CancelledError:
            pass

    async def resolve_mentions(self) -> None:
        if not self.content:
            return
        ret = []
        argument = self.content.split()
        for arg in argument:
            if arg in ("@here", "@everyone"):
                ret.append(arg)
                continue
            user_or_role = None
            try:
                user_or_role = await commands.RoleConverter().convert(self.ctx, arg)
            except commands.BadArgument:
                try:
                    user_or_role = await commands.MemberConverter().convert(self.ctx, arg)
                except commands.BadArgument:
                    raise commands.BadArgument(f"Unable to convert {arg} to user or role mention.")
            if user_or_role is not None:
                ret.append(user_or_role.mention)
        self.content = ", ".join(ret) if ret else None

    def create_embed(
        self,
        *,
        description: str = MISSING,
        color: str = MISSING,
        thumbnail_url: str = MISSING,
        image_url: str = MISSING,
    ) -> discord.Embed:
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
        embed.set_footer(text="Announcement", icon_url=self.channel.guild.icon)
        self.embed = embed
        return embed

    def send_params(self) -> Dict[str, Any]:
        params = {"embed": self.embed}
        if self.content:
            params["content"] = self.content
        return params

    async def post(self) -> None:
        self.message = await self.channel.send(**self.send_params())
        self.posted = True

    async def publish(self) -> None:
        await self.message.publish()
