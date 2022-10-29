from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set, Tuple, Union, TYPE_CHECKING

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


ends_senconds: int = 60 * 60 * 24


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
        """
        Called on startup.
        """
        channel_id, message_id = self._resolve_ids()
        if not all((channel_id, message_id)):
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return
        self.channel = channel
        self.message = discord.PartialMessage(channel=self.channel, id=int(message_id))
        view = ContactView(self.cog, self.message)
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


class Feedback:
    """
    Represents Feedback instance.
    """

    def __init__(
        self,
        manager: FeedbackManager,
        user: discord.Member,
        *,
        message: Union[dicord.Message, discord.PartialMessage],
        view: FeedbackView,
        started: float,
        ends: float,
    ):
        self.bot: ModmailBot = manager.bot
        self.cog: SupportUtility = manager.cog
        self.manager: FeedbackManager = manager
        self.user: discord.Member = user
        self.message: Union[dicord.Message, discord.PartialMessage] = message
        view.feedback = self
        self.view: FeedbackView = view
        self.started: float = started
        self.ends: float = ends
        self._submitted: bool = False
        self.event: asyncio.Event = asyncio.Event()
        self.task: asyncio.Task = MISSING

    def __hash__(self):
        return hash((self.message.id, self.message.channel.id))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} message_id={self.message.id}>"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Feedback):
            return False
        return self.user.id == other.user.id

    @classmethod
    async def from_data(cls, manager: FeedbackManager, *, data: Dict[str, Any]) -> Feedback:
        """
        Initiate the feedback session from data.
        """
        bot = manager.bot
        ends = data["ends"]
        now = discord.utils.utcnow().timestamp()
        timeout = ends - now
        if timeout < 0:
            raise ValueError("Feedback session has ended.")

        user_id = int(data["user"])
        user = bot.guild.get_member(user_id)
        if user is None:
            raise ValueError(f"User with ID `{user_id}` not found.")
        if not user.dm_channel:
            await user.create_dm()
        channel_id = int(data["channel"])
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = user.dm_channel
        message = discord.PartialMessage(channel=channel, id=int(data["message"]))
        view = FeedbackView(user, manager.cog, message=message)
        instance = cls(
            manager,
            user,
            message=message,
            view=view,
            started=data["started"],
            ends=ends,
        )
        bot.add_view(view, message_id=message.id)
        bot.loop.create_task(instance.run())
        return instance

    @property
    def submitted(self) -> bool:
        return self._submitted

    @submitted.setter
    def submitted(self, flag: bool) -> None:
        self._submitted = flag
        if flag:
            self.event.set()
        else:
            self.event.clear()

    async def run(self) -> None:
        await self.wait()
        self.view.disable_and_stop()
        try:
            await self.message.edit(view=self.view)
        except discord.HTTPException:
            pass

        self.manager.remove(self)
        await self.cog.config.update()

    async def wait(self) -> None:
        """
        Wait until the feedback is submitted or timeout.
        """
        now = discord.utils.utcnow().timestamp()
        sleep_time = self.ends - now
        if sleep_time < 0:
            return
        self.task = self.bot.loop.create_task(asyncio.wait_for(self.event.wait(), sleep_time))
        try:
            await self.task
        except asyncio.TimeoutError as exc:
            pass

    def stop(self) -> None:
        """
        Stops the session.
        """
        self.event.set()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user": str(self.user.id),
            "message": str(self.message.id),
            "channel": str(self.message.channel.id),
            "started": self.started,
            "ends": self.ends,
        }


class FeedbackManager:
    """
    Handles feedback or review on `thread_close` event.
    """

    def __init__(self, cog: SupportUtility):
        self.cog: SupportUtility = cog
        self.bot: ModmailBot = cog.bot
        self.active: Set = set()

    @property
    def config(self) -> Dict[str, Any]:
        """
        Feedback configurations.
        """
        return self.cog.config.feedback

    async def populate(self) -> None:
        """
        Populate active feedback sessions from database.
        """
        to_remove = []
        active = self.config.get("active_sessions", [])
        for data in active:
            try:
                instance = await Feedback.from_data(self, data=data)
            except Exception as exc:
                logger.error(f"{type(exc).__name__}: {str(exc)}")
                to_remove.append(data)
            else:
                self.active.add(instance)
        if to_remove:
            for data in to_remove:
                active.remove(data)
            await self.cog.config.update()

    def add(self, feedback: Feedback) -> None:
        self.active.add(feedback)
        active = self.config.get("active_sessions", [])
        active.append(feedback.to_dict())
        self.config["active_sessions"] = active

    def remove(self, feedback: Feedback) -> None:
        self.active.remove(feedback)
        data = None
        active = self.config.get("active_sessions", [])
        for data in active:
            if data["user"] == str(feedback.user.id):
                active.remove(data)
                self.config["active_sessions"] = active
                break

    @property
    def channel(self) -> discord.TextChannel:
        """
        Returns the log channel where the submitted feedback will be posted.
        If the channel is not set in config, defaults to bot's log channel.
        """
        channel_id = self.config.get("channel")
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                return channel
        return self.bot.log_channel

    def is_active(self, user: discord.Member) -> bool:
        """
        Returns whether the user has an active feedback session running.
        """
        exists = self.find_session(user)
        if exists:
            return True
        return False

    def find_session(self, user: discord.Member) -> Optional[Feedback]:
        return next((fb for fb in self.active if fb.user == user), None)

    async def send(self, user: discord.Member, thread: Optional[Thread] = None) -> None:
        """
        Sends the feedback prompt message to user and initiate the session.
        """
        if self.is_active(user):
            raise RuntimeError(f"There is already active feedback session for {user}.")

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

        message = await user.send(embed=embed)
        view = FeedbackView(user, self.cog, message=message, thread=thread)
        await message.edit(view=view)
        started = message.created_at.timestamp()
        feedback = Feedback(
            self,
            user,
            message=message,
            view=view,
            started=started,
            ends=started + ends_senconds,
        )
        self.add(feedback)
        await self.cog.config.update()
        self.bot.loop.create_task(feedback.run())

    async def feedback_submit(self, interaction: discord.Interaction, modal: Modal) -> None:
        """
        Called when the user submits their feedback.
        """
        view = modal.view
        view.value = True
        modal.stop()

        description = "__**Feedback:**__\n\n"
        description += view.input_map.get("feedback") or "No content."
        embed = discord.Embed(
            color=discord.Color.dark_orange(),
            description=description,
            timestamp=discord.utils.utcnow(),
        )
        user = view.user
        embed.set_author(name=str(user))
        embed.set_footer(text=f"User ID: {user.id}", icon_url=user.display_avatar)
        await self.channel.send(embed=embed)

        embed = discord.Embed(
            description=self.config.get("response", "Thanks for your time."),
            color=self.bot.main_color,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
