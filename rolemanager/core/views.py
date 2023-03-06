from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction, ui
from discord.ext import commands
from discord.ext.modmail_utils import Limit
from discord.utils import MISSING

from core.models import getLogger

from .converters import AssignableRole, UnionEmoji
from .models import Bind, TriggerType
from .utils import bind_string_format, error_embed


if TYPE_CHECKING:
    from ..rolemanager import RoleManager
    from .models import ReactionRole

    Callback = Callable[..., Awaitable]

logger = getLogger(__name__)

_short_length = 256


class Modal(ui.Modal):

    children: List[ui.TextInput]

    def __init__(self, view: ui.View, options: List[Dict[str, Any]], callback: Callback, **kwargs):
        super().__init__(**kwargs)
        self.view = view
        self.view.modals.append(self)
        self.followup_callback: Callback = callback
        for data in options:
            self.add_item(ui.TextInput(**data))

    async def on_submit(self, interaction: Interaction) -> None:
        for child in self.children:
            value = child.value
            if not value:
                value = None
            self.view.inputs[child.label.lower()] = value

        await self.followup_callback(interaction, self)

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        logger.error("Ignoring exception in modal %r:", self, exc_info=error)


class Select(ui.Select):
    def __init__(self, category: str, *, options: List[discord.SelectOption], callback: Callback, **kwargs):
        self.category: str = category
        self.followup_callback = callback
        super().__init__(options=options, **kwargs)

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        option = self.get_option(self.values[0])
        for opt in self.options:
            opt.default = opt.value in self.values
        await self.followup_callback(interaction, self, option=option)

    def get_option(self, value: str) -> discord.SelectOption:
        """
        Get select option from value.
        """
        for option in self.options:
            if value == option.value:
                return option
        raise ValueError(f"Cannot find select option with value of `{value}`.")


class Button(ui.Button):
    def __init__(self, *args, callback: Callback, **kwargs):
        self.followup_callback: Callback = callback
        super().__init__(*args, **kwargs)

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        await self.followup_callback(interaction, self)


class RoleManagerView(ui.View):
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
        self.message: Union[discord.Message, discord.PartialMessage] = message
        self.cog: RoleManager = cog
        super().__init__(timeout=timeout)

    async def on_error(self, interaction: Interaction, error: Exception, item: Any) -> None:
        logger.error("Ignoring exception in view %r for item %r", self, item, exc_info=error)

    async def update_view(self, **kwargs) -> None:
        self.refresh()
        if self.message:
            await self.message.edit(view=self, **kwargs)

    def disable_all(self) -> None:
        """
        Disable all components in this View.
        """
        for child in self.children:
            child.disabled = True

    def disable_and_stop(self) -> None:
        """
        Disable all components is this View and stop from listening to
        interactions.
        """
        self.disable_all()
        self.stop()

    async def on_timeout(self) -> None:
        """
        Called on View's timeout. This will disable all components and update the message.
        """
        self.disable_and_stop()
        if self.message:
            await self.message.edit(view=self)


_TRIGGER_TYPES = [
    ("reaction", "Legacy reaction with emojis."),
    ("interaction", "Interaction with new discord buttons."),
]
_RULES = [
    ("normal", "Allow users to have multiple roles in group."),
    ("unique", "Remove existing role when assigning another role in group."),
]
_BUTTON_STYLES = [
    ("blurple", None),
    ("green", None),
    ("red", None),
    ("grey", None),
]


class ReactionRoleCreationPanel(RoleManagerView):
    """
    Represents the Reaction Roles creation view.
    This view  will be used to create or edit reaction roles menu.

    Parameters
    -----------
    ctx : commands.Context
        The Context object.
    model : ReactionRole
        The ReactionRole object.
    input_sessions : List[Dict[[str, Any]]]
        A list of dictionaries containing session keys and their description.
        This will be used to switch the pages after the users choose or submit the values for current session.
    """

    def __init__(
        self,
        ctx: commands.Context,
        model: ReactionRole,
        *,
        input_sessions: List[Dict[[str, Any]]],
    ):
        self.ctx: commands.Context = ctx
        self.user: discord.Member = ctx.author
        self.model: ReactionRole = model
        self.input_sessions: List[Dict[[str, Any]]] = input_sessions
        self.value: Optional[bool] = None
        self.inputs: Dict[str, Any] = {}
        self.__bind: Bind = MISSING
        self.__underlying_binds: List[Bind] = []
        self.__index: int = 0
        self.output_embed: discord.Embed = MISSING
        self.preview_description: str = MISSING
        self.modals: List[Modal] = []
        super().__init__(ctx.cog)
        self.add_menu()
        self.add_buttons()
        self.refresh()

    def add_menu(self) -> None:
        attrs = None
        if self.session_key == "type":
            attrs = _TRIGGER_TYPES
            category = "type"
            placeholder = "Choose a trigger type"
        elif self.session_key == "rule":
            attrs = _RULES
            category = "rule"
            placeholder = "Choose a rule"
        elif self.session_key == "bind":
            if self.model.trigger_type and self.model.trigger_type == TriggerType.INTERACTION:
                attrs = _BUTTON_STYLES
                category = "style"
                placeholder = "Choose a color style for button"
        else:
            raise KeyError(f"Session key `{self.session_key}` is not recognized for menu.")

        if attrs is None:
            return

        options = []
        for key, description in attrs:
            option = discord.SelectOption(label=key.title(), description=description, value=key)
            options.append(option)
        self.add_item(
            Select(
                category, options=options, row=0, placeholder=placeholder, callback=self.on_dropdown_select
            )
        )

    def add_buttons(self) -> None:
        if self.session_key == "bind":
            config_buttons = ["add", "set", "clear"]
            for name in config_buttons:
                button = Button(
                    label=name.title(),
                    style=ButtonStyle.blurple if name == "add" else ButtonStyle.grey,
                    row=3,
                    callback=getattr(self, f"_action_{name}"),
                )
                self.add_item(button)

        ret_buttons: Dict[str, Any] = [
            ("done", ButtonStyle.green),
            ("preview", ButtonStyle.grey),
            ("cancel", ButtonStyle.red),
        ]
        for name, style in ret_buttons:
            self.add_item(
                Button(label=name.title(), style=style, callback=getattr(self, f"_action_{name}"), row=4)
            )

    def rebind(self) -> None:
        """
        Clear and regenerate the components.
        """
        self.clear_items()
        self.add_menu()
        self.add_buttons()

    def refresh(self) -> None:
        for child in self.children:
            if not isinstance(child, Button):
                continue
            label = child.label.lower()
            if label in ("done", "clear"):
                child.disabled = len(self.model.binds + self.__underlying_binds) < 1
            elif label == "add":
                child.disabled = not self.__bind or not self.__bind.is_set()
            else:
                child.disabled = False

    @property
    def session_key(self) -> str:
        return self.input_sessions[self.__index]["key"]

    @property
    def session_description(self) -> str:
        return self.input_sessions[self.__index]["description"]

    def _parse_output_description(self) -> str:
        desc = self.preview_description
        for bind in self.model.binds + self.__underlying_binds + [self.__bind]:
            if not bind or not bind.is_set():
                continue
            if self.model.trigger_type == TriggerType.INTERACTION:
                emoji = bind.button.emoji
                label = bind.button.label
            else:
                emoji = bind.emoji
                label = None
            desc += f"- {bind_string_format(str(emoji) if emoji else None, label, str(bind.role.id))}\n"
        return desc

    def get_output_buttons(self) -> List[Button]:
        if not self.model.trigger_type or self.model.trigger_type == TriggerType.REACTION:
            return []
        buttons = []
        for bind in self.model.binds + self.__underlying_binds + [self.__bind]:
            if not bind or not bind.is_set():
                continue
            buttons.append(bind.button)
        return buttons

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        return False

    def stop(self) -> None:
        """
        Stop the View from listening to interactions.
        Internally this will also stop the underlying Modal instances.
        """
        for modal in self.modals:
            if modal.is_dispatching() or not modal.is_finished():
                modal.stop()
        super().stop()

    async def _action_add(self, interaction: Interaction, *args) -> None:
        bind = self.__bind
        if self.model.trigger_type == TriggerType.INTERACTION:
            emoji = bind.button.emoji
            label = bind.button.label
        elif self.model.trigger_type == TriggerType.REACTION:
            emoji = bind.emoji
            label = None
        else:
            raise TypeError(f"`{self.model.trigger_type}` is invalid for reaction roles trigger type.")

        self.__underlying_binds.append(bind)
        self.__bind = MISSING
        self.inputs.clear()
        embed = discord.Embed(
            color=self.ctx.bot.main_color,
            description=f"Added '{bind_string_format(str(emoji) if emoji else None, label, str(bind.role.id))}' bind to the list.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.rebind()
        await self.update_view()

    async def _action_set(self, interaction: Interaction, *args) -> None:
        options = [
            {
                "label": "Role",
                "max_length": _short_length,
            },
            {
                "label": "Emoji",
                "required": self.model.trigger_type == TriggerType.REACTION,
                "max_length": _short_length,
            },
        ]
        if self.model.trigger_type == TriggerType.INTERACTION:
            options.append(
                {
                    "label": "Label",
                    "required": False,
                    "max_length": Limit.button_label,
                },
            )

        for opt in options:
            opt["default"] = self.inputs.get(opt["label"].lower())

        modal = Modal(self, options, self.resolve_inputs, title="Reaction Role")
        await interaction.response.send_modal(modal)

    async def _action_clear(self, interaction: Interaction, *args) -> None:
        await interaction.response.defer()
        self.inputs.clear()
        self.__bind = MISSING
        self.__underlying_binds.clear()
        self.model.binds.clear()
        await self.update_view()

    async def _action_preview(self, interaction: Interaction, *args) -> None:
        buttons = self.get_output_buttons()
        if buttons:
            view = ui.View(timeout=10)  # default View instance
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
        if self.__underlying_binds:
            self.model.binds.extend(self.__underlying_binds)
        self.disable_and_stop()

    async def _action_cancel(self, interaction: Interaction, *args) -> None:
        self.inputs.clear()
        self.__bind = MISSING
        self.__underlying_binds.clear()
        self.value = False
        self.disable_and_stop()
        await interaction.response.edit_message(view=self)

    async def on_dropdown_select(
        self,
        interaction: Interaction,
        select: Select,
        option: discord.SelectOption,
    ) -> None:
        await interaction.response.defer()
        category = select.category
        if category in ("type", "rule"):
            if category == "rule":
                self.model.rule = option.value.upper()
            else:
                value = option.value.upper()
                self.model.trigger_type = value
            self.__index += 1
            embed = self.message.embeds[0]
            embed.description = self.session_description
            self.rebind()
        elif category == "style":
            value = ButtonStyle[option.value]
            if self.__bind and self.__bind.button:
                self.__bind.button.style = value
            else:
                self.inputs["style"] = value
        else:
            raise KeyError(f"Category `{category}` is invalid for `on_dropdown_select` method.")
        await self.update_view(embed=self.message.embeds[0])

    async def resolve_inputs(self, interaction: Interaction, modal: Modal) -> None:
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
                    if any(entity.id == bind.role.id for bind in self.model.binds + self.__underlying_binds):
                        errors.append(f"Duplicate role ID: `{entity.id}`. Please set other role.")
                elif key == "emoji":
                    if self.model.trigger_type == TriggerType.REACTION:
                        # check emoji
                        for bind in self.model.binds + self.__underlying_binds:
                            if entity == bind.emoji:
                                errors.append(
                                    f"Emoji {entity} has already linked to <@&{bind.role.id}> on this message."
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
            if self.__bind is MISSING:
                self.__bind = Bind(self.model)

            self.__bind.role = ret.pop("role")
            if self.model.trigger_type == TriggerType.REACTION:
                self.__bind.emoji = ret.pop("emoji")
            else:
                payload = {}
                for key in list(ret):
                    payload[key] = ret.pop(key)
                payload["callback"] = self.model.handle_interaction
                self.__bind.button = Button(**payload)
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
        self.binds: List[Bind] = model.binds
        model.view = self
        self.add_buttons()

    def rebind(self) -> None:
        """
        Clear and re-add the buttons.
        """
        self.clear_items()
        self.add_buttons()

    def add_buttons(self) -> None:
        """
        Add buttons. Custom IDs will be assigned from here.
        """
        for bind in self.binds:
            button = bind.button
            button.custom_id = f"reactrole:{self.message.id}-{bind.role.id}"
            self.add_item(button)

    async def update_view(self) -> None:
        for button in self.children:
            if not isinstance(button, Button):
                continue
            custom_id = button.custom_id
            if not any(custom_id.split("-")[-1] == str(bind.role.id) for bind in self.binds):
                self.remove_item(button)
        await self.message.edit(view=self)
