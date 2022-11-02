from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord.ext import commands
from discord.ext.modmail_utils import Limit
from discord.ext.modmail_utils.ui import Button, Modal, Select, View
from discord.ui import Button as uiButton
from discord.utils import MISSING

from core.models import getLogger

from .converters import AssignableRole, UnionEmoji
from .models import TriggerType
from .utils import bind_string_format, error_embed


if TYPE_CHECKING:
    from ..rolemanager import RoleManager
    from .models import ReactionRole

    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]

logger = getLogger(__name__)

_short_length = 256


def _resolve_button_style(value: str) -> ButtonStyle:
    try:
        return ButtonStyle[value]
    except (KeyError, TypeError):
        return ButtonStyle.blurple


class RoleManagerModal(Modal):
    async def on_submit(self, interaction: Interaction) -> None:
        for child in self.children:
            value = child.value
            if not value:
                value = None
            self.view.inputs[child.name] = value

        await self.followup_callback(interaction, self)

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        logger.error("Ignoring exception in modal %r:", self, exc_info=error)


class RoleManagerSelect(Select):
    def __init__(self, category: str, *args, **kwargs):
        self.category: str = category
        super().__init__(*args, **kwargs)


class RoleManagerView(View):
    """
    Base view class.
    """

    children: List[Button]

    def __init__(
        self,
        cog: RoleManager,
        *,
        message: Union[discord.Message, discord.PartialMessage] = MISSING,
        timeout: float = 600.0,
    ):
        super().__init__(message=message, timeout=timeout)
        self.cog: RoleManager = cog

    async def on_error(self, interaction: Interaction, error: Exception, item: Any) -> None:
        logger.error("Ignoring exception in view %r for item %r", self, item, exc_info=error)

    async def update_view(self) -> None:
        self.refresh()
        if self.message:
            await self.update_message(embed=self.message.embeds[0])


class ReactionRoleCreationPanel(RoleManagerView):
    """
    Represents the Reaction Roles creation view.
    This view  will be used to create or edit reaction roles menu.

    Parameters
    -----------
    ctx : commands.Context
        The Context object.
    input_sessions : List[Dict[[str, Any]]]
        A list of dictionaries containing session keys and their description.
        This will be used to switch the pages after the users choose or submit the values for current session.
    binds : List[Dict[str, Any]]
        The role-button or role-emoji bind data. This should be passed if this class is instantiated to
        edit an existing reaction roles menu. Defaults to `MISSING`.
    trigger_type : str
        The reaction roles trigger type. Valid options are `REACTION` and `INTERACTION`.
        This should be passed if this class is instantiated to edit an existing reaction roles menu.
        Defaults to `MISSING`.
    rule : str
        The reaction roles rule.
        This should be passed if this class is instantiated to edit an existing reaction roles menu.
        Defaults to `MISSING`.
    """

    def __init__(
        self,
        ctx: commands.Context,
        *,
        input_sessions: List[Dict[[str, Any]]],
        binds: List[Dict[str, Any]] = MISSING,
        trigger_type: str = MISSING,
        rule: str = MISSING,
    ):
        self.ctx: commands.Context = ctx
        self.user: discord.Member = ctx.author
        self.input_sessions: List[Dict[[str, Any]]] = input_sessions
        self.binds: List[Dict[str, Any]] = binds if binds else []
        self.trigger_type: str = trigger_type
        self.rule: str = rule
        self.__bind: Dict[str, Any] = {}
        self.__index: int = 0
        self.output_embed: discord.Embed = MISSING
        self.placeholder_description: str = MISSING
        super().__init__(ctx.cog)
        self.add_menu()
        self.add_buttons()
        self.refresh()

    def add_menu(self) -> None:
        attrs = None
        if self.session_key == "type":
            attrs = {
                "reaction": "Legacy reaction with emojis.",
                "interaction": "Interaction with new discord buttons.",
            }
            category = "type"
            placeholder = "Choose a trigger type"
        elif self.session_key == "rule":
            attrs = {
                "normal": "Allow users to have multiple roles in group.",
                "unique": "Remove existing role when assigning another role in group.",
            }
            category = "rule"
            placeholder = "Choose a rule"
        elif self.session_key == "bind":
            if self.trigger_type and self.trigger_type == TriggerType.INTERACTION:
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
        if not attrs:
            return
        options = []
        for key, value in attrs.items():
            option = discord.SelectOption(label=key.title(), description=value, value=key)
            options.append(option)
        self.add_item(
            RoleManagerSelect(
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
                if label == "add":
                    style = ButtonStyle.blurple
                else:
                    style = ButtonStyle.grey
                self.add_item(Button(label=label.title(), style=style, callback=callback, row=3))

        ret_buttons: Dict[str, Any] = {
            "done": (ButtonStyle.green, self._action_done),
            "preview": (ButtonStyle.grey, self._action_preview),
            "cancel": (ButtonStyle.red, self._action_cancel),
        }
        for label, item in ret_buttons.items():
            self.add_item(Button(label=label.title(), style=item[0], callback=item[1], row=4))

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, Button):
                continue
            label = child.label.lower()
            if label == "cancel":
                continue
            if label in ("done", "clear"):
                child.disabled = len(self.binds) < 1
            elif label == "add":
                child.disabled = not self.__bind
            else:
                child.disabled = False

    @property
    def session_key(self) -> str:
        return self.input_sessions[self.__index]["key"]

    @property
    def session_description(self) -> str:
        return self.input_sessions[self.__index]["description"]

    def _parse_output_description(self) -> str:
        desc = self.placeholder_description
        for bind in self.binds + [self.__bind]:
            if not bind:
                continue
            if self.trigger_type == TriggerType.INTERACTION:
                emoji = bind["button"].get("emoji")
                label = bind["button"].get("label")
            else:
                emoji = bind.get("emoji")
                label = None
            desc += f"- {bind_string_format(emoji, label, bind['role'])}\n"
        return desc

    def get_output_buttons(self) -> List[Button]:
        if not self.trigger_type or self.trigger_type == TriggerType.REACTION:
            return []
        buttons = []
        for bind in self.binds + [self.__bind]:
            if not bind:
                continue
            payload = bind["button"]
            button = uiButton(
                label=payload["label"],
                emoji=payload["emoji"],
                style=_resolve_button_style(payload.get("style")),
                custom_id=bind["role"],
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
        role = self.__bind.pop("role")
        bind = {"role": role}
        if self.trigger_type == TriggerType.INTERACTION:
            payload = {key: self.__bind["button"].pop(key) for key in list(self.__bind["button"])}
            emoji = payload.get("emoji")
            label = payload.get("label")
            if not payload.get("style"):
                payload["style"] = "blurple"
            bind["button"] = payload
        elif self.trigger_type == TriggerType.REACTION:
            bind["emoji"] = emoji = self.__bind.pop("emoji")
            label = None
        else:
            raise TypeError(f"`{self.trigger_type}` is invalid for reaction roles trigger type.")

        self.binds.append(bind)
        self.__bind.clear()
        self.inputs.clear()
        embed = discord.Embed(
            color=self.ctx.bot.main_color,
            description=f"Added '{bind_string_format(emoji, label, role)}' bind to the list.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.clear_items()
        self.add_menu()
        self.add_buttons()
        await self.update_view()

    async def _action_set(self, interaction: Interaction, *args) -> None:
        options = {
            "role": {
                "label": "Role",
                "max_length": _short_length,
            },
            "emoji": {
                "label": "Emoji",
                "required": self.trigger_type == TriggerType.REACTION,
                "max_length": _short_length,
            },
        }
        if self.trigger_type == TriggerType.INTERACTION:
            options["label"] = {
                "label": "Label",
                "required": False,
                "max_length": Limit.button_label,
            }
        if self.inputs:
            for key in list(options):
                options[key]["default"] = self.inputs.get(key)

        modal = RoleManagerModal(self, options, self.resolve_inputs, title="Reaction Role")
        self._underlying_modals.append(modal)
        await interaction.response.send_modal(modal)
        await modal.wait()

    async def _action_clear(self, interaction: Interaction, *args) -> None:
        await interaction.response.defer()
        self.inputs.clear()
        self.__bind.clear()
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

        description = self._parse_output_description()
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
        self.__index += 1
        if self.output_embed:
            self.output_embed.description = self._parse_output_description()
        self.clear_items()
        self.value = True
        self.disable_and_stop()

    async def _action_cancel(self, interaction: Interaction, *args) -> None:
        self.inputs.clear()
        self.__bind.clear()
        self.binds.clear()
        self.value = False
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def on_dropdown_select(
        self,
        interaction: Interaction,
        select: RoleManagerSelect,
        option: discord.SelectOption,
    ) -> None:
        await interaction.response.defer()
        select.placeholder = option.label
        category = select.category
        if category in ("type", "rule"):
            if category == "rule":
                self.rule = option.value.upper()
            else:
                self.trigger_type = option.value.upper()
            self.__index += 1
            embed = self.message.embeds[0]
            embed.description = self.session_description
            self.clear_items()
            self.add_menu()
            self.add_buttons()
        elif category == "style":
            if self.__bind:
                self.__bind["button"]["style"] = option.value
            else:
                self.inputs["style"] = option.value
        else:
            raise KeyError(f"Category `{category}` is invalid for `on_dropdown_select` method.")
        await self.update_view()

    async def resolve_inputs(self, interaction: Interaction, modal: RoleManagerModal) -> None:
        """
        Resolves and converts input values.
        Currently this is only called after submitting inputs from Modal view.
        """
        modal.stop()
        converters = {
            "emoji": UnionEmoji,
            "role": AssignableRole,
        }
        errors = []
        if self.inputs["emoji"] is None and self.inputs.get("label") is None:
            errors.append("ValueError: Emoji and Label cannot both be None.")

        ret = {}
        for key, value in self.inputs.items():
            conv = converters.get(key)
            if conv is None or value is None:
                ret[key] = value
                continue
            try:
                entity = await conv().convert(self.ctx, value)
            except Exception as exc:
                errors.append(f"{key.title()} error: {type(exc).__name__} - {str(exc)}")
            else:
                if isinstance(entity, discord.Role):
                    if any(str(entity.id) == bind["role"] for bind in self.binds):
                        errors.append(f"Duplicate role ID: `{entity.id}`. Please set other role.")
                    ret[key] = str(entity.id)
                    continue
                if key == "emoji":
                    entity = str(entity)
                    if self.trigger_type == TriggerType.REACTION:
                        # check emoji
                        for bind in self.binds:
                            if entity == bind.get("emoji"):
                                errors.append(
                                    f"Emoji {entity} has already linked to <@&{bind['role']}> on this message."
                                )
                                break
                ret[key] = entity

        if errors:
            content = "\n".join(f"{n}. {error}" for n, error in enumerate(errors, start=1))
            embed = error_embed(self.ctx.bot, description=content)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.defer()
            # resolve bind data
            if self.trigger_type == TriggerType.REACTION:
                self.__bind = ret
            else:
                self.__bind["role"] = ret.pop("role")
                payload = self.__bind.get("button", {})
                for key in list(ret):
                    payload[key] = ret.pop(key)
                self.__bind["button"] = payload
        await self.update_view()


class ReactionRoleView(RoleManagerView):
    """
    Reaction Roles persistent view.
    """

    children: List[Button]

    def __init__(self, cog: RoleManager, message: discord.Message, *, model: ReactionRole):
        if model.view is not MISSING:
            raise RuntimeError(
                f"View `{type(model.view).__name__}` is already attached to `<{type(model).__name__} message={message.id}>`."
            )
        super().__init__(cog, message=message, timeout=None)
        self.model: ReactionRole = model
        self.binds: List[Dict[str, Any]] = model.binds
        model.view = self
        self.add_buttons()

    def rebind(self) -> None:
        self.clear_items()
        self.add_buttons()

    def add_buttons(self) -> None:
        for bind in self.binds:
            payload = bind["button"]
            button = Button(
                label=payload["label"],
                emoji=payload["emoji"],
                style=_resolve_button_style(payload["style"]),
                callback=self.handle_interaction,
                custom_id=f"reactrole:{self.message.id}-{bind['role']}",
            )
            self.add_item(button)

    async def update_view(self) -> None:
        for button in self.children:
            if not isinstance(button, Button):
                continue
            custom_id = button.custom_id
            if not any(custom_id.split("-")[-1] == bind["role"] for bind in self.binds):
                self.remove_item(button)
        await self.update_message()

    async def handle_interaction(self, interaction: Interaction, button: Button) -> None:
        await interaction.response.defer()
        await self.model.manager.handle_interaction(self.model, interaction, button)
