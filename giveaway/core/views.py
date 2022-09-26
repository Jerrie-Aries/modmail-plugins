from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction, TextStyle
from discord.ext import commands
from discord.ui import Button, Modal, TextInput, View
from discord.utils import MISSING

from .utils import duration_syntax, format_time_remaining, time_converter


if TYPE_CHECKING:
    from ..giveaway import Giveaway

    ButtonCallbackT = Callable[[Union[Interaction, Any]], Awaitable]


_max_embed_length = 6000
_short_length = 256
_long_length = 4000  # maximum length allowed for modal input
_field_value_length = 2048

GIFT = "\U0001F381"


class GiveawayTextInput(TextInput):
    def __init__(self, name: str, **kwargs):
        self.name: str = name
        super().__init__(**kwargs)


class GiveawayModal(Modal):

    children: List[GiveawayTextInput]

    def __init__(self, view: GiveawayView):
        super().__init__(title="Giveaway")
        self.view = view
        for key, value in self.view.input_map.items():
            self.add_item(GiveawayTextInput(key, **value))

    async def on_submit(self, interaction: Interaction) -> None:
        errors = []
        for child in self.children:
            self.view.input_map[child.name]["default"] = child.value

        await interaction.response.defer()
        self.stop()
        for _, value in self.view.input_map.items():
            if value.get("required", True) and value["default"] is None:
                self.view._ready = False
                await self.view.update_view()
                return

        self.view.giveaway_prize = self.view.input_map["prize"].get("default")
        winners = self.view.input_map["winners"]["default"]
        try:
            winners = int(winners)
        except ValueError:
            errors.append("Unable to parse giveaway winners to numbers.")
        else:
            if winners < 0:
                errors.append("Giveaway can only be held with 1 or more winners.")
            else:
                self.view.giveaway_winners = winners

        duration = self.view.input_map["duration"]["default"]
        try:
            converted = await time_converter(self.view.ctx, duration, now=discord.utils.utcnow())
        except (commands.BadArgument, commands.CommandError):
            errors.append(
                "Failed to parse duration. Please use the following syntax.\n\n" f"{duration_syntax}"
            )
        else:
            if converted.dt.timestamp() - converted.now.timestamp() <= 0:
                errors.append("Invalid duration provided.")
            else:
                self.view.giveaway_end = converted.dt.timestamp()
                self.view._ready = True
        if errors:
            self.view._ready = False
            for error in errors:
                await interaction.followup.send(error, ephemeral=True)
        else:
            self.view._ready = True
        await self.view.update_view()


class GiveawayViewButton(Button["GiveawayView"]):
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


class GiveawayView(View):

    children: List[GiveawayViewButton]

    def __init__(
        self,
        ctx: commands.Context,
        user: discord.Member,
        *,
        channel: discord.TextChannel,
        timeout: float = 600.0,
    ):
        super().__init__(timeout=timeout)
        self.ctx: commands.Context = ctx
        self.cog: Giveaway = ctx.cog
        self.user: discord.Member = user
        self.message: discord.Message = MISSING  # should be reassigned
        self.giveaway_channel: discord.TextChannel = channel
        self.giveaway_end: float = MISSING
        self.giveaway_winners: int = MISSING
        self.giveaway_prize: str = MISSING
        self.giveaway_message: discord.Message = MISSING

        # button labels and modal titles will be based on these keys
        self.input_map: Dict[str, Any] = {
            "content": {
                "label": "Mention or short content",
                "max_length": _short_length,
                "required": False,
            },
            "description": {
                "label": "Description",
                "default": f"React with {self.cog.giveaway_emoji} to enter the giveaway!",
                "style": TextStyle.long,
                "max_length": _long_length,
            },
            "prize": {
                "label": "Giveaway prize",
                "max_length": _field_value_length,
            },
            "winners": {
                "label": "Winners count",
                "max_length": 2,
            },
            "duration": {
                "label": "Duration",
                "max_length": _short_length,
            },
        }
        self.ret_buttons: Dict[str, Any] = {
            "send": (ButtonStyle.green, self._action_done),
            "edit": (ButtonStyle.grey, self._action_edit),
            "preview": (ButtonStyle.grey, self._action_preview),
            "cancel": (ButtonStyle.red, self._action_cancel),
        }

        self._ready: bool = False
        self._generate_buttons()
        self.refresh()

    def _generate_buttons(self) -> None:
        for label, item in self.ret_buttons.items():
            # `item` is a tuple of (ButtonStyle, callback)
            self.add_item(GiveawayViewButton(label.title(), style=item[0], callback=item[1]))

    def refresh(self) -> None:
        for child in self.children:
            if child.label.lower() in ("send", "preview"):
                child.disabled = not self._ready

    async def update_view(self) -> None:
        self.refresh()
        await self.message.edit(view=self)

    def giveaway_data(self) -> Dict[str, Any]:
        data = {
            "item": self.giveaway_prize,
            "winners": self.giveaway_winners,
            "time": self.giveaway_end,
            "guild": self.giveaway_channel.guild.id,
            "channel": self.giveaway_channel.id,
            "message": self.giveaway_message.id,
        }
        return data

    async def _action_done(self, interaction: Interaction) -> None:
        message = await self.giveaway_channel.send(**self.send_params())
        await message.add_reaction(self.cog.giveaway_emoji)
        self.giveaway_message = message
        await interaction.response.send_message(
            f"Done. Giveaway has been posted in {self.giveaway_channel.mention}.", ephemeral=True
        )
        self.disable_and_stop()
        await self.message.edit(view=self)

    async def _action_edit(self, interaction: Interaction) -> None:
        modal = GiveawayModal(self)
        await interaction.response.send_modal(modal)
        await modal.wait()

    async def _action_preview(self, interaction: Interaction) -> None:
        try:
            await interaction.response.send_message(ephemeral=True, **self.send_params())
        except discord.HTTPException as exc:
            error = f"**Error:**\n```py\n{type(exc).__name__}: {str(exc)}\n```"
            await interaction.response.send_message(error, ephemeral=True)

    async def _action_cancel(self, interaction: Interaction) -> None:
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

    def send_params(self) -> Dict[str, Any]:
        params = {}
        content = self.input_map["content"].get("default")
        if content:
            params["content"] = content
        embed = self.create_embed()
        params["embed"] = embed
        return params

    def create_embed(self) -> discord.Embed:
        winners = self.giveaway_winners
        now_utc = discord.utils.utcnow().timestamp()
        time_left = self.giveaway_end - now_utc
        time_remaining = format_time_remaining(time_left)

        embed = discord.Embed(title=self.cog._giveaway_title, colour=0x00FF00)
        embed.set_author(
            **self.cog.author_data("system", extra="giveaway", channel_id=self.giveaway_channel.id)
        )
        embed.description = f"React with {self.cog.giveaway_emoji} to enter the giveaway!"
        embed.add_field(name=f"{GIFT} Prize:", value=self.giveaway_prize)
        embed.add_field(name="Hosted by:", value=self.ctx.author.mention, inline=False)
        embed.add_field(name="Time remaining:", value=f"_**{time_remaining}**_", inline=False)
        embed.set_footer(text=f"{winners} {'winners' if winners > 1 else 'winner'} | Ends at")
        embed.timestamp = datetime.fromtimestamp(self.giveaway_end)
        length = len(embed)
        if not length:
            raise ValueError("Embed is empty.")
        if length > _max_embed_length:
            raise ValueError(
                f"Embed length exceeds the maximum length allowed, {length}/{_max_embed_length}."
            )
        return embed

    def disable_and_stop(self) -> None:
        for child in self.children:
            child.disabled = True
        if not self.is_finished():
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        # Edit the message without `interaction.response`
        await self.message.edit(view=self)
