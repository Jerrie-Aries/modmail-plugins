from __future__ import annotations

from distutils.util import strtobool
from typing import Any, Awaitable, Callable, Dict, List, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction, TextStyle
from discord.ui import Button, Modal, TextInput, View
from discord.utils import MISSING
from yarl import URL


if TYPE_CHECKING:
    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]


def _color_converter(value: str) -> int:
    try:
        return int(discord.Color.from_str(value))
    except ValueError:
        raise ValueError(f"`{value}` is unknown color format.")


def _bool_converter(value: str) -> bool:
    if not value:
        return False
    try:
        return strtobool(value)
    except ValueError:
        raise ValueError(f"`{value}` is unknown boolean value.")


def _url_checker(value: str) -> str:
    if not value:
        return ""
    url = URL(value)
    if url.scheme not in ("http", "https"):
        raise ValueError("Invalid url schema. URLs must start with either `http` or `https`.")
    if "." not in url.host:
        raise ValueError(f"Not a well formed URL, `{value}`.")
    return str(url)


_max_embed_length = 6000
_short_length = 256
_long_length = 4000  # maximum length allowed for modal input
_footer_text_length = 2048
_max_fields = 25
_field_name_length = 1024
_field_value_length = 2048

example_url = "https://example.com/"

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
        "converter": _url_checker,
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
        "converter": _url_checker,
    },
    "url": {
        "label": "Author URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
        "converter": _url_checker,
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
        "converter": _url_checker,
    },
    "image": {
        "label": "Image URL",
        "placeholder": example_url,
        "max_length": _short_length,
        "required": False,
        "converter": _url_checker,
    },
}
color_raw = {
    "value": {
        "label": "Value",
        "placeholder": "#ffffff",
        "max_length": 32,
        "converter": _color_converter,
    }
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
        "converter": _url_checker,
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
    "inline": {
        "label": "Inline",
        "placeholder": "Boolean",
        "max_length": 5,
        "required": False,
        "converter": _bool_converter,
    },
}


class EmbedBuilderTextInput(TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        try:
            self.convert_value = kwargs.pop("converter")
        except KeyError:
            self.convert_value = MISSING
        super().__init__(**kwargs)


class EmbedBuilderModal(Modal):
    """
    Represents modal view for embed builder.
    """

    children: List[EmbedBuilderTextInput]

    def __init__(self, manager: EmbedBuilderView, key: str):
        super().__init__(title=key.title())
        self.manager = manager
        data = self.manager.base_input_map[key]
        for key, value in data.items():
            self.add_item(EmbedBuilderTextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        response_data = {}
        for child in self.children:
            if child.convert_value is not MISSING:
                try:
                    value = child.convert_value(child.value)
                except ValueError as exc:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                    self.stop()
                    return
            else:
                value = child.value
            response_data[child.name] = value

        title = self.title.lower()
        if title == "add field":
            # special case where we actually append the data
            self.manager.response_map["fields"].append(response_data)
        else:
            self.manager.response_map[title] = response_data
        await interaction.response.defer()
        self.stop()


class EmbedBuilderButton(Button["EmbedBuilderView"]):
    def __init__(
        self,
        label: str,
        *,
        style: ButtonStyle = ButtonStyle.blurple,
        row: int = None,
        callback: ButtonCallbackT = MISSING,
    ):
        super().__init__(label=label, style=style, row=row)
        self.callback_override: ButtonCallbackT = callback

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        key = self.label.lower()
        params = [interaction]
        if key in self.view.base_input_map.keys():
            params.append(key)
        await self.callback_override(*tuple(params))


class EmbedBuilderView(View):
    """
    Main class to handle the view components and responses from interactions.
    """

    children: List[EmbedBuilderButton]

    def __init__(self, user: discord.Member, *, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.user: discord.Member = user
        self.message: discord.Message = MISSING  # should be reassigned

        # button labels and modal titles will be based on these keys
        self.base_input_map: Dict[str, Any] = {
            "title": title_raw,
            "author": author_raw,
            "body": body_raw,
            "footer": footer_raw,
            "color": color_raw,
            "add field": add_field_raw,
        }
        self.ret_buttons: Dict[str, Any] = {
            "done": (ButtonStyle.green, self._action_done),
            "preview": (ButtonStyle.grey, self._action_preview),
            "cancel": (ButtonStyle.red, self._action_cancel),
        }
        self.response_map = {
            "fields": [],
        }

        self.embed: discord.Embed = MISSING

    def _generate_buttons(self) -> None:
        for key, data in self.base_input_map.items():
            self.add_item(EmbedBuilderButton(key.title(), callback=self._create_modal))

        # manually add this one
        self.add_item(
            EmbedBuilderButton(
                "Clear Fields",
                style=ButtonStyle.grey,
                callback=self._action_clear_fields,
            )
        )

        for label, item in self.ret_buttons.items():
            # `item` is a tuple of (ButtonStyle, callback)
            self.add_item(EmbedBuilderButton(label.title(), style=item[0], row=4, callback=item[1]))

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
            "**Color:**\n"
            "- `Value`: Color code of the embed.\n"
            "The following formats are accepted:\n\t- `0x<hex>`\n\t- `#<hex>`\n\t- `0x#<hex>`\n\t- `rgb(<number>, <number>, <number>)`\n"
            "Like CSS, `<number>` can be either 0-255 or 0-100% and `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).\n\n"
            "**Add Field:**\n"
            "- `Name`: Name of the field.\n- `Value`: Value of the field, can be up to 1024 characters.\n- `Inline`: Whether or not this field should display inline.\n\n\n"
            "__**Note:**__\n"
            "- Embed fields can be added up to 25.\n"
            "- The combine sum of characters in embeds in a single message must not exceed 6000 characters."
        )
        embed.description = description
        await interaction.response.edit_message(embed=embed, view=self)

    async def _create_modal(self, interaction: Interaction, key: str) -> None:
        modal = EmbedBuilderModal(self, key)
        await interaction.response.send_modal(modal)
        await modal.wait()

    async def _action_clear_fields(self, interaction: Interaction) -> None:
        self.response_map["fields"] = []
        await interaction.response.send_message("Cleared all fields.", ephemeral=True)

    async def _action_done(self, interaction: Interaction) -> None:
        try:
            self.embed = self.build_embed()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
        else:
            self.disable_and_stop()
            await interaction.response.edit_message(view=self)

    async def _action_preview(self, interaction: Interaction) -> None:
        try:
            embed = self.build_embed()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
        else:
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except discord.HTTPException as exc:
                error = f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```"
                await interaction.response.send_message(error, ephemeral=True)

    async def _action_cancel(self, interaction: Interaction) -> None:
        self.disable_and_stop()
        if self.embed:
            self.embed = MISSING
        await interaction.response.edit_message(view=self)

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
        color_data = self.response_map.get("color", {})
        if color_data:
            embed_data["color"] = color_data["value"]
        embed_data["fields"] = self.response_map.get("fields", [])
        embed_data["timestamp"] = str(discord.utils.utcnow())

        embed = discord.Embed.from_dict(embed_data)
        length = len(embed)
        if not length:
            raise ValueError("Embed is empty.")
        if length > _max_embed_length:
            raise ValueError(
                f"Embed length exceeds the maximum length allowed, {length}/{_max_embed_length}."
            )

        return embed

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        # Edit the message without `interaction.response`
        await self.message.edit(view=self)
