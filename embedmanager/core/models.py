from __future__ import annotations

from typing import Any, Dict, Optional, List, TYPE_CHECKING

import discord

from discord.utils import MISSING

from .data import INPUT_DATA


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..embedmanager import EmbedManager


class EmbedEditor:
    def __init__(
        self,
        cog: EmbedManager,
        embeds: List[discord.Embed] = MISSING,
        *,
        index: int = MISSING,
    ):
        self.cog: EmbedManager = cog
        self.bot: ModmailBot = cog.bot
        self.embeds: List[discord.Embed] = embeds if embeds is not MISSING else [discord.Embed()]
        self.index: int = index if index is not MISSING else 0
        self._inputs: Dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        if str(self.index) not in self._inputs:
            self._populate_default_inputs()
        return self._inputs[str(self.index)][key]

    def _populate_default_inputs(self) -> None:
        payload = {}
        for key, val in list(INPUT_DATA.items()):
            payload[key] = {}
            for k, _ in list(val.items()):
                payload[key][k] = {}
        self._inputs[str(self.index)] = payload

    @property
    def embed(self) -> discord.Embed:
        return self.embeds[self.index]

    def add(self, embed: Optional[discord.Embed] = None) -> None:
        if embed is None:
            embed = discord.Embed()
        self.embeds.append(embed)

    def update(self, *, data: Dict[str, Any], category: str) -> discord.Embed:
        """
        Update embed from the response data.
        """
        embed = self.embed
        if category == "title":
            title = data["title"]
            embed.title = title
            if title:
                url = data["url"]
            else:
                url = None
            embed.url = url
        if category == "author":
            embed.set_author(**data)
        if category == "body":
            embed.description = data["description"]
            thumbnail_url = data["thumbnail"]
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
            image_url = data["image"]
            if image_url:
                embed.set_image(url=image_url)
        if category == "color":
            embed.colour = data["value"]
        if category == "footer":
            embed.set_footer(**data)
        if category == "fields":
            embed.add_field(**data)
        embed.timestamp = discord.utils.utcnow()
        return embed
