from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set, Tuple, Union, TYPE_CHECKING

import discord
from discord.utils import MISSING

from core.models import getLogger
from core.thread import Thread

from .views import ContactView, FeedbackView


logger = getLogger(__name__)


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..supportutils import SupportUtility
    from .views import Modal


ends_seconds: int = 60 * 60 * 24


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

    async def create_thread(
        self,
        recipient: Union[discord.Member, discord.User],
        *,
        category: discord.CategoryChannel = None,
        interaction: Optional[discord.Interaction] = None,
    ) -> Thread:
        """
        Thread creation that was initiated by successful interaction on Contact Menu.
        """
        # checks for existing thread in cache
        thread = self.bot.threads.cache.get(recipient.id)
        if thread:
            # unlike in core/thread.py, we will not do the .wait_until_ready and .CancelledError stuff here
            # just send error message and return
            embed = discord.Embed(
                color=self.bot.error_color,
                description="Something went wrong. A thread for you already exists.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        thread = Thread(self.bot.threads, recipient)
        self.bot.threads.cache[recipient.id] = thread

        embed = discord.Embed(
            title=self.bot.config["thread_creation_contact_title"],
            description=self.bot.config["thread_creation_self_contact_response"],
            color=self.bot.main_color,
        )
        embed.set_footer(text=f"{recipient}", icon_url=recipient.display_avatar.url)
        message = await recipient.send(embed=embed)
        self.bot.loop.create_task(thread.setup(creator=recipient, category=category, initial_message=message))
        del embed

        embed = discord.Embed(
            title="Created Thread",
            description=f"Thread started by {recipient.mention}.",
            color=self.bot.main_color,
        )
        await thread.wait_until_ready()
        await thread.channel.send(embed=embed)


class Feedback:
    """
    Represents Feedback instance.
    """

    def __init__(
        self,
        manager: FeedbackManager,
        user: discord.Member,
        *,
        thread_channel_id: Optional[int] = None,
        # these three should be manually assigned if not passed
        message: Union[discord.Message, discord.PartialMessage] = MISSING,
        started: float = MISSING,
        ends: float = MISSING,
    ):
        self.bot: ModmailBot = manager.bot
        self.cog: SupportUtility = manager.cog
        self.manager: FeedbackManager = manager
        self.user: discord.Member = user
        self._message: Union[discord.Message, discord.PartialMessage] = message
        self.thread_channel_id: Optional[int] = thread_channel_id
        self.started: float = started
        self.ends: float = ends
        self.view: FeedbackView = MISSING  # assigned in FeedbackView
        self._submitted: bool = False
        self.timed_out: bool = False
        self.cancelled: bool = False
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

    @property
    def message(self) -> Union[discord.Message, discord.PartialMessage]:
        """Returns the feedback prompt message object that was sent to user DMs."""
        return self._message

    @message.setter
    def message(self, item: Union[discord.Message, discord.PartialMessage]) -> None:
        """
        Set the `.message` attribute. Values for `.started` and `.ends` attributes will also be automatically
        set from here.
        """
        if not isinstance(item, (discord.Message, discord.PartialMessage)):
            raise TypeError(
                f"Invalid type of item received. Expected Message or PartialMessage, got {type(item).__name__} instead."
            )
        self._message = item
        self.started = item.created_at.timestamp()
        self.ends = self.started + ends_seconds

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
        instance = cls(
            manager,
            user,
            thread_channel_id=data.get("thread_channel"),
            message=message,
            started=data["started"],
            ends=ends,
        )
        view = FeedbackView(user, manager.cog, feedback=instance, message=message)
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

        if self.cancelled:
            # graceful stop on cog_unload
            return

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
        except asyncio.TimeoutError:
            self.timed_out = True
        except asyncio.CancelledError:
            self.cancelled = True

    def stop(self) -> None:
        """
        Stops the session.
        """
        self.event.set()

    def get_log_url(self, log_data: Dict[str, Any]) -> str:
        """
        Returns the log url.
        """
        prefix = self.bot.config["log_url_prefix"].strip("/")
        if prefix == "NONE":
            prefix = ""
        return f"{self.bot.config['log_url'].strip('/')}{'/' + prefix if prefix else ''}/{log_data['key']}"

    def get_mod_ids(self, log_data: Dict[str, Any]) -> Set[int]:
        """
        Returns the IDs of Moderators or Staff that replied to the thread.
        """
        mod_ids = set()
        messages = log_data.get("messages", [])
        for msg in messages:
            author = msg["author"]
            if not author.get("mod", False):
                continue
            if int(author["id"]) not in mod_ids:
                mod_ids.add(int(author["id"]))
        return mod_ids

    async def submit(self, interaction: discord.Interaction, modal: Modal) -> None:
        """
        Called when the user submits their feedback.
        """
        self.submitted = True
        modal.stop()

        description = "__**Feedback:**__\n\n"
        description += self.view.inputs.get("feedback") or "No content."
        embed = discord.Embed(
            color=discord.Color.dark_orange(),
            description=description,
            timestamp=discord.utils.utcnow(),
        )
        if self.view.rating is not None:
            embed.add_field(name="Rating", value=self.view.rating.label)
        thread_channel_id = self.thread_channel_id
        if thread_channel_id:
            log_data = await self.bot.api.get_log(thread_channel_id)
            if log_data:
                mod_ids = self.get_mod_ids(log_data)
                if mod_ids:
                    embed.add_field(name="Staff", value=", ".join(f"<@{i}>" for i in mod_ids))
                log_url = self.get_log_url(log_data)
                embed.add_field(name="Thread log", value=f'[`{log_data["key"]}`]({log_url})')
        user = self.user
        embed.set_author(name=str(user))
        embed.set_footer(text=f"User ID: {user.id}", icon_url=user.display_avatar)
        await self.manager.channel.send(embed=embed)

        embed = discord.Embed(
            description=self.manager.config.get("response", "Thanks for your time."),
            color=self.bot.main_color,
        )
        await interaction.response.send_message(embed=embed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user": str(self.user.id),
            "message": str(self.message.id),
            "channel": str(self.message.channel.id),
            "thread_channel": self.thread_channel_id,
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
        self.active: Set[Feedback] = set()

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

        feedback = Feedback(
            self,
            user,
            thread_channel_id=thread.channel.id if thread else None,
        )
        view = FeedbackView(user, self.cog, feedback=feedback, thread=thread)
        view.message = feedback.message = await user.send(embed=embed, view=view)
        self.add(feedback)
        await self.cog.config.update()
        self.bot.loop.create_task(feedback.run())
