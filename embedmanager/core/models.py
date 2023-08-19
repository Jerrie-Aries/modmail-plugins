from __future__ import annotations

from typing import Any, Dict, Optional, List, TYPE_CHECKING

import discord

from discord import Embed
from discord.utils import MISSING

from .data import INPUT_DATA


if TYPE_CHECKING:
    from ..embedmanager import EmbedManager


class EmbedEditor:
    def __init__(self, cog: EmbedManager, embeds: List[Embed] = MISSING):
        self.cog: EmbedManager = cog
        self.embeds: List[Embed] = embeds if embeds is not MISSING else [Embed()]
        self.index: int = 0
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
    def embed(self) -> Embed:
        return self.embeds[self.index]

    def add(self, embed: Optional[Embed] = None) -> None:
        if embed is None:
            embed = Embed()
        self.embeds.append(embed)

    @classmethod
    def from_embeds(cls, cog: EmbedManager, *, embeds: List[Embed]) -> EmbedEditor:
        editor = cls(cog, embeds)
        for i, embed in enumerate(editor.embeds):
            editor.index = i
            if embed.type != "rich":
                continue
            data = embed.to_dict()
            title = data.get("title")
            editor["title"]["title"]["default"] = title
            url = data.get("url")
            if url:
                editor["title"]["url"]["default"] = embed.url
            editor["body"]["description"]["default"] = data.get("description")
            editor["color"]["value"]["default"] = data.get("color")
            images = ["thumbnail", "image"]
            elems = ["author", "footer"]
            for elem in images + elems:
                elem_data = data.get(elem)
                if elem_data:
                    for key, val in elem_data.items():
                        if elem in images:
                            if key != "url":
                                continue
                            key = elem
                            elem = "body"
                        try:
                            editor[elem][key]["default"] = val
                        except KeyError:
                            continue
            if embed.timestamp:
                editor["timestamp"]["timestamp"]["default"] = str(embed.timestamp.timestamp())
        return editor

    def update(self, *, data: Dict[str, Any], category: str) -> Embed:
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
        if category == "timestamp":
            embed.timestamp = data["timestamp"]
        return embed
