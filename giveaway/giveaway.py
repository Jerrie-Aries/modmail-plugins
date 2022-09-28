from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

import discord
import yarl

from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel

from .core.checks import can_execute_giveaway
from .core.sessions import GiveawaySession
from .core.utils import duration_syntax
from .core.views import GiveawayView


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


logger = getLogger(__name__)

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

    def is_giveaway_embed(self, embed: discord.Embed) -> bool:
        """
        Returns `True` if the given Embed is a giveaway embed. Otherwise, `False`.
        """
        if not embed.title or embed.title != self.giveaway_title:
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
    def giveaway_title(self) -> str:
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
        
        _**Notes:**_
        - Make sure the bot has these permissions in your Giveaway channel:
        `View Channel`, `Send Messages`, `Read Message History`, `Embed Links`, and `Add Reactions`.
        - Only 15 active giveaways are allowed at a time.
        """
        await ctx.send_help(ctx.command)

    @giveaway.command(aliases=["create"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def start(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Start a giveaway with interactive buttons and text inputs.

        `channel` may be a channel ID, mention, or name.
        """
        if not can_execute_giveaway(ctx, channel):
            ch_text = "this channel"
            if ctx.channel != channel:
                ch_text += f" and {channel.mention} channel"
            raise commands.BadArgument(
                "Need `Send Messaged`, `Read Message History`, "
                f"`Embed Links`, and `Add Reactions` permissions in {ch_text}."
            )
        if len(self.active_giveaways) >= 15:
            raise commands.BadArgument("Only 15 active giveaways are allowed at the same time.")

        view = GiveawayView(ctx)
        embed = discord.Embed(
            title="Giveaway Settings",
            color=self.bot.main_color,
            description=(
                f"Giveaway will be posted in {channel.mention}.\n"
                "Click the `Edit` button to set the values.\n\n"
                "See the notes below for additional info."
            ),
        )
        embed.add_field(name="Winners count", value="Must be integers between 1 to 50.")
        embed.add_field(name="Duration syntax", value=duration_syntax)
        embed.set_footer(text="This view will time out after 10 minutes.")
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()

        if not view.giveaway_ready:
            return

        message = await channel.send(**view.send_params())
        await message.add_reaction(self.giveaway_emoji)
        await ctx.send(f"Done. Giveaway has been posted in {channel.mention}.", ephemeral=True)
        data = {
            "item": view.giveaway_prize,
            "winners": view.giveaway_winners,
            "time": view.giveaway_end,
            "guild": channel.guild.id,
            "channel": channel.id,
            "message": message.id,
        }
        session = GiveawaySession.start(self, data)
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
        await ctx.send(f"Giveaway ID `{message_id}` is now cancelled!")

    @giveaway.command(name="list")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def gaway_list(self, ctx: commands.Context):
        """
        Show the list of active giveaways.
        """
        embed = discord.Embed(title="Active giveaways", color=self.bot.main_color)
        desc = ""
        n = 0
        for session in self.active_giveaways:
            n += 1
            message = session.message
            if not message:
                message = await session.channel.fetch_message(session.id)
            desc += (
                f"[Giveaway {n}]({message.jump_url})\n"
                f"End: {discord.utils.format_dt(datetime.fromtimestamp(session.ends), 'R')}\n\n"
            )

        if not desc:
            desc = "No active giveaways."
        embed.description = desc

        await ctx.send(embed=embed)

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
