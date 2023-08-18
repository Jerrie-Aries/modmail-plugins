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
from .models import EmbedEditor


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
        self,
        cog: EmbedManager,
        user: discord.Member,
        *,
        editor: EmbedEditor = MISSING,
        timeout: float = 300.0,
    ):
        super().__init__(extras=deepcopy(INPUT_DATA), timeout=timeout)
        self.bot: ModmailBot = cog.bot
        self.cog: EmbedManager = cog
        self.editor: EmbedEditor = editor if editor is not MISSING else EmbedEditor(cog)
        self.user: discord.Member = user
        self.category: Optional[str] = None
        self.__base_description: Optional[str] = None

        self._populate_select_options()
        self.refresh()

    def _populate_select_options(self) -> None:
        self._embed_select.options.clear()
        for i, embed in enumerate(self.editor.embeds):
            default = i == self.editor.index and len(self.editor.embeds) > 1
            self._embed_select.append_option(
                discord.SelectOption(
                    label=f"Embed {i + 1}",
                    value=str(i),
                    default=default,
                ),
            )
        self._category_select.options.clear()
        for key in self.extras:
            option = discord.SelectOption(
                label=key.title(),
                description=SHORT_DESCRIPTIONS[key],
                value=key,
                default=key == self.category,
            )
            self._category_select.append_option(option)

    def refresh(self) -> None:
        for child in self.children:
            if child == self._embed_select:
                child.disabled = len(self.editor.embeds) <= 1
                continue
            if not isinstance(child, ui.Button):
                continue
            key = child.label.lower()
            if key == "cancel":
                continue
            curr_not_ready = len(self.editor.embed) == 0
            if not self.category and curr_not_ready and len(self.editor.embeds) <= 1:
                # first launch
                child.disabled = True
                continue
            if key == "new":
                child.disabled = curr_not_ready or len(self.editor.embeds) >= 10
                continue
            if key == "edit":
                child.disabled = not self.category
                continue
            if key in ("done", "preview"):
                child.disabled = curr_not_ready
            else:
                child.disabled = False

    async def update_view(self, interaction: Optional[Interaction] = None) -> None:
        self.refresh()
        if interaction and not interaction.response.is_done():
            func = interaction.response.edit_message
        else:
            func = self.message.edit
        await func(embed=self.message.embeds[0], view=self)

    @ui.select(placeholder="Select an embed", row=0)
    async def _embed_select(self, interaction: Interaction, select: ui.Select) -> None:
        value = select.values[0]
        self.editor.index = int(value)
        self.category = None
        self._populate_select_options()
        if self.__base_description:
            embed = self.message.embeds[0]
            embed.description = self.__base_description
        await self.update_view(interaction)

    @ui.select(placeholder="Select a category", row=1)
    async def _category_select(self, interaction: Interaction, select: ui.Select) -> None:
        self.category = value = select.values[0]
        for opt in select.options:
            opt.default = opt.value == value
        embed = self.message.embeds[0]
        if self.__base_description is None:
            self.__base_description = embed.description
        embed.description = "\n".join(DESCRIPTIONS[value])
        if not embed.footer:
            embed.set_footer(text="\n".join(FOOTER_TEXTS["note"]))
        await self.update_view(interaction)

    @ui.button(label="Done", style=ButtonStyle.green)
    async def _action_done(self, *args: Any) -> None:
        interaction, _ = args
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.value = True
        self.disable_and_stop()

    @ui.button(label="New", style=ButtonStyle.blurple)
    async def _action_new(self, *args: Any) -> None:
        interaction, _ = args
        self.editor.add()
        self.editor.index = len(self.editor.embeds) - 1
        self.category = None
        self._populate_select_options()
        await self.update_view(interaction)

    async def _field_session(self, interaction: Interaction) -> None:
        buttons = {
            "add field": (ButtonStyle.blurple, self._action_add_field),
            "clear fields": (ButtonStyle.grey, self._action_clear_fields),
            "quit": (ButtonStyle.red, self._action_quit_fields),
        }
        async with FollowupView(self, interaction, timeout=self.timeout) as view:
            for label, item in buttons.items():
                disabled = label == "clear fields" and not self.editor.embed.fields
                view.add_item(
                    muui.Button(label=label.capitalize(), style=item[0], callback=item[1], disabled=disabled)
                )
            view.message = await interaction.followup.send(view=view)
            await view.wait()

    @ui.button(label="Edit", style=ButtonStyle.grey)
    async def _action_edit(self, *args: Any) -> None:
        interaction, _ = args
        if self.category == "fields":
            await self._field_session(interaction)
        else:
            payload = deepcopy(self.extras[self.category])
            for key in list(payload.keys()):
                try:
                    payload[key]["default"] = self.editor[self.category][key]["default"]
                except KeyError:
                    continue
            modal = muui.Modal(
                self,
                payload,
                callback=self.on_modal_submit,
                title=self.category.title(),
            )
            await interaction.response.send_modal(modal)

    @ui.button(label="Preview", style=ButtonStyle.grey)
    async def _action_preview(self, *args: Any) -> None:
        interaction, _ = args
        try:
            await interaction.response.send_message(embeds=self.editor.embeds, ephemeral=True)
        except discord.HTTPException as exc:
            error = f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```"
            await interaction.response.send_message(error, ephemeral=True)

    @ui.button(label="Cancel", style=ButtonStyle.red)
    async def _action_cancel(self, *args: Any) -> None:
        interaction, _ = args
        self.disable_and_stop()
        self.editor.embeds.clear()
        await interaction.response.edit_message(view=self)

    async def _action_add_field(self, interaction: Interaction, button: ui.Button) -> None:
        modal = muui.Modal(
            button.view,
            self.extras[self.category],
            callback=self.on_modal_submit,
            title=self.category.title(),
        )
        await interaction.response.send_modal(modal)
        await modal.wait()
        button.view.stop()

    async def _action_clear_fields(self, interaction: Interaction, button: ui.Button) -> None:
        if self.editor.embed.fields:
            self.editor.embed.clear_fields()
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
            self.editor[self.category][child.name]["default"] = value

        errors = []
        data = self.editor[self.category]
        resp_data = {}
        for key, group in data.items():
            if self.category == "fields":
                value = group.pop("default")
            else:
                value = group.get("default")
            try:
                value = _resolve_conversion(self.category, key, value)
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
            self.editor.update(data=resp_data, category=self.category)

        if self.category != "fields":
            await self.update_view()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        return False

    @classmethod
    def from_embed(
        cls,
        cog: EmbedManager,
        user: discord.Member,
        *,
        embeds: List[discord.Embed],
        index: int = 0,
    ) -> EmbedBuilderView:
        editor = EmbedEditor(cog, embeds)
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
        editor.index = index
        return cls(cog, user, editor=editor)
