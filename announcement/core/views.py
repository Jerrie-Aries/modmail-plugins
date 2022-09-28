from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction, TextStyle
from discord.ext import commands
from discord.ui import Button, Modal, Select, TextInput, View
from discord.utils import MISSING

from .models import AnnouncementType


if TYPE_CHECKING:
    from ..announcement import Announcement as AnnouncementCog
    from .models import AnnouncementModel

    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]


_max_embed_length = 6000
_short_length = 256
_long_length = 4000


class AnnouncementTextInput(TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        super().__init__(**kwargs)


class AnnouncementModal(Modal):

    children: List[AnnouncementTextInput]

    def __init__(self, view: AnnouncementView, options: Dict[str, Any]):
        super().__init__(title="Announcement")
        self.view = view
        for key, value in options.items():
            self.add_item(AnnouncementTextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        for child in self.children:
            self.view.input_map[child.name]["default"] = child.value

        await interaction.response.defer()
        self.stop()
        await self.view.on_modal_submit(interaction)


class DropdownMenu(Select):
    def __init__(self, *, options: List[discord.SelectOption], **kwargs):
        super().__init__(
            placeholder="Choose the type of announcement",
            min_values=1,
            max_values=1,
            options=options,
            **kwargs,
        )

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        await self.view.on_dropdown_select(self, interaction)


class AnnouncementViewButton(Button["AnnouncementView"]):
    def __init__(
        self,
        label: str,
        *,
        style: ButtonStyle = ButtonStyle.blurple,
        callback: ButtonCallbackT = MISSING,
    ):
        super().__init__(label=label, style=style)
        self.callback_override: ButtonCallbackT = callback

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        await self.callback_override(interaction)


class AnnouncementView(View):

    children: List[AnnouncementViewButton]

    def __init__(self, ctx: commands.Context, announcement: AnnouncementModel, *, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.ctx: commands.Context = ctx
        self.cog: AnnouncementCog = ctx.cog
        self.user: discord.Member = ctx.author
        self.message: discord.Message = MISSING
        self.announcement: AnnouncementModel = announcement

        self.content_data: Dict[str, Any] = {
            "label": "Content",
            "default": None,
            "style": TextStyle.long,
            "max_length": _long_length,
        }
        self.embed_data: Dict[str, Any] = {
            "description": {
                "label": "Announcement",
                "default": "This is an announcement.",
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
                "default": self.cog.bot.main_color,
                "required": False,
                "max_length": 20,
            },
        }
        self.input_map: Dict[str, Any] = {"content": self.content_data}
        self.ret_buttons: Dict[str, Any] = {
            "post": (ButtonStyle.green, self._action_post),
            "edit": (ButtonStyle.grey, self._action_edit),
            "preview": (ButtonStyle.grey, self._action_preview),
            "cancel": (ButtonStyle.red, self._action_cancel),
        }
        self.menu_map: Dict[str, Any] = {
            "normal": {
                "label": "Normal",
                "emoji": None,
                "description": "Plain text announcement.",
            },
            "embed": {
                "label": "Embed",
                "emoji": None,
                "description": "Embedded announcement. Image and thumbnail image are alose supported.",
            },
        }

        self._add_menu()
        self._generate_buttons()
        self.refresh()

    def _add_menu(self) -> None:
        options = []
        for key, value in self.menu_map.items():
            option = discord.SelectOption(
                label=value["label"],
                emoji=value["emoji"],
                description=value["description"],
                value=key,
            )
            options.append(option)
        self.add_item(DropdownMenu(options=options, row=0))

    def _generate_buttons(self) -> None:
        for label, item in self.ret_buttons.items():
            # `item` is a tuple of (ButtonStyle, callback)
            self.add_item(AnnouncementViewButton(label.title(), style=item[0], callback=item[1]))

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, AnnouncementViewButton):
                continue
            if not self.announcement.type:
                if child.label.lower() == "cancel":
                    continue
                child.disabled = True
                continue
            if child.label.lower() in ("post", "preview"):
                child.disabled = not self.announcement.ready

    async def update_view(self) -> None:
        self.refresh()
        await self.message.edit(view=self)

    async def _action_post(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        self.disable_and_stop()
        await self.announcement.post()
        await self.message.edit(view=self)

    async def _action_edit(self, interaction: Interaction) -> None:
        modal = AnnouncementModal(self, self.input_map)
        await interaction.response.send_modal(modal)
        await modal.wait()

    async def _action_preview(self, interaction: Interaction) -> None:
        try:
            await interaction.response.send_message(ephemeral=True, **self.announcement.send_params())
        except discord.HTTPException as exc:
            error = f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```"
            await interaction.response.send_message(error, ephemeral=True)

    async def _action_cancel(self, interaction: Interaction) -> None:
        self.announcement.ready = False
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        await interaction.response.send_message(
            "This view cannot be controlled by you!",
            ephemeral=True,
        )
        return False

    async def on_dropdown_select(self, select: DropdownMenu, interaction: Interaction) -> None:
        await interaction.response.defer()
        value = select.values[0]
        self.announcement.type = AnnouncementType.from_value(value)
        if self.announcement.type == AnnouncementType.EMBED:
            self.content_data = {
                "label": "Mention",
                "default": "@here",
                "required": False,
                "max_length": _short_length,
            }
            self.input_map.update(content=self.content_data, **self.embed_data)
        self.clear_items()
        self._generate_buttons()
        await self.update_view()

    async def on_modal_submit(self, interaction: Interaction) -> None:
        self.announcement.content = self.input_map["content"].get("default")
        errors = []
        if self.announcement.type == AnnouncementType.EMBED:
            kwargs = {}
            elems = [
                "description",
                "thumbnail_url",
                "image_url",
                "color",
            ]
            for elem in elems:
                kwargs = {elem: self.input_map[elem].get("default") for elem in elems}
            try:
                self.announcement.create_embed(**kwargs)
            except Exception as exc:
                errors.append(f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```")

        if errors:
            self.announcement.ready = False
            for error in errors:
                await interaction.followup.send(error, ephemeral=True)
        else:
            self.announcement.ready = True
        await self.update_view()

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.announcement.ready = False
        self.disable_and_stop()
        await self.message.edit(view=self)
