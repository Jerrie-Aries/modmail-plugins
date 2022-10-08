from __future__ import annotations

from distutils.util import strtobool
from typing import Any, Awaitable, Callable, Dict, Optional, List, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction, TextStyle
from discord.ui import Button, Modal, Select, TextInput, View
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


def _resolve_conversion(key: str, sub_key: str, value: str) -> Any:
    if sub_key in ("url", "icon_url", "thumbnail", "image"):
        return _url_checker(value)
    if key == "color":
        return _color_converter(value)
    if key == "fields" and sub_key == "inline":
        return _bool_converter(value)
    return value


_max_embed_length = 6000
_short_length = 256
_long_length = 4000  # maximum length allowed for modal input
_footer_text_length = 2048
_max_fields = 25
_field_name_length = 1024
_field_value_length = 2048

_description = {
    "title": ["**Title:**", "- `Title`: The title of embed.", "- `Embed URL`: The URL of embed.\n"],
    "author": [
        "**Author:**",
        "- `Name`: Name of author.",
        "- `Icon URL`: URL of author icon.",
        "- `Author URL`: URL of author.\n",
    ],
    "body": [
        "**Body:**",
        "- `Description`: Description of embed.",
        "- `Thumbnail URL`: URL of thumbnail image (shown at top right).",
        "- `Image URL`: URL of embed image (shown at bottom).\n",
    ],
    "footer": [
        "**Footer:**",
        "- `Text`: The text shown on footer (can be up to 2048 characters).",
        "- `Icon URL`: URL of footer icon.\n",
    ],
    "color": [
        "**Color:**",
        "- `Value`: Color code of the embed.",
        "The following formats are accepted:",
        "\t- `0x<hex>`\n\t- `#<hex>`\n\t- `0x#<hex>`\n\t- `rgb(<number>, <number>, <number>)`",
        "Like CSS, `<number>` can be either 0-255 or 0-100% and `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).\n",
    ],
    "fields": [
        "**Fields:**",
        "- `Name`: Name of the field.",
        "- `Value`: Value of the field, can be up to 1024 characters.",
        "- `Inline`: Whether or not this field should display inline.\n",
        "Click `Add Field` to add a new field, or `Clear Fields` to clear all fields, if any.",
        "Embed fields can be added up to 25.\n",
    ],
    "note": [
        "__**Notes:**__",
        "- The combine sum of characters in embeds in a single message must not exceed 6000 characters.\n",
    ],
}

_short_desc = {
    "title": "The title of embed including URL.",
    "author": "The author of the embed.",
    "body": "Description, thumbnail and image URLs.",
    "footer": "The footer text and/or icon of the embed.",
    "color": "Embed's color.",
    "fields": "Add or remove fields.",
}


class EmbedBuilderTextInput(TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        super().__init__(**kwargs)


class EmbedBuilderModal(Modal):
    """
    Represents modal view for embed builder.
    """

    children: List[EmbedBuilderTextInput]

    def __init__(self, view: EmbedBuilderView, key: str):
        super().__init__(title=key.title())
        self.view = view
        data = self.view.input_map[key]
        for key, value in data.items():
            self.add_item(EmbedBuilderTextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        for child in self.children:
            value = child.value
            if not value:
                value = None
            self.view.input_map[self.view.current][child.name]["default"] = value

        await interaction.response.defer()
        self.stop()
        await self.view.on_modal_submit(interaction)


class EmbedBuilderDropdown(Select):
    def __init__(self, *, options: List[discord.SelectOption], **kwargs):
        super().__init__(
            placeholder=kwargs.pop("placeholder") or "Select a category",
            min_values=1,
            max_values=1,
            options=options,
            **kwargs,
        )

    async def callback(self, interaction: Interaction):
        await interaction.response.defer()
        assert self.view is not None
        value = self.values[0]
        option = self.get_option(value)
        self.placeholder = option.label
        await self.view.on_dropdown_select(value)

    def get_option(self, value: str) -> discord.SelectOption:
        for option in self.options:
            if option.value == value:
                return option
        raise ValueError(f"Cannot find select option with value of `{value}`.")


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
        if key in self.view.input_map.keys():
            params.append(key)
        await self.callback_override(*tuple(params))


class EmbedBuilderView(View):
    """
    Main class to handle the view components and responses from interactions.
    """

    children: List[EmbedBuilderButton]

    def __init__(self, user: discord.Member, *, timeout: float = 600.0, add_items: bool = True):
        super().__init__(timeout=timeout)
        self.user: discord.Member = user
        self.message: discord.Message = MISSING
        self.embed: discord.Embed = discord.Embed()
        self.current: Optional[str] = None

        self.input_map: Dict[str, Any] = {
            "title": {
                "title": {
                    "label": "Title",
                    "placeholder": "Embed title here...",
                    "max_length": _short_length,
                },
                "url": {
                    "label": "Embed URL",
                    "max_length": _short_length,
                    "required": False,
                },
            },
            "author": {
                "name": {
                    "label": "Name",
                    "placeholder": "Author name",
                    "max_length": _short_length,
                },
                "icon_url": {
                    "label": "Icon URL",
                    "max_length": _short_length,
                    "required": False,
                },
                "url": {
                    "label": "Author URL",
                    "max_length": _short_length,
                    "required": False,
                },
            },
            "body": {
                "description": {
                    "label": "Description",
                    "style": TextStyle.long,
                    "max_length": _long_length,
                },
                "thumbnail": {
                    "label": "Thumbnail URL",
                    "max_length": _short_length,
                    "required": False,
                },
                "image": {
                    "label": "Image URL",
                    "max_length": _short_length,
                    "required": False,
                },
            },
            "color": {
                "value": {
                    "label": "Value",
                    "placeholder": "#ffffff",
                    "max_length": 32,
                },
            },
            "footer": {
                "text": {
                    "label": "Text",
                    "placeholder": "Footer text",
                    "max_length": _footer_text_length,
                },
                "icon_url": {
                    "label": "Icon URL",
                    "max_length": _short_length,
                    "required": False,
                },
            },
            "fields": {
                "name": {
                    "label": "Name",
                    "max_length": _field_name_length,
                },
                "value": {
                    "label": "Value",
                    "max_length": _field_value_length,
                    "style": TextStyle.long,
                },
                "inline": {
                    "label": "Inline",
                    "max_length": 5,
                    "required": False,
                },
            },
        }
        if add_items:
            self._add_menu()
            self._generate_buttons()
            self.refresh()

    def _add_menu(self) -> None:
        options = []
        placeholder = None
        for key in self.input_map:
            if key == self.current:
                placeholder = key.title()
            option = discord.SelectOption(
                label=key.title(),
                description=_short_desc[key],
                value=key,
            )
            options.append(option)
        self.add_item(EmbedBuilderDropdown(options=options, row=0, placeholder=placeholder))

    def _generate_buttons(self) -> None:
        if self.current == "fields":
            self._add_field_buttons()

        buttons: Dict[str, Any] = {
            "done": (ButtonStyle.green, self._action_done),
            "edit": (ButtonStyle.grey, self._action_edit),
            "preview": (ButtonStyle.grey, self._action_preview),
            "cancel": (ButtonStyle.red, self._action_cancel),
        }

        for label, item in buttons.items():
            self.add_item(EmbedBuilderButton(label.title(), style=item[0], row=4, callback=item[1]))

    def _add_field_buttons(self) -> None:
        buttons = {
            "add field": (ButtonStyle.blurple, self._action_add_field),
            "clear fields": (ButtonStyle.grey, self._action_clear_fields),
        }
        for label, item in buttons.items():
            self.add_item(EmbedBuilderButton(label.title(), style=item[0], row=3, callback=item[1]))

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, EmbedBuilderButton):
                continue
            key = child.label.lower()
            if key == "cancel":
                continue
            if not self.current:
                child.disabled = True
                continue
            if self.current == "fields":
                if key == "edit":
                    child.disabled = True
                    continue
                if key == "clear fields":
                    child.disabled = not self.embed.fields
                    continue
            if key in ("done", "preview"):
                child.disabled = len(self.embed) == 0
            else:
                child.disabled = False

    async def update_view(self) -> None:
        self.refresh()
        await self.message.edit(embed=self.message.embeds[0], view=self)

    async def on_dropdown_select(self, value: str) -> None:
        self.current = value
        embed = self.message.embeds[0]
        embed.description = "\n".join(_description[value]) + "\n\n" + "\n".join(_description["note"])
        self.clear_items()
        self._add_menu()
        self._generate_buttons()
        await self.update_view()

    async def on_modal_submit(self, interaction: Interaction) -> None:
        errors = []
        data = self.input_map[self.current]
        resp_data = {}
        for key, group in data.items():
            if self.current == "fields":
                value = group.pop("default")
            else:
                value = group.get("default")
            try:
                value = _resolve_conversion(self.current, key, value)
            except Exception as exc:
                errors.append(str(exc))
            else:
                resp_data[key] = value

        if errors:
            for error in errors:
                await interaction.followup.send(error, ephemeral=True)
        else:
            self.embed = self.update_embed(data=resp_data)
        await self.update_view()

    async def _action_add_field(self, interaction: Interaction) -> None:
        await self._action_edit(interaction)

    async def _action_clear_fields(self, interaction: Interaction) -> None:
        if self.embed.fields:
            self.embed = self.embed.clear_fields()
        await self.update_view()
        await interaction.response.send_message("Cleared all fields.", ephemeral=True)

    async def _action_done(self, interaction: Interaction) -> None:
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def _action_edit(self, interaction: Interaction) -> None:
        modal = EmbedBuilderModal(self, self.current)
        await interaction.response.send_modal(modal)
        await modal.wait()

    async def _action_preview(self, interaction: Interaction) -> None:
        try:
            await interaction.response.send_message(embed=self.embed, ephemeral=True)
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
            "This panel cannot be controlled by you!",
            ephemeral=True,
        )
        return False

    def update_embed(self, *, data: Dict[str, Any]) -> discord.Embed:
        """
        Update embed from the response data.
        """
        if self.current == "title":
            title = data["title"]
            self.embed.title = title
            if title:
                url = data["url"]
            else:
                url = None
            self.embed.url = url
        if self.current == "author":
            self.embed.set_author(**data)
        if self.current == "body":
            self.embed.description = data["description"]
            thumbnail_url = data["thumbnail"]
            if thumbnail_url:
                self.embed.set_thumbnail(url=thumbnail_url)
            image_url = data["image"]
            if image_url:
                self.embed.set_image(url=image_url)
        if self.current == "color":
            self.embed.colour = data["value"]
        if self.current == "footer":
            self.embed.set_footer(**data)
        if self.current == "fields":
            self.embed.add_field(**data)
        self.embed.timestamp = discord.utils.utcnow()
        return self.embed

    @classmethod
    def from_embed(cls, user: discord.Member, *, embed: discord.Embed) -> EmbedBuilderView:
        self = cls(user, add_items=False)
        self.embed = embed
        data = embed.to_dict()
        title = data.get("title")
        self.input_map["title"]["title"]["default"] = title
        url = data.get("url")
        if url:
            self.input_map["title"]["url"]["default"] = embed.url
        self.input_map["body"]["description"]["default"] = data.get("description")
        self.input_map["color"]["value"]["default"] = data.get("color")
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
                        self.input_map[elem][key]["default"] = val
                    except KeyError:
                        continue

        self._add_menu()
        self._generate_buttons()
        self.refresh()
        return self

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        await self.message.edit(view=self)
