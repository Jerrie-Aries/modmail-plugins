import discord
from discord.utils import escape_markdown
from io import BytesIO
import re


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

    def __format__(self, format_spec):
        v = self.value
        singular, sep, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        if abs(v) != 1:
            return f"{v} {plural}"
        return f"{v} {singular}"
