from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord.ext import commands
from discord.ui import Button, Modal, Select, TextInput, View
from discord.utils import MISSING

from core.models import getLogger

from .converters import AssignableRole, UnionEmoji
from .utils import error_embed


if TYPE_CHECKING:
    from ..rolemanager import RoleManager
    from .models import ReactionRole

    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]

logger = getLogger(__name__)

_max_embed_length = 6000
_button_label_length = 80
_short_length = 256
_long_length = 4000


def _resolve_button_style(value: str) -> ButtonStyle:
    try:
        return ButtonStyle[value]
    except (KeyError, TypeError):
        return ButtonStyle.blurple


class RoleManagerTextInput(TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        super().__init__(**kwargs)


class RoleManagerModal(Modal):

    children: List[RoleManagerTextInput]

    def __init__(self, view: RoleManagerView, options: Dict[str, Any]):
        super().__init__(title="Reaction Role")
        self.view = view
        for key, value in options.items():
            self.add_item(RoleManagerTextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        for child in self.children:
            value = child.value
            if not value:
                # resolve empty string value
                value = None
            self.view.current_input[child.name] = value
        self.view.current_input["converted"] = False

        await interaction.response.defer()
        self.stop()
        await self.view.on_modal_submit(interaction)

    async def on_error(self, interaction: Interaction, error: Exception, item: Any) -> None:
        logger.error("Ignoring exception in modal %r for item %r", self, item, exc_info=error)


class DropdownMenu(Select):
    def __init__(self, category: str, *, options: List[discord.SelectOption], **kwargs):
        self.category: str = category
        placeholder = kwargs.pop("placeholder", "Choose option")
        self.after_callback = kwargs.pop("callback")
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            **kwargs,
        )

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        option = self.get_option(self.values[0])
        await self.after_callback(interaction, self, option)

    def get_option(self, value: str) -> discord.SelectOption:
        for option in self.options:
            if value == option.value:
                return option
        raise ValueError(f"Cannot find select option with value of `{value}`.")


class RoleManagerButton(Button["RoleManagerView"]):
    def __init__(
        self,
        label: str,
        *,
        style: ButtonStyle = ButtonStyle.blurple,
        callback: ButtonCallbackT = MISSING,
        **kwargs,
    ):
        super().__init__(label=label, style=style, **kwargs)
        self.__callback: ButtonCallbackT = callback

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        await self.__callback(interaction, self)


class RoleManagerView(View):

    children: List[RoleManagerButton]

    def __init__(self, cog: RoleManager, *, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.cog: RoleManager = cog
        self.message: discord.Message = MISSING
        self.value: Optional[bool] = None

    async def on_error(self, interaction: Interaction, error: Exception, item: Any) -> None:
        logger.error("Ignoring exception in view %r for item %r", self, item, exc_info=error)

    async def update_view(self) -> None:
        self.refresh()
        if self.message:
            await self.message.edit(embed=self.message.embeds[0], view=self)

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        if self.message:
            await self.message.edit(view=self)


class ReactionRoleCreationPanel(RoleManagerView):
    def __init__(
        self,
        ctx: commands.Context,
        *,
        input_sessions: List[Tuple[[str, Any]]],
        rule: str = MISSING,
        binds: Dict[str, Any] = MISSING,
    ):
        self.ctx: commands.Context = ctx
        self.user: discord.Member = ctx.author
        self.input_sessions: List[Tuple[[str, Any]]] = input_sessions
        self.current_input: Dict[str, Any] = {}  # keys would be emoji, label, role, color
        self.current_index: int = 0
        self.rule: Optional[str] = rule
        self.output_embed: discord.Embed = MISSING
        self.placeholder_description: str = MISSING
        self.binds: Dict[str, Any] = binds if binds else {}
        super().__init__(ctx.cog)
        self.add_menu()
        self.add_buttons()
        self.refresh()

    def add_menu(self) -> None:
        options = []
        if self.session_key == "rule":
            attrs = {
                "normal": "Allow users to have multiple roles in group.",
                "unique": "Remove existing role when assigning another role in group.",
            }
            category = "rule"
            placeholder = "Choose a rule"
        elif self.session_key == "bind":
            attrs = {
                "blurple": None,
                "green": None,
                "red": None,
                "grey": None,
            }
            category = "style"
            placeholder = "Choose a color style"
        else:
            raise KeyError(f"Session key `{self.session_key}` is not recognized for menu.")
        for key, value in attrs.items():
            option = discord.SelectOption(label=key.title(), description=value, value=key)
            options.append(option)
        self.add_item(
            DropdownMenu(
                category, options=options, row=0, placeholder=placeholder, callback=self.on_dropdown_select
            )
        )

    def add_buttons(self) -> None:
        if self.session_key == "bind":
            config_buttons = {
                "add": self._action_add,
                "set": self._action_set,
                "clear": self._action_clear,
            }
            for label, callback in config_buttons.items():
                if label in ("title", "add"):
                    style = ButtonStyle.blurple
                else:
                    style = ButtonStyle.grey
                self.add_item(RoleManagerButton(label.title(), style=style, callback=callback, row=3))

        ret_buttons: Dict[str, Any] = {
            "done": (ButtonStyle.green, self._action_done),
            "preview": (ButtonStyle.grey, self._action_preview),
            "cancel": (ButtonStyle.red, self._action_cancel),
        }
        for label, item in ret_buttons.items():
            self.add_item(RoleManagerButton(label.title(), style=item[0], callback=item[1], row=4))

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, RoleManagerButton):
                continue
            label = child.label.lower()
            if label == "cancel":
                continue
            if label in ("done", "clear"):
                child.disabled = len(self.binds) < 1
            # elif label == "preview":
            #     child.disabled = not self.output_embed or len(self.output_embed) < 1
            elif label == "add":
                child.disabled = not self.current_input.get("converted", False)
            else:
                child.disabled = False

    @property
    def session_key(self) -> str:
        return self.input_sessions[self.current_index]["key"]

    @property
    def session_description(self) -> str:
        return self.input_sessions[self.current_index]["description"]

    def _parse_output_description(self, *, buttons: List[Button] = None) -> str:
        desc = self.placeholder_description
        if not buttons:
            return desc
        for button in buttons:
            prefix = f"{str(button.emoji)} " if button.emoji else ""
            desc += f"> **{prefix}{button.label}** - <@&{button.custom_id}>\n"
        return desc

    def get_output_buttons(self) -> List[Button]:
        buttons = []
        for key, value in self.binds.items():
            button = Button(
                label=value["label"],
                emoji=value["emoji"],
                style=_resolve_button_style(value["style"]),
                custom_id=key,
            )
            buttons.append(button)
        if self.current_input.get("converted", False):
            button = Button(
                label=self.current_input["label"],
                emoji=self.current_input["emoji"],
                style=_resolve_button_style(self.current_input.get("style")),
                custom_id=str(self.current_input["role"].id),
            )
            buttons.append(button)
        return buttons

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        await interaction.response.send_message(
            "This panel cannot be controlled by you!",
            ephemeral=True,
        )
        return False

    async def _action_add(self, interaction: Interaction, *args) -> None:
        # need to store raw inputs for the database later
        role = self.current_input.pop("role")
        self.binds[str(role.id)] = {
            "emoji": self.current_input.pop("emoji"),
            "label": self.current_input.pop("label"),
            "style": self.current_input.pop("style", "blurple"),
        }
        self.current_input.clear()
        await interaction.response.send_message(f"Added role {role.mention}.", ephemeral=True)
        self.clear_items()
        self.add_menu()
        self.add_buttons()
        await self.update_view()

    async def _action_set(self, interaction: Interaction, *args) -> None:
        options = {
            "emoji": {
                "label": "Emoji",
                "required": False,
                "max_length": _short_length,
            },
            "label": {
                "label": "Label",
                "required": False,
                "max_length": _button_label_length,
            },
            "role": {
                "label": "Role",
                "max_length": _short_length,
            },
        }
        modal = RoleManagerModal(self, options)
        await interaction.response.send_modal(modal)
        await modal.wait()

    async def _action_clear(self, interaction: Interaction, *args) -> None:
        await interaction.response.defer()
        self.binds.clear()
        await self.update_view()

    async def _action_preview(self, interaction: Interaction, *args) -> None:
        buttons = self.get_output_buttons()
        if buttons:
            view = RoleManagerView(self.ctx, timeout=10)
            for button in buttons:
                view.add_item(button)
        else:
            view = MISSING

        description = self._parse_output_description(buttons=buttons)
        if self.output_embed:
            embed = self.output_embed
            embed.description = description
        else:
            embed = discord.Embed(
                title="Preview",
                color=self.ctx.bot.main_color,
                description=description,
            )
        try:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except discord.HTTPException as exc:
            description = f"```py\n{type(exc).__name__}: {str(exc)}\n```"
            embed = error_embed(self.ctx.bot, description=description)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _action_done(self, interaction: Interaction, *args) -> None:
        await interaction.response.defer()
        self.current_index += 1
        if self.output_embed:
            self.output_embed.description = self._parse_output_description(buttons=self.get_output_buttons())
        self.clear_items()
        self.value = True
        self.stop()

    async def _action_cancel(self, interaction: Interaction, *args) -> None:
        self.current_input.clear()
        self.binds.clear()
        self.value = False
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def on_dropdown_select(
        self,
        interaction: Interaction,
        select: DropdownMenu,
        option: discord.SelectOption,
    ) -> None:
        await interaction.response.defer()
        select.placeholder = option.label
        category = select.category
        if category == "rule":
            select.disabled = True
            self.rule = option.value
            self.current_index += 1
            embed = self.message.embeds[0]
            embed.description = self.session_description
            self.clear_items()
            self.add_menu()
            self.add_buttons()
        elif category == "style":
            self.current_input["style"] = option.value
        else:
            raise KeyError(f"Category `{category}` is invalid for `on_dropdown_select` method.")
        await self.update_view()

    async def on_modal_submit(self, interaction: Interaction) -> None:
        if self.session_key != "bind":
            raise KeyError(
                f"Session key `{self.session_key}` is not recognized for `on_modal_submit` method."
            )
        converters = {
            "emoji": UnionEmoji,
            "role": AssignableRole,
        }
        errors = []
        if self.current_input["emoji"] is None and self.current_input["label"] is None:
            errors.append("ValueError: Emoji and Label cannot both be None.")

        ret = {}
        for key, value in self.current_input.items():
            conv = converters.get(key)
            if conv is None or value is None:
                ret[key] = value
                continue
            try:
                entity = await conv().convert(self.ctx, value)
            except Exception as exc:
                errors.append(f"{key.title()} error: {type(exc).__name__} - {str(exc)}")
            else:
                if isinstance(entity, discord.Role) and str(entity.id) in self.binds:
                    errors.append(f"Duplicate role ID: `{entity.id}`. Please set other role.")
                ret[key] = entity

        if errors:
            content = "\n".join(f"{n}. {error}" for n, error in enumerate(errors, start=1))
            embed = error_embed(self.ctx.bot, description=content)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            self.current_input = ret
            self.current_input["converted"] = True
        await self.update_view()


class ReactionRoleView(RoleManagerView):

    children: List[RoleManagerButton]

    def __init__(self, cog: RoleManager, message: discord.Message, *, model: ReactionRole):
        if model.view is not MISSING:
            raise RuntimeError(
                f"View `{type(model.view).__name__}` is already attached to `<{type(model).__name__} message={message.id}>`."
            )
        super().__init__(cog, timeout=None)
        self.model: ReactionRole = model
        self.binds: Dict[str, Any] = model.binds
        self.message: discord.Message = message
        model.view = self
        self.add_buttons()

    def rebind(self) -> None:
        self.clear_items()
        self.add_buttons()

    def add_buttons(self) -> None:
        for key, value in self.binds.items():
            button = RoleManagerButton(
                label=value["label"],
                emoji=value["emoji"],
                style=_resolve_button_style(value["style"]),
                callback=self.handle_interaction,
                custom_id=f"reactrole:{self.message.id}-{key}",
            )
            self.add_item(button)

    async def update_view(self) -> None:
        for button in self.children:
            if not isinstance(button, RoleManagerButton):
                continue
            custom_id = button.custom_id
            if custom_id.split("-")[-1] not in self.binds:
                self.remove_item(button)
        await self.message.edit(view=self)

    async def handle_interaction(self, interaction: Interaction, button: RoleManagerButton) -> None:
        await interaction.response.defer()
        await self.model.manager.handle_interaction(self.model, interaction, button)
