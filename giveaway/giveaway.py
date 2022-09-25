from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, TYPE_CHECKING

import discord
import yarl

from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel
from core.time import human_timedelta, UserFriendlyTime

from .core.checks import can_execute_giveaway, is_cancelled, validate_message
from .core.sessions import GiveawaySession
from .core.utils import duration_syntax, format_time_remaining


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


logger = getLogger(__name__)

YES = "\u2705"
NO = "\u274C"
GIFT = "\U0001F381"
BASE_URL = "https://discordapp.com"


class Giveaway(commands.Cog):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.db: AsyncIOMotorCollection = bot.api.get_plugin_partition(self)
        self.active_giveaways: List["GiveawaySession"] = []

    async def cog_load(self) -> None:
        await self.populate_from_db()

    async def cog_unload(self) -> None:
        for session in self.active_giveaways:
            session.force_stop()

    async def populate_from_db(self) -> None:
        config = await self.db.find_one({"_id": "config"})
        if config is None:
            config = await self.db.find_one_and_update(
                {"_id": "config"},
                {"$set": {"giveaways": {}}},
                upsert=True,
                return_document=True,
            )
        giveaways = config.get("giveaways", {})
        if not giveaways:
            return

        for message_id, giveaway in giveaways.items():
            is_running = self._get_giveaway_session(int(message_id))
            if is_running is not None:
                continue
            session = GiveawaySession.start(self, giveaway)
            self.active_giveaways.append(session)

    async def _update_db(self) -> None:
        active_giveaways = {}
        for session in self.active_giveaways:
            if session.done or session.stopped:
                continue
            active_giveaways.update({str(session.id): session.data})

        await self.db.find_one_and_update(
            {"_id": "config"},
            {"$set": {"giveaways": active_giveaways}},
            upsert=True,
        )

    def author_data(
        self, message_type: str = "other", *, extra: Optional[str] = None, **kwargs
    ) -> Dict[str, str]:
        """
        Generates author data for embed author.

        The `extra` parameter if provided will be placed in the URL's fragment.
        """
        url = yarl.URL(f"{BASE_URL}/users/{self.bot.user.id}")
        kwargs["type"] = message_type
        url = url.update_query(**kwargs)
        if extra:
            url = url.with_fragment(extra)
        return {
            "name": self.bot.user.name,
            "icon_url": str(self.bot.user.display_avatar),
            "url": str(url),
        }

    def generate_embed(self, description: str) -> discord.Embed:
        embed = discord.Embed()
        embed.colour = self.bot.main_color
        embed.description = description
        embed.set_footer(text='To cancel, type "cancel".')

        return embed

    def is_giveaway_embed(self, embed: discord.Embed) -> bool:
        """
        Returns `True` if the given Embed is a giveaway embed. Otherwise, `False`.
        """
        if not embed.title or embed.title != self._giveaway_title:
            return False
        url = getattr(embed.author, "url", "")
        if not url:
            return False
        url = yarl.URL(url)
        msg_type = url.query.get("type")
        if msg_type != "system" or url.fragment != "giveaway":
            return False
        path_re = re.compile(r"^/users/(?P<id>\d{17,21})(.+)?")
        match = path_re.match(url.path)
        if match is None:
            return False
        data = match.groupdict()
        bot_id = data.get("id")
        try:
            return bot_id and int(bot_id) == self.bot.user.id
        except TypeError:
            return False

    def _get_giveaway_session(self, message_id: int) -> GiveawaySession:
        return next(
            (session for session in self.active_giveaways if session.id == message_id),
            None,
        )

    @property
    def _giveaway_title(self) -> str:
        return "Giveaway"

    @property
    def giveaway_emoji(self) -> str:
        return "🎉"

    @property
    def time_index(self) -> int:
        """Returns the index of embed field of 'Time remaining'."""
        return 2

    @commands.group(aliases=["gaway"], invoke_without_command=True)
    @commands.guild_only()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def giveaway(self, ctx: commands.Context):
        """
        Create / Stop Giveaways.
        """
        await ctx.send_help(ctx.command)

    @giveaway.command(aliases=["create"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def start(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Start a giveaway in interactive mode.
        """
        if not can_execute_giveaway(ctx, channel):
            ch_text = "this channel"
            if ctx.channel != channel:
                ch_text += f" and {channel.mention} channel"
            raise commands.BadArgument(
                "Need `SEND_MESSAGES`, `READ_MESSAGES`, `MANAGE_MESSAGES`, "
                f"`EMBED_LINKS`, and `ADD_REACTIONS` permissions in {ch_text}."
            )

        async def send_fail_embed(description="Cancelled.") -> None:
            embed = discord.Embed(color=self.bot.error_color, description=description)
            return await ctx.send(embed=embed)

        await ctx.send(
            embed=self.generate_embed(
                f"Giveaway will be posted in {channel.mention}.\n\n" "What is the giveaway item?"
            )
        )

        def check(message: discord.Message) -> bool:
            return validate_message(ctx, message)

        try:
            message = await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            return send_fail_embed("Time out.")
        if is_cancelled(ctx, message):
            return await send_fail_embed()
        prize = message.content
        await ctx.send(
            embed=self.generate_embed(
                f"Giveaway item:\n**{prize}**\n\n" "How many winners are to be selected?"
            )
        )
        try:
            message = await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            return send_fail_embed("Time out.")
        if is_cancelled(ctx, message):
            return await send_fail_embed()

        try:
            winners = int(message.content)
        except ValueError:
            raise commands.BadArgument("Unable to parse giveaway winners to numbers, exiting.")

        if winners <= 0:
            raise commands.BadArgument(
                "Giveaway can only be held with 1 or more winners. Cancelling command."
            )
        await ctx.send(
            embed=self.generate_embed(
                f"**{winners} {'winners' if winners > 1 else 'winner'}** will be selected.\n\n"
                f"How long will the giveaway last?\n\n{duration_syntax}"
            )
        )

        while True:
            try:
                message = await self.bot.wait_for("message", check=check, timeout=30.0)
            except asyncio.TimeoutError:
                return send_fail_embed("Time out.")
            if is_cancelled(ctx, message):
                return await send_fail_embed()

            try:
                # <!-- Developer -->
                ends_at = await UserFriendlyTime().convert(ctx, message.content, now=discord.utils.utcnow())
            except (commands.BadArgument, commands.CommandError):
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            "I was not able to parse the time properly. Please use the following syntax.\n\n"
                            f"{duration_syntax}"
                        ),
                        color=self.bot.error_color,
                    ).set_footer(text='To cancel, type "cancel".')
                )
                continue

            if (ends_at.dt.timestamp() - ends_at.now.timestamp()) <= 0:
                return await send_fail_embed("I was not able to parse the time properly. Exiting.")

            gtime = ends_at.dt
            break

        reactions = [YES, NO]
        confirm_message = await ctx.send(
            embed=discord.Embed(
                description=f"Giveaway will last for **{human_timedelta(gtime)}**. Proceed?",
                color=self.bot.main_color,
            ).set_footer(text=f"React with {YES} to proceed, {NO} to cancel")
        )
        for emoji in reactions:
            await confirm_message.add_reaction(emoji)
            await asyncio.sleep(0.2)

        def reaction_check(reaction, user) -> bool:
            return (
                user.id == ctx.author.id
                and reaction.message.id == confirm_message.id
                and reaction.emoji in reactions
            )

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", check=reaction_check, timeout=20.0)
        except asyncio.TimeoutError:
            return await confirm_message.clear_reactions()
        try:
            await confirm_message.clear_reactions()
        except (discord.Forbidden, discord.HTTPException):
            pass
        if reaction.emoji == NO:
            await send_fail_embed()
            return

        now_utc = discord.utils.utcnow().timestamp()
        gtime_utc = gtime.replace(tzinfo=timezone.utc).timestamp()
        time_left = gtime_utc - now_utc
        time_remaining = format_time_remaining(time_left)

        embed = discord.Embed(title=self._giveaway_title, colour=0x00FF00)
        embed.set_author(**self.author_data("system", extra="giveaway", channel_id=channel.id))
        embed.description = f"React with {self.giveaway_emoji} to enter the giveaway!"
        embed.add_field(name=f"{GIFT} Prize:", value=prize)
        embed.add_field(name="Hosted by:", value=ctx.author.mention, inline=False)
        embed.add_field(name="Time remaining:", value=f"_**{time_remaining}**_", inline=False)
        embed.set_footer(text=f"{winners} {'winners' if winners > 1 else 'winner'} | Ends at")
        embed.timestamp = datetime.fromtimestamp(gtime.timestamp())
        msg = await channel.send(embed=embed)

        await msg.add_reaction(self.giveaway_emoji)
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"Done! Giveaway embed has been posted in {channel.mention}!",
        )
        await ctx.send(embed=embed)

        giveaway_data = {
            "item": prize,
            "winners": winners,
            "time": gtime_utc,
            "guild": ctx.guild.id,
            "channel": channel.id,
            "message": msg.id,
        }
        session = GiveawaySession.start(self, giveaway_data)
        self.active_giveaways.append(session)
        await self._update_db()

    @giveaway.command(aliases=["rroll"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def reroll(self, ctx: commands.Context, message_id: int, winners_count: int):
        """
        Reroll the giveaway.

        **Usage:**
        `{prefix}giveaway reroll <message_id> <winners_count>`

        __**Note:**__
        - This command must be run in the same channel where the message is.
        """

        # Don't roll if giveaway is active
        session = self._get_giveaway_session(message_id)
        if session is not None:
            raise commands.BadArgument("You can't reroll an active giveaway.")

        try:
            message = await ctx.channel.fetch_message(int(message_id))
        except discord.Forbidden:
            raise commands.BadArgument("No permission to read the history.")
        except discord.NotFound:
            raise commands.BadArgument("Message not found.")

        if message.author.id != self.bot.user.id:
            raise commands.BadArgument("The given message wasn't from me.")

        if not message.embeds or message.embeds[0] is None:
            raise commands.BadArgument(
                "The given message doesn't have an embed, it isn't related to a giveaway."
            )

        if not self.is_giveaway_embed(message.embeds[0]):
            raise commands.BadArgument("The given message isn't related to giveaway.")

        # giveaway dict to init the GiveawaySession, just pass in the `winners_count`for this purpose
        giveaway_obj = {"winners": winners_count}
        session = GiveawaySession(self, giveaway_obj)

        winners = await session.get_winners(message)
        if not winners:
            raise commands.BadArgument("There is no legit guild member participated in that giveaway.")

        embed = message.embeds[0]
        winners_fmt = ""
        for winner in winners:
            winners_fmt += f"<@{winner}> "

        embed.description = (
            "Giveaway has ended!\n\n" f"**{'Winners' if winners_count > 1 else 'Winner'}:** {winners_fmt}"
        )
        embed.set_footer(text=f"{winners_count} {'winners' if winners_count > 1 else 'winner'} | Ended at")
        await message.edit(embed=embed)
        await ctx.channel.send(
            f"{self.giveaway_emoji} Congratulations {winners_fmt}, you have won **{embed.title}**!"
        )
        return

    @giveaway.command(aliases=["stop"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cancel(self, ctx: commands.Context, message_id: int):
        """
        Stop an active giveaway.

        **Usage:**
        `{prefix}giveaway stop <message_id>`
        """
        session = self._get_giveaway_session(message_id)
        if session is None:
            raise commands.BadArgument("Unable to find an active giveaway with that ID!")

        channel = self.bot.get_channel(session.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise TypeError(f"Invalid type. Expected `TextChannel`, got `{type(channel).__name__}` instead.")

        try:
            message = await channel.fetch_message(int(message_id))
        except discord.Forbidden:
            raise commands.BadArgument("No permission to read the history.")
        except discord.NotFound:
            raise commands.BadArgument("Message not found.")

        if not message.embeds or message.embeds[0] is None:
            raise commands.BadArgument(
                "The given message doesn't have an embed, it isn't related to a giveaway."
            )

        embed = message.embeds[0]
        embed.description = "The giveaway has been cancelled."
        await message.edit(embed=embed)

        session.force_stop()
        self.active_giveaways.remove(session)
        await self._update_db()
        await ctx.send("Cancelled!")

    @commands.Cog.listener()
    async def on_giveaway_end(self, session: GiveawaySession) -> None:
        """
        A custom event that is dispatched when the giveaway session has ended.
        """
        if session in self.active_giveaways:
            self.active_giveaways.remove(session)
            await self._update_db()


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Giveaway(bot))
