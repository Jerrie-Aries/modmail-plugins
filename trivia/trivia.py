from __future__ import annotations

import json
import yaml

from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import MISSING

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession, MessagePaginatorSession


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot

    TriviaDict = Dict[str, List[str]]

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)

logger = getLogger(__name__)


# <!-- Developer -->
try:
    from discord.ext.modmail_utils import bold, plural
except ImportError as exc:
    required = __plugin_info__["cogs_required"][0]
    raise RuntimeError(
        f"`modmail_utils` package is required for {__plugin_name__} plugin to function.\n"
        f"Install {required} plugin to resolve this issue."
    ) from exc

from .core.session import TriviaSession
from .core.checks import trivia_stop_check


# <-- ----- -->


class InvalidListError(Exception):
    """A Trivia list file is in invalid format."""

    pass


# Actual Cog
class Trivia(commands.Cog):
    __doc__ = __description__

    config_keys = {
        "max_score": 10,
        "timeout": 120.0,
        "delay": 15.0,
        "bot_plays": False,
        "reveal_answer": True,
        "allow_override": True,
    }
    member_data = {"wins": 0, "games": 0, "total_score": 0}

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.trivia_sessions: List[TriviaSession] = []
        self.db: AsyncIOMotorCollection = bot.api.get_plugin_partition(self)
        self._config_cache: Dict[str, Any] = {}

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self.initialize())

    async def cog_unload(self) -> None:
        for session in self.trivia_sessions:
            session.force_stop()

    async def initialize(self) -> None:
        await self.bot.wait_for_connected()
        await self.populate_config_cache()

    async def populate_config_cache(self) -> None:
        for guild in self.bot.guilds:
            config = await self.db.find_one({"_id": guild.id})
            if config is None:
                config = await self.db.find_one_and_update(
                    {"_id": guild.id},
                    {"$set": self.config_keys},
                    upsert=True,
                    return_document=True,
                )
            self._config_cache[str(guild.id)] = config

    async def update_config(self, ctx: commands.Context, data: Dict[str, Any]) -> None:
        """
        Updates the database with new data and refresh the config cache.

        Parameters
        ----------
        ctx : commands.Context
            Context where the command is executed.
        data : Dict[str, Any]
            New data to be stored in cache and updated in the database.
        """
        await self.db.find_one_and_update({"_id": ctx.guild.id}, {"$set": data}, upsert=True)
        config = self._config_cache[str(ctx.guild.id)]
        for key, value in data.items():
            config[key] = value

        self._config_cache[str(ctx.guild.id)] = config

    def guild_config(self, guild_id: str) -> Dict[str, Any]:
        return self._config_cache[guild_id]

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def triviaset(self, ctx: commands.Context):
        """Manage Trivia settings."""
        await ctx.send_help(ctx.command)

    @triviaset.command(name="showsettings")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def triviaset_showsettings(self, ctx: commands.Context):
        """Show the current trivia settings."""
        settings = await self.db.find_one({"_id": ctx.guild.id})
        desc = str(
            "Bot gains points: `{bot_plays}`\n"
            "Answer time limit: `{delay} seconds`\n"
            "Lack of response timeout: `{timeout} seconds`\n"
            "Points to win: `{max_score}`\n"
            "Reveal answer on timeout: `{reveal_answer}`\n"
            "Allow lists to override settings: `{allow_override}`".format(**settings),
        )
        embed = discord.Embed(color=discord.Color.dark_theme(), title="Current settings", description=desc)
        await ctx.send(embed=embed)

    @triviaset.command(name="maxscore")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def triviaset_max_score(self, ctx: commands.Context, score: int):
        """Set the total points required to win."""
        if score < 0:
            raise commands.BadArgument("Score must be greater than `0`.")
        new_settings = {"max_score": score}
        await self.update_config(ctx, new_settings)
        desc = "Done. Points required to win set to `{score}`.".format(score=score)
        embed = discord.Embed(color=discord.Color.dark_theme(), description=desc)
        await ctx.send(embed=embed)

    @triviaset.command(name="timelimit")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def triviaset_timelimit(self, ctx: commands.Context, seconds: int):
        """Set the maximum seconds permitted to answer a question."""
        if seconds < 4.0:
            raise commands.BadArgument("Must be at least `4 seconds`.")
        new_settings = {"delay": seconds}
        await self.update_config(ctx, new_settings)
        desc = "Done. Maximum seconds to answer set to `{num}`.".format(num=seconds)
        embed = discord.Embed(color=discord.Color.dark_theme(), description=desc)
        await ctx.send(embed=embed)

    @triviaset.command(name="stopafter")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def triviaset_stopafter(self, ctx: commands.Context, seconds: int):
        """Set how long until trivia stops due to no response."""
        settings = self._config_cache[str(ctx.guild.id)]
        if seconds < settings["delay"]:
            raise commands.BadArgument("Must be larger than the answer time limit.")
        new_settings = {"timeout": seconds}
        await self.update_config(ctx, new_settings)
        desc = "Done. Trivia sessions will now time out after {num} seconds of no responses.".format(
            num=seconds
        )
        embed = discord.Embed(color=discord.Color.dark_theme(), description=desc)
        await ctx.send(embed=embed)

    @triviaset.command(name="override")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def triviaset_allowoverride(self, ctx: commands.Context, enabled: bool):
        """Allow/disallow trivia lists to override settings."""
        new_settings = {"allow_override": enabled}
        await self.update_config(ctx, new_settings)

        if enabled:
            desc = "Done. Trivia lists can now override the trivia settings for this server."
        else:
            desc = "Done. Trivia lists can no longer override the trivia settings for this " "server."
        embed = discord.Embed(color=discord.Color.dark_theme(), description=desc)
        await ctx.send(embed=embed)

    @triviaset.command(name="botplays", usage="<true_or_false>")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def trivaset_bot_plays(self, ctx: commands.Context, enabled: bool):
        """
        Set whether or not the bot gains points.

        If enabled, the bot will gain a point if no one guesses correctly.
        """
        new_settings = {"bot_plays": enabled}
        await self.update_config(ctx, new_settings)
        if enabled:
            desc = "Done. I'll now gain a point if users don't answer in time."
        else:
            desc = "Alright, I won't embarass you at trivia anymore."
        embed = discord.Embed(color=discord.Color.dark_theme(), description=desc)
        await ctx.send(embed=embed)

    @triviaset.command(name="revealanswer", usage="<true_or_false>")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def trivaset_reveal_answer(self, ctx: commands.Context, enabled: bool):
        """
        Set whether or not the answer is revealed.

        If enabled, the bot will reveal the answer if no one guesses correctly in time.
        """
        new_settings = {"reveal_answer": enabled}
        await self.update_config(ctx, new_settings)
        if enabled:
            desc = "Done. I'll reveal the answer if no one knows it."
        else:
            desc = "Alright, I won't reveal the answer to the questions anymore."
        embed = discord.Embed(color=discord.Color.dark_theme(), description=desc)
        await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True, extras={"add_slash_option": True})
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def trivia(self, ctx: commands.Context, categories: str):
        """
        Start trivia session on the specified category.

        You may list multiple categories, in which case the trivia will involve questions from all of them.
        """
        categories = (c for c in categories.split())
        if not categories:
            return await ctx.send_help(ctx.command)
        categories = [c.lower() for c in categories]
        session = self._get_trivia_session(ctx.channel)
        if session is not None:
            raise commands.BadArgument("There is already an ongoing trivia session in this channel.")
        trivia_dict: TriviaDict = {}  # type: ignore
        authors = []
        for category in reversed(categories):
            # We reverse the categories so that the first list's config takes
            # priority over the others.
            try:
                dict_ = self.get_trivia_list(category)
            except FileNotFoundError:
                raise commands.BadArgument(
                    (
                        "Invalid category `{name}`. See `{prefix}trivia list` for a list of "
                        "trivia categories."
                    ).format(name=category, prefix=self.bot.prefix)
                )
            except InvalidListError:
                raise commands.BadArgument(
                    (
                        "There was an error parsing the trivia list for the `{name}` category. It "
                        "may be formatted incorrectly."
                    ).format(name=category)
                )
            else:
                trivia_dict.update(dict_)
                authors.append(trivia_dict.pop("AUTHOR", None))

        if not trivia_dict:
            raise commands.BadArgument(
                "The trivia list was parsed successfully, however it appears to be empty!"
            )

        settings = self.guild_config(str(ctx.guild.id))
        settings["lists"] = dict(zip(categories, reversed(authors)))
        session = TriviaSession.start(ctx, trivia_dict, settings)
        self.trivia_sessions.append(session)
        logger.debug("New trivia session; <#%s> in %d", ctx.channel.id, ctx.guild.id)

    @trivia_stop_check()
    @trivia.command(name="stop", aliases=["end"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def trivia_stop(self, ctx: commands.Context):
        """Stop an ongoing trivia session."""
        session = self._get_trivia_session(ctx.channel)
        if session is None:
            raise commands.BadArgument("There is no ongoing trivia session in this channel.")
        await session.end_game()
        session.force_stop()

        stop_message = bold("Trivia stopped.")
        if ctx.channel.permissions_for(ctx.me).embed_links:
            await ctx.send(embed=discord.Embed(color=discord.Color.dark_theme(), description=stop_message))
        else:
            await ctx.send(stop_message)

    @trivia.command(name="list")
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def trivia_list(self, ctx: commands.Context):
        """List available trivia categories."""
        lists = set(p.stem for p in self._all_lists())

        def base_embed(description: str = "", continued: bool = False):
            title = "Available trivia categories"
            if continued:
                title += " (Continued)"
            embed = discord.Embed(title=title, color=discord.Color.dark_theme(), description=description)
            len_list = len(lists)
            footer_text = f"Found {plural(len_list):trivia category|trivia categories}"
            embed.set_footer(text=footer_text)
            return embed

        embeds = [base_embed()]
        line = 1
        if lists:
            embed = embeds[0]
            for triv in sorted(lists):
                desc = f"`{triv}`\n"
                if line == 15:
                    embed = base_embed(desc, True)
                    embeds.append(embed)
                    line = 1
                else:
                    embed.description += desc
                    line += 1
        else:
            embeds[0].description = "There is no trivia category available."
        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @trivia.command(name="leaderboard", aliases=["lboard", "lb"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def trivia_leaderboard(self, ctx: commands.Context, sort_by: str = "wins", top: int = 10):
        """
        Leaderboard for trivia.

        Defaults to the top 10 of this server, sorted by total wins.
        Use subcommands for a more customised leaderboard.

        `<sort_by>` can be any of the following fields:
         - `wins`  : total wins
         - `avg`   : average score
         - `total` : total correct answers
         - `games` : total games played

        `<top>` is the number of ranks to show on the leaderboard.
        """
        key = self._get_sort_key(sort_by)
        if key is None:
            raise commands.BadArgument(
                (
                    "Unknown field `{field_name}`, see `{prefix}help trivia leaderboard server` "
                    "for valid fields to sort by."
                ).format(field_name=sort_by, prefix=self.bot.prefix)
            )

        guild = ctx.guild
        leaderboard = await self.db.find_one({"_id": "leaderboard"})
        data = {}
        if leaderboard is not None:
            data = {k: v for k, v in leaderboard.items() if k != "_id"}
        if not data:
            raise commands.BadArgument("Currently there is no data for leaderboard.")
        data = {guild.get_member(int(u)): d for u, d in data.items()}
        data.pop(None, None)  # remove any members which aren't in the guild
        await self.send_leaderboard(ctx, data, key, top)

    @staticmethod
    def _get_sort_key(key: str) -> str:
        key = key.lower()
        if key in ("wins", "average_score", "total_score", "games"):
            return key
        elif key in ("avg", "average"):
            return "average_score"
        elif key in ("total", "score", "answers", "correct"):
            return "total_score"

    async def send_leaderboard(
        self,
        ctx: commands.Context,
        data: Dict[discord.Member, Dict[str, Any]],
        key: str,
        top: int,
    ) -> None:
        """
        Send the leaderboard from the given data.

        Parameters
        ----------
        ctx : commands.Context
            The context to send the leaderboard to.
        data : Dict[discord.Member, Dict[str, Any]]
            The data for the leaderboard. This must map `discord.Member` ->
            `dict`.
        key : str
            The field to sort the data by. Can be ``wins``, ``total_score``,
            ``games`` or ``average_score``.
        top : int
            The number of members to display on the leaderboard.
        """
        if not data:
            raise commands.BadArgument("There are no scores on record!")

        leaderboard = self._get_leaderboard(data, key, top)
        ret = []
        msg = "```py\n"
        for line in leaderboard.splitlines(keepends=True):
            if len(line) + len(msg) + 3 > 2000:
                msg += "```"
                ret.append(msg)
                msg = "```py\n"
            msg += line
            if len(msg) + 3 > 2000:
                msg = msg[:1993] + "[...]```"
                ret.append(msg)
                msg = "```py\n"

        if msg != "```py\n":
            msg += "```"
            ret.append(msg)

        embed = discord.Embed(color=self.bot.main_color)
        footer_text = "Leaderboard"
        if len(ret) > 1:
            footer_text += " - Navigate using the buttons below."
        embed.set_footer(text=footer_text)

        session = MessagePaginatorSession(ctx, *ret, embed=embed)
        await session.run()

    @staticmethod
    def _get_leaderboard(data: Dict[discord.Member, Dict[str, Any]], key: str, top: int) -> str:
        # Mix in average score
        for member, stats in data.items():
            if stats["games"] != 0:
                stats["average_score"] = stats["total_score"] / stats["games"]
            else:
                stats["average_score"] = 0.0
        # Sort by reverse order of priority
        priority = ["average_score", "total_score", "wins", "games"]
        try:
            priority.remove(key)
        except ValueError:
            raise ValueError(f"{key} is not a valid key.")
        # Put key last in reverse priority
        priority.append(key)
        items = data.items()
        for key in priority:
            items = sorted(items, key=lambda t: t[1][key], reverse=True)
        max_name_len = max(map(lambda m: len(str(m)), data.keys()))
        # Headers
        headers = (
            "Rank",
            "Member" + " " * (max_name_len - 6),
            "Wins",
            "Games Played",
            "Total Score",
            "Average Score",
        )
        lines = [" | ".join(headers), " | ".join(("-" * len(h) for h in headers))]
        # Header underlines
        for rank, tup in enumerate(items, 1):
            member, m_data = tup
            # Align fields to header width
            fields = tuple(
                map(
                    str,
                    (
                        rank,
                        member,
                        m_data["wins"],
                        m_data["games"],
                        m_data["total_score"],
                        round(m_data["average_score"], 2),
                    ),
                )
            )
            padding = [" " * (len(h) - len(f)) for h, f in zip(headers, fields)]
            fields = tuple(f + padding[i] for i, f in enumerate(fields))
            lines.append(" | ".join(fields))
            if rank == top:
                break
        return "\n".join(lines)

    @commands.Cog.listener()
    async def on_trivia_end(self, session: TriviaSession) -> None:
        """
        Event for a trivia session ending.

        This method removes the session from this cog's sessions, and
        cancels any tasks which it was running.

        Parameters
        ----------
        session : TriviaSession
            The session which has just ended.
        """
        channel = session.ctx.channel
        logger.debug("Ending trivia session; <#%s> in %s", channel.id, channel.guild.id)
        if session in self.trivia_sessions:
            self.trivia_sessions.remove(session)
        if session.scores:
            await self.update_leaderboard(session)

    async def update_leaderboard(self, session: TriviaSession) -> None:
        """Update the leaderboard with the given scores.

        Parameters
        ----------
        session : TriviaSession
            The trivia session to update scores from.
        """
        max_score = session.settings["max_score"]
        leaderboard = await self.db.find_one({"_id": "leaderboard"})
        if leaderboard is None:
            leaderboard = {}

        new_scores = {}
        for member, score in session.scores.items():
            if member.id == session.ctx.bot.user.id:
                continue
            stats = leaderboard.get(str(member.id), self.member_data)
            if score == max_score:
                stats["wins"] += 1
            stats["total_score"] += score
            stats["games"] += 1
            new_scores[str(member.id)] = stats

        if not new_scores:
            return

        await self.db.find_one_and_update(
            {"_id": "leaderboard"},
            {"$set": new_scores},
            upsert=True,
        )

    def get_trivia_list(self, category: str) -> TriviaDict:
        """Get the trivia list corresponding to the given category.

        Parameters
        ----------
        category : str
            The desired category. Case sensitive.

        Returns
        -------
        Dict[str, List[str]]
            A dict mapping questions (`str`) to answers (`list` of `str`).
        """
        try:
            path = next(p for p in self._all_lists() if p.stem == category)
        except StopIteration:
            raise FileNotFoundError("Could not find the `{}` category.".format(category))

        with path.open(encoding="utf-8") as file:
            try:
                dict_ = yaml.safe_load(file)
            except yaml.error.YAMLError as exc:
                raise InvalidListError("YAML parsing failed.") from exc
            else:
                return dict_

    def _get_trivia_session(self, channel: discord.TextChannel) -> Optional[TriviaSession]:
        return next(
            (session for session in self.trivia_sessions if session.ctx.channel == channel),
            None,
        )

    def _all_lists(self) -> List[Path]:
        return self.get_core_lists()

    @staticmethod
    def get_core_lists() -> List[Path]:
        """Return a list of paths for all trivia lists packaged with the bot."""
        core_lists_path = Path(__file__).parent.resolve() / "lists"
        return list(core_lists_path.glob("*.yaml"))


async def setup(bot: ModmailBot) -> None:
    """Load Trivia."""
    await bot.add_cog(Trivia(bot))
