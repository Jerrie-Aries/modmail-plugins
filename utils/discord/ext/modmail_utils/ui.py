from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Union, TYPE_CHECKING

import discord
from discord import Interaction, ui
from discord.utils import MISSING

if TYPE_CHECKING:
    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]


class TextInput(ui.TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        super().__init__(**kwargs)


class Modal(ui.Modal):
    """
    Represent custom Modal instance.

    Parameters
    -----------
    view : View
        The view this Modal attached to.
    options : Dict[str, Any]
        A map to construct items i.e. text input for this Modal.
    callback : Any
        A callback to call inside the `on_modal_submit`. This callback should take two parameters;
        Interaction amd the Modal itself.
    title: :class:`str`
        The title of the modal. Can only be up to 45 characters.
    timeout: Optional[:class:`float`]
        Timeout in seconds from last interaction with the UI before no longer accepting input.
        If ``None`` then there is no timeout.
    custom_id: :class:`str`
        The ID of the modal that gets received during an interaction.
        If not given then one is generated for you.
        Can only be up to 100 characters.
    """

    children: List[TextInput]

    def __init__(self, view: View, options: Dict[str, Any], callback: Any, **kwargs):
        super().__init__(**kwargs)
        self.view = view
        if hasattr(self.view, "modals"):
            self.view.modals.append(self)
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


class Button(ui.Button):
    def __init__(self, *args, callback: ButtonCallbackT, **kwargs):
        self.__callback: ButtonCallbackT = callback
        super().__init__(*args, **kwargs)

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        self.view.interaction = interaction
        await self.__callback(interaction, self)


class View(ui.View):
    """
    Base view class.
    """

    children: List[Button]

    def __init__(self, *args, message: Union[discord.Message, discord.PartialMessage] = MISSING, **kwargs):
        super().__init__(*args, **kwargs)
        self._message: Union[discord.Message, discord.PartialMessage] = message
        self.interaction: Optional[discord.Interaction] = None
        self.value: Optional[bool] = None
        self.input_map: Dict[str, Any] = {}
        self.extras: Dict[str, Any] = {}
        self._underlying_modals: List[Modal] = []

    @property
    def modals(self) -> List[Modal]:
        """
        Returns underlying Modal instances initiated from this view. This is mainly
        to properly stop the Modal instances after the view is stopped.

        The case were neither Discord nor discord.py library provide specific event for modal close
        without submitting, so we have to stop them manually. Otherwise it would be waiting for user
        to press the `Submit` button forever even though its windows has long gone.
        """
        return self._underlying_modals

    @property
    def message(self) -> Union[discord.Message, discord.PartialMessage]:
        """
        Returns `discord.Message` or `discord.PartialMessage` object for this instance,
        or `MISSING` if it has never been set.

        This property must be set manually. If it hasn't been set after instantiating the view,
        consider using:
            `view.message = await ctx.send(content="Content.", view=view)`
        """
        return self._message

    @message.setter
    def message(self, item: discord.Message):
        """
        Manually set the `message` attribute for this instance.

        With this attribute set, the view for the message will be automatically updated after
        times out.
        """
        if not isinstance(item, (discord.Message, discord.PartialMessage)):
            raise TypeError(f"Invalid type. Expected `Message`, got `{type(item).__name__}` instead.")

        self._message = item

    async def update_message(self) -> None:
        """
        Update this View's current state on a message.

        This will only work if the `.message` attribute is set.
        """
        await self.message.edit(view=self)

    def stop(self) -> None:
        for modal in self.modals:
            if modal.is_dispatching() or not modal.is_finished():
                modal.stop()
        super().stop()

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        if self.message:
            await self.update_message()
