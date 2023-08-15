from __future__ import annotations

from typing import Any, Awaitable, Callable, TYPE_CHECKING

import discord

from discord import ui, Interaction
from discord.ext.modmail_utils.ui import View
from discord.utils import MISSING


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..moderation import Moderation
    from .logging import ModerationLogging

    Callback = Callable[..., Awaitable]


class Select(ui.Select):
    def __init__(self, *args: Any, **kwargs: Any):
        self._select_callback = kwargs.pop("callback", MISSING)
        super().__init__(*args, **kwargs)

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        await self._select_callback(interaction, self)


class LoggingView(View):
    def __init__(self, user: discord.Member, cog: Moderation, glogger: ModerationLogging):
        self.user: discord.Member = user
        self.cog: Moderation = cog
        self.bot: ModmailBot = cog.bot
        self.logger: ModerationLogging = glogger
        super().__init__(timeout=300)
        self._embed: discord.Embed = MISSING

    def fill_items(self) -> None:
        options = []
        for key, val in self.logger.config["log_events"].items():
            option = discord.SelectOption(
                label=" ".join(key.split("_")).capitalize(),
                value=key,
                default=val,
            )
            options.append(option)
        select = Select(
            options=options,
            placeholder="Choose features to enable",
            min_values=0,
            max_values=len(options),
            callback=self._select_callback,
        )
        self.add_item(select)

    @property
    def embed(self) -> discord.Embed:
        if self._embed is MISSING:
            embed = discord.Embed(
                title="Logging events",
                description=self.output_description,
                color=self.bot.main_color,
            )
            embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.display_avatar)
            self._embed = embed
        return self._embed

    @property
    def output_description(self) -> str:
        description = ""
        for key, val in self.logger.config["log_events"].items():
            value = "\N{WHITE HEAVY CHECK MARK}" if val else "\N{CROSS MARK}"
            description += f'- {" ".join(key.split("_")).capitalize()}' + f" -> {value}\n"
        return description

    async def _select_callback(self, interaction: Interaction, select: Select) -> None:
        embed = self.message.embeds[0]
        for key in self.logger.config["log_events"].keys():
            self.logger.config["log_events"][key] = key in select.values
        embed.description = self.output_description
        await interaction.response.edit_message(embed=embed, view=None)
        self.value = True
        self.stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        return False

    async def on_timeout(self) -> None:
        self.stop()
        for child in self.children:
            child.disabled = True
        await self.message.edit(view=self)
