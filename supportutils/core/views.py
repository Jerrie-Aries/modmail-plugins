from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction, ui
from discord.ext import commands
from discord.utils import MISSING

from core.models import getLogger, DMDisabled


if TYPE_CHECKING:
    from bot import ModmailBot
    from core.thread import Thread
    from .models import ContactManager, FeedbackManager
    from ..supportutils import SupportUtility

    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]

logger = getLogger(__name__)


class TextInput(ui.TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        super().__init__(**kwargs)


class Modal(ui.Modal):

    children: List[TextInput]

    def __init__(self, view: BaseView, options: Dict[str, Any], callback: Any, title: str = MISSING):
        if title is MISSING:
            title = "Support Utility"
        super().__init__(title=title)
        self.view = view
        if view.timeout is not None:
            view.modals.append(self)
        self.__callback = callback
        for key, value in options.items():
            self.add_item(TextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        for child in self.children:
            value = child.value
            if not value:
                # resolve empty string value
                value = None
            self.view.input_map[child.name] = value

        await interaction.response.defer()
        self.stop()
        self.view.interaction = interaction
        await self.__callback(interaction, self)

    async def on_error(self, interaction: Interaction, error: Exception, item: Any) -> None:
        logger.error("Ignoring exception in modal %r for item %r", self, item, exc_info=error)


class DropdownMenu(ui.Select):
    def __init__(self, *, options: List[discord.SelectOption], **kwargs):
        placeholder = kwargs.pop("placeholder", "Choose option")
        self.__callback = kwargs.pop("callback")
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
        self.view.interaction = interaction
        await self.__callback(interaction, self, option=option)

    def get_option(self, value: str) -> discord.SelectOption:
        for option in self.options:
            if value == option.value:
                return option
        raise ValueError(f"Cannot find select option with value of `{value}`.")


class Button(ui.Button):
    def __init__(self, *args, callback: ButtonCallbackT, **kwargs):
        self.__callback: ButtonCallbackT = callback
        super().__init__(*args, **kwargs)

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        self.view.interaction = interaction
        await self.__callback(interaction, self)


class BaseView(ui.View):
    """
    Base view class.
    """

    children: List[Button]

    def __init__(self, cog: SupportUtility, *, message: discord.Message = MISSING, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.cog: SupportUtility = cog
        self.bot: ModmailBot = cog.bot
        self.message: discord.Message = message
        self.interaction: Optional[discord.Interaction] = None
        self.value: Optional[bool] = None
        self._underlying_modals: List[Modal] = []

    @property
    def modals(self) -> List[Modal]:
        return self._underlying_modals

    async def on_error(self, interaction: Interaction, error: Exception, item: Any) -> None:
        logger.error("Ignoring exception in view %r for item %r", self, item, exc_info=error)

    async def update_view(self) -> None:
        if self.message:
            await self.message.edit(view=self)

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        for modal in self._underlying_modals:
            if modal.is_dispatching() or not modal.is_finished():
                modal.stop()
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        if self.message:
            await self.message.edit(view=self)


class SupportUtilityView(BaseView):
    def __init__(self, ctx: commands.Context, *, input_session: str = MISSING):
        self.ctx: commands.Context = ctx
        self.user: discord.Member = ctx.author
        super().__init__(ctx.cog)
        self.input_session: str = input_session
        self.input_map: Dict[str, Any] = {}
        self.extras: Dict[str, Any] = {}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message(
                "These message components cannot be controlled by you.",
                ephemeral=True,
            )
            return False
        return True

    async def _action_cancel(self, *args) -> None:
        """
        Consistent callback for Cancel button.
        """
        interaction, _ = args
        self.value = None
        await interaction.response.defer()
        self.disable_and_stop()
        return


class ContactView(BaseView):
    """
    Represents a persistent view for contact panel.

    This view can only be added to the bot's message (discord limitation)
    and in the main guild.

    Parameters
    -----------
    cog : SupportUtility
        The SupportUtility cog.
    message : discord.Message
        The message object containing the view the bot listens to.

    """

    children: List[Button]

    def __init__(self, cog: SupportUtility, message: discord.Message = MISSING):
        super().__init__(cog, message=message, timeout=None)

        self.manager: ContactManager = self.cog.contact_manager
        if self.manager.view is not MISSING:
            raise RuntimeError("Another view is already attached to ContactManager instance.")
        self.manager.view = self
        select_config = self.manager.config["select"]
        self.select_options = select_config["options"]
        options = []
        for data in self.select_options:
            options.append(
                discord.SelectOption(
                    emoji=data.get("emoji"), label=data["label"], description=data.get("description")
                )
            )
        if options:
            self.add_item(
                DropdownMenu(
                    options=options,
                    placeholder=select_config.get("placeholder"),
                    callback=self.handle_interaction,
                    custom_id=f"contact_dropdown:{self.message.channel.id}-{self.message.id}",
                )
            )
        button_config = self.manager.config["button"]
        emoji = button_config.get("emoji")
        label = button_config.get("label")
        if emoji is None and label is None:
            label = "Contact"
        try:
            style = ButtonStyle[button_config.get("style")]
        except (KeyError, TypeError, ValueError):
            style = ButtonStyle.grey
        payload = {
            "emoji": emoji,
            "label": label,
            "style": style,
            "custom_id": f"contact_button:{self.message.channel.id}-{self.message.id}",
            "callback": self.handle_interaction,
        }
        self.add_item(Button(**payload))

    async def interaction_check(self, interaction: Interaction) -> bool:
        user = interaction.user
        if self.bot.guild.get_member(user.id) is None:
            await interaction.response.defer()
            return False
        exists = await self.bot.threads.find(recipient=user)
        embed = discord.Embed(color=self.bot.error_color)
        if exists:
            content = "A thread for you already exists"
            if exists.channel:
                content += f" in {exists.channel.mention}"
            content += "."
            embed.description = content
        elif await self.bot.is_blocked(user):
            embed.description = f"You are currently blocked from contacting {self.bot.user.name}."
        elif self.bot.config["dm_disabled"] in (DMDisabled.NEW_THREADS, DMDisabled.ALL_THREADS):
            embed.description = self.bot.config["disabled_new_thread_response"]
            logger.info(
                "A new thread using contact menu was blocked from %s due to disabled Modmail.",
                user,
            )
        else:
            return True

        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    async def handle_interaction(
        self, interaction: Interaction, item: Union[Button, DropdownMenu], **kwargs
    ) -> None:
        """
        Entry point for interactions on this view after all check has passed.
        Thread creation and sending response will be done from here.
        """
        if not isinstance(item, (Button, DropdownMenu)):
            raise TypeError(
                f"Invalid type of item received. Expected Button or DropdownMenu, got {type(item).__name__} instead."
            )

        await interaction.response.defer()
        user = interaction.user
        category = None
        if isinstance(item, DropdownMenu):
            option = kwargs.pop("option")
            for data in self.select_options:
                if data.get("label") == option.label:
                    category_id = data.get("category")
                    if not category_id:
                        break
                    entity = self.bot.get_channel(int(category_id))
                    if entity:
                        category = entity
                    break

        thread = await self.manager.create(
            recipient=user,
            category=category,
            interaction=interaction,
        )

        if thread.cancelled:
            return

        embed = discord.Embed(
            title=self.bot.config["thread_creation_contact_title"],
            description=self.bot.config["thread_creation_self_contact_response"],
            color=self.bot.main_color,
        )
        if self.bot.config["show_timestamp"]:
            embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text=f"{user}", icon_url=user.display_avatar.url)
        await user.send(embed=embed)
        del embed

        await thread.wait_until_ready()
        embed = discord.Embed(
            title="Created Thread",
            description=f"Thread started by {user.mention}.",
            color=self.bot.main_color,
        )
        await thread.channel.send(embed=embed)

    async def force_stop(self) -> None:
        """
        Stops listening to interactions made on this view and removes the view from the message.
        """
        self.stop()

        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                # just supress this
                return


class FeedbackView(BaseView):
    """
    Feedback view.
    """

    def __init__(
        self, user: discord.Member, cog: SupportUtility, thread: Thread, *, message: discord.Message = MISSING
    ):
        self.user: discord.Member = user
        self.thread: Thread = thread
        super().__init__(cog, message=message)
        self.manager: FeedbackManager = self.cog.feedback_manager
        self.input_map: Dict[str, Any] = {}

        button_config = self.manager.config["button"]
        emoji = button_config.get("emoji")
        label = button_config.get("label")
        if emoji is None and label is None:
            label = "Feedback"
        try:
            style = ButtonStyle[button_config.get("style")]
        except (KeyError, TypeError, ValueError):
            style = ButtonStyle.grey
        payload = {
            "emoji": emoji,
            "label": label,
            "style": style,
            "callback": self._button_callback,
        }
        self.add_item(Button(**payload))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user == self.user:
            return True
        # most likely not going to happen since this is only sent in DM's
        # defer anyways
        await interaction.response.defer()
        return False

    async def _button_callback(self, interaction: Interaction, item: Button, **kwargs) -> None:
        """
        A single callback called when user presses the feedback button attached to this view.
        """
        text_input = {
            "label": "Content",
            "max_length": 4000,
            "style": discord.TextStyle.long,
            "required": True,
        }
        modal = Modal(self, {"feedback": text_input}, self.manager.feedback_submit, title="Feedback")
        await interaction.response.send_modal(modal)
        await modal.wait()

        if self.value:
            self.disable_and_stop()
            return
