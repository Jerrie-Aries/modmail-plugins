from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set, Tuple, Union, TYPE_CHECKING

import discord
from discord.ext import tasks
from discord.utils import MISSING

from core.models import getLogger
from core.thread import Thread

from .views import ContactView, FeedbackView


if TYPE_CHECKING:
    from datetime import datetime
    from bot import ModmailBot
    from ..supportutils import SupportUtility
    from .views import Modal


logger = getLogger(__name__)


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

    def find_thread(self, recipient: Union[discord.Member, discord.User]) -> Optional[Thread]:
        """
        Find existing thread for recipient.
        The lookup will be in cache and other recipients.
        """
        # find in cache
        thread = self.bot.threads.cache.get(recipient.id)
        if thread:
            return thread

        # check if they were other recipients in someone else's thread
        for thread in self.bot.threads:
            if recipient in thread.recipients:
                return thread
        return None

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
        # checks for existing thread
        thread = self.find_thread(recipient)
        if thread:
            # unlike in core/thread.py, we will not do the .wait_until_ready and .CancelledError stuff here
            # just send error message and return
            embed = discord.Embed(
                color=self.bot.error_color,
                description="A thread for you already exists.",
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
        return hash((self.user.id, self.message.id))

    def __repr__(self) -> str:
        attrs = (
            ("user", self.user),
            ("thread_channel_id", self.thread_channel_id),
            ("message", self.message),
            ("started", self.started),
            ("ends", self.ends),
        )
        inner = " ".join("%s=%r" % attr for attr in attrs)
        return f"<{self.__class__.__name__} {inner}>"

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
        self.ends = self.started + self.manager.session_timeout

    @classmethod
    async def from_data(cls, manager: FeedbackManager, *, data: Dict[str, Any]) -> Feedback:
        """
        Initiate the feedback session from data.
        """
        bot = manager.bot
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
            ends=data["ends"],
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
        if not self.task and self.timed_out:
            # will be resolved in FeedbackManager.populate
            return
        await self.conclude()

    async def wait(self) -> None:
        """
        Wait until the feedback is submitted or timeout.
        """
        now = discord.utils.utcnow().timestamp()
        sleep_time = self.ends - now
        if sleep_time < 0:
            self.timed_out = True
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

    async def conclude(self, *, update: bool = True) -> None:
        """
        Finishing coroutine that is called when session is complete or timed out.
        """
        self.view.disable_and_stop()

        if self.cancelled:
            # graceful stop on cog_unload
            return
        try:
            await self.message.edit(view=self.view)
        except discord.HTTPException:
            pass

        self.manager.remove(self)
        if update:
            await self.cog.config.update()

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

    session_timeout: int = 60 * 60 * 24  # in seconds, hardcoded to 24 hours

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

    def is_enabled(self) -> bool:
        return self.config.get("enable")

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

        async def resolve_broken_and_timeouts() -> None:
            resolved = 0
            if to_remove:
                for data in to_remove:
                    active.remove(data)
                resolved += len(to_remove)
            for feedback in list(self.active):
                if not feedback.task and feedback.timed_out:
                    await feedback.conclude(update=False)
                    resolved += 1
                    await asyncio.sleep(1.0)
            if resolved:
                await self.cog.config.update()
                if resolved == 1:
                    links = ("was", " has")
                else:
                    links = ("were", "s have")
                logger.debug(
                    f"There {links[0]} {resolved} broken and/or timed out feedback session{links[1]} been resolved."
                )

        self.bot.loop.create_task(resolve_broken_and_timeouts())

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

    async def handle_prompt(self, thread: Thread, *args: Any) -> None:
        _, silent, *_ = args
        if silent:
            return

        if not self.is_enabled():
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
                await self.send(user, thread)
            except RuntimeError:
                pass

    def clear_for(self, thread: Thread) -> None:
        if not self.is_enabled():
            return

        for user in thread.recipients:
            if user is None:
                continue
            feedback = self.find_session(user)
            if feedback:
                logger.debug(f"Stopping active feedback session for {user}.")
                feedback.stop()

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


class ThreadMoveManager:
    """
    Represents an instance that handles moving responded and inactive threads to
    designated category.
    """

    __underlying_objects: Dict[str, discord.CategoryChannel] = {
        "inactive_category": None,
        "responded_category": None,
    }

    def __init__(self, cog: SupportUtility):
        self.cog: SupportUtility = cog
        self.bot: ModmailBot = cog.bot
        self.inactivity_tasks: Dict[str, asyncio.Task] = {}
        self._schedule_update: bool = False

    async def initialize(self) -> None:
        tasks = self.config["inactive"]["tasks"]
        now = discord.utils.utcnow().timestamp()
        for channel_id, ends_at in list(tasks.items()):
            channel = self.bot.modmail_guild.get_channel(int(channel_id))
            if channel is None or ends_at < now:
                tasks.pop(channel_id)
                self._schedule_update = True
                continue
            thread = await self.bot.threads.find(channel=channel)
            if not thread:
                tasks.pop(channel_id)
                self._schedule_update = True
                continue
            timeout = ends_at - now
            task = self.bot.loop.create_task(self.set_to_inactive_after(timeout, thread))
            self.inactivity_tasks[channel_id] = task

        self.update_loop.start()

    def teardown(self) -> None:
        self.update_loop.cancel()
        for task in self.inactivity_tasks.values():
            task.cancel()
        self.inactivity_tasks.clear()

    @property
    def config(self) -> Dict[str, Any]:
        return self.cog.config.thread_move

    def is_enabled(self) -> bool:
        return self.config.get("enable")

    def _get_category(self, key: str) -> Optional[discord.CategoryChannel]:
        try:
            category_id = int(self.config[key]["category"])
        except ValueError:
            self.__underlying_objects[f"{key}_category"] = category = None
            return category

        category = self.__underlying_objects[f"{key}_category"]
        if category:
            if category.id == category_id:
                return category
            self.__underlying_objects[f"{key}_category"] = None

        category = self.bot.modmail_guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            logger.error(
                f"Invalid type of category. Expected CategoryChannel, got {type(category).__name__} instead."
            )
            category = None
        self.__underlying_objects[f"{key}_category"] = category
        return category

    @property
    def responded_category(self) -> Optional[discord.CategoryChannel]:
        """
        Category where the responded threads will be moved to.
        """
        return self._get_category("responded")

    @property
    def inactive_category(self) -> Optional[discord.CategoryChannel]:
        """
        Category where the inactive threads will be moved to.
        """
        return self._get_category("inactive")

    async def handle_responded(self, thread: Thread) -> None:
        if not self.is_enabled():
            return
        category = self.responded_category
        if not category or category == thread.channel.category:
            return
        await self._move_thread_channel(thread, category, event="responded")

    async def _move_thread_channel(
        self, thread: Thread, category: discord.CategoryChannel, *, event: str
    ) -> None:
        if event not in ("responded", "inactive"):
            raise ValueError(f"Invalid type of move event. Got {event}.")

        reason = f"This thread has been {event}."
        old_category = thread.channel.category
        await thread.channel.move(category=category, end=True, sync_permissions=True, reason=reason)

        description = self.bot.formatter.format(
            self.config[event]["embed"]["description"],
            old_category=old_category.mention if old_category else "unknown category",
            new_category=category.mention,
        )
        embed = discord.Embed(
            title=self.config[event]["embed"]["title"],
            description=description,
            color=self.bot.main_color,
        )
        footer_text = self.config[event]["embed"]["footer"]
        if footer_text:
            embed.set_footer(text=footer_text)
        await thread.channel.send(embed=embed)

    async def schedule_inactive_timer(self, thread: Thread, start_time: datetime) -> None:
        channel_id = str(thread.channel.id)
        # cancel existing task
        await self.cancel_inactivity_task(channel_id)

        if not self.is_enabled() or not self.inactive_category:
            return
        timeout = self.config["inactive"]["timeout"]
        if not timeout:
            return

        task = self.bot.loop.create_task(self.set_to_inactive_after(timeout, thread))
        self.inactivity_tasks[channel_id] = task

        after_timestamp = start_time.timestamp() + timeout
        self.config["inactive"]["tasks"][channel_id] = after_timestamp
        self._schedule_update = True

    async def set_to_inactive_after(self, after: float, thread: Thread) -> None:
        """
        Set the thread to inactive. The thread will be moved to inactive category.

        Note: This method should be created as a task with `bot.loop.create_task` and stored
        in cache, so the task can be cancelled if the thread is responded.
        """
        await asyncio.sleep(after)
        category = self.inactive_category
        if category and category != thread.channel.category:
            await self._move_thread_channel(thread, category, event="inactive")
        await self.cancel_inactivity_task(thread.channel.id)

    async def cancel_inactivity_task(self, channel_id: Union[int, str], force_update: bool = False) -> None:
        """
        Cancel or stop the inactivity task for thread specified.
        """
        channel_id = str(channel_id)
        task = self.inactivity_tasks.pop(channel_id, None)
        if task and not task.done():
            task.cancel()
        ends_at = self.config["inactive"]["tasks"].pop(channel_id, None)
        # if this was in config, we need to resolve updating the config in db
        if ends_at:
            if force_update:
                await self._update_inactive_tasks()
            else:
                self._schedule_update = True

    # updating config everytime a message is sent in thread channel is quite expensive
    # to prevent unnecessary API calls to database, we just do tasks.loop to handle it.
    @tasks.loop(seconds=60)
    async def update_loop(self) -> None:
        if not self._schedule_update:
            return
        await self._update_inactive_tasks()

    async def _update_inactive_tasks(self) -> None:
        # we do manual insertion here so it won't touch other keys in the document
        data = {"thread_move.inactive.tasks": self.config["inactive"]["tasks"]}
        await self.cog.config.update(data=data)
        self._schedule_update = False
