from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import MISSING

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession
from core.time import UserFriendlyTime
from core.utils import truncate


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot
    from core.thread import Thread


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


logger = getLogger(__name__)

max_selectmenu_description = 100

# <!-- Developer -->
try:
    from discord.ext.modmail_utils import ConfirmView, EmojiConverter, Limit
except ImportError as exc:
    required = __plugin_info__["cogs_required"][0]
    raise RuntimeError(
        f"`modmail_utils` package is required for {__plugin_name__} plugin to function.\n"
        f"Install {required} plugin to resolve this issue."
    ) from exc

from .core.config import SupportUtilityConfig
from .core.models import ContactManager, FeedbackManager, ThreadMoveManager
from .core.views import Button, ContactView, Modal, SupportUtilityView


# <!-- ----- -->


class SupportUtility(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self.db: AsyncIOMotorCollection = self.bot.api.get_plugin_partition(self)
        self.config: SupportUtilityConfig = SupportUtilityConfig(self, self.db)
        self.contact_manager: ContactManager = ContactManager(self)
        self.feedback_manager: FeedbackManager = FeedbackManager(self)
        self.move_manager: ThreadMoveManager = ThreadMoveManager(self)

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self.initialize())

    async def cog_unload(self) -> None:
        view = self.contact_manager.view
        if view is not MISSING:
            view.stop()
        for feedback in self.feedback_manager.active:
            feedback.task.cancel()
        self.move_manager.teardown()

    async def initialize(self) -> None:
        await self.bot.wait_for_connected()
        await self.config.fetch()
        await self.contact_manager.initialize()
        await self.feedback_manager.populate()
        await self.move_manager.initialize()

    def _resolve_modal_payload(self, item: Button) -> Dict[str, Any]:
        """
        Internal method to respectively resolve the required payload to initiate
        the `Modal` view.
        """
        view = item.view
        keys = view.extras.get("keys", [])
        if not 1 < len(keys) < 4:
            raise ValueError("Unable to unpack. keys length must only be 2 or 3.")
        if len(keys) == 2:
            prefix, key, subkey = *keys, None
        else:
            prefix, key, subkey = keys

        # confusing part
        # valid_prefixes = ("contact", "feedback", "thread_move")  # these three were root config
        # valid_keys = ("button", "confirmation", "select", "embed", "rating", "response", "inactive", "responded")
        # valid_subkeys = ("embed", "placeholder", "options")
        options = {}
        current = view.extras.get("current")
        if key == "button":
            elements = [("emoji", 256), ("label", Limit.button_label), ("style", 32)]
            for name, length in elements:
                options[name] = {
                    "label": name.title(),
                    "max_length": length,
                    "required": False,
                    "default": view.inputs.get(name) or current.get(name),
                }
        elif key in ("select", "rating"):
            if subkey == "placeholder":
                options[subkey] = {
                    "label": subkey.title(),
                    "max_length": Limit.select_placeholder,
                    "required": True,
                    "default": view.inputs.get(subkey) or current,
                }
            else:
                # select options
                elements = [
                    ("emoji", 256),
                    ("label", Limit.button_label),
                    ("description", Limit.select_description),
                    ("category", 256),
                ]
                for name, length in elements:
                    options[name] = {
                        "label": name.title(),
                        "max_length": length,
                        "required": name in ("label", "category"),
                        "default": view.inputs.get(name),
                    }
        elif "embed" in (key, subkey):
            elements = [
                ("title", Limit.embed_title),
                ("description", Limit.text_input_max),
                ("footer", Limit.embed_footer),
            ]
            for name, length in elements:
                options[name] = {
                    "label": name.title(),
                    "max_length": length,
                    "style": discord.TextStyle.long if name == "description" else discord.TextStyle.short,
                    "required": name == "description",
                    "default": view.inputs.get(name) or current.get(name),
                }
        elif key == "response":
            options[key] = {
                "label": key.title(),
                "max_length": Limit.text_input_max,
                "style": discord.TextStyle.long,
                "required": True,
                "default": view.inputs.get(key) or current,
            }
        else:
            raise ValueError(f"Invalid view input session. Got `{prefix}.{key}.{subkey}` keys.")
        return options

    async def _button_callback(self, interaction: discord.Interaction, item: Button) -> None:
        if not isinstance(item, Button):
            raise TypeError(
                f"Invalid type of item received. Expected Button, got {type(item).__name__} instead."
            )

        options = self._resolve_modal_payload(item)
        view = item.view
        title = view.extras["title"] + " config"
        modal = Modal(view, options, self._modal_callback, title=title)
        await interaction.response.send_modal(modal)
        await modal.wait()

        if view.value:
            view.disable_and_stop()
            return

    async def _modal_callback(self, interaction: discord.Interaction, modal: Modal) -> None:
        """
        Resolve and convert inputs submitted from Modal view.

        Things that need to be converted:
            - Emoji
            - Category channel
            - Button style

        Everything else is just a plain string.
        """
        view = modal.view
        converters = {
            "emoji": EmojiConverter,
            "category": commands.CategoryChannelConverter,
        }
        errors = []
        if view.extras["keys"][1] in ("button", "select") and all(
            (view.inputs.get(elem) is None for elem in ("emoji", "label"))
        ):
            errors.append("ValueError: Emoji and Label cannot both be None.")

        for key, value in view.inputs.items():
            if value is None:
                view.outputs[key] = value
                continue

            if key == "style":
                try:
                    value = value.lower()
                    entity = discord.ButtonStyle[value]
                    if entity == discord.ButtonStyle.url:
                        errors.append("ValueError: ButtonStyle.url is not supported.")
                        continue
                except (KeyError, TypeError, ValueError):
                    errors.append(f"ValueError: `{value}` is invalid for color style.")
                    continue
                view.outputs[key] = value
                continue

            conv = converters.get(key)
            if conv is None:
                # mostly plain string
                view.outputs[key] = value
                continue
            try:
                entity = await conv().convert(view.ctx, value)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)}")
                continue
            if isinstance(entity, discord.CategoryChannel):
                # check exists
                for data in self.config.contact["select"]["options"]:
                    category_id = data["category"]
                    if category_id and str(entity.id) == category_id:
                        errors.append(
                            f"ValueError: Category {entity} is already linked to {data['label'] or data['emoji']}."
                        )
                        continue
                value = str(entity.id)
            elif isinstance(entity, (discord.PartialEmoji, discord.Emoji)):
                value = str(entity)
            else:
                errors.append(f"TypeError: Invalid type of converted value, `{type(entity).__name__}`.")
                continue
            view.outputs[key] = value

        if errors:
            embed = discord.Embed(
                description="\n".join(errors),
                color=self.bot.error_color,
            )
            view.value = False
            modal.stop()
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer()
        view.value = True
        modal.stop()

    def get_config_view(self, ctx: commands.Context, **extras: Dict[str, Any]) -> SupportUtilityView:
        view = SupportUtilityView(ctx, extras=extras)
        set_label = "add" if extras["keys"][-1] == "options" else "set"
        buttons = [
            (set_label, discord.ButtonStyle.grey, self._button_callback),
            ("cancel", discord.ButtonStyle.red, view._action_cancel),
        ]
        for label, style, callback in buttons:
            button = Button(
                label=label.title(),
                style=style,
                callback=callback,
            )
            view.add_item(button)
        return view

    async def _set_button_invoker(
        self,
        ctx: commands.Context,
        name: str,
        keys: List[str],
        button_config: Dict[str, Any],
        defaults: Dict[str, Any],
        argument: Optional[str],
    ) -> None:
        if argument and argument.lower() in ("clear", "reset"):
            button_config.clear()
            for key, value in defaults.items():
                button_config[key] = value
            await self.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{name.capitalize()} configurations are now reset to defaults.",
            )
            await ctx.send(embed=embed)
        elif argument is None:
            description = ctx.command.help.split("\n\n")[:-1]
            description = "\n\n".join(description) + "\n\n"
            description += (
                "__**Available fields:**__\n"
                "- **Emoji** : Emoji shown on the button. May be a unicode emoji, "
                "format of `:name:`, `<:name:id>` or `<a:name:id>` (animated emoji).\n"
                f"- **Label** : Button label. Must not exceed {Limit.button_label} characters.\n"
                "- **Style** : The color style for the button. Must be one of these (case insensitive):\n"
                " - `Blurple`\n"
                " - `Green`\n"
                " - `Red`\n"
                " - `Grey`\n\n"
            )
            embed = discord.Embed(
                title=name.capitalize(),
                color=self.bot.main_color,
                description=description,
            )
            embed.set_footer(text="Press Set to set/edit the values")
            embed.description += "### Current values"
            for key in ("emoji", "label", "style"):
                embed.add_field(name=key.title(), value=f"`{button_config.get(key)}`")
            view = self.get_config_view(ctx, title=embed.title, keys=keys, current=button_config)
            view.message = message = await ctx.send(embed=embed, view=view)

            await view.wait()
            await message.edit(view=view)

            if view.value:
                payload = view.outputs
                embed = discord.Embed(
                    description=f"Successfully set the new configurations for {name}.\n\n",
                    color=self.bot.main_color,
                )
                embed.description += "### New values"
                for key in list(payload):
                    embed.add_field(name=key.title(), value=f"`{payload[key]}`")
                    button_config[key] = payload.pop(key)
                await self.config.update()
                await view.interaction.followup.send(embed=embed)
        else:
            raise commands.BadArgument(f"{argument} is not a valid argument.")

    async def _set_embed_invoker(
        self,
        ctx: commands.Context,
        name: str,
        keys: List[str],
        embed_config: Dict[str, Any],
        defaults: Dict[str, Any],
        argument: Optional[str],
    ) -> None:
        if argument and argument.lower() in ("clear", "reset"):
            embed_config.clear()
            for key, value in defaults.items():
                embed_config[key] = value
            await self.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{name.capitalize()} embed configurations are now reset to defaults.",
            )
            await ctx.send(embed=embed)
        elif argument is None:
            # remove the last part where it shows the argument usage bit
            description = ctx.command.help.format(prefix=self.bot.prefix).split("\n\n")[:-1]
            description = "\n\n".join(description) + "\n\n"
            description += (
                "__**Available fields:**__\n"
                f"- **Title** : Embed title. Max {Limit.embed_title} characters.\n"
                f"- **Description** : Embed description. Max {Limit.text_input_max} characters.\n"
                f"- **Footer** : Embed footer text. Max {Limit.embed_footer} characters.\n\n"
            )
            embed = discord.Embed(
                title=name.capitalize(),
                color=self.bot.main_color,
                description=description,
            )
            embed.set_footer(text="Press Set to set/edit the values")
            embed.description += "### Current values"
            for key in ("title", "description", "footer"):
                embed.add_field(name=key.title(), value=f"`{truncate(str(embed_config.get(key)), max=256)}`")
            view = self.get_config_view(ctx, title=embed.title, keys=keys, current=embed_config)
            view.message = message = await ctx.send(embed=embed, view=view)

            await view.wait()
            await message.edit(view=view)

            if view.value:
                payload = view.outputs
                embed = discord.Embed(
                    description=f"Successfully set the new configurations for {name} embed.\n\n",
                    color=self.bot.main_color,
                )
                embed.description += "### New values"
                for key in list(payload):
                    embed.add_field(name=key.title(), value=f"`{truncate(str(payload[key]), max=1024)}`")
                    embed_config[key] = payload.pop(key)
                await self.config.update()
                await view.interaction.followup.send(embed=embed)
        else:
            raise commands.BadArgument(f"{argument} is not a valid argument.")

    async def _set_enable_invoker(
        self,
        ctx: commands.Context,
        name: str,
        parent_config: Dict[str, Any],
        mode: Optional[bool],
    ) -> None:
        enabled = parent_config.get("enable", False)
        if mode is None:
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{name.capitalize()} feature is currently "
                + ("enabled." if enabled else "disabled."),
            )
            return await ctx.send(embed=embed)
        if mode == enabled:
            raise commands.BadArgument(
                f"{name.capitalize()} feature is already " + ("enabled." if enabled else "disabled.")
            )

        parent_config["enable"] = mode
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"{name.capitalize()} feature is now " + ("enabled." if mode else "disabled."),
        )
        await ctx.send(embed=embed)

    async def _set_category_invoker(
        self,
        ctx: commands.Context,
        key: str,
        entity: Optional[Union[discord.CategoryChannel, str]],
    ) -> None:
        embed = discord.Embed(color=self.bot.main_color)
        if entity is None:
            category = getattr(self.move_manager, f"{key}_category")
            embed.description = f"{key.capitalize()} category is currently set to {category}."
            await ctx.send(embed=embed)
        elif isinstance(entity, discord.CategoryChannel):
            if ctx.guild != self.bot.modmail_guild:
                raise commands.BadArgument(
                    f"{key.capitalize()} category can only be set in modmail guild: {self.bot.modmail_guild}."
                )
            self.move_manager.config[key]["category"] = str(entity.id)
            await self.config.update()
            embed.description = f"{key.capitalize()} category is now set to {entity}."
            await ctx.send(embed=embed)
        elif entity in ("reset", "clear"):
            default = self.config.copy(self.config.defaults["thread_move"][key]["category"])
            self.move_manager.config[key]["category"] = default
            await self.config.update()
            embed.description = f"{key.capitalize()} category is now reset to default."
            await ctx.send(embed=embed)
        else:
            raise commands.BadArgument(f"Category {entity} not found.")

    @commands.group(aliases=["conmenu"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def contactmenu(self, ctx: commands.Context):
        """
        Base command for contact menu.

        Create and customise button, dropdown, and embed content for contact menu.
        """
        await ctx.send_help(ctx.command)

    @contactmenu.command(name="create")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_create(self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None):
        """
        Create a contact message and add contact components to it.
        Button and dropdown settings will be retrieved from config. If you want to have custom settings, make sure to set those first with:
        - `{prefix}contactmenu config [option] [value]`
        Otherwise default settings will be used.

        Or you can customise the settings later, then apply the new settings with command:
        - `{prefix}contactmenu refresh`

        `channel` if specified, may be a channel ID, mention, or name.
        If not specified, fallbacks to current channel.
        """
        manager = self.contact_manager
        if manager.view is not MISSING:
            message = manager.view.message
            if message:
                trail = f" on this [message]({message.jump_url})"
            else:
                trail = ""
            raise commands.BadArgument(
                f"There is already active contact menu{trail}. Please disable it first before creating a new one."
            )

        if channel is None:
            channel = ctx.channel
        embed_config = self.config.contact["embed"]
        embed = discord.Embed(
            title=embed_config["title"],
            color=self.bot.main_color,
            description=embed_config["description"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.display_avatar)
        footer_text = embed_config.get("footer")
        if not footer_text:
            footer_text = f"{self.bot.guild.name}: Contact menu"
        embed.set_footer(text=footer_text, icon_url=self.bot.guild.icon)

        view = ContactView(self)
        manager.message = view.message = message = await channel.send(embed=embed, view=view)
        self.config.contact["message"] = str(message.id)
        self.config.contact["channel"] = str(message.channel.id)
        await self.config.update()

        if channel != ctx.channel:
            await ctx.message.add_reaction("\u2705")

    @contactmenu.command(name="attach")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_attach(self, ctx: commands.Context, *, message: discord.Message):
        """
        Attach contact components to the message specified.
        Button and dropdown settings will be retrieved from config.

        `message` may be a message ID, link, or format of `channelid-messageid`.
        """
        manager = self.contact_manager
        if manager.view is not MISSING:
            message = manager.view.message
            if message:
                trail = f" on this [message]({message.jump_url})"
            else:
                trail = ""
            raise commands.BadArgument(
                f"There is already active contact menu{trail}. Please disable it first before creating a new one."
            )
        if message.author != self.bot.user:
            raise commands.BadArgument("Cannot attach components to a message sent by others.")

        view = ContactView(self, message)
        await message.edit(view=view)
        self.config.contact["message"] = str(message.id)
        self.config.contact["channel"] = str(message.channel.id)
        await self.config.update()
        await ctx.message.add_reaction("\u2705")

    @contactmenu.command(name="refresh")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_refresh(self, ctx: commands.Context):
        """
        Refresh components on the contact menu message.
        This should be run to update the button and dropdown to apply new settings.
        """
        manager = self.contact_manager
        if manager.view is MISSING:
            raise commands.BadArgument("There is currently no active contact menu.")

        manager.view.stop()
        manager.view = MISSING
        message = manager.message
        view = ContactView(self, message)
        try:
            await message.edit(view=view)
        except discord.HTTPException as exc:
            logger.error(f"{type(exc).__name__}: {str(exc)}")
            raise commands.BadArgument("Unable to refresh contact menu message.")
        await ctx.message.add_reaction("\u2705")

    @contactmenu.command(name="disable", aliases=["clear"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_disable(self, ctx: commands.Context):
        """
        Clear contact components attached to the contact menu message.
        This will remove the button and dropdown, and stop listening to interactions made on the message.
        """
        manager = self.contact_manager
        if manager.view is MISSING:
            raise commands.BadArgument("There is currently no active contact menu.")

        await manager.view.force_stop()
        manager.clear()
        self.config.contact["message"] = None
        self.config.contact["channel"] = None
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Contact menu is now cleared.",
        )
        await ctx.send(embed=embed)

    @contactmenu.group(name="config", usage="<subcommand> [argument]", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config(self, ctx: commands.Context):
        """
        Contact menu configurations. Retrieve, set or update the values.

        __**Customisable options:**__
        - Button
        - Dropdown
        - Embed (title, description, footer)
        - Confirmation embed (title, description, footer)
        """
        await ctx.send_help(ctx.command)

    @cm_config.command(
        name="embed",
        help=(
            "Customise the embed title, description and footer text for contact menu message.\n"
            "Please note that this embed will only be posted if the contact menu is initiated from "
            "`{prefix}contactmenu create` command.\n\n"
            "Leave `argument` empty to set the values.\n"
            "Set `argument` to `clear` or `reset` to restore the default value."
        ),
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_embed(self, ctx: commands.Context, *, argument: Optional[str] = None):
        await self._set_embed_invoker(
            ctx,
            "contact menu",
            ["contact", "embed"],
            self.config.contact["embed"],
            self.config.deepcopy(self.config.defaults["contact"]["embed"]),
            argument,
        )

    @cm_config.command(name="button")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_button(self, ctx: commands.Context, *, argument: Optional[str] = None):
        """
        Customise the contact button using buttons and text input.

        Leave `argument` empty to set the values.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_button_invoker(
            ctx,
            "contact button",
            ["contact", "button"],
            self.config.contact["button"],
            self.config.deepcopy(self.config.defaults["contact"]["button"]),
            argument,
        )

    @cm_config.group(name="dropdown", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_dropdown(self, ctx: commands.Context):
        """
        Contact menu dropdown configurations.
        """
        await ctx.send_help(ctx.command)

    @cm_config_dropdown.command(
        name="placeholder",
        help=(
            "Placeholder text shown on the dropdown menu if nothing is selected.\n"
            f"Must not exceed {Limit.select_placeholder} characters."
        ),
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_dropdown_placeholder(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Contact dropdown placeholder",
            color=self.bot.main_color,
            description=ctx.command.help,
        )
        current = self.config.contact["select"]["placeholder"]
        embed.add_field(name="Current value", value=f"`{current}`")
        embed.set_footer(text="Press Set to set/edit the dropdown placeholder")
        view = self.get_config_view(
            ctx, title=embed.title, keys=["contact", "select", "placeholder"], current=current
        )
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if not view.value:
            return

        placeholder = view.outputs["placeholder"]
        self.config.contact["select"]["placeholder"] = placeholder
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"Placeholder is now set to:\n{placeholder}",
        )
        await view.interaction.followup.send(embed=embed)

    @cm_config_dropdown.command(
        name="add",
        help=(
            "Add and customise the dropdown for contact menu.\n\n"
            "A select option can be linked to a custom category where the thread will be created.\n\n"
            "__**Available fields:**__\n"
            "- **Emoji** : Emoji for select option. May be a unicode emoji, format of `:name:`, `<:name:id>` "
            "or `<a:name:id>` (animated emoji).\n"
            f"- **Label** : Label for select option. Must be {Limit.select_label} or fewer in length. "
            "This field is required.\n"
            f"- **Description** : Short description for the option. Must not exceed {Limit.select_description} characters.\n"
            "- **Category** : The discord category channel where the thread will be created if the user choose the option. "
            "This field is required.\n"
        ),
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_dropdown_add(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Contact dropdown",
            color=self.bot.main_color,
            description=ctx.command.help,
        )
        embed.set_footer(text="Press Add to add new dropdown option")
        view = self.get_config_view(ctx, title=embed.title, keys=["contact", "select", "options"])
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if not view.value:
            return

        payload = {}
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Successfully added a dropdown option.\n\n",
        )
        embed.description += "### New option"
        for key in list(view.outputs):
            embed.add_field(name=key.title(), value=f"`{view.outputs[key]}`")
            payload[key] = view.outputs.pop(key)
        self.config.contact["select"]["options"].append(payload)
        await self.config.update()
        await view.interaction.followup.send(embed=embed)

    @cm_config_dropdown.command(name="list")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_dropdown_list(self, ctx: commands.Context):
        """
        Show the list of currently set dropdown options for contact menu.
        """
        options = self.config.contact["select"]["options"]
        if not options:
            raise commands.BadArgument("There is no dropdown option set.")
        embeds = []
        for n, elem in enumerate(options, start=1):
            embed = discord.Embed(
                title=f"Option {n}",
                color=self.bot.main_color,
            )
            embed.add_field(name="Label", value=f"{elem.get('label')}")
            embed.add_field(name="Description", value=f"{elem.get('description')}")
            category_id = elem.get("category")
            if not category_id:
                category = self.bot.main_category
            else:
                category = self.bot.get_channel(int(category_id))
            embed.add_field(name="Category", value=category.mention if category else "Not found")
            embeds.append(embed)
        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @cm_config_dropdown.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_dropdown_clear(self, ctx: commands.Context):
        """
        Clear the dropdown configurations. Please note that this operation cannot be undone.
        """
        options = self.config.contact["select"]["options"]
        if not options:
            raise commands.BadArgument("There is no dropdown option set.")

        view = ConfirmView(self.bot, ctx.author)
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Are you sure you want to clear all dropdown configurations?",
        )
        view.message = await ctx.send(embed=embed, view=view)

        await view.wait()

        if not view.value:
            return
        del embed

        options.clear()
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color, description="All dropdown configurations are now cleared."
        )
        await ctx.send(embed=embed)

    @cm_config.command(name="confirmembed")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_confirmembed(self, ctx: commands.Context, *, argument: Optional[str] = None):
        """
        Customise the embed title, description and footer text for thread creation confirmation embed.
        This embed will be sent as ephemeral after a user presses the Contact button.

        Leave `argument` empty to set the values.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_embed_invoker(
            ctx,
            "thread creation confirmation",
            ["contact", "confirmation", "embed"],
            self.config.contact["confirmation"]["embed"],
            self.config.deepcopy(self.config.defaults["contact"]["confirmation"]["embed"]),
            argument,
        )

    @cm_config.command(name="override_dmdisabled", aliases=["ignore_dmdisabled"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_override_dmdisabled(self, ctx: commands.Context, *, mode: Optional[bool] = None):
        """
        Enable or disable the override of DM disabled.

        `mode` may be `True` or `False` (case insensitive).
        Leave `mode` empty to retrieve the current set value.

        __**Note:**__
        - This can only override the disable new thread setting.
        """
        config = self.config.contact
        enabled = config.get("override_dmdisabled", False)
        if mode is None:
            embed = discord.Embed(
                color=self.bot.main_color,
                description="DM disabled override is currently " + ("enabled." if enabled else "disabled."),
            )
            return await ctx.send(embed=embed)
        if mode == enabled:
            raise commands.BadArgument(
                "DM disabled override is already " + ("enabled." if enabled else "disabled.")
            )

        config["override_dmdisabled"] = mode
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="DM disabled override is now " + ("enabled." if mode else "disabled."),
        )
        await ctx.send(embed=embed)

    @cm_config.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_clear(self, ctx: commands.Context):
        """
        Clear the contact menu configurations.
        This will reset all the contact menu settings (e.g. button, dropdown, embed etc) to defaults.

        __**Note:**__
        - This operation cannot be undone.
        """
        view = ConfirmView(self.bot, ctx.author)
        embed = discord.Embed(
            color=self.bot.main_color, description="Are you sure you want to clear all contact menu settings?"
        )
        view.message = await ctx.send(embed=embed, view=view)

        await view.wait()

        if not view.value:
            return
        del embed

        self.config.remove("contact", restore_default=True)
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="All contact menu configurations have been reset to defaults.",
        )
        await ctx.send(embed=embed)

    @commands.group(aliases=["fback"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def feedback(self, ctx: commands.Context):
        """
        Feedback prompt after the thread is closed.

        This feature is disabled by default. To enable, use command:
        `{prefix}feedback config enable true`

        To see more customisable options, see:
        `{prefix}feedback config`

        __**Notes:**__
        - The button on the feedback prompt message will only available for 24 hours.
        - Each user can only have one active session at a time.
        """
        await ctx.send_help(ctx.command)

    @feedback.command(name="send")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def fb_send(self, ctx: commands.Context, *, user: Optional[discord.Member] = None):
        """
        Manually send the feedback prompt message to user.

        `user` if specified, may be a user ID, mention, or name.
        Leave `user` parameter empty if this command is run in thread channel to send to the current recipient.
        """
        embed = discord.Embed(color=self.bot.main_color)
        manager = self.feedback_manager
        if user:
            if manager.is_active(user):
                raise commands.BadArgument(f"There is already active feedback session for {user.mention}.")
            await manager.send(user)
            embed.description = f"Feedback prompt message has been sent to {user.mention}."
            await ctx.send(embed=embed)
            return

        thread = ctx.thread
        if not thread:
            raise commands.BadArgument(
                "This command can only be run in thread channel is `user` parameter is not specified."
            )

        for user in thread.recipients:
            if user is None:
                continue
            if not isinstance(user, discord.Member):
                entity = self.bot.guild.get_member(user.id)
                if not entity:
                    continue
                user = entity

            try:
                await manager.send(user, thread)
            except RuntimeError as exc:
                logger.error(f"{type(exc).__name__}: {str(exc)}")
                logger.error(f"Skipping sending feedback prompt message to {user}.")

        if len(thread.recipients) > 1:
            recip = "all recipients"
        else:
            recip = "recipient"
        embed.description = f"Successfully sent to {recip}."
        await ctx.reply(embed=embed)

    @feedback.command(name="cancel")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def fb_cancel(self, ctx: commands.Context, *, user: discord.Member):
        """
        Manually cancel the feedback session sent to user.

        `user` may be a user ID, mention, or name.
        """
        feedback = self.feedback_manager.find_session(user)
        if not feedback:
            raise commands.BadArgument(f"There is no active feedback session for {user.mention}.")

        feedback.stop()
        embed = discord.Embed(
            color=self.bot.main_color, description=f"Feedback session for {user.mention} is now stopped."
        )
        await ctx.send(embed=embed)

    @feedback.command(name="list")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def fb_list(self, ctx: commands.Context):
        """
        Show active feedback sessions sent to users.
        """
        manager = self.feedback_manager
        if not manager.active:
            raise commands.BadArgument("There is currently no active feedback session.")
        embeds = []
        for feedback in manager.active:
            user = feedback.user
            embed = discord.Embed(
                color=self.bot.main_color,
            )
            embed.set_author(name=str(user), icon_url=user.display_avatar)
            embed.set_footer(text=f"User ID: {user.id}")
            embed.add_field(
                name="Sent", value=discord.utils.format_dt(datetime.fromtimestamp(feedback.started), "F")
            )
            embed.add_field(
                name="Ends", value=discord.utils.format_dt(datetime.fromtimestamp(feedback.ends), "F")
            )
            embed.add_field(name="Message ID", value=f"`{feedback.message.id}`")
            embed.add_field(name="Channel ID", value=f"`{feedback.message.channel.id}`")
            embeds.append(embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @feedback.group(name="config", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config(self, ctx: commands.Context):
        """
        Feedback feature configurations.

        Use the subcommands respectively to change the values.
        """
        await ctx.send_help(ctx.command)

    @fb_config.command(name="channel")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_channel(
        self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None
    ):
        """
        Feedback log channel.
        If this config has never been set, fallbacks to bot's `log_channel`.

        `channel` if specified, may be a channel ID, mention, or name.
        Leave `channel` empty to get current set feedback log channel.
        """
        embed = discord.Embed(color=self.bot.main_color)
        if channel is None:
            embed.description = (
                f"Feedback log channel is currently set to: {self.feedback_manager.channel.mention}."
            )
            return await ctx.send(embed=embed)

        self.config.feedback["channel"] = str(channel.id)
        await self.config.update()
        embed.description = f"Feedback log channel is now set to {channel.mention}."
        await ctx.send(embed=embed)

    @fb_config.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_enable(self, ctx: commands.Context, *, mode: Optional[bool] = None):
        """
        Enable or disable sending the feedback prompt message.

        `mode` may be `True` or `False` (case insensitive).
        Leave `mode` empty to retrieve the current set value.
        """
        await self._set_enable_invoker(
            ctx,
            "feedback",
            self.config.feedback,
            mode,
        )

    @fb_config.command(name="embed")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_embed(self, ctx: commands.Context, *, argument: Optional[str] = None):
        """
        Customise the feedback embed.

        Leave `argument` empty to set the values.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_embed_invoker(
            ctx,
            "feedback prompt",
            ["feedback", "embed"],
            self.config.feedback["embed"],
            self.config.deepcopy(self.config.defaults["feedback"]["embed"]),
            argument,
        )

    @fb_config.command(name="button")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_button(self, ctx: commands.Context, *, argument: Optional[str] = None):
        """
        Customise the feedback button using buttons and text input.

        Leave `argument` empty to set the values.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_button_invoker(
            ctx,
            "feedback button",
            ["feedback", "button"],
            self.config.feedback["button"],
            self.config.deepcopy(self.config.defaults["feedback"]["button"]),
            argument,
        )

    @fb_config.command(name="response")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_response(self, ctx: commands.Context):
        """
        Response message that will be sent to the user after submitting the feedback.
        """
        embed = discord.Embed(
            title="Feedback response",
            color=self.bot.main_color,
            description=ctx.command.help,
        )
        current = self.config.feedback["response"]
        embed.add_field(name="Current value", value=f"`{current}`")
        embed.set_footer(text="Press Set to set/edit the feedback response")
        view = self.get_config_view(ctx, title=embed.title, keys=["feedback", "response"], current=current)
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if not view.value:
            return

        response = view.outputs["response"]
        self.config.feedback["response"] = response
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"Feedback response is now set to:\n{response}",
        )
        await view.interaction.followup.send(embed=embed)

    @fb_config.group(name="rating", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_rating(self, ctx: commands.Context):
        """
        Rating feature. Allow users to choose a rating before submitting feedback.

        This feature is disabled by default. To enable, use command:
        `{prefix}feedback config rating enable true`
        """
        await ctx.send_help(ctx.command)

    @fb_config_rating.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_rating_enable(self, ctx: commands.Context, *, mode: Optional[bool] = None):
        """
        Enable or disable the rating feature on feedback prompt message.

        `mode` may be `True` or `False` (case insensitive).
        Leave `mode` empty to retrieve the current set value.
        """
        await self._set_enable_invoker(
            ctx,
            "rating",
            self.config.feedback["rating"],
            mode,
        )

    @fb_config_rating.command(
        name="placeholder",
        help=(
            "Placeholder text shown on the dropdown menu if nothing is selected.\n"
            f"Must not exceed {Limit.select_placeholder} characters."
        ),
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_rating_placeholder(self, ctx: commands.Context):
        """
        Placeholder text shown on the dropdown menu if nothing is selected.
        """
        embed = discord.Embed(
            title="Feedback rating placeholder",
            color=self.bot.main_color,
            description=ctx.command.help,
        )
        current = self.config.feedback["rating"]["placeholder"]
        embed.add_field(name="Current value", value=f"`{current}`")
        embed.set_footer(text="Press Set to set/edit the dropdown placeholder")
        view = self.get_config_view(
            ctx, title=embed.title, keys=["feedback", "rating", "placeholder"], current=current
        )
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if not view.value:
            return

        placeholder = view.outputs["placeholder"]
        self.config.feedback["rating"]["placeholder"] = placeholder
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"Placeholder for rating dropdown is now set to:\n{placeholder}",
        )
        await view.interaction.followup.send(embed=embed)

    @fb_config.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_clear(self, ctx: commands.Context):
        """
        Clear the feedback feature configurations.
        This will reset all the settings (e.g. button, channel, embed, rating etc) to defaults.

        __**Note:**__
        - This operation cannot be undone.
        """
        view = ConfirmView(self.bot, ctx.author)
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Are you sure you want to clear all feedback feature settings?",
        )
        view.message = await ctx.send(embed=embed, view=view)

        await view.wait()

        if not view.value:
            return
        del embed

        self.config.remove("feedback", restore_default=True)
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="All feedback feature configurations have been reset to defaults.",
        )
        await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def threadmove(self, ctx: commands.Context):
        """
        Thread move automation manager.

        This feature supports moving responded or inactive threads to designated category.
        To enable this feature, just simply enable with command and set a category for the ones you want to enable.

        See `{prefix}threadmove config <subcommand>`
        """
        await ctx.send_help(ctx.command)

    @threadmove.group(name="config", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config(self, ctx: commands.Context):
        """
        Thread move automation configurations.
        """
        await ctx.send_help(ctx.command)

    @tm_config.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_enable(self, ctx: commands.Context, *, mode: Optional[bool] = None):
        """
        Enable or disable the move feature for responded and inactive threads.

        `mode` may be `True` or `False` (case insensitive).
        Leave `mode` empty to retrieve the current set value.
        """
        await self._set_enable_invoker(
            ctx,
            "thread move",
            self.config.thread_move,
            mode,
        )

    @tm_config.group(name="responded", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_responded(self, ctx: commands.Context):
        """
        Responded thread move configurations.
        """
        await ctx.send_help(ctx.command)

    @tm_config_responded.command(name="category")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_responded_category(
        self,
        ctx: commands.Context,
        *,
        argument: Optional[Union[discord.CategoryChannel, str]] = None,
    ):
        """
        Category where the thread will be moved to if a respond has been made.

        `argument` may be a category ID, mention or name.
        Leave `argument` empty to see the current set category.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_category_invoker(ctx, "responded", argument)

    @tm_config_responded.command(name="embed")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_responded_embed(self, ctx: commands.Context, *, argument: Optional[str] = None):
        """
        Customise the embed title, description and footer text for responded thread move message.

        Leave `argument` empty to set the values.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_embed_invoker(
            ctx,
            "responded thread move",
            ["thread_move", "responded", "embed"],
            self.config.thread_move["responded"]["embed"],
            self.config.deepcopy(self.config.defaults["thread_move"]["responded"]["embed"]),
            argument,
        )

    @tm_config.group(name="inactive", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_inactive(self, ctx: commands.Context):
        """
        Inactive thread move configurations.
        """
        await ctx.send_help(ctx.command)

    @tm_config_inactive.command(name="timeout")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_inactive_timeout(
        self, ctx: commands.Context, *, argument: Optional[UserFriendlyTime] = None
    ):
        """
        Timeout before the thread channel will be moved to inactive category.

        `argument` for timeout must be in one of the formats shown below:
        - `30m` or `30 minutes` = 30 minutes
        - `2d` or `2days` or `2day` = 2 days
        - `1mo` or `1 month` = 1 month
        - `7 days 12 hours` or `7days12hours` (with/without spaces)
        - `6d12h` (this syntax must be without spaces)

        Leave `argument` empty to see the current set value.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        embed = discord.Embed(color=self.bot.main_color)
        if argument is None:
            timeout = self.config.thread_move["inactive"]["timeout"]
            suffix = " seconds" if timeout else ""
            embed.description = f"Inactive timeout is currently set to {timeout}{suffix}."
            await ctx.send(embed=embed)
        elif argument.arg in ("reset", "clear"):
            default = self.config.copy(self.config.defaults["thread_move"]["inactive"]["timeout"])
            self.config.thread_move["inactive"]["timeout"] = default
            await self.config.update()
            embed.description = "Inactive timeout is now reset to default."
            await ctx.send(embed=embed)
        else:
            if argument.dt == argument.now:
                raise commands.BadArgument(f"{argument.arg} is unrecognized time syntax.")
            timeout = (argument.dt - argument.now).total_seconds()
            if timeout < 600:
                raise commands.BadArgument("Timeout cannot be lower than 10 minutes.")
            self.config.thread_move["inactive"]["timeout"] = timeout
            await self.config.update()
            embed.description = f"Inactive timeout is now set to {timeout} seconds."
            await ctx.send(embed=embed)

    @tm_config_inactive.command(name="category")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_inactive_category(
        self,
        ctx: commands.Context,
        *,
        argument: Optional[Union[discord.CategoryChannel, str]] = None,
    ):
        """
        Category where the thread will be moved to if inactive timeout has passed.

        `argument` may be a category ID, mention or name.
        Leave `argument` empty to see the current set category.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_category_invoker(ctx, "inactive", argument)

    @tm_config_inactive.command(name="embed")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_inactive_embed(self, ctx: commands.Context, *, argument: Optional[str] = None):
        """
        Customise the embed title, description and footer text for inactive thread move message.

        Leave `argument` empty to set the values.
        Set `argument` to `clear` or `reset` to restore the default value.
        """
        await self._set_embed_invoker(
            ctx,
            "inactive thread move",
            ["thread_move", "inactive", "embed"],
            self.config.thread_move["inactive"]["embed"],
            self.config.deepcopy(self.config.defaults["thread_move"]["inactive"]["embed"]),
            argument,
        )

    @tm_config.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def tm_config_clear(self, ctx: commands.Context):
        """
        Clear all the thread move feature configurations.
        This will reset all the settings to defaults.

        __**Note:**__
        - This operation cannot be undone.
        """
        view = ConfirmView(self.bot, ctx.author)
        embed = discord.Embed(
            color=self.bot.main_color, description="Are you sure you want to clear all thread move settings?"
        )
        view.message = await ctx.send(embed=embed, view=view)

        await view.wait()

        if not view.value:
            return
        del embed

        self.config.remove("thread_move", restore_default=True)
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="All thread move configurations have been reset to defaults.",
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_thread_ready(self, thread: Thread, *args: Any) -> None:
        """
        Dispatched when the thread is ready.
        """
        self.feedback_manager.clear_for(thread)
        await self.move_manager.schedule_inactive_timer(thread, thread.channel.created_at)

    @commands.Cog.listener()
    async def on_thread_close(self, thread: Thread, *args: Any) -> None:
        """
        Dispatched when the thread is closed.
        """
        tasks = [
            self.bot.loop.create_task(self.move_manager.cancel_inactivity_task(thread.channel.id, True)),
            self.bot.loop.create_task(self.feedback_manager.handle_prompt(thread, *args)),
        ]
        await asyncio.gather(*tasks)

    @commands.Cog.listener()
    async def on_thread_reply(self, thread: Thread, *args: Any) -> None:
        manager = self.move_manager
        _, message, *_ = args
        tasks = [
            self.bot.loop.create_task(manager.handle_responded(thread)),
            self.bot.loop.create_task(manager.schedule_inactive_timer(thread, message.created_at)),
        ]
        await asyncio.gather(*tasks)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(SupportUtility(bot))
