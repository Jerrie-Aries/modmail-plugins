from __future__ import annotations

from typing import Any, Awaitable, Callable, TYPE_CHECKING

import discord

from discord import ui, ButtonStyle, Interaction
from discord.ext.modmail_utils import ui as muui
from discord.utils import MISSING


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..moderation import Moderation
    from .logging import ModerationLogging

    Callback = Callable[..., Awaitable]


_check_mark = "\N{WHITE HEAVY CHECK MARK}"
_cross_mark = "\N{CROSS MARK}"


class Select(ui.Select):
    def __init__(self, *args: Any, **kwargs: Any):
        self._select_callback = kwargs.pop("callback", MISSING)
        super().__init__(*args, **kwargs)

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        await self._select_callback(interaction, self)


class EphemeralView(muui.View):
    def __init__(self, handler: LoggingPanelView, *args: Any, timeout: int = 120, **kwargs: Any):
        self.handler: LoggingPanelView = handler
        super().__init__(*args, **kwargs)


class LoggingPanelView(muui.View):
    def __init__(self, user: discord.Member, cog: Moderation, logger: ModerationLogging):
        self.user: discord.Member = user
        self.cog: Moderation = cog
        self.bot: ModmailBot = cog.bot
        self.logger: ModerationLogging = logger
        self.guild: discord.Guild = logger.guild
        super().__init__(timeout=300)
        self._resolve_components()

    def _resolve_components(self) -> None:
        enabled = self.logger.config["logging"]
        self.enable_button.label = "Disable" if enabled else "Enable"
        if self.value:
            self.close_button.label = "Done"
            self.close_button.style = ButtonStyle.green

    async def edit_message(self, *args: Any, **kwargs: Any) -> None:
        self._resolve_components()
        await super().edit_message(*args, **kwargs)

    async def lock(self, interaction: Interaction) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    async def unlock(self, interaction: Interaction, **kwargs: Any) -> None:
        for child in self.children:
            child.disabled = False
        self._resolve_components()
        await interaction.edit_original_response(view=self, **kwargs)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.user.id == interaction.user.id:
            return True
        return False

    @property
    def embed(self) -> discord.Embed:
        if self.message:
            return self.message.embeds[0]
        embed = discord.Embed(
            title="Logging Config",
            description=("Moderation logging configuration."),
            color=self.bot.main_color,
        )
        embed.set_thumbnail(url=self.guild.icon)
        embed.set_author(name=self.bot.user.name, url=self.bot.user.display_avatar)
        embed.set_footer(text="Use buttons below to change the value.")

        config = self.logger.config
        embed.add_field(name="Enabled", value=f"`{config['logging']}`")
        log_channel = config.log_channel
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "`None`")
        webhook = config["webhook"]
        embed.add_field(name="Webhook", value=f"[Webhook URL]({webhook })" if webhook else "`None`")
        wl_channels = self.wl_channels_fmt_string
        embed.add_field(name="Whitelist channels", value=wl_channels if wl_channels else "`None`")
        embed.add_field(name="Events", value=self.log_events_fmt_string, inline=False)
        return embed

    @property
    def log_events_fmt_string(self) -> None:
        ret = ""
        for key, val in self.logger.config["log_events"].items():
            enabled = _check_mark if val else _cross_mark
            ret += f'- {" ".join(key.split("_")).capitalize()}' + f" -> {enabled}\n"
        return ret

    @property
    def wl_channels_fmt_string(self) -> str:
        wl_channels = self.logger.config["channel_whitelist"]
        ret = ""
        for c in wl_channels:
            ret += f"<#{c}>\n"
        return ret

    def update_embed_field(self, name: str, value: str, inline: bool = True) -> discord.Embed:
        embed = self.embed
        for i, field in enumerate(embed.fields):
            if field.name == name:
                embed.set_field_at(i, name=name, value=value, inline=inline)
        return embed

    @discord.ui.button(label="Enable", style=ButtonStyle.grey)
    async def enable_button(self, interaction: Interaction, button: discord.ui.Button):
        old_val = self.logger.config["logging"]
        self.logger.config["logging"] = not old_val
        self.value = True
        self._resolve_components()
        embed = self.update_embed_field("Enabled", f"`{self.logger.config['logging']}`")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Log channel", style=ButtonStyle.grey)
    async def log_channel_button(self, interaction: Interaction, button: discord.ui.Button):
        payload = {
            "label": "Log channel",
            "max_length": 100,
            "required": True,
            "default": self.logger.config["log_channel"],
        }
        modal = muui.Modal(
            self,
            {"log_channel": payload},
            callback=self._channel_modal_submit,
            title="Log channel",
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Log events", style=ButtonStyle.grey)
    async def log_events_button(self, interaction: Interaction, button: discord.ui.Button):
        view = EphemeralView(self, timeout=self.timeout)
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
        view.add_item(select)
        await self.lock(interaction)
        message = await interaction.followup.send(view=view)
        await view.wait()
        await message.delete()
        kwargs = {}
        if view.value:
            kwargs["embed"] = self.update_embed_field("Events", self.log_events_fmt_string, False)
        await self.unlock(interaction, **kwargs)

    @discord.ui.button(label="Whitelist", style=ButtonStyle.grey)
    async def whitelist_button(self, interaction: Interaction, button: discord.ui.Button):
        button_args = [
            ("Add", self._add_whitelist),
            ("Clear", self._clear_whitelist),
            ("Close", self._close_ephemeral),
        ]
        view = EphemeralView(self, timeout=self.timeout)
        for arg in button_args:
            button = muui.Button(
                label=arg[0],
                callback=arg[1],
                style=ButtonStyle.grey if arg[0] != "Close" else ButtonStyle.red,
            )
            view.add_item(button)
        await self.lock(interaction)
        message = await interaction.followup.send(view=view)
        await view.wait()
        await message.delete()
        await self.unlock(interaction)

    @discord.ui.button(label="Close", style=ButtonStyle.red, row=1)
    async def close_button(self, interaction: Interaction, button: discord.ui.Button):
        self.interaction = interaction
        self.value = False
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)

    async def _add_whitelist(self, interaction: Interaction, button: muui.Button) -> None:
        payload = {
            "label": "Channel",
            "max_length": 100,
            "required": True,
        }
        modal = muui.Modal(
            self,
            {"channel_whitelist": payload},
            callback=self._channel_modal_submit,
            title="Whitelist channels",
        )
        await interaction.response.send_modal(modal)

    async def _clear_whitelist(self, interaction: Interaction, button: muui.Button) -> None:
        wl_channels = self.logger.config["channel_whitelist"]
        if not wl_channels:
            await interaction.response.send_message("There is no whitelist channel.")
        else:
            wl_channels.clear()
            await interaction.response.defer()
            self.value = True
            embed = self.update_embed_field("Whitelist channels", "`None`")
            await self.edit_message(embed=embed)

    async def _close_ephemeral(self, interaction: Interaction, button: muui.Button) -> None:
        view = button.view
        await interaction.response.defer()
        view.stop()

    async def _select_callback(self, interaction: Interaction, select: Select) -> None:
        view = select.view
        for key in self.logger.config["log_events"].keys():
            old_val = self.logger.config["log_events"][key]
            if (old_val and key in select.values) or (not old_val and key not in select.values):
                continue
            self.logger.config["log_events"][key] = key in select.values
            view.value = True
            view.handler.value = True
        await interaction.response.defer()
        view.stop()

    async def _channel_modal_submit(self, interaction: Interaction, modal: muui.Modal) -> None:
        modal.stop()
        child = modal.children[0]  # we only have one element
        err_embed = discord.Embed(title="Error", color=self.bot.error_color)
        try:
            value = int(child.value)
        except (TypeError, ValueError):
            err_embed.description = "Invalid input type. The input must be integers for channel ID."
            return await interaction.response.send_message(embed=err_embed, ephemeral=True)

        channel = self.guild.get_channel(value)
        if channel is None:
            err_embed.description = f"Channel `{value}` not found."
            return await interaction.response.send_message(embed=err_embed, ephemeral=True)
        if child.name == "log_channel":
            self.logger.config[child.name] = str(channel.id)
            self.value = True
            embed = self.update_embed_field(modal.title, f"<#{channel.id}>")
        elif child.name == "channel_whitelist":
            # whitelist channel
            wl_channels = self.logger.config["channel_whitelist"]
            if str(channel.id) in wl_channels:
                err_embed.description = f"Channel {channel.mention} is already whitelisted."
                return await interaction.response.send_message(embed=err_embed, ephemeral=True)
            wl_channels.append(str(channel.id))
            self.value = True
            embed = self.update_embed_field(modal.title, self.wl_channels_fmt_string)
        else:
            raise TypeError(f"Invalid modal input session, `{child.name}`.")
        await interaction.response.defer()
        await self.edit_message(embed=embed)
