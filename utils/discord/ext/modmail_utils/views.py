from __future__ import annotations

from typing import (
    Awaitable,
    Callable,
    List,
    TypedDict,
    Union,
    TYPE_CHECKING,
)

import discord
from discord import ButtonStyle, Interaction
from discord.utils import MISSING

from .ui import Button, View


__all__ = ("ConfirmView",)


if TYPE_CHECKING:
    from bot import ModmailBot

    ConfirmationButtonCallback = Callable[[Button, Interaction], Awaitable]

    class ConfirmationButtonPayload(TypedDict):
        label: str
        style: ButtonStyle
        callback: ConfirmationButtonCallback


class ConfirmView(View):
    """
    Confirmation views. This can be used to add buttons on confirmation messages.

    Users can only select one of the accept and deny buttons on this view.
    After one of them is selected, the view will stop which means the bot will no longer listen to
    interactions on this view, and the buttons will be disabled.

    Parameters
    -----------
    bot : ModmailBot
        The Modmail bot.
    user : Union[discord.Member, discord.User]
        The author that triggered this confirmation view.
    timeout : float
        Time before this view timed out. Defaults to `20` seconds.
    """

    children: List[Button]

    def __init__(
        self,
        bot: ModmailBot,
        user: Union[discord.Member, discord.User],
        timeout: float = 20.0,
    ):
        self.bot: ModmailBot = bot
        self.user: Union[discord.Member, discord.User] = user
        super().__init__(timeout=timeout)

        self.button_map: List[ConfirmationButtonPayload] = [
            {
                "label": "Yes",
                "style": ButtonStyle.green,
                "callback": self._action_confirm,
            },
            {
                "label": "No",
                "style": ButtonStyle.red,
                "callback": self._action_cancel,
            },
        ]
        self._selected_button: Button = MISSING

        for payload in self.button_map:
            self.add_item(Button(**payload))

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        await interaction.response.send_message("These buttons cannot be controlled by you.", ephemeral=True)
        return False

    async def _action_confirm(self, interaction: Interaction, button: Button):
        """
        Executed when the user presses the `confirm` button.
        """
        self._selected_button = button
        self.value = True
        await self._update_view(interaction)

    async def _action_cancel(self, interaction: Interaction, button: Button):
        """
        Executed when the user presses the `cancel` button.
        """
        self._selected_button = button
        self.value = False
        await self._update_view(interaction)

    async def _update_view(self, interaction: Interaction):
        """
        Disable buttons and stop the view after interaction is made.
        """
        self.refresh()
        await interaction.response.edit_message(view=self)
        self.stop()

    def refresh(self) -> None:
        """
        Disables the buttons on the view. Unselected button will be greyed out.
        """
        for child in self.children:
            child.disabled = True
            if self._selected_button and child != self._selected_button:
                child.style = discord.ButtonStyle.grey
