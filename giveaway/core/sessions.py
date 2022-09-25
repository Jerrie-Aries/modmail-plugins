from __future__ import annotations

import asyncio
import random

from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

import discord

from core.models import getLogger

from .utils import format_time_remaining


if TYPE_CHECKING:
    from bot import ModmailBot
    from ..giveaway import Giveaway

    ReactedUserT = Union[discord.User, discord.Member]


logger = getLogger(__name__)


class GiveawaySession:
    """
    Giveaway session.

    To run the giveaway session immediately, use `GiveawaySession.start` instead of
    instantiating directly.

    Attributes
    ----------
    cog : Giveaway
        The Giveaway cog.
    bot : bot.ModmailBot
        The Modmail bot.
    giveaway_data : dict
        Giveaway object retrieved from database, or when starting the giveaway from command.
    channel_id : int
        The ID of channel where the giveaway embed was posted.
    guild_id : int
        The ID of the guild where the giveaway session is running.
    id : int
        The message ID of the giveaway embed.
    winners_count : int
        Numbers of giveaway winners to be choosen.
    ends : float
        Time the giveaway will end, in UTC timestamp format.
    message : discord.Message
        The giveaway message object. This will only be implemented if this class is instantiated
        using the `GiveawaySession.start`.
    """

    def __init__(self, cog: Giveaway, giveaway_data: Dict[str, Any]):
        """
        Parameters
        -----------
        cog : Giveaway
            The Giveaway cog.
        giveaway_data : Dict[str, Any]
            Giveaway object retrieved from database, or when starting the giveaway from command.
        """
        self.cog: Giveaway = cog
        self.bot: ModmailBot = cog.bot
        self.data: Dict[str, Any] = giveaway_data
        self.channel_id: int = self.data.get("channel", int())
        self.guild_id: int = self.data.get("guild", int())
        self.id: int = self.data.get("message", int())
        self.giveaway_item: str = self.data.get("item", None)
        self.winners_count: int = self.data.get("winners", 1)
        self.ends: float = self.data.get("time", float())

        self.message: Optional[discord.Message] = None  # Implemented in `handle_giveaway`

        self._task: Optional[asyncio.Task] = None
        self._stopped: bool = False
        self._done: bool = False

    @classmethod
    def start(cls, cog: Giveaway, giveaway_data: Dict[str, Any]) -> "GiveawaySession":
        """
        Create and start a giveaway session.

        This allows the session to manage the running and cancellation of its
        own tasks.

        Parameters
        ----------
        cog : Giveaway
            The Giveaway cog.
        giveaway_data : Dict[str, Any]
            Same as `GiveawaySession.data`.

        Returns
        -------
        GiveawaySession
            The new giveaway session being run.
        """
        session = cls(cog, giveaway_data)
        loop = session.bot.loop
        session._task = loop.create_task(session._handle_giveaway())
        session._task.add_done_callback(session._error_handler)
        return session

    def _error_handler(self, fut: asyncio.Future) -> None:
        """Catches errors in the session task."""
        try:
            fut.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"{type(exc).__name__}: {str(exc)}")
            if isinstance(exc, discord.NotFound) and "Unknown Message" in str(exc):
                # Probably message got deleted
                self.stop()
            else:
                logger.error("A giveaway session has encountered an error.\n", exc_info=exc)
                self.suspend()

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        """
        The :class:`discord.TextChannel` object for this :class:`GiveawaySession`.

        Returns
        -------
        discord.TextChannel or None
            The Channel object, or None.
        """
        return self.bot.get_channel(self.channel_id)

    @property
    def guild(self) -> Optional[discord.Guild]:
        """
        The :class:`discord.Guild` object for this :class:`GiveawaySession`.

        Returns
        -------
        discord.Guild or None
            The Guild object, or None.
        """
        if self.message:
            return self.message.guild
        if self.channel:
            return self.channel.guild
        return self.bot.get_guild(self.guild_id)

    @property
    def stopped(self) -> bool:
        """
        Returns `True` if the giveaway session has been stopped, otherwise `False`.
        """
        return self._stopped

    @property
    def done(self) -> bool:
        """
        Checks whether the giveaway has ended.
        This will return `True` if the giveaway has ended, otherwise `False`.
        """
        return self._done

    def suspend(self) -> None:
        """Suspends the giveaway task."""
        self._stopped = True

    def stop(self) -> None:
        """Stops the giveaway session, and the `giveaway_end` event will be dispatched."""
        if self.stopped:
            return

        self._stopped = True
        self._done = True
        logger.debug(
            "Stopping giveaway session; channel `%s`, guild `%s`.",
            self.channel or f"<#{self.channel_id}>",
            self.guild or self.guild_id,
        )
        self.bot.dispatch("giveaway_end", self)

    def force_stop(self) -> None:
        """Cancel whichever tasks this session is running without dispatching the `giveaway_end` event."""
        self._stopped = True
        self._task.cancel()
        logger.debug(
            "Force stopping giveaway session; channel `%s`, guild `%s`.",
            self.channel or f"<#{self.channel_id}>",
            self.guild or self.guild_id,
        )

    async def get_winners(self, message: discord.Message) -> List[int]:
        """
        Get giveaway winners.
        """
        reaction = discord.utils.find(
            lambda r: r.emoji == self.cog.giveaway_emoji,
            message.reactions,
        )
        reacted_users = []
        if reaction is not None:
            reacted_users = [user async for user in reaction.users()]
        return self._get_random_user(message.guild, reacted_users)

    def _get_random_user(self, guild: discord.Guild, reacted_users: List[ReactedUserT]) -> List[int]:
        """
        A method to get random users based on reactions on the giveaway embed.

        Also checks whether the member is present in the guild or is a bot.
        If the member is not in the guild, or the member is a bot, they will be removed from the list.

        Returns
        -------
        List[int or None]
            The list of unique IDs of selected winners, or an empty list if no winners selected
            in some way.
        """
        if not reacted_users:
            return []
        # remove bots and any None members
        for member in list(reacted_users):
            if member.bot:
                reacted_users.remove(member)
                continue
            if isinstance(member, discord.User) or guild.get_member(member.id) is None:
                reacted_users.remove(member)

        win = []
        for _ in range(self.winners_count):
            if not reacted_users:
                break

            rnd = random.choice(reacted_users)
            # to make sure this member won't get chosen again
            reacted_users.remove(rnd)

            win.append(rnd.id)
            if len(win) == self.winners_count:
                break
        return win

    def embed_no_one_participated(
        self, message: discord.Message, winners: Optional[int] = None
    ) -> discord.Embed:
        if winners is None:
            winners = self.winners_count
        embed = message.embeds[0]
        embed.description = "Giveaway has ended!\n\nSadly no one participated."
        embed.set_footer(text=f"{winners} {'winners' if winners > 1 else 'winner'} | Ended at")
        embed.remove_field(self.cog.time_index)
        return embed

    async def _handle_giveaway(self) -> None:
        """
        Task to handle this giveaway session. This task will loop each minute continuously
        until it is stopped, ends, or an error occurs in some way.
        """
        await self.bot.wait_for_connected()

        while True:
            if self.done or self.stopped:
                return
            if self.channel is None:
                self.stop()
                break

            fetched = False
            if not self.message:
                # error raised here will be catched in `._error_handler`
                self.message = await self.channel.fetch_message(self.id)
                fetched = True
            if not self.message.embeds:
                self.stop()
                break

            now_utc = discord.utils.utcnow().timestamp()
            gtime = self.ends - now_utc

            if gtime > 0:
                time_remaining = format_time_remaining(gtime)
                embed = self.message.embeds[0]
                embed.set_field_at(
                    self.cog.time_index, name="Time remaining:", value=f"_**{time_remaining}**_", inline=False
                )
                await self.message.edit(embed=embed)
                await asyncio.sleep(60 if gtime > 60 else 30)
                continue

            if not fetched:
                self.message = await self.channel.fetch_message(self.message.id)  # update the message object

            winners = await self.get_winners(self.message)
            if not winners:
                embed = self.embed_no_one_participated(self.message, self.winners_count)
                await self.message.edit(embed=embed)
                del embed
                self.stop()
                break

            embed = self.message.embeds[0]
            winners_fmt = " ".join(f"<@{winner}>" for winner in winners)

            embed.description = (
                "Giveaway has ended!\n\n" f"**{'Winners' if len(winners) > 1 else 'Winner'}:** {winners_fmt} "
            )
            embed.set_footer(text=f"{len(winners)} {'winners' if len(winners) > 1 else 'winner'} | Ended at")
            embed.remove_field(self.cog.time_index)
            await self.message.edit(embed=embed)
            await self.channel.send(
                f"{self.cog.giveaway_emoji} Congratulations {winners_fmt}, "
                f"you have won **{self.giveaway_item}**!"
            )
            self.stop()
            del winners_fmt, winners, embed
            break
