import asyncio
import discord
import random
import time
import yaml

from collections import Counter
from discord.ext import commands
from pathlib import Path
from typing import List

from core import checks
from core.models import PermissionLevel, getLogger
from core.paginator import EmbedPaginatorSession, MessagePaginatorSession

from .utils.chat_formatting import bold, code_block, normalize_smartquotes, plural
from .utils.checks import trivia_stop_check


logger = getLogger(__name__)


class InvalidListError(Exception):
    """A Trivia list file is in invalid format."""
    pass


_REVEAL_MESSAGES = (
    "I know this one! {answer}!",
    "Easy: {answer}.",
    "Oh really? It's {answer} of course.",
)
_FAIL_MESSAGES = (
    "To the next one I guess...",
    "Moving on...",
    "I'm sure you'll know the answer of the next one.",
    "\N{PENSIVE FACE} Next one.",
)


# A class to handle a Trivia session.
class TriviaSession:
    """
    Class to run a session of trivia with the user.

    To run the trivia session immediately, use `TriviaSession.start` instead of
    instantiating directly.

    Attributes
    ----------
    ctx : `commands.Context`
        Context object from which this session will be run.
        This object assumes the session was started in `ctx.channel`
        by `ctx.author`.
    question_list : `dict`
        A list of tuples mapping questions (`str`) to answers (`list` of
        `str`).
    settings : `dict`
        Settings for the trivia session, with values for the following:
         - ``max_score`` (`int`)
         - ``delay`` (`float`)
         - ``timeout`` (`float`)
         - ``reveal_answer`` (`bool`)
         - ``bot_plays`` (`bool`)
         - ``allow_override`` (`bool`)
         - ``payout_multiplier`` (`float`)
    scores : `collections.Counter`
        A counter with the players as keys, and their scores as values. The
        players are of type `discord.Member`.
    count : `int`
        The number of questions which have been asked.
    """

    def __init__(self, ctx, question_list: dict, settings: dict):
        self.ctx = ctx
        list_ = list(question_list.items())
        random.shuffle(list_)
        self.question_list = list_
        self.settings = settings
        self.scores = Counter()
        self.count = 0
        self._last_response = time.time()
        self._task = None

    @classmethod
    def start(cls, ctx, question_list, settings) -> "TriviaSession":
        """
        Create and start a trivia session.

        This allows the session to manage the running and cancellation of its
        own tasks.

        Parameters
        ----------
        ctx : `commands.Context`
            Same as `TriviaSession.ctx`
        question_list : `dict`
            Same as `TriviaSession.question_list`
        settings : `dict`
            Same as `TriviaSession.settings`

        Returns
        -------
        TriviaSession
            The new trivia session being run.
        """
        session = cls(ctx, question_list, settings)
        loop = ctx.bot.loop
        session._task = loop.create_task(session.run())
        session._task.add_done_callback(session._error_handler)
        return session

    def _error_handler(self, fut):
        """Catches errors in the session task."""
        try:
            fut.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("A trivia session has encountered an error.\n", exc_info=exc)
            error_msg = (
                "An unexpected error occurred in the trivia session.\n"
                "Check your console or logs for details."
            )
            asyncio.create_task(self.send_error_reply(error_msg))
            self.stop()

    async def run(self):
        """
        Run the trivia session.

        In order for the trivia session to be stopped correctly, this should
        only be called internally by `TriviaSession.start`.
        """
        await self._send_startup_msg()
        max_score = self.settings["max_score"]
        delay = self.settings["delay"]
        timeout = self.settings["timeout"]
        for question, answers in self._iter_questions():
            async with self.ctx.typing():
                await asyncio.sleep(1.5)  # Decreased to 1.5, original was 3.
            self.count += 1
            msg = bold("Question number {num}!".format(num=self.count)) + "\n\n" + question
            await self.ctx.send(msg)
            continue_ = await self.wait_for_answer(answers, delay, timeout)
            if continue_ is False:
                break
            if any(score >= max_score for score in self.scores.values()):
                await self.end_game()
                break
        else:
            await self.send_normal_reply(bold("There are no more questions!"))
            await self.end_game()

    async def _send_startup_msg(self):
        list_names = []
        for idx, tup in enumerate(self.settings["lists"].items()):
            name, author = tup
            if author:
                title = "`{trivia_list} (by {author})`".format(trivia_list=name, author=author)
            else:
                title = f"`{name}`"
            list_names.append(title)
        await self.send_normal_reply(
            bold("Starting Trivia:") + " {list_names}".format(list_names=", ".join(list_names))
        )

    def _iter_questions(self):
        """
        Iterate over questions and answers for this session.

        Yields
        ------
        `tuple`
            A tuple containing the question (`str`) and the answers (`tuple` of
            `str`).
        """
        for question, answers in self.question_list:
            answers = self._parse_answers(answers)
            yield question, answers

    async def wait_for_answer(self, answers, delay: float, timeout: float):
        """
        Wait for a correct answer, and then respond.

        Scores are also updated in this method.

        Returns False if waiting was cancelled; this is usually due to the
        session being forcibly stopped.

        Parameters
        ----------
        answers : `iterable` of `str`
            A list of valid answers to the current question.
        delay : float
            How long users have to respond (in seconds).
        timeout : float
            How long before the session ends due to no responses (in seconds).

        Returns
        -------
        bool
            :code:`True` if the session wasn't interrupted.
        """
        try:
            message = await self.ctx.bot.wait_for(
                "message", check=self.check_answer(answers), timeout=delay
            )
        except asyncio.TimeoutError:
            if time.time() - self._last_response >= timeout:
                await self.ctx.send("Guys...? Well, I guess I'll stop then.")
                await self.send_normal_reply(bold("Trivia stopped."))
                self.stop()
                return False
            if self.settings["reveal_answer"]:
                reply = (random.choice(_REVEAL_MESSAGES)).format(answer=answers[0])
            else:
                reply = random.choice(_FAIL_MESSAGES)
            if self.settings["bot_plays"]:
                reply += " **+1** for me!"
                self.scores[self.ctx.guild.me] += 1
            await self.ctx.send(reply)
        else:
            self.scores[message.author] += 1
            reply = "You got it {user}! **+1** to you!".format(user=message.author.display_name)
            await message.reply(reply)
        return True

    def check_answer(self, answers):
        """
        Get a predicate to check for correct answers.

        The returned predicate takes a message as its only parameter,
        and returns ``True`` if the message contains any of the
        given answers.

        Parameters
        ----------
        answers : `iterable` of `str`
            The answers which the predicate must check for.

        Returns
        -------
        function
            The message predicate.
        """
        answers = tuple(s.lower() for s in answers)

        def _pred(message: discord.Message):
            early_exit = (
                message.channel != self.ctx.channel
                or message.author == self.ctx.guild.me
                or message.author.bot
            )
            if early_exit:
                return False

            self._last_response = time.time()
            guess = message.content.lower()
            guess = normalize_smartquotes(guess)
            for answer in answers:
                if " " in answer and answer in guess:
                    # Exact matching, issue #331
                    return True
                elif any(word == answer for word in guess.split(" ")):
                    return True
            return False

        return _pred

    async def end_game(self):
        """End the trivia session and display scrores."""
        if self.scores:
            await self.send_table()
        self.stop()

    async def send_table(self):
        """Send a table of scores to the session's channel."""
        table = "+ Results: \n\n"
        for user, score in self.scores.most_common():
            table += "+ {}\t{}\n".format(user, score)
        await self.ctx.send(code_block(table, lang="diff"))

    def stop(self):
        """Stop the trivia session, without showing scores."""
        self.ctx.bot.dispatch("trivia_end", self)

    def force_stop(self):
        """Cancel whichever tasks this session is running."""
        self._task.cancel()
        channel = self.ctx.channel
        logger.debug("Force stopping trivia session; <#%s> in %s", channel.id, channel.guild.id)

    async def send_normal_reply(self, description):
        perms = self.ctx.channel.permissions_for(self.ctx.me)
        if perms.embed_links:
            embed = discord.Embed(color=discord.Color.dark_theme(), description=description)
            await self.ctx.send(embed=embed)
        else:
            await self.ctx.send(description)

    async def send_error_reply(self, description):
        perms = self.ctx.channel.permissions_for(self.ctx.me)
        if perms.embed_links:
            embed = discord.Embed(color=self.ctx.bot.error_color, description=description)
            await self.ctx.send(embed=embed)
        else:
            await self.ctx.send(description)

    @staticmethod
    def _parse_answers(answers):
        """
        Parse the raw answers to readable strings.

        The reason this exists is because of YAML's ambiguous syntax. For example,
        if the answer to a question in YAML is ``yes``, YAML will load it as the
        boolean value ``True``, which is not necessarily the desired answer. This
        function aims to undo that for bools, and possibly for numbers in the
        future too.

        Parameters
        ----------
        answers : `iterable` of `str`
            The raw answers loaded from YAML.

        Returns
        -------
        `tuple` of `str`
            The answers in readable/ guessable strings.
        """
        ret = []
        for answer in answers:
            if isinstance(answer, bool):
                if answer is True:
                    ret.extend(["True", "Yes", "On"])
                else:
                    ret.extend(["False", "No", "Off"])
            else:
                ret.append(str(answer))
        # Uniquify list
        seen = set()
        return tuple(x for x in ret if not (x in seen or seen.add(x)))


# Actual Cog
class Trivia(commands.Cog):
    """Play trivia with friends!"""

    config_keys = {
        "max_score": 10,
        "timeout": 120.0,
        "delay": 15.0,
        "bot_plays": False,
        "reveal_answer": True,
        "allow_override": True,
    }
    member_data = {"wins": 0, "games": 0, "total_score": 0}

    def __init__(self, bot):
        """
        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot = bot
        self.trivia_sessions = []
        self.db = bot.plugin_db.get_partition(self)
        self._config_cache = {}

        asyncio.create_task(self.populate_config_cache())

    async def populate_config_cache(self):
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

    async def update_config(self, ctx, data: dict):
        """
        Updates the database with new data and refresh the config cache.

        Parameters
        ----------
        ctx : commands.Context
            Context where the command is executed.
        data : dict
            New data to be stored in cache and updated in the database.
        """
        await self.db.find_one_and_update({"_id": ctx.guild.id}, {"$set": data}, upsert=True)
        config = self._config_cache[ctx.guild.id]
        for key, value in data.items():
            config[key] = value

        self._config_cache[ctx.guild.id] = config

    def guild_config(self, guild_id: str):
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
        embed = discord.Embed(
            color=discord.Color.dark_theme(), title="Current settings", description=desc
        )
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
        settings = self._config_cache[ctx.guild.id]
        if seconds < settings["delay"]:
            raise commands.BadArgument("Must be larger than the answer time limit.")
        new_settings = {"timeout": seconds}
        await self.update_config(ctx, new_settings)
        desc = (
            "Done. Trivia sessions will now time out after {num} seconds of no responses.".format(
                num=seconds
            )
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
            desc = (
                "Done. Trivia lists can no longer override the trivia settings for this " "server."
            )
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

        If enabled, the bot will reveal the answer if no one guesses correctly
        in time.
        """
        new_settings = {"reveal_answer": enabled}
        await self.update_config(ctx, new_settings)
        if enabled:
            desc = "Done. I'll reveal the answer if no one knows it."
        else:
            desc = "Alright, I won't reveal the answer to the questions anymore."
        embed = discord.Embed(color=discord.Color.dark_theme(), description=desc)
        await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def trivia(self, ctx: commands.Context, *categories: str):
        """
        Start trivia session on the specified category.

        You may list multiple categories, in which case the trivia will involve
        questions from all of them.
        """
        if not categories:
            return await ctx.send_help(ctx.command)
        categories = [c.lower() for c in categories]
        session = self._get_trivia_session(ctx.channel)
        if session is not None:
            raise commands.BadArgument(
                "There is already an ongoing trivia session in this channel."
            )
        trivia_dict = {}
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
    @trivia.command(name="stop")
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
            await ctx.send(
                embed=discord.Embed(color=discord.Color.dark_theme(), description=stop_message)
            )
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
            embed = discord.Embed(
                title=title, color=discord.Color.dark_theme(), description=description
            )
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
    async def trivia_leaderboard(
        self, ctx: commands.Context, sort_by: str = "wins", top: int = 10
    ):
        """
        Leaderboard for trivia.

        Defaults to the top 10 of this server, sorted by total wins. Use
        subcommands for a more customised leaderboard.

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
    def _get_sort_key(key: str):
        key = key.lower()
        if key in ("wins", "average_score", "total_score", "games"):
            return key
        elif key in ("avg", "average"):
            return "average_score"
        elif key in ("total", "score", "answers", "correct"):
            return "total_score"

    async def send_leaderboard(self, ctx: commands.Context, data: dict, key: str, top: int):
        """
        Send the leaderboard from the given data.

        Parameters
        ----------
        ctx : commands.Context
            The context to send the leaderboard to.
        data : dict
            The data for the leaderboard. This must map `discord.Member` ->
            `dict`.
        key : str
            The field to sort the data by. Can be ``wins``, ``total_score``,
            ``games`` or ``average_score``.
        top : int
            The number of members to display on the leaderboard.

        Returns
        -------
        `list` of `discord.Message`
            The sent leaderboard messages.
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
            footer_text += " - Navigate using the reactions below."
        embed.set_footer(text=footer_text)

        session = MessagePaginatorSession(ctx, *ret, embed=embed)
        return await session.run()

    @staticmethod
    def _get_leaderboard(data: dict, key: str, top: int):
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
    async def on_trivia_end(self, session: TriviaSession):
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

    async def update_leaderboard(self, session):
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

    def get_trivia_list(self, category: str) -> dict:
        """Get the trivia list corresponding to the given category.

        Parameters
        ----------
        category : str
            The desired category. Case sensitive.

        Returns
        -------
        `dict`
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

    def _get_trivia_session(self, channel: discord.TextChannel) -> TriviaSession:
        return next(
            (session for session in self.trivia_sessions if session.ctx.channel == channel), None
        )

    def _all_lists(self) -> List[Path]:
        return self.get_core_lists()

    def cog_unload(self):
        for session in self.trivia_sessions:
            session.force_stop()

    @staticmethod
    def get_core_lists() -> List[Path]:
        """Return a list of paths for all trivia lists packaged with the bot."""
        core_lists_path = Path(__file__).parent.resolve() / "lists"
        return list(core_lists_path.glob("*.yaml"))


def setup(bot):
    """Load Trivia."""
    bot.add_cog(Trivia(bot))
