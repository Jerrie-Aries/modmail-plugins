from __future__ import annotations

from copy import deepcopy
from distutils.util import strtobool
from typing import Any, Awaitable, Callable, Dict, Optional, List, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord import ui
from discord.utils import MISSING
from discord.ext.modmail_utils import ui as muui
from yarl import URL

from .data import DESCRIPTIONS, SHORT_DESCRIPTIONS, INPUT_DATA


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..embedmamager import EmbedManager

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


class Select(ui.Select):
    def __init__(self, *, options: List[discord.SelectOption], **kwargs):
        super().__init__(
            placeholder=kwargs.pop("placeholder") or "Select a category",
            min_values=1,
            max_values=1,
            options=options,
            **kwargs,
        )

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        await self.view.on_dropdown_select(interaction, self)

    def get_option(self, value: str) -> discord.SelectOption:
        for option in self.options:
            if option.value == value:
                return option
        raise ValueError(f"Cannot find select option with value of `{value}`.")


class EmbedBuilderView(muui.View):
    """
    Main class to handle the view components and responses from interactions.
    """

    children: List[muui.Button]

    def __init__(
        self, cog: EmbedManager, user: discord.Member, *, timeout: float = 600.0, add_items: bool = True
    ):
        super().__init__(extras=deepcopy(INPUT_DATA), timeout=timeout)
        self.bot: ModmailBot = cog.bot
        self.cog: EmbedManager = cog
        self.user: discord.Member = user
        self.embed: discord.Embed = discord.Embed()
        self.current: Optional[str] = None

        if add_items:
            self._add_menu()
            self._generate_buttons()
            self.refresh()

    def _add_menu(self) -> None:
        options = []
        placeholder = None
        for key in self.extras:
            if key == self.current:
                placeholder = key.title()
            option = discord.SelectOption(
                label=key.title(),
                description=SHORT_DESCRIPTIONS[key],
                value=key,
            )
            options.append(option)
        self.add_item(Select(options=options, row=0, placeholder=placeholder))

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
            self.add_item(muui.Button(label=label.title(), style=item[0], row=4, callback=item[1]))

    def _add_field_buttons(self) -> None:
        buttons = {
            "add field": (ButtonStyle.blurple, self._action_add_field),
            "clear fields": (ButtonStyle.grey, self._action_clear_fields),
        }
        for label, item in buttons.items():
            self.add_item(muui.Button(label=label.title(), style=item[0], row=3, callback=item[1]))

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, muui.Button):
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

    async def update_view(self, interaction: Optional[Interaction] = None) -> None:
        self.refresh()
        if interaction and not interaction.response.is_done():
            func = interaction.response.edit_message
        else:
            func = self.message.edit
        await func(embed=self.message.embeds[0], view=self)

    async def on_dropdown_select(self, interaction: Interaction, select: Select) -> None:
        value = select.values[0]
        option = select.get_option(value)
        select.placeholder = option.label
        self.current = value
        embed = self.message.embeds[0]
        embed.description = "\n".join(DESCRIPTIONS[value]) + "\n\n" + "\n".join(DESCRIPTIONS["note"])
        self.clear_items()
        self._add_menu()
        self._generate_buttons()
        await self.update_view(interaction)

    async def on_modal_submit(self, interaction: Interaction, modal: muui.Modal) -> None:
        modal.stop()
        for child in modal.children:
            value = child.value
            if not value:
                value = None
            self.extras[self.current][child.name]["default"] = value

        errors = []
        data = self.extras[self.current]
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
            content = "\n".join(f"{n}. {error}" for n, error in enumerate(errors, start=1))
            embed = discord.Embed(
                title="__Errors__",
                color=self.bot.error_color,
                description=content,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            self.embed = self.update_embed(data=resp_data)
        await self.update_view(interaction)

    async def _action_add_field(self, *args: Any) -> None:
        await self._action_edit(*args)

    async def _action_clear_fields(self, *args: Any) -> None:
        interaction, _ = args
        if self.embed.fields:
            self.embed = self.embed.clear_fields()
        await self.update_view(interaction)
        await interaction.followup.send("Cleared all fields.", ephemeral=True)

    async def _action_done(self, *args: Any) -> None:
        interaction, _ = args
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def _action_edit(self, *args: Any) -> None:
        interaction, _ = args
        modal = muui.Modal(
            self,
            self.extras[self.current],
            callback=self.on_modal_submit,
            title=self.current.title(),
        )
        await interaction.response.send_modal(modal)
        await modal.wait()

    async def _action_preview(self, *args: Any) -> None:
        interaction, _ = args
        try:
            await interaction.response.send_message(embed=self.embed, ephemeral=True)
        except discord.HTTPException as exc:
            error = f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```"
            await interaction.response.send_message(error, ephemeral=True)

    async def _action_cancel(self, *args: Any) -> None:
        interaction, _ = args
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
    def from_embed(cls, cog: EmbedManager, user: discord.Member, *, embed: discord.Embed) -> EmbedBuilderView:
        self = cls(cog, user, add_items=False)
        self.embed = embed
        data = embed.to_dict()
        title = data.get("title")
        self.extras["title"]["title"]["default"] = title
        url = data.get("url")
        if url:
            self.extras["title"]["url"]["default"] = embed.url
        self.extras["body"]["description"]["default"] = data.get("description")
        self.extras["color"]["value"]["default"] = data.get("color")
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
                        self.extras[elem][key]["default"] = val
                    except KeyError:
                        continue

        self._add_menu()
        self._generate_buttons()
        self.refresh()
        return self
