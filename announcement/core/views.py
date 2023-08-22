from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction, TextStyle, ui
from discord.ext import commands
from discord.utils import MISSING

from .models import AnnouncementType


if TYPE_CHECKING:
    from ..announcement import Announcement as AnnouncementCog
    from .models import AnnouncementModel

    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]


_max_embed_length = 6000
_short_length = 256
_long_length = 4000

type_select_maps = [
    {
        "label": "Plain",
        "description": "Plain text announcement.",
    },
    {
        "label": "Embed",
        "description": "Embedded announcement. Image and thumbnail image are alose supported.",
    },
]
mention_select_maps = [
    {"label": "@here", "description": "Mention @here."},
    {"label": "@everyone", "description": "Mention @everyone."},
    {"label": "Others", "description": "Mention users or roles."},
]
embed_modal_payload = {
    "description": {
        "label": "Announcement",
        "style": TextStyle.long,
        "max_length": _long_length,
    },
    "thumbnail_url": {
        "label": "Thumbnail URL",
        "required": False,
        "max_length": _short_length,
    },
    "image_url": {
        "label": "Image URL",
        "required": False,
        "max_length": _short_length,
    },
    "color": {
        "label": "Embed color",
        "required": False,
        "max_length": 20,
    },
}


class TextInput(ui.TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        super().__init__(**kwargs)


class Modal(ui.Modal):
    children: List[TextInput]

    def __init__(self, view: AnnouncementView, options: Dict[str, Any]):
        super().__init__(title="Announcement")
        self.view = view
        self.view.modals.append(self)
        for key, value in options.items():
            self.add_item(TextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        for child in self.children:
            self.view.inputs[child.name]["default"] = child.value

        self.stop()
        await self.view.on_modal_submit(interaction)


class AnnouncementView(ui.View):
    """
    Represents the AnnouncementView class. The announcement creation panel and sessions
    will be handled from here.
    """

    def __init__(
        self,
        ctx: commands.Context,
        announcement: AnnouncementModel,
        *,
        input_sessions: List[Tuple[str]],
        timeout: float = 600.0,
    ):
        super().__init__(timeout=timeout)
        self.ctx: commands.Context = ctx
        self.cog: AnnouncementCog = ctx.cog
        self.user: discord.Member = ctx.author
        self.announcement: AnnouncementModel = announcement
        self.input_sessions: List[Tuple[str]] = input_sessions
        self.index: int = 0
        self.message: discord.Message = MISSING
        self.confirmed: Optional[bool] = None
        self._underlying_modals: List[Modal] = []
        self.inputs: Dict[str, Any] = {}

    @property
    def modals(self) -> List[Modal]:
        return self._underlying_modals

    @property
    def session_description(self) -> None:
        return self.input_sessions[self.index][1]

    @property
    def current(self) -> str:
        return self.input_sessions[self.index][0]

    def fill_items(self, *, post: bool = False, confirmation: bool = False) -> None:
        self.select_menu.options.clear()
        if self.current == "type":
            for ts in type_select_maps:
                option = discord.SelectOption(**ts)
                option.value = ts["label"].lower()
                self.select_menu.append_option(option)
            self.select_menu.placeholder = "Choose a type"
            self.add_item(self.select_menu)
        elif self.current == "mention":
            for ms in mention_select_maps:
                self.select_menu.append_option(discord.SelectOption(**ms))
            self.select_menu.placeholder = "Select mention"
            self.add_item(self.select_menu)
            self.add_item(self.mentionable_select)
        elif self.current == "channel":
            self.add_item(self.channel_select)

        if confirmation:
            buttons = [self._button_yes, self._button_no]
        else:
            if post:
                self._button_next_or_post.label = "Post"
                self._button_next_or_post.style = ButtonStyle.green
            else:
                self._button_next_or_post.label = "Next"
                self._button_next_or_post.style = ButtonStyle.blurple
            buttons = [
                self._button_next_or_post,
                self._button_edit,
                self._button_preview,
                self._button_cancel,
            ]
        for button in buttons:
            self.add_item(button)

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, ui.Button):
                continue
            if child.label.lower() == "cancel":
                continue
            if not self.announcement.type:
                child.disabled = True
                continue
            if child.label.lower() in ("post", "preview", "next"):
                child.disabled = not self.announcement.ready
            elif child.label.lower() == "edit":
                child.disabled = self.current not in ("embed", "plain")
            else:
                child.disabled = False

    async def create_base(self) -> None:
        """
        Create a base message and attach this view's components to it.
        """
        if self.message is not MISSING:
            raise RuntimeError("The base message already exists.")
        self.clear_items()
        self.fill_items()
        self.refresh()
        embed = discord.Embed(
            title="Announcement Creation Panel",
            description=self.session_description,
            color=self.ctx.bot.main_color,
        )
        self.message = await self.ctx.send(embed=embed, view=self)

    def _populate_base_inputs(self, type_: AnnouncementType) -> None:
        if type_ == AnnouncementType.EMBED:
            self.inputs.update(**deepcopy(embed_modal_payload))
        else:
            content = {
                "label": "Content",
                "default": None,
                "style": TextStyle.long,
                "max_length": _long_length,
            }
            self.inputs["content"] = content

    def _resolve_unused_sessions(self) -> None:
        for session in self.input_sessions:
            stype = session[0]
            if self.announcement.type == AnnouncementType.EMBED:
                if stype == "plain":
                    self.input_sessions.remove(session)
            elif self.announcement.type == AnnouncementType.PLAIN:
                if stype in ("embed", "mention"):
                    self.input_sessions.remove(session)
            else:
                raise TypeError(f"Invalid type of announcement, `{self.announcement.type}`.")

    async def _action_next(self, *args: Tuple[Interaction, Optional[ui.Button]]) -> None:
        """Go to next page."""
        interaction, _ = args
        self.index += 1
        self.inputs.clear()
        self.clear_items()
        post = False
        if self.current in ("embed", "plain"):
            self._populate_base_inputs(self.announcement.type)
            description = f"__**{self.current.title()}:**__\n"
        elif self.current == "mention":
            description = "__**Select mentions:**__\n"
        elif self.current == "channel":
            post = True
            description = "__**Select a channel:**__\n"
        else:
            raise ValueError(f"Invalid session in `_action_next`: `{self.current}`.")
        description += f"{self.session_description}\n"
        embed = self.message.embeds[0]
        embed.description = description
        self.fill_items(post=post)
        await self.update_view(interaction)

    @ui.select(placeholder="...", row=0)
    async def select_menu(self, interaction: Interaction, select: ui.Select) -> None:
        value = select.values[0]
        for opt in select.options:
            opt.default = opt.value == value
        if self.current == "type":
            self.announcement.type = AnnouncementType.from_value(value)
            self._resolve_unused_sessions()
            await self._action_next(interaction, None)
        elif self.current == "mention":
            if value in ("@here", "@everyone"):
                self.mentionable_select.disabled = True
                self.announcement.content = value
            else:
                self.mentionable_select.disabled = False
                self.announcement.content = MISSING
            await self.update_view(interaction)
        else:
            raise ValueError(f"Invalid session in `{self.__class__.__name__}.select_menu`: `{self.current}`.")

    @ui.select(
        cls=ui.MentionableSelect,
        placeholder="Other mentions",
        row=1,
        min_values=0,
        max_values=25,
        disabled=True,
    )
    async def mentionable_select(self, interaction: Interaction, select: ui.MentionableSelect) -> None:
        if select.values:
            self.announcement.content = ", ".join(v.mention for v in select.values)
        else:
            self.announcement.content = MISSING
        await interaction.response.defer()

    @ui.select(
        cls=ui.ChannelSelect,
        placeholder="Select a channel",
        channel_types=[discord.ChannelType.news, discord.ChannelType.text],
    )
    async def channel_select(self, interaction: Interaction, select: ui.ChannelSelect) -> None:
        value = select.values[0]
        channel = value.resolve() or await value.fetch()
        self.announcement.channel = channel
        await interaction.response.defer()

    @ui.button(label="...")
    async def _button_next_or_post(self, interaction: Interaction, button: ui.Button) -> None:
        """
        First button in the row. The label could be `Next` or `Post`. The attributes for this item
        are modified in `.fill_items()`.
        """
        if button.label == "Post":
            self.index += 1
            await interaction.response.defer()
            self.clear_items()
            self.announcement.event.set()
        else:
            await self._action_next(interaction, button)

    @ui.button(label="Edit", style=ButtonStyle.grey)
    async def _button_edit(self, *args: Tuple[Interaction, ui.Button]) -> None:
        interaction, _ = args
        modal = Modal(self, self.inputs)
        await interaction.response.send_modal(modal)

    @ui.button(label="Preview", style=ButtonStyle.grey)
    async def _button_preview(self, *args: Tuple[Interaction, ui.Button]) -> None:
        interaction, _ = args
        try:
            await interaction.response.send_message(ephemeral=True, **self.announcement.send_params())
        except discord.HTTPException as exc:
            error = f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```"
            await interaction.response.send_message(error, ephemeral=True)

    @ui.button(label="Cancel", style=ButtonStyle.red)
    async def _button_cancel(self, *args: Tuple[Interaction, ui.Button]) -> None:
        interaction, _ = args
        self.announcement.cancel()
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    @ui.button(label="Yes", style=ButtonStyle.green)
    async def _button_yes(self, interaction: Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        self.confirmed = True
        self.disable_and_stop()

    @ui.button(label="No", style=ButtonStyle.red)
    async def _button_no(self, interaction: Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        self.confirmed = False
        self.disable_and_stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        return False

    async def on_modal_submit(self, interaction: Interaction) -> None:
        errors = []
        if self.announcement.type == AnnouncementType.EMBED:
            elems = [
                "description",
                "thumbnail_url",
                "image_url",
                "color",
            ]
            kwargs = {elem: self.inputs[elem].get("default") for elem in elems}
            try:
                self.announcement.create_embed(**kwargs)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)}")
        else:
            self.announcement.content = self.inputs["content"].get("default")

        if errors:
            self.announcement.ready = False
            content = "\n".join(f"{n}. {error}" for n, error in enumerate(errors, start=1))
            embed = discord.Embed(
                title="__Errors__",
                color=self.ctx.bot.error_color,
                description=content,
            )
            await interaction.respose.send_message(embed=embed, ephemeral=True)
        else:
            self.announcement.ready = True
        await self.update_view(interaction)

    async def wait(self) -> None:
        if not self.announcement.ready_to_post():
            await self.announcement.wait()
        else:
            await super().wait()

    async def update_view(self, interaction: Optional[Interaction] = None) -> None:
        """
        Refresh the components and update the view.
        """
        if interaction and not interaction.response.is_done():
            func = interaction.response.edit_message
        else:
            func = self.message.edit
        self.refresh()
        await func(embed=self.message.embeds[0], view=self)

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        for modal in self.modals:
            if modal.is_dispatching() or not modal.is_finished():
                modal.stop()
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.announcement.cancel()
        self.disable_and_stop()
        await self.message.edit(view=self)
