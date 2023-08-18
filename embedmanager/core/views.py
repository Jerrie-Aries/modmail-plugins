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

from .data import DESCRIPTIONS, FOOTER_TEXTS, SHORT_DESCRIPTIONS, INPUT_DATA


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


class FollowupView(muui.View):
    def __init__(self, handler: EmbedBuilderView, interaction: Interaction, *args: Any, **kwargs: Any):
        self.handler: EmbedBuilderView = handler
        self.original_interaction: Interaction = interaction
        super().__init__(*args, **kwargs)

    async def __aenter__(self) -> "FollowupView":
        await self.lock(self.original_interaction)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.message.delete()
        await self.unlock(self.original_interaction)

    async def interaction_check(self, interaction: Interaction) -> bool:
        return await self.handler.interaction_check(interaction)

    async def lock(self, interaction: Interaction) -> None:
        for child in self.handler.children:
            child.disabled = True
        await interaction.response.edit_message(view=self.handler)

    async def unlock(self, interaction: Interaction, **kwargs: Any) -> None:
        for child in self.handler.children:
            child.disabled = False
        self.handler.refresh()
        await interaction.edit_original_response(view=self.handler, **kwargs)

    async def on_timeout(self) -> None:
        pass


class EmbedBuilderView(muui.View):
    """
    Main class to handle the view components and responses from interactions.
    """

    children: List[muui.Button]

    def __init__(
        self, cog: EmbedManager, user: discord.Member, *, timeout: float = 300.0, add_items: bool = True
    ):
        super().__init__(extras=deepcopy(INPUT_DATA), timeout=timeout)
        self.bot: ModmailBot = cog.bot
        self.cog: EmbedManager = cog
        self.user: discord.Member = user
        self.embed: discord.Embed = discord.Embed()
        self.current: Optional[str] = None

        if add_items:
            self._populate_select_options()
            self.refresh()

    def _populate_select_options(self) -> None:
        self._category_select.options.clear()
        for key in self.extras:
            option = discord.SelectOption(
                label=key.title(),
                description=SHORT_DESCRIPTIONS[key],
                value=key,
                default=key == self.current,
            )
            self._category_select.append_option(option)

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, ui.Button):
                continue
            key = child.label.lower()
            if key == "cancel":
                continue
            if not self.current:
                child.disabled = True
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

    @ui.select(placeholder="Select a category", row=0)
    async def _category_select(self, interaction: Interaction, select: ui.Select) -> None:
        self.current = value = select.values[0]
        for opt in select.options:
            opt.default = opt.value == value
        embed = self.message.embeds[0]
        embed.description = "\n".join(DESCRIPTIONS[value])
        if not embed.footer:
            embed.set_footer(text="\n".join(FOOTER_TEXTS["note"]))
        await self.update_view(interaction)

    @ui.button(label="Done", style=ButtonStyle.green)
    async def _action_done(self, *args: Any) -> None:
        interaction, _ = args
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def _field_session(self, interaction: Interaction) -> None:
        buttons = {
            "add field": (ButtonStyle.blurple, self._action_add_field),
            "clear fields": (ButtonStyle.grey, self._action_clear_fields),
            "quit": (ButtonStyle.red, self._action_quit_fields),
        }
        async with FollowupView(self, interaction, timeout=self.timeout) as view:
            for label, item in buttons.items():
                disabled = label == "clear fields" and not self.embed.fields
                view.add_item(
                    muui.Button(label=label.capitalize(), style=item[0], callback=item[1], disabled=disabled)
                )
            view.message = await interaction.followup.send(view=view)
            await view.wait()

    @ui.button(label="Edit", style=ButtonStyle.grey)
    async def _action_edit(self, *args: Any) -> None:
        interaction, _ = args
        if self.current == "fields":
            await self._field_session(interaction)
        else:
            modal = muui.Modal(
                self,
                self.extras[self.current],
                callback=self.on_modal_submit,
                title=self.current.title(),
            )
            await interaction.response.send_modal(modal)

    @ui.button(label="Preview", style=ButtonStyle.grey)
    async def _action_preview(self, *args: Any) -> None:
        interaction, _ = args
        try:
            await interaction.response.send_message(embed=self.embed, ephemeral=True)
        except discord.HTTPException as exc:
            error = f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```"
            await interaction.response.send_message(error, ephemeral=True)

    @ui.button(label="Cancel", style=ButtonStyle.red)
    async def _action_cancel(self, *args: Any) -> None:
        interaction, _ = args
        self.disable_and_stop()
        if self.embed:
            self.embed = MISSING
        await interaction.response.edit_message(view=self)

    async def _action_add_field(self, interaction: Interaction, button: ui.Button) -> None:
        modal = muui.Modal(
            button.view,
            self.extras[self.current],
            callback=self.on_modal_submit,
            title=self.current.title(),
        )
        await interaction.response.send_modal(modal)
        await modal.wait()
        button.view.stop()

    async def _action_clear_fields(self, interaction: Interaction, button: ui.Button) -> None:
        if self.embed.fields:
            self.embed = self.embed.clear_fields()
        await self.update_view()
        await interaction.response.send_message("Cleared all fields.", ephemeral=True)
        button.view.stop()

    async def _action_quit_fields(self, interaction: Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        button.view.stop()

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
            await interaction.response.defer()
            self.embed = self.update_embed(data=resp_data)

        if self.current != "fields":
            await self.update_view()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
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

        self._populate_select_options()
        self.refresh()
        return self