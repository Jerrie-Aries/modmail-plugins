from __future__ import annotations

from typing import Any, Awaitable, Callable, TYPE_CHECKING

import discord

from discord import ui, ButtonStyle, Interaction
from discord.ext.modmail_utils import ui as muui
from discord.utils import MISSING


if TYPE_CHECKING:
    from discord.ext import commands
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


class FollowupView(muui.View):
    """
    Represents followup view. Initiating this class with asynchronous context manager (`async with ...` syntax)
    will automatically disable all components attached to the handler's view. Meanwhile exiting the context manager
    will enable all those components back.

    This is useful if you want to make sure the user do not interact on handler's components before they are done
    interacting on this view's components.
    """

    def __init__(self, handler: LoggingPanelView, interaction: Interaction, *args: Any, **kwargs: Any):
        self.handler: LoggingPanelView = handler
        self.original_interaction: Interaction = interaction
        super().__init__(*args, **kwargs)

    async def __aenter__(self) -> "FollowupView":
        await self.lock(self.original_interaction)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.message.delete()
        # Note: by default we will be using `.inputs` as kwargs for `Interaction.edit_original_response`
        # in `unlock`.
        await self.unlock(self.original_interaction, **self.inputs)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.handler.user.id == interaction.user.id:
            return True
        return False

    async def lock(self, interaction: Interaction) -> None:
        for child in self.handler.children:
            child.disabled = True
        await interaction.response.edit_message(view=self.handler)

    async def unlock(self, interaction: Interaction, **kwargs: Any) -> None:
        for child in self.handler.children:
            child.disabled = False
        self.handler._resolve_components()
        await interaction.edit_original_response(view=self.handler, **kwargs)

    async def on_timeout(self) -> None:
        pass


class LoggingPanelView(muui.View):
    """
    Control panel view to configure logging settings.
    """

    def __init__(self, ctx: commands.Context, logger: ModerationLogging):
        self.ctx: commands.Context = ctx
        self.user: discord.Member = ctx.author
        self.cog: Moderation = ctx.cog
        self.bot: ModmailBot = ctx.bot
        self.logger: ModerationLogging = logger
        self.guild: discord.Guild = logger.guild
        super().__init__(timeout=300)
        self._resolve_components()

    def _resolve_components(self) -> None:
        enabled = self.logger.is_enabled()
        self.enable_button.label = "Disable" if enabled else "Enable"
        self.quit_button.label = "Done" if self.value else "Quit"
        self.quit_button.style = ButtonStyle.green if self.value else ButtonStyle.red

    async def edit_message(self, *args: Any, **kwargs: Any) -> None:
        self._resolve_components()
        await super().edit_message(*args, **kwargs)

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
            description=(
                "Moderation logging configuration. Use the buttons below to change the configurations.\n\n"
                "__**Button info**__\n"
                "- **Enable/Disable:** Enable or disable the logging. Disabling this will override the **Events** config.\n"
                "- **Log channel:** Channel where the events will be logged.\n"
                "- **Log events:** Enable or disable certain type of events.\n"
                "- **Whitelist:** Channels where the message updates will be ignored.\n"
            ),
            color=self.bot.main_color,
        )
        embed.set_thumbnail(url=self.guild.icon)
        embed.set_author(name=self.bot.user.name, url=self.bot.user.display_avatar)

        embed.add_field(name="Enabled", value=f"`{self.logger.is_enabled()}`")
        log_channel = self.logger.channel
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "`None`")
        wh_url = self.logger.config.webhook_url
        embed.add_field(name="Webhook", value=f"[Webhook URL]({wh_url})" if wh_url else "`None`")
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
        async with FollowupView(self, interaction, timeout=self.timeout) as view:
            view.add_item(select)
            view.message = await interaction.followup.send(view=view)
            await view.wait()
            if view.value:
                self.value = True
                view.inputs["embed"] = self.update_embed_field("Events", self.log_events_fmt_string, False)

    @discord.ui.button(label="Whitelist", style=ButtonStyle.grey)
    async def whitelist_button(self, interaction: Interaction, button: discord.ui.Button):
        button_args = [
            ("Add", self._add_whitelist),
            ("Clear", self._clear_whitelist),
            ("Close", self._close_ephemeral),
        ]
        async with FollowupView(self, interaction, timeout=self.timeout) as view:
            for arg in button_args:
                button = muui.Button(
                    label=arg[0],
                    callback=arg[1],
                    style=ButtonStyle.grey if arg[0] != "Close" else ButtonStyle.red,
                )
                view.add_item(button)
            view.message = await interaction.followup.send(view=view)
            await view.wait()

    @discord.ui.button(label="...", row=1)
    async def quit_button(self, interaction: Interaction, button: discord.ui.Button):
        self.interaction = interaction
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.value:
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()
            await interaction.delete_original_response()
            await self.ctx.message.add_reaction(_check_mark)

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
            self.logger.channel = channel
            self.update_embed_field(modal.title, f"<#{channel.id}>")
            self.update_embed_field("Webhook", "`None`")
        elif child.name == "channel_whitelist":
            # whitelist channel
            wl_channels = self.logger.config["channel_whitelist"]
            if str(channel.id) in wl_channels:
                err_embed.description = f"Channel {channel.mention} is already whitelisted."
                return await interaction.response.send_message(embed=err_embed, ephemeral=True)
            wl_channels.append(str(channel.id))
            self.update_embed_field(modal.title, self.wl_channels_fmt_string)
        else:
            raise TypeError(f"Invalid modal input session, `{child.name}`.")
        self.value = True
        await interaction.response.defer()
        await self.edit_message(embed=self.embed)
