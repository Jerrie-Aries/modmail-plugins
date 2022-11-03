from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import MISSING

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession
from core.utils import human_join, truncate


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
from .core.models import ContactManager, FeedbackManager
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

    async def cog_load(self) -> None:
        await self.config.fetch()
        self.bot.loop.create_task(self.initialize())

    async def cog_unload(self) -> None:
        view = self.contact_manager.view
        if view is not MISSING:
            view.stop()
        for feedback in self.feedback_manager.active:
            feedback.task.cancel()

    async def initialize(self) -> None:
        await self.bot.wait_for_connected()
        await self.contact_manager.initialize()
        await self.feedback_manager.populate()

    def _resolve_modal_payload(self, item: Button) -> Dict[str, Any]:
        """
        Internal method to respectively resolve the required payload to initiate
        the `Modal` view.
        """
        view = item.view
        args = view.input_session.split(" ")
        if len(args) == 1:
            prefix = None
            session = args[0]
        else:
            prefix, session = args

        valid_sessions = ("button", "dropdown", "embed")
        if session not in valid_sessions:
            raise ValueError(
                f"Invalid view input session. Expected {human_join(valid_sessions)}, "
                f"got `{session}` instead."
            )

        options = {}
        if session == "button":
            elements = [("emoji", 256), ("label", Limit.button_label), ("style", 32)]
            button_config = getattr(self.config, prefix, {}).get("button")
            for elem in elements:
                options[elem[0]] = {
                    "label": elem[0].title(),
                    "max_length": elem[1],
                    "required": False,
                    "default": view.inputs.get(elem[0]) or button_config.get(elem[0]),
                }
        elif session == "embed":
            elements = [
                ("title", Limit.embed_title),
                ("description", Limit.text_input_max),
                ("footer", Limit.embed_footer),
            ]
            embed_config = getattr(self.config, prefix, {}).get("embed")
            for elem in elements:
                options[elem[0]] = {
                    "label": elem[0].title(),
                    "max_length": elem[1],
                    "style": discord.TextStyle.long if elem[0] == "description" else discord.TextStyle.short,
                    "required": elem[0] == "description",
                    "default": view.inputs.get(elem[0]) or embed_config.get(elem[0]),
                }
        else:
            elements = [
                ("emoji", 256),
                ("label", Limit.button_label),
                ("description", Limit.select_description),
                ("category", 256),
            ]
            for elem in elements:
                options[elem[0]] = {
                    "label": elem[0].title(),
                    "max_length": elem[1],
                    "required": elem[0] in ("label", "category"),
                    "default": view.inputs.get(elem[0]),
                }
        return options

    async def _button_callback(self, interaction: discord.Interaction, item: Button) -> None:
        if not isinstance(item, Button):
            raise TypeError(
                f"Invalid type of item received. Expected Button, got {type(item).__name__} instead."
            )

        view = item.view
        options = self._resolve_modal_payload(item)
        title = view.input_session.title() + " config"
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
        if view.input_session in ("button", "dropdown") and all(
            (view.inputs.get(elem) is None for elem in ("emoji", "label"))
        ):
            errors.append("ValueError: Emoji and Label cannot both be None.")

        for key, value in view.inputs.items():
            if value is None:
                view.extras[key] = value
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
                view.extras[key] = value
                continue

            conv = converters.get(key)
            if conv is None:
                # mostly plain string
                view.extras[key] = value
                continue
            try:
                entity = await conv().convert(view.ctx, value)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)}")
                continue
            if isinstance(entity, discord.CategoryChannel):
                if entity == self.bot.main_category:
                    errors.append("ValueError: Category must be different than the main category.")
                    continue
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
            view.extras[key] = value

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

    @commands.group(aliases=["conmenu"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def contactmenu(self, ctx: commands.Context):
        """
        Base command for contact menu.

        Create and customize button, dropdown, and embed content for contact menu.
        """
        await ctx.send_help(ctx.command)

    @contactmenu.command(name="create")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_create(self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None):
        """
        Create a contact message and add contact components to it.
        Button and dropdown settings will be retrieved from config.

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

        message = await channel.send(embed=embed)
        view = ContactView(self, message)
        await message.edit(view=view)
        self.config.contact["message"] = str(message.id)
        self.config.contact["channel"] = str(message.channel.id)
        await self.config.update()

        if channel != ctx.channel:
            await ctx.message.add_reaction("\u2705")

    @contactmenu.command(name="attach")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_attach(self, ctx: commands.Context, *, message: discord.Message):
        """
        Attach the contact components to the message specified.
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
        Clear the contact components attached to the contact menu message.
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

        __**Customizable options:**__
        - Button
        - Dropdown
        - Embed (title, description, footer)
        """
        await ctx.send_help(ctx.command)

    @cm_config.group(
        name="embed",
        help=(
            "Customize the embed title, description and footer text for contact menu message.\n"
            "Please note that this embed will only be posted if the contact menu is initiated from "
            "`{prefix}contactmenu create` command.\n\n"
            "__**Available fields:**__\n"
            f"- **Title** : Embed title. Max {Limit.embed_title} characters.\n"
            f"- **Description** : Embed description. Max {Limit.text_input_max} characters.\n"
            f"- **Footer** : Embed footer text. Max {Limit.embed_footer} characters.\n"
        ),
        invoke_without_command=True,
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_embed(self, ctx: commands.Context):
        """
        Customize the embed title, description and footer text for contact menu message.
        """
        embed = discord.Embed(
            title="Contact embed",
            color=self.bot.main_color,
            description=ctx.command.help,
        )
        embed.set_footer(text="Press Set to set/edit the values")
        embed_config = self.config.contact.get("embed")
        embed.add_field(
            name="Current values",
            value="\n".join(
                f"- **{key.title()}** : `{truncate(str(embed_config.get(key)), max=256)}`"
                for key in ("title", "description", "footer")
            ),
        )
        view = SupportUtilityView(ctx, input_session="contact embed")
        buttons = [
            ("set", discord.ButtonStyle.grey, self._button_callback),
            ("cancel", discord.ButtonStyle.red, view._action_cancel),
        ]
        for elem in buttons:
            key = elem[0]
            button = Button(
                label=key.title(),
                style=elem[1],
                callback=elem[2],
            )
            view.add_item(button)
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if view.value:
            payload = view.extras
            updated = []
            for key in list(payload):
                updated.append(f"- **{key.title()}** : `{truncate(str(payload[key]), max=1024)}`")
                self.config.contact["embed"][key] = payload.pop(key)
            await self.config.update()
            embed = discord.Embed(
                description="Successfully set the new configurations for contact menu embed.\n\n"
                + "\n".join(updated),
                color=self.bot.main_color,
            )
            await view.interaction.followup.send(embed=embed)

    @cm_config_embed.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_embed_clear(self, ctx: commands.Context):
        """
        Clear the contact menu embed configurations and reset to default values.
        """
        default = self.config.defaults["contact"].get("embed", {})

        self.config.contact["embed"] = self.config.deepcopy(default)
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Contact menu embed configurations are now reset to defaults.",
        )
        await ctx.send(embed=embed)

    @cm_config.group(
        name="button",
        help=(
            "Customize the contact button using buttons and text input.\n\n"
            "__**Available fields:**__\n"
            "- **Emoji** : Emoji shown on the button. May be a unicode emoji, "
            "format of `:name:`, `<:name:id>` or `<a:name:id>` (animated emoji).\n"
            f"- **Label** : Button label. Must not exceed {Limit.button_label} characters.\n"
            "- **Style** : The color style for the button. Must be one of these (case insensitive):\n"
            "    - `Blurple`\n"
            "    - `Green`\n"
            "    - `Red`\n"
            "    - `Grey`\n"
        ),
        invoke_without_command=True,
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_button(self, ctx: commands.Context):
        """
        Customize the contact button using buttons and text input.
        """
        description = ctx.command.help
        embed = discord.Embed(
            title="Contact button",
            color=self.bot.main_color,
            description=description,
        )
        embed.set_footer(text="Press Set to set/edit the values")
        button_config = self.config.contact.get("button")
        embed.add_field(
            name="Current values",
            value="\n".join(
                f"- **{key.title()}** : `{button_config.get(key)}`" for key in ("emoji", "label", "style")
            ),
        )
        view = SupportUtilityView(ctx, input_session="contact button")
        buttons = [
            ("set", discord.ButtonStyle.grey, self._button_callback),
            ("cancel", discord.ButtonStyle.red, view._action_cancel),
        ]
        for elem in buttons:
            key = elem[0]
            button = Button(
                label=key.title(),
                style=elem[1],
                callback=elem[2],
            )
            view.add_item(button)
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if view.value:
            payload = view.extras
            updated = []
            for key in list(payload):
                updated.append(f"- **{key.title()}** : `{payload[key]}`")
                self.config.contact["button"][key] = payload.pop(key)
            await self.config.update()
            embed = discord.Embed(
                description="Successfully set the new configurations for contact button.\n\n"
                + "\n".join(updated),
                color=self.bot.main_color,
            )
            await view.interaction.followup.send(embed=embed)

    @cm_config_button.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_button_clear(self, ctx: commands.Context):
        """
        Clear the contact button configurations and reset to default values.
        """
        self.config.contact["button"].clear()
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color, description="Contact button configurations are now reset to defaults."
        )
        await ctx.send(embed=embed)

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
    async def cm_config_dropdown_placeholder(
        self, ctx: commands.Context, *, placeholder: Optional[str] = None
    ):
        """
        Placeholder text shown on the dropdown menu if nothing is selected.
        """
        if placeholder is None:
            current = self.config.contact["select"]["placeholder"]
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"Placeholder text for dropdown menu is currently set to:\n`{current}`",
            )
            await ctx.send(embed=embed)
            return
        if len(placeholder) >= Limit.select_placeholder:
            raise commands.BadArgument(
                f"Placeholder text must be {Limit.select_placeholder} or fewer in length."
            )

        self.config.contact["select"]["placeholder"] = placeholder
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color, description=f"Placeholder is now set to:\n{placeholder}"
        )
        await ctx.send(embed=embed)

    @cm_config_dropdown.command(
        name="add",
        help=(
            "Add and customize the dropdown for contact menu.\n\n"
            "A select option can be linked to a custom category where the thread will be created.\n\n"
            "__**Available options:**__\n"
            "- **Emoji** : Emoji for select option. May be a unicode emoji, format of `:name:`, `<:name:id>` "
            "or `<a:name:id>` (animated emoji).\n"
            f"- **Label** : Label for select option. Must be {Limit.select_label} or fewer in length.\n"
            f"- **Description** : Short description for the option. Must not exceed {Limit.select_description} characters.\n"
            "- **Category** : The discord category channel where the thread will be created if the user choose the option. "
            "This field is required and the value must be different than the `main category`.\n"
        ),
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_dropdown_add(self, ctx: commands.Context):
        """
        Add and customize the dropdown for contact menu.
        """
        embed = discord.Embed(
            title="Contact menu option",
            color=self.bot.main_color,
            description=ctx.command.help,
        )
        embed.set_footer(text="Press Add to add a dropdown option")
        view = SupportUtilityView(ctx, input_session="contact dropdown")
        buttons = [
            ("add", discord.ButtonStyle.grey, self._button_callback),
            ("cancel", discord.ButtonStyle.red, view._action_cancel),
        ]
        for elem in buttons:
            key = elem[0]
            button = Button(
                label=key.title(),
                style=elem[1],
                callback=elem[2],
            )
            view.add_item(button)
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if not view.value:
            return

        # retrieve inputs and parse
        payload = {}
        updated = []
        for key in list(view.extras):
            updated.append(f"- **{key.title()}** : `{view.extras[key]}`")
            payload[key] = view.extras.pop(key)
        self.config.contact["select"]["options"].append(payload)
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Successfully added a dropdown option:\n\n" + "\n".join(updated),
        )
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

        To see more customizable options, see:
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
        embed = discord.Embed(
            color=self.bot.main_color,
        )
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
        feedback_config = self.config.feedback
        enabled = feedback_config.get("enable", False)
        if mode is None:
            embed = discord.Embed(
                color=self.bot.main_color,
                description="Feedback feature is currently " + ("enabled." if enabled else "disabled."),
            )
            return await ctx.send(embed=embed)
        if mode == enabled:
            raise commands.BadArgument(
                "Feedback feature is already " + ("enabled." if enabled else "disabled.")
            )

        feedback_config["enable"] = mode
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Feedback feature is now " + ("enabled." if mode else "disabled."),
        )
        await ctx.send(embed=embed)

    @fb_config.group(
        name="embed",
        help=(
            "Customize the feedback embed.\n\n"
            "__**Available fields:**__\n"
            f"- **Title** : Embed title. Max {Limit.embed_title} characters.\n"
            f"- **Description** : Embed description. Max {Limit.text_input_max} characters.\n"
            f"- **Footer** : Embed footer text. Max {Limit.embed_footer} characters.\n"
        ),
        invoke_without_command=True,
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_embed(self, ctx: commands.Context):
        """
        Customize the feedback embed.
        """
        embed = discord.Embed(
            title="Feedback embed",
            color=self.bot.main_color,
            description=ctx.command.help,
        )
        embed.set_footer(text="Press Set to set/edit the values")
        embed_config = self.config.feedback.get("embed")
        embed.add_field(
            name="Current values",
            value="\n".join(
                f"- **{key.title()}** : `{truncate(str(embed_config.get(key)), max=256)}`"
                for key in ("title", "description", "footer")
            ),
        )
        view = SupportUtilityView(ctx, input_session="feedback embed")
        buttons = [
            ("set", discord.ButtonStyle.grey, self._button_callback),
            ("cancel", discord.ButtonStyle.red, view._action_cancel),
        ]
        for elem in buttons:
            key = elem[0]
            button = Button(
                label=key.title(),
                style=elem[1],
                callback=elem[2],
            )
            view.add_item(button)
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if view.value:
            payload = view.extras
            updated = []
            for key in list(payload):
                updated.append(f"- **{key.title()}** : `{truncate(str(payload[key]), max=1024)}`")
                self.config.feedback["embed"][key] = payload.pop(key)
            await self.config.update()
            embed = discord.Embed(
                description="Successfully set the new configurations for feedback embed.\n\n"
                + "\n".join(updated),
                color=self.bot.main_color,
            )
            await view.interaction.followup.send(embed=embed)

    @fb_config_embed.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_embed_clear(self, ctx: commands.Context):
        """
        Clear the feedback embed configurations and reset to default values.
        """
        default = self.config.defaults["feedback"].get("embed", {})

        self.config.feedback["embed"] = self.config.deepcopy(default)
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Feedback embed configurations are now reset to defaults.",
        )
        await ctx.send(embed=embed)

    @fb_config.group(
        name="button",
        help=(
            "Customize the feedback button using buttons and text input.\n\n"
            "__**Available fields:**__\n"
            "- **Emoji** : Emoji shown on the button. May be a unicode emoji, "
            "format of `:name:`, `<:name:id>` or `<a:name:id>` (animated emoji).\n"
            f"- **Label** : Button label. Must not exceed {Limit.button_label} characters.\n"
            "- **Style** : The color style for the button. Must be one of these (case insensitive):\n"
            "    - `Blurple`\n"
            "    - `Green`\n"
            "    - `Red`\n"
            "    - `Grey`\n"
        ),
        invoke_without_command=True,
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_button(self, ctx: commands.Context):
        """
        Customize the feedback button using buttons and text input.
        """
        description = ctx.command.help
        embed = discord.Embed(
            title="Feedback button",
            color=self.bot.main_color,
            description=description,
        )
        embed.set_footer(text="Press Set to set/edit the values")
        feedback_config = self.config.feedback.get("button")
        embed.add_field(
            name="Current values",
            value="\n".join(
                f"- **{key.title()}** : `{feedback_config.get(key)}`" for key in ("emoji", "label", "style")
            ),
        )
        view = SupportUtilityView(ctx, input_session="feedback button")
        buttons = [
            ("set", discord.ButtonStyle.grey, self._button_callback),
            ("cancel", discord.ButtonStyle.red, view._action_cancel),
        ]
        for elem in buttons:
            key = elem[0]
            button = Button(
                label=key.title(),
                style=elem[1],
                callback=elem[2],
            )
            view.add_item(button)
        view.message = message = await ctx.send(embed=embed, view=view)

        await view.wait()
        await message.edit(view=view)

        if view.value:
            payload = view.extras
            updated = []
            for key in list(payload):
                updated.append(f"- **{key.title()}** : `{payload[key]}`")
                self.config.feedback["button"][key] = payload.pop(key)
            await self.config.update()
            embed = discord.Embed(
                description="Successfully set the new configurations for feedback button.\n\n"
                + "\n".join(updated),
                color=self.bot.main_color,
            )
            await view.interaction.followup.send(embed=embed)

    @fb_config_button.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_button_clear(self, ctx: commands.Context):
        """
        Clear the feedback button configurations and reset to default values.
        """
        self.config.feedback["button"].clear()
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color, description="Feedback button configurations are now reset to defaults."
        )
        await ctx.send(embed=embed)

    @fb_config.command(name="response")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fb_config_response(self, ctx: commands.Context, *, response: Optional[str] = None):
        """
        Response message that will be sent to the user after submitting the feedback.

        Leave `response` parameter empty to see the current value.
        """
        embed = discord.Embed(color=self.bot.main_color)
        feedback_config = self.config.feedback
        if response is None:
            embed.description = f"Feedback response is currently set to:\n\n{feedback_config['response']}"
            return await ctx.send(embed=embed)

        feedback_config["response"] = response
        await self.config.update()
        embed.description = f"Feedback response is now set to:\n\n{response}"
        await ctx.send(embed=embed)

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
        rating_config = self.config.feedback.get("rating", {})
        # TODO: remove as this is just temporary for migration
        if not rating_config:
            rating_config = self.config.deepcopy(self.config.defaults["feedback"]["rating"])
            self.config.feedback["rating"] = rating_config
            await self.config.update()

        enabled = rating_config.get("enable", False)
        if mode is None:
            embed = discord.Embed(
                color=self.bot.main_color,
                description="Rating feature is currently " + ("enabled." if enabled else "disabled."),
            )
            return await ctx.send(embed=embed)
        if mode == enabled:
            raise commands.BadArgument(
                "Rating feature is already " + ("enabled." if enabled else "disabled.")
            )

        rating_config["enable"] = mode
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description="Rating feature is now " + ("enabled." if mode else "disabled."),
        )
        await ctx.send(embed=embed)

    @fb_config_rating.command(
        name="placeholder",
        help=(
            "Placeholder text shown on the dropdown menu if nothing is selected.\n"
            f"Must not exceed {Limit.select_placeholder} characters."
        ),
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cm_config_rating_placeholder(self, ctx: commands.Context, *, placeholder: Optional[str] = None):
        """
        Placeholder text shown on the dropdown menu if nothing is selected.
        """
        if placeholder is None:
            current = self.config.feedback["rating"]["placeholder"]
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"Placeholder text for rating dropdown menu is currently set to:\n`{current}`",
            )
            await ctx.send(embed=embed)
            return
        if len(placeholder) >= Limit.select_placeholder:
            raise commands.BadArgument(
                f"Placeholder text must be {Limit.select_placeholder} or fewer in length."
            )

        self.config.feedback["rating"]["placeholder"] = placeholder
        await self.config.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"Placeholder for rating dropdown is now set to:\n{placeholder}",
        )
        await ctx.send(embed=embed)

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

    @commands.Cog.listener()
    async def on_thread_ready(self, thread: Thread, *args) -> None:
        """
        Dispatched when the thread is ready.

        Here we're going to close active feedback session for the recipients if any and
        if the auto send feedback on thread close feature is enabled.
        """
        if not self.config.feedback.get("enable", False):
            return

        for user in thread.recipients:
            if user is None:
                continue
            feedback = self.feedback_manager.find_session(user)
            if feedback:
                logger.debug(f"Stopping active feedback session for {user}.")
                feedback.stop()

    @commands.Cog.listener()
    async def on_thread_close(self, thread: Thread, *args) -> None:
        """
        Dispatched when the thread is closed.
        """
        _, silent, *_ = args
        if silent:
            return

        if not self.config.feedback.get("enable", False):
            return

        for user in thread.recipients:
            if user is None:
                continue
            if not isinstance(user, discord.Member):
                entity = self.bot.guild.get_member(user.id)
                if not entity:
                    continue
                user = entity
            try:
                await self.feedback_manager.send(user, thread)
            except RuntimeError:
                pass


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(SupportUtility(bot))
