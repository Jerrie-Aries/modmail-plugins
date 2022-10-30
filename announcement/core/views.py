from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Union, TYPE_CHECKING

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
        self.view.modals.append(self)
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
            placeholder="Choose a type",
            min_values=1,
            max_values=1,
            options=options,
            **kwargs,
        )

    async def callback(self, interaction: Interaction):
        await interaction.response.defer()
        assert self.view is not None
        value = self.values[0]
        self.placeholder = value.title()
        self.disabled = True
        await self.view.set_announcement_type(value)


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
        self.confirm: Optional[bool] = None
        self._underlying_modals: List[AnnouncementModal] = []

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
                "required": False,
                "max_length": 20,
            },
        }
        self.input_map: Dict[str, Any] = {"content": self.content_data}

        self._add_menu()
        self.generate_buttons()
        self.refresh()

    @property
    def modals(self) -> List[AnnouncementModal]:
        return self._underlying_modals

    def _add_menu(self) -> None:
        attrs = [
            {
                "label": "Normal",
                "emoji": None,
                "description": "Plain text announcement.",
            },
            {
                "label": "Embed",
                "emoji": None,
                "description": "Embedded announcement. Image and thumbnail image are alose supported.",
            },
        ]
        options = []
        for attr in attrs:
            option = discord.SelectOption(
                label=attr["label"],
                emoji=attr["emoji"],
                description=attr["description"],
                value=attr["label"].lower(),
            )
            options.append(option)
        self.add_item(DropdownMenu(options=options, row=0))

    def generate_buttons(self, *, confirmation: bool = False) -> None:
        if confirmation:
            buttons = {
                "yes": (ButtonStyle.green, self._action_yes),
                "no": (ButtonStyle.red, self._action_no),
            }
        else:
            buttons: Dict[str, Any] = {
                "post": (ButtonStyle.green, self._action_post),
                "edit": (ButtonStyle.grey, self._action_edit),
                "preview": (ButtonStyle.grey, self._action_preview),
                "cancel": (ButtonStyle.red, self._action_cancel),
            }
        for label, item in buttons.items():
            self.add_item(AnnouncementViewButton(label.title(), style=item[0], callback=item[1]))

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, AnnouncementViewButton):
                continue
            if child.label.lower() == "cancel":
                continue
            if not self.announcement.type:
                child.disabled = True
                continue
            if child.label.lower() in ("post", "preview"):
                child.disabled = not self.announcement.ready
            else:
                child.disabled = False

    async def update_view(self) -> None:
        self.refresh()
        await self.message.edit(embed=self.message.embeds[0], view=self)

    async def _action_post(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        await self.announcement.post()
        self.clear_items()

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
        self.announcement.posted = False
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def _action_yes(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        self.confirm = True
        self.disable_and_stop()

    async def _action_no(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        self.confirm = False
        self.disable_and_stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        await interaction.response.send_message(
            "This panel cannot be controlled by you!",
            ephemeral=True,
        )
        return False

    async def set_announcement_type(self, value: str) -> None:
        self.announcement.type = AnnouncementType.from_value(value)
        description = f"__**{value.title()}:**__\n"
        if self.announcement.type == AnnouncementType.EMBED:
            self.content_data = {
                "label": "Mention",
                "default": "@here",
                "required": False,
                "max_length": _short_length,
            }
            self.input_map.update(content=self.content_data, **self.embed_data)
            description += (
                "Click the `Edit` button below to set/edit the values.\n\n"
                "__**Available fields:**__\n"
                "- **Mention** : Mention @User, @Role, @here, or @everyone.\n"
                "Multiple mentions is also supported, just separate the values with space.\n"
                "For User or Role, you may pass an ID, mention (in the format of `<@id>` for User or `<@&id>` for Role), or name.\n"
                "- **Description** : The content of the announcement. Must not exceed 4000 characters.\n"
                "- **Thumbnail URL** : URL of the image shown at the top right of the embed.\n"
                "- **Image URL** : URL of the large image shown at the bottom of the embed.\n"
                "- **Color** : The color code of the embed. If not specified, fallbacks to bot main color.\n"
                "The following formats are accepted:\n\t- `0x<hex>`\n\t- `#<hex>`\n\t- `0x#<hex>`\n\t- `rgb(<number>, <number>, <number>)`\n"
                "Like CSS, `<number>` can be either 0-255 or 0-100% and `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).\n\n"
            )
        else:
            description += "Click the `Edit` button below to set/edit the content."
        embed = self.message.embeds[0]
        embed.description = description
        await self.update_view()

    async def on_modal_submit(self, interaction: Interaction) -> None:
        self.announcement.content = self.input_map["content"].get("default")
        errors = []
        if self.announcement.type == AnnouncementType.EMBED:
            try:
                await self.announcement.resolve_mentions()
            except commands.BadArgument as exc:
                errors.append(str(exc))
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

    async def wait(self, *, input_event: bool = False) -> None:
        if input_event:
            await self.announcement.wait()
        else:
            await super().wait()

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        for modal in self.modals:
            if modal.is_dispatching() or not modal.is_finished():
                modal.stop()
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.announcement.posted = False
        self.disable_and_stop()
        await self.message.edit(view=self)
