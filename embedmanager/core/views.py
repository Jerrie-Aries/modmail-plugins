from __future__ import annotations

from copy import deepcopy
from datetime import datetime
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


def _timestamp_converter(value: Optional[str]) -> str:
    if value is None:
        return None
    if value.lower() in ("now", "0"):
        return discord.utils.utcnow()
    try:
        return datetime.fromtimestamp(float(value))
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"`{value}` is not a valid format for timestamp.")


def _resolve_conversion(key: str, sub_key: str, value: str) -> Any:
    if sub_key in ("url", "icon_url", "thumbnail", "image"):
        return _url_checker(value)
    if key == "color":
        return _color_converter(value)
    if key == "fields" and sub_key == "inline":
        return _bool_converter(value)
    if key == "timestamp":
        return _timestamp_converter(value)
    return value


class FieldEditorView(muui.View):
    __default = {"name": None, "value": None, "inline": None}

    def __init__(self, handler: EmbedBuilderView, interaction: Interaction, *args: Any, **kwargs: Any):
        self.handler: EmbedBuilderView = handler
        self.editor: EmbedEditor = handler.editor
        self.original_interaction: Interaction = interaction
        self.index: int = 0
        super().__init__(*args, **kwargs)

        self._populate_select_options()
        self.refresh()

    @property
    def raw_fields(self) -> List[Dict[str, Any]]:
        return self.editor["fields"]

    def _populate_select_options(self) -> None:
        self._field_select.options.clear()
        if not self.raw_fields:
            self.raw_fields.append(deepcopy(self.__default))
        for i, _ in enumerate(self.raw_fields):
            option = discord.SelectOption(
                label=f"Field {i + 1}",
                value=str(i),
                default=i == self.index,
            )
            self._field_select.append_option(option)

    def refresh(self) -> None:
        for child in self.children:
            if child == self._field_select:
                child.disabled = len(self.raw_fields) <= 1
                continue
            if not isinstance(child, ui.Button):
                continue
            key = child.label.lower()
            if key == "new":
                child.disabled = (
                    any((f == self.__default for f in self.raw_fields))
                    or len(self.raw_fields) >= 25
                    or len(self.raw_fields) > len(self.editor.embed.fields)
                )
            elif key == "clear":
                child.disabled = len(self.raw_fields) <= 1
            else:
                child.disabled = False

    async def __aenter__(self) -> "FieldEditorView":
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

    async def unlock(self, interaction: Interaction) -> None:
        for child in self.handler.children:
            child.disabled = False
        self.handler.refresh()
        await interaction.edit_original_response(view=self.handler)

    async def update_view(self, interaction: Optional[Interaction] = None) -> None:
        self.refresh()
        if interaction and not interaction.response.is_done():
            func = interaction.response.edit_message
        else:
            func = self.message.edit
        await func(view=self)

    @ui.select(placeholder="Select a field", row=0)
    async def _field_select(self, interaction: Interaction, select: ui.Select) -> None:
        value = select.values[0]
        for opt in select.options:
            opt.default = opt.value == value
        self.index = int(value)
        await self.update_view(interaction)

    @ui.button(label="New", style=ButtonStyle.blurple)
    async def _action_add_field(self, interaction: Interaction, button: ui.Button) -> None:
        self.raw_fields.append(deepcopy(self.__default))
        self.index += 1
        self._populate_select_options()
        await self.update_view(interaction)

    @ui.button(label="Edit", style=ButtonStyle.grey)
    async def _action_edit_field(self, interaction: Interaction, button: ui.Button) -> None:
        options = self.handler.extras[self.handler.category]
        for key, value in self.raw_fields[self.index].items():
            options[key]["default"] = value
        modal = muui.Modal(
            self,
            options,
            callback=self._parse_inputs,
            title=self.handler.category.title(),
        )
        await interaction.response.send_modal(modal)

    @ui.button(label="Clear", style=ButtonStyle.grey)
    async def _action_clear_fields(self, interaction: Interaction, button: ui.Button) -> None:
        self.raw_fields.clear()
        self.editor.embed.clear_fields()
        self.index = 0
        self._populate_select_options()
        await self.update_view(interaction)
        await interaction.followup.send("Cleared all fields.", ephemeral=True)

    @ui.button(label="Exit", style=ButtonStyle.red)
    async def _action_exit_fields(self, interaction: Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        for f in self.raw_fields:
            # remove any unset element
            if f == self.__default:
                self.raw_fields.remove(f)
        self.stop()

    async def _parse_inputs(self, interaction: Interaction, modal: muui.Modal) -> None:
        data = {}
        for child in modal.children:
            value = child.value
            if not value:
                value = None
            data[child.name] = value
        self.raw_fields[self.index] = data
        resolved = {"index": self.index}
        for key, value in list(data.items()):
            if key == "inline":
                if value is None:
                    # defaults to True
                    value = True
                else:
                    try:
                        value = _resolve_conversion("fields", "inline", value)
                    except Exception as exc:
                        embed = discord.Embed(
                            title="__Errors__",
                            color=self.handler.bot.error_color,
                            description=str(exc),
                        )
                        return await interaction.response.send_message(embed=embed, ephemeral=True)
            resolved[key] = value
        self.editor.update(data=resolved, category=self.handler.category)
        await self.update_view(interaction)

    async def on_timeout(self) -> None:
        self.stop()


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
        for i, _ in enumerate(self.editor.embeds):
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
            curr_not_ready = len(self.editor.embed) == 0
            if key == "cancel":
                continue
            elif not self.category and curr_not_ready and len(self.editor.embeds) <= 1:
                # first launch
                child.disabled = True
            elif key == "new":
                child.disabled = curr_not_ready or len(self.editor.embeds) >= 10
            elif key == "edit":
                child.disabled = not self.category
            elif key in ("done", "preview"):
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
        self.editor.resolve()
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

    @ui.button(label="Edit", style=ButtonStyle.grey)
    async def _action_edit(self, *args: Any) -> None:
        interaction, _ = args
        if self.category == "fields":
            embed = discord.Embed(
                description="\n".join(DESCRIPTIONS["field"]),
                color=self.bot.main_color,
            )
            async with FieldEditorView(self, interaction, timeout=self.timeout) as view:
                view.message = await interaction.followup.send(embed=embed, view=view)
                await view.wait()
        else:
            payload = deepcopy(self.extras[self.category])
            for key in list(payload.keys()):
                try:
                    payload[key]["default"] = self.editor[self.category][key]
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
        self.editor.resolve()
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

    async def on_modal_submit(self, interaction: Interaction, modal: muui.Modal) -> None:
        for child in modal.children:
            value = child.value
            if not value:
                value = None
            self.editor[self.category][child.name] = value

        errors = []
        data = self.editor[self.category]
        resp_data = {}
        for key, value in list(data.items()):
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

        await self.update_view()
        modal.stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        return False

    @classmethod
    def from_embeds(
        cls,
        cog: EmbedManager,
        user: discord.Member,
        *,
        embeds: List[discord.Embed],
        index: int = 0,
    ) -> EmbedBuilderView:
        editor = EmbedEditor.from_embeds(cog, embeds=embeds)
        editor.index = index
        return cls(cog, user, editor=editor)
