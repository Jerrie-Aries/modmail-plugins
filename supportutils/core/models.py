from __future__ import annotations

import asyncio
from typing import Optional, Tuple, Union, TYPE_CHECKING

import discord
from discord.ext.modmail_utils import ConfirmView
from discord.utils import MISSING

from core.models import getLogger
from core.thread import Thread

from .views import ContactView


logger = getLogger(__name__)


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..supportutils import SupportUtility


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
        channel_id = self.cog.config.contact.get("channel")
        if channel_id:
            channel_id = int(channel_id)
        message_id = self.cog.config.contact.get("message")
        if message_id:
            message_id = int(message_id)
        return channel_id, message_id

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
