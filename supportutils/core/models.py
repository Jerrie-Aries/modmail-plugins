from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple, Union, TYPE_CHECKING

import discord
from discord.ext.modmail_utils import ConfirmView
from discord.utils import MISSING

from core.models import getLogger
from core.thread import Thread

from .views import ContactView, FeedbackView


logger = getLogger(__name__)


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..supportutils import SupportUtility
    from .views import Modal


class ContactManager:
    """
    Represents a class to handle and store all stuff related to contact menu events.
    """

    def __init__(self, cog: SupportUtility):
        self.cog: SupportUtility = cog
        self.bot: ModmailBot = cog.bot
        self.channel: discord.TextChannel = MISSING
        self.message: Union[discord.PartialMessage, discord.Message] = MISSING

        # automatically assigned from ContactView class
        self.view: ContactView = MISSING

    async def initialize(self) -> None:
        channel_id, message_id = self._resolve_ids()
        if not all((channel_id, message_id)):
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return
        self.channel = channel
        self.message = discord.PartialMessage(channel=self.channel, id=int(message_id))
        view = ContactView(self.cog, self.message)
        try:
            await self.message.edit(view=view)
        except Exception as exc:
            logger.error("Unexpected exception occured while trying to edit contact menu message.")
            logger.error(f"{type(exc).__name__}: {str(exc)}")
        else:
            self.bot.add_view(view, message_id=self.message.id)

    def _resolve_ids(self) -> Tuple[Optional[int]]:
        channel_id = self.config.get("channel")
        if channel_id:
            channel_id = int(channel_id)
        message_id = self.config.get("message")
        if message_id:
            message_id = int(message_id)
        return channel_id, message_id

    @property
    def config(self) -> Dict[str, Any]:
        """
        Contact configurations.
        """
        return self.cog.config.contact

    def clear(self) -> None:
        """
        Reset the attributes to MISSING.
        """
        self.channel = MISSING
        self.message = MISSING
        self.view = MISSING

    async def create(
        self,
        recipient: Union[discord.Member, discord.User],
        *,
        category: discord.CategoryChannel = None,
        interaction: Optional[discord.Interaction] = None,
    ) -> Thread:
        """
        Handles thread creation. Adapted from core/thread.py.
        """

        # checks for existing thread in cache
        thread = self.bot.threads.cache.get(recipient.id)
        if thread:
            try:
                await thread.wait_until_ready()
            except asyncio.CancelledError:
                logger.warning("Thread for %s cancelled, abort creating.", recipient)
                return thread
            else:
                if thread.channel and self.bot.get_channel(thread.channel.id):
                    logger.warning("Found an existing thread for %s, abort creating.", recipient)
                    return thread
                logger.warning("Found an existing thread for %s, closing previous thread.", recipient)
                self.bot.loop.create_task(
                    thread.close(closer=self.bot.user, silent=True, delete_channel=False)
                )

        thread = Thread(self.bot.threads, recipient)
        self.bot.threads.cache[recipient.id] = thread

        view = ConfirmView(bot=self.bot, user=recipient, timeout=20.0)
        view.message = await interaction.followup.send(
            embed=discord.Embed(
                title=self.bot.config["confirm_thread_creation_title"],
                description=self.bot.config["confirm_thread_response"],
                color=self.bot.main_color,
            ),
            view=view,
            ephemeral=True,
        )

        await view.wait()

        if not view.value:
            thread.cancelled = True

        if thread.cancelled:
            del self.bot.threads.cache[recipient.id]
            return thread

        self.bot.loop.create_task(thread.setup(creator=recipient, category=category, initial_message=None))
        return thread


class FeedbackManager:
    """
    Handles feedback or review on `thread_close` event.
    """

    def __init__(self, cog: SupportUtility):
        self.cog: SupportUtility = cog
        self.bot: ModmailBot = cog.bot

    @property
    def config(self) -> Dict[str, Any]:
        """
        Feedback configurations.
        """
        return self.cog.config.feedback

    @property
    def channel(self) -> discord.TextChannel:
        channel_id = self.config.get("channel")
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                return channel
        return self.bot.log_channel

    async def send(
        self,
        thread: Thread,
        closer: discord.Member,
        delete_channel: bool,
        close_message: Optional[str],
        scheduled: bool,
    ) -> None:
        if not isinstance(thread.recipient, discord.Member):
            member = self.bot.guild.get_member(thread.recipient.id)
            if not member:
                return
        else:
            member = thread.recipient

        embed = discord.Embed(
            title=self.config["embed"].get("title"),
            color=self.bot.main_color,
            description=self.config["embed"]["description"],
        )
        embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.display_avatar)
        footer_text = self.config["embed"].get("footer")
        if not footer_text:
            footer_text = "Your feedback will be submitted to our staff"
        embed.set_footer(text=footer_text, icon_url=self.bot.guild.icon)

        view = FeedbackView(member, self.cog, thread)
        view.message = message = await member.send(embed=embed, view=view)
        await view.wait()
        await message.edit(view=view)

    async def feedback_submit(self, interaction: discord.Interaction, modal: Modal) -> None:
        view = modal.view
        view.value = True
        modal.stop()

        feedback = view.input_map.get("feedback")
        embed = discord.Embed(
            title="Review submitted",
            color=discord.Color.green(),
            description=feedback or "No content.",
            timestamp=discord.utils.utcnow(),
        )
        user = view.user
        embed.set_author(name=str(user), icon_url=user.display_avatar)
        if view.thread.channel:
            channel_id = view.thread.channel.id
        else:
            channel_id = None
        embed.set_footer(text=f"User ID: {user.id}\nChannel ID: {channel_id}", icon_url=self.bot.guild.icon)
        await self.channel.send(embed=embed)

        embed = discord.Embed(
            # TODO: config option
            description="Your feedback has been submitted to our staff.",
            color=self.bot.main_color,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
