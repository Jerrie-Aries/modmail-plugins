from __future__ import annotations

from typing import Any, Dict

import discord
from discord import ButtonStyle, Interaction, TextStyle
from discord.ui import Button, Modal, TextInput, View
from discord.utils import MISSING


_max_embed_length = 6000
_short_length = 256
_long_length = 4000  # maximum length allowed for modal input
_footer_text_length = 2048
_max_fields = 25
_field_name_length = 1024
_field_value_length = 2048

example_url = "https://discordapp.com/..."

title_raw = {
    "title": {
        "label": "Title",
        "placeholder": "Embed title here...",
        "max_length": _short_length,
    },
    "url": {
        "label": "Embed URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
    },
}
author_raw = {
    "name": {
        "label": "Name",
        "placeholder": "Author name",
        "max_length": _short_length,
    },
    "icon_url": {
        "label": "Icon URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
    },
    "url": {
        "label": "Author URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
    },
}
body_raw = {
    "description": {
        "label": "Description",
        "placeholder": "Your description here...",
        "style": TextStyle.long,
        "max_length": _long_length,
    },
    "thumbnail": {
        "label": "Thumbnail URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
    },
    "image": {
        "label": "Image URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
    },
}
footer_raw = {
    "text": {
        "label": "Text",
        "placeholder": "Footer text",
        "max_length": _footer_text_length,
    },
    "icon_url": {
        "label": "Icon URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
    },
}
add_field_raw = {
    "name": {
        "label": "Name",
        "placeholder": "Field name",
        "max_length": _field_name_length,
        "required": False,
    },
    "value": {
        "label": "Value",
        "placeholder": "Field value",
        "max_length": _field_value_length,
        "style": TextStyle.long,
        "required": False,
    },
}


ret_buttons = {
    "done": ButtonStyle.green,
    "preview": ButtonStyle.grey,
    "cancel": ButtonStyle.red,
}


class EmbedBuilderViewButton(Button):
    def __init__(
        self,
        label: str,
        style: ButtonStyle,
        *,
        row: int = None,
        data: Dict[str, Any] = MISSING,
    ):
        super().__init__(label=label, style=style, row=row)

        # for `ret_buttons` the value for this attribute would
        # be the default value i.e. `MISSING`
        self.data: Dict[str, Any] = data

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        label = self.label.lower()
        if label == "done":
            try:
                self.view.embed = self.view.build_embed()
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
            else:
                await self.view.disable_and_stop()
                await interaction.response.edit_message(view=self.view)
        elif label == "preview":
            try:
                embed = self.view.build_embed()
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        elif label == "cancel":
            await self.view.disable_and_stop()
            await interaction.response.edit_message(view=self.view)
        else:
            modal = EmbedBuilderModal(self.view, self.label, data=self.data)
            await interaction.response.send_modal(modal)
            await modal.wait()


class EmbedBuilderView(View):
    def __init__(self, user: discord.Member, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.user: discord.Member = user
        self.message: discord.Message = MISSING  # should be reassigned
        self.base_input_map: Dict[str, Any] = {
            "title": title_raw,
            "author": author_raw,
            "body": body_raw,
            "footer": footer_raw,
            "add field": add_field_raw,
        }
        self.response_map = {
            "fields": [],
        }

        self.embed: discord.Embed = MISSING

    @discord.ui.button(label="Create embed", style=ButtonStyle.grey)
    async def create(self, interaction: Interaction, button: Button):
        self.clear_items()
        self._generate_buttons()
        embed = self.message.embeds[0]
        description = (
            "Use the buttons below respectively to set the value.\n\n"
            "__**Button map:**__\n\n"
            "**Title:**\n"
            "- `Title`: The title of embed.\n- `Embed URL`: The URL of embed.\n\n"
            "**Author:**\n"
            "- `Name`: Name of author.\n- `Icon URL`: URL of author icon.\n- `Author URL`: URL of author.\n\n"
            "**Body:**\n"
            "- `Description`: Description of embed.\n- `Thumbnail URL`: URL of thumbnail image (shown at top right).\n- `Image URL`: URL of embed image (shown at bottom).\n\n"
            "**Footer:**\n"
            "- `Text`: The text shown on footer (can be up to 2048 characters).\n- `Icon URL`: URL of footer icon.\n\n"
            "**Add Field:**\n"
            "- `Name`: Name of the field.\n- `Value`: Value of the field, can be up to 1024 characters.\n\n\n"
            "__**Note:**__\n"
            "- Embed fields can be added up to 25.\n"
            "- The combine sum of characters in embeds in a single message must not exceed 6000 characters."
        )
        embed.description = description
        await interaction.response.edit_message(embed=embed, view=self)

    def _generate_buttons(self) -> None:
        for key, data in self.base_input_map.items():
            self.add_item(
                EmbedBuilderViewButton(key.title(), ButtonStyle.blurple, data=data)
            )

        for label, style in ret_buttons.items():
            self.add_item(EmbedBuilderViewButton(label.title(), style, row=4))

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        await interaction.response.send_message(
            "This view cannot be controlled by you!",
            ephemeral=True,
        )
        return False

    def build_embed(self) -> discord.Embed:
        """
        Build an embed from the stored response data.
        """
        embed_data = {}
        body = self.response_map.get("body", {})
        description = body.get("description", "")
        if description:
            embed_data["description"] = description
        thumbnail_url = body.get("thumbnail", "")
        if thumbnail_url:
            embed_data["thumbnail"] = {"url": thumbnail_url}
        image_url = body.get("image", "")
        if image_url:
            embed_data["image"] = {"url": image_url}
        author_data = self.response_map.get("author", {})
        if author_data:
            embed_data["author"] = author_data
        title_data = self.response_map.get("title", {})
        title = title_data.get("title", "")
        if title:
            embed_data["title"] = title
            url = title_data.get("url", "")

            # url can only be added if the title exists
            if url:
                embed_data["url"] = url
        footer_data = self.response_map.get("footer", {})
        if footer_data:
            embed_data["footer"] = footer_data
        embed_data["fields"] = self.response_map.get("fields", [])
        embed_data["timestamp"] = str(discord.utils.utcnow())

        embed = discord.Embed.from_dict(embed_data)
        length = len(embed)
        if not length:
            raise ValueError("Embed is emp.")
        if length > _max_embed_length:
            raise ValueError(
                f"Embed length exceeds the maximum length allowed, {length}/{_max_embed_length}."
            )

        return embed

    async def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        # Edit the message without `interaction.response`
        await self.message.edit(view=self)


class EmbedBuilderTextInput(TextInput):
    def __init__(self, name: str, **data):
        self.name: str = name
        super().__init__(**data)


class EmbedBuilderModal(Modal):
    """
    Represents modal view for embed builder.
    """

    children: [EmbedBuilderTextInput]

    def __init__(self, manager: EmbedBuilderView, title: str, *, data: Any = MISSING):
        super().__init__(title=title)
        self.manager = manager
        for key, value in data.items():
            self.add_item(EmbedBuilderTextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        response_data = {}
        for child in self.children:
            response_data[child.name] = child.value

        title = self.title.lower()
        if title == "add field":
            # special case where we actually append the data
            self.manager.response_map["fields"].append(response_data)
        else:
            self.manager.response_map[title] = response_data
        await interaction.response.defer()
        self.stop()
