from __future__ import annotations

from typing import Any, Dict, Optional, List, TYPE_CHECKING

import discord

from discord import Embed
from discord.utils import MISSING

from .data import INPUT_DATA


if TYPE_CHECKING:
    from ..embedmanager import EmbedManager


class EmbedEditor:
    """
    Represents embed editor. This instance is used to store and manage the raw input values for embeds.
    """

    def __init__(self, cog: EmbedManager, *, embeds: List[Embed] = MISSING):
        self.cog: EmbedManager = cog
        self.embeds: List[Embed] = embeds if embeds is not MISSING else [Embed()]
        self.index: int = 0
        self._inputs: Dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        if str(self.index) not in self._inputs:
            self._populate_default_inputs()
        return self._inputs[str(self.index)][key]

    def __setitem__(self, key: str, item: Any) -> None:
        self._inputs[str(self.index)][key] = item

    def _populate_default_inputs(self) -> None:
        payload = {}
        for key, val in list(INPUT_DATA.items()):
            if key == "fields":
                payload[key] = []
                continue
            payload[key] = {}
            for k, _ in list(val.items()):
                payload[key][k] = None
        self._inputs[str(self.index)] = payload

    @property
    def embed(self) -> Embed:
        return self.embeds[self.index]

    def add(self, embed: Optional[Embed] = None) -> None:
        if embed is None:
            embed = Embed()
        self.embeds.append(embed)

    def resolve(self) -> None:
        now = discord.utils.utcnow()
        for i, data in self._inputs.items():
            try:
                value = data["timestamp"]["timestamp"]
            except KeyError:
                continue
            if str(value).lower() in ("now", "0"):
                embed = self.embeds[int(i)]
                embed.timestamp = now

    @classmethod
    def from_embeds(cls, cog: EmbedManager, *, embeds: List[Embed]) -> EmbedEditor:
        """
        Instantiate this class from message's embeds with addition of default values
        were pre-populated from the provided embeds.

        Notes:
        ------
        Since our data scheme is little different from the one in discord.py, we cannot use the
        data from `Embed.to_dict` directly, instead we have to manually pull them out and put in our `._inputs`.
        """
        editor = cls(cog, embeds=embeds)
        for i, embed in enumerate(editor.embeds):
            editor.index = i
            if embed.type != "rich":
                continue
            data = embed.to_dict()
            title = data.pop("title", None)
            editor["title"]["title"] = title
            url = data.pop("url", None)
            if url:
                editor["title"]["url"] = embed.url
            editor["body"]["description"] = data.pop("description", None)
            editor["color"]["value"] = data.pop("color", None)
            images = ["thumbnail", "image"]
            elems = ["author", "footer"]
            for elem in images + elems:
                elem_data = data.pop(elem, {})
                for key, val in elem_data.items():
                    if elem in images:
                        if key != "url":
                            continue
                        key = elem
                        elem = "body"
                    try:
                        editor[elem][key] = val
                    except KeyError:
                        continue
            editor["fields"] = data.pop("fields", [])
            editor["timestamp"]["timestamp"] = data.pop("timestamp", None)
        return editor

    def _set_field_at(self, index: int, *, name: str, value: str, inline: bool) -> None:
        if index > len(self.embed.fields) - 1:
            self.embed.add_field(name=name, value=value, inline=inline)
        else:
            self.embed.set_field_at(index, name=name, value=value, inline=inline)

    def update(self, *, data: Dict[str, Any], category: str) -> Embed:
        """
        Update embed from the response data.
        """
        embed = self.embed
        if category == "fields":
            self._set_field_at(data.pop("index"), **data)
        elif category == "title":
            title = data["title"]
            embed.title = title
            if title:
                url = data["url"]
            else:
                url = None
            embed.url = url
        elif category == "author":
            embed.set_author(**data)
        elif category == "body":
            embed.description = data["description"]
            thumbnail_url = data["thumbnail"]
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
            image_url = data["image"]
            if image_url:
                embed.set_image(url=image_url)
        elif category == "color":
            embed.colour = data["value"]
        elif category == "footer":
            embed.set_footer(**data)
        elif category == "timestamp":
            embed.timestamp = data["timestamp"]
        else:
            raise TypeError(f"`{category}` is invalid category.")
        return embed
