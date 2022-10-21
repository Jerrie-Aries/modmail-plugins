import re

from io import BytesIO
from typing import Iterator, List, Optional, Sequence, Union

import discord
from discord.utils import escape_markdown


# Chat formatting

__all__ = (
    "bold",
    "code_block",
    "cleanup_code",
    "days",
    "escape",
    "escape_code_block",
    "escape_mentions",
    "human_join",
    "humanize_roles",
    "inline",
    "normalize_smartquotes",
    "paginate",
    "text_to_file",
    "plural",
)


def bold(text: str, escape_formatting: bool = True) -> str:
    """
    Get the given text in bold.

    Note: By default, this function will escape ``text`` prior to emboldening.

    Parameters
    ----------
    text : str
        The text to be marked up.
    escape_formatting : `bool`, optional
        Set to :code:`False` to not escape markdown formatting in the text.

    Returns
    -------
    str
        The marked up text.

    """
    text = escape(text, formatting=escape_formatting)
    return "**{}**".format(text)


def code_block(text: str, lang: str = "") -> str:
    """
    Get the given text in a code block.

    Parameters
    ----------
    text : str
        The text to be marked up.
    lang : `str`, optional
        The syntax highlighting language for the codeblock.

    Returns
    -------
    str
        The marked up text.

    """
    ret = "```{}\n{}\n```".format(lang, text)
    return ret


def cleanup_code(content: str) -> str:
    """
    Automatically removes code blocks from the code.

    Parameters
    ----------
    content : str
        The content to be cleaned.

    Returns
    -------
    str
        The cleaned content.
    """
    # remove ```py\n```
    if content.startswith("```") and content.endswith("```"):
        return "\n".join(content.split("\n")[1:-1])

    # remove `foo`
    return content.strip("` \n")


def days(day: Union[int, str]) -> str:
    """
    Humanize the number of days.

    Parameters
    ----------
    day: Union[int, str]
        The number of days passed.

    Returns
    -------
    str
        A formatted string of the number of days passed.
    """
    day = int(day)
    if day == 0:
        return "**today**"
    return f"{day} day ago" if day == 1 else f"{day} days ago"


def escape_code_block(text: str) -> str:
    """
    Returns the text with code block (i.e ```) escaped.
    """
    return re.sub(r"```", "`\u200b``", text)


SMART_QUOTE_REPLACEMENT_DICT = {
    "\u2018": "'",  # Left single quote
    "\u2019": "'",  # Right single quote
    "\u201C": '"',  # Left double quote
    "\u201D": '"',  # Right double quote
}

SMART_QUOTE_REPLACE_RE = re.compile("|".join(SMART_QUOTE_REPLACEMENT_DICT.keys()))


def escape(text: str, *, mass_mentions: bool = False, formatting: bool = False) -> str:
    """
    Get text with all mass mentions or markdown escaped.

    Parameters
    ----------
    text : str
        The text to be escaped.
    mass_mentions : `bool`, optional
        Set to :code:`True` to escape mass mentions in the text.
    formatting : `bool`, optional
        Set to :code:`True` to escape any markdown formatting in the text.

    Returns
    -------
    str
        The escaped text.

    """
    if mass_mentions:
        text = text.replace("@everyone", "@\u200beveryone")
        text = text.replace("@here", "@\u200bhere")
    if formatting:
        text = escape_markdown(text)
    return text


MENTION_RE = re.compile(r"@(everyone|here|&[0-9]{17,21})")


def escape_mentions(text: str):
    return MENTION_RE.sub("@\u200b\\1", text)


def human_join(sequence: Sequence[str], delim: str = ", ", final: str = "or") -> str:
    """
    Get comma-separated list, with the last element joined with *or*.

    Parameters
    ----------
    sequence : Sequence[str]
        The items of the list to join together.
    delim : str
        The delimiter to join the sequence with. Defaults to ", ".
        This will be ignored if the length of `sequence` is or less then 2, otherwise "final" will be used instead.
    final : str
        The final delimiter to format the string with. Defaults to "or".

    Returns
    --------
    str
        The formatted string, e.g. "seq_one, seq_two and seq_three".
    """
    size = len(sequence)
    if size == 0:
        return ""

    if size == 1:
        return sequence[0]

    if size == 2:
        return f"{sequence[0]} {final} {sequence[1]}"

    return delim.join(sequence[:-1]) + f" {final} {sequence[-1]}"


def humanize_roles(
    roles: Union[List[discord.Role], List[discord.Member]],
    *,
    mention: bool = False,
    bold: bool = True,
) -> Optional[str]:
    if not roles:
        return None
    role_strings = []
    for role in roles:
        role_name = escape_mentions(role.name)
        if mention:
            role_strings.append(role.mention)
        elif bold:
            role_strings.append(f"**{role_name}**")
        else:
            role_strings.append(role_name)
    return human_join(role_strings, final="and")


humanize_members = humanize_roles


def inline(text: str) -> str:
    """Get the given text as inline code.

    Parameters
    ----------
    text : str
        The text to be marked up.

    Returns
    -------
    str
        The marked up text.

    """
    if "`" in text:
        return "``{}``".format(text)
    else:
        return "`{}`".format(text)


def normalize_smartquotes(to_normalize: str) -> str:
    """
    Get a string with smart quotes replaced with normal ones

    Parameters
    ----------
    to_normalize : str
        The string to normalize.

    Returns
    -------
    str
        The normalized string.
    """

    def replacement_for(obj):
        return SMART_QUOTE_REPLACEMENT_DICT.get(obj.group(0), "")

    return SMART_QUOTE_REPLACE_RE.sub(replacement_for, to_normalize)


def paginate(
    text: str,
    delims: Optional[Sequence[str]] = None,
    *,
    priority: bool = False,
    escape_mass_mentions: bool = True,
    shorten_by: int = 8,
    page_length: int = 2000,
) -> Iterator[str]:
    """Generate multiple pages from the given text.

    Note
    ----
    This does not respect code blocks or inline code.

    Parameters
    ----------
    text : str
        The content to pagify and send.
    delims : `sequence` of `str`, optional
        Characters where page breaks will occur. If no delimiters are found
        in a page, the page will break after ``page_length`` characters.
        By default this only contains the newline.
    priority : `bool`
        Set to :code:`True` to choose the page break delimiter based on the
        order of ``delims``. Otherwise, the page will always break at the
        last possible delimiter.
    escape_mass_mentions : `bool`
        If :code:`True`, any mass mentions (here or everyone) will be
        silenced.
    shorten_by : `int`
        How much to shorten each page by. Defaults to 8.
    page_length : `int`
        The maximum length of each page. Defaults to 2000.

    Yields
    ------
    str
        Pages of the given text.
    """
    if delims is None:
        delims = ["\n"]
    in_text = text
    page_length -= shorten_by
    while len(in_text) > page_length:
        this_page_len = page_length
        if escape_mass_mentions:
            this_page_len -= in_text.count("@here", 0, page_length) + in_text.count(
                "@everyone", 0, page_length
            )
        closest_delim = (in_text.rfind(d, 1, this_page_len) for d in delims)
        if priority:
            closest_delim = next((x for x in closest_delim if x > 0), -1)
        else:
            closest_delim = max(closest_delim)
        closest_delim = closest_delim if closest_delim != -1 else this_page_len
        if escape_mass_mentions:
            to_send = escape(in_text[:closest_delim], mass_mentions=True)
        else:
            to_send = in_text[:closest_delim]
        if len(to_send.strip()) > 0:
            yield to_send
        in_text = in_text[closest_delim:]

    if len(in_text.strip()) > 0:
        if escape_mass_mentions:
            yield escape(in_text, mass_mentions=True)
        else:
            yield in_text


def text_to_file(
    text: str,
    filename: str = "file.txt",
    *,
    spoiler: bool = False,
    encoding: str = "utf-8",
):
    """
    Prepares text to be sent as a file on Discord, without character limit.

    This writes text into a bytes object that can be used for the ``file`` or ``files`` parameters
    of :meth:`discord.abc.Messageable.send`.

    Parameters
    ----------
    text: str
        The text to put in your file.
    filename: str
        The name of the file sent. Defaults to ``file.txt``.
    spoiler: bool
        Whether the attachment is a spoiler. Defaults to ``False``.
    encoding: str
        Encoding style. Defaults to ``utf-8``.

    Returns
    -------
    discord.File
        The file containing your text.

    """
    file = BytesIO(text.encode(encoding))
    return discord.File(file, filename, spoiler=spoiler)


# noinspection PyPep8Naming
class plural:
    """
    Formats a string to singular or plural based on the length objects it refers to.

    Examples
    --------
    - 'plural(len(data)):member'
    - 'plural(len(data)):entry|entries'
    """

    def __init__(self, value):
        self.value = value

    def __format__(self, format_spec) -> str:
        v = self.value
        singular, _, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        if abs(v) != 1:
            return f"{v} {plural}"
        return f"{v} {singular}"
