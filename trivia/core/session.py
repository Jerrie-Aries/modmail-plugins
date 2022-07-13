from __future__ import annotations

import asyncio
import random
import time

from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

import discord

# <!-- Developer -->
from discord.utils import MISSING
from discord.ext import commands
from core.models import getLogger

# <-- ----- -->

if TYPE_CHECKING:
    TriviaDict = Dict[str, List[str]]

logger = getLogger(__name__)


# <!-- Developer -->
if TYPE_CHECKING:
    from ..utils.utils import bold, code_block, normalize_smartquotes
else:
    bold = MISSING
    code_block = MISSING
    normalize_smartquotes = MISSING


def _set_globals(*args, **kwargs) -> None:
    # This should be called from main plugin file once
    global bold, code_block, normalize_smartquotes
    bold = kwargs.pop("bold")
    code_block = kwargs.pop("code_block")
    normalize_smartquotes = kwargs.pop("normalize_smartquotes")


# <-- ----- -->


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
    question_list : `List[Tuple[str, List[str]]]`
        A list of tuples mapping questions (`str`) to answers (`list` of
        `str`).
    settings : `Dict[str, Any]`
        Settings for the trivia session, with values for the following:
         - ``max_score`` (`int`)
         - ``delay`` (`float`)
         - ``timeout`` (`float`)
         - ``reveal_answer`` (`bool`)
         - ``bot_plays`` (`bool`)
         - ``allow_override`` (`bool`)
         - ``payout_multiplier`` (`float`)
    scores : `Counter`
        A counter with the players as keys, and their scores as values. The
        players are of type `discord.Member`.
    count : `int`
        The number of questions which have been asked.
    """

    def __init__(
        self,
        ctx: commands.Context,
        question_list: TriviaDict,
        settings: Dict[str, Any],
    ):
        self.ctx: commands.Context = ctx
        list_ = list((k, v) for k, v in question_list.items())
        random.shuffle(list_)
        self.question_list: List[Tuple[str, List[str]]] = list_
        self.settings: Dict[str, Any] = settings
        self.scores: Counter = Counter()
        self.count: int = 0
        self._last_response: float = time.time()
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def start(
        cls,
        ctx: commands.Context,
        question_list: TriviaDict,
        settings: Dict[str, Any],
    ) -> "TriviaSession":
        """
        Create and start a trivia session.

        This allows the session to manage the running and cancellation of its
        own tasks.

        Parameters
        ----------
        ctx : `commands.Context`
            Same as `TriviaSession.ctx`
        question_list : `TriviaDict`
            Same as `TriviaSession.question_list` parameter.
        settings : `Dict[str, Any]`
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

    def _error_handler(self, fut: asyncio.Task) -> None:
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

    async def run(self) -> None:
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

    async def _send_startup_msg(self) -> None:
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

    def _iter_questions(self) -> Tuple[str, Tuple[str]]:
        """
        Iterate over questions and answers for this session.

        Yields
        ------
        Tuple[str, Tuple[str]]
            A tuple containing the question (`str`) and the answers (`tuple` of
            `str`).
        """
        for question, answers in self.question_list:
            answers = self._parse_answers(answers)
            yield question, answers

    async def wait_for_answer(self, answers: List[str], delay: float, timeout: float) -> bool:
        """
        Wait for a correct answer, and then respond.

        Scores are also updated in this method.

        Returns False if waiting was cancelled; this is usually due to the
        session being forcibly stopped.

        Parameters
        ----------
        answers : List[str]
            A list of valid answers to the current question.
        delay : float
            How long users have to respond (in seconds).
        timeout : float
            How long before the session ends due to no responses (in seconds).

        Returns
        -------
        bool
            `True` if the session wasn't interrupted.
        """
        try:
            message = await self.ctx.bot.wait_for("message", check=self.check_answer(answers), timeout=delay)
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

    def check_answer(self, answers: Iterable[str]) -> Callable[[discord.Message], bool]:
        """
        Get a predicate to check for correct answers.

        The returned predicate takes a message as its only parameter,
        and returns ``True`` if the message contains any of the
        given answers.

        Parameters
        ----------
        answers : Iterable[str]
            The list of answers which the predicate must check for.

        Returns
        -------
        Callable[[discord.Message], bool]
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

    async def end_game(self) -> None:
        """End the trivia session and display scrores."""
        if self.scores:
            await self.send_table()
        self.stop()

    async def send_table(self) -> None:
        """Send a table of scores to the session's channel."""
        table = "+ Results: \n\n"
        for user, score in self.scores.most_common():
            table += "+ {}\t{}\n".format(user, score)
        await self.ctx.send(code_block(table, lang="diff"))

    def stop(self) -> None:
        """Stop the trivia session, without showing scores."""
        self.ctx.bot.dispatch("trivia_end", self)

    def force_stop(self) -> None:
        """Cancel whichever tasks this session is running."""
        self._task.cancel()
        channel = self.ctx.channel
        logger.debug("Force stopping trivia session; <#%s> in %s", channel.id, channel.guild.id)

    async def send_normal_reply(self, description: str) -> None:
        perms = self.ctx.channel.permissions_for(self.ctx.me)
        if perms.embed_links:
            embed = discord.Embed(color=discord.Color.dark_theme(), description=description)
            await self.ctx.send(embed=embed)
        else:
            await self.ctx.send(description)

    async def send_error_reply(self, description: str) -> None:
        perms = self.ctx.channel.permissions_for(self.ctx.me)
        if perms.embed_links:
            embed = discord.Embed(color=self.ctx.bot.error_color, description=description)
            await self.ctx.send(embed=embed)
        else:
            await self.ctx.send(description)

    @staticmethod
    def _parse_answers(answers: Iterable[str]) -> Tuple[str]:
        """
        Parse the raw answers to readable strings.

        The reason this exists is because of YAML's ambiguous syntax. For example,
        if the answer to a question in YAML is ``yes``, YAML will load it as the
        boolean value ``True``, which is not necessarily the desired answer. This
        function aims to undo that for bools, and possibly for numbers in the
        future too.

        Parameters
        ----------
        answers : Iterable[str]
            The raw answers loaded from YAML.

        Returns
        -------
        Tuple[str]
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
