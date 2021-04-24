import re
from typing import Iterator, List, Optional, Sequence, Union

import discord
from discord.utils import escape_markdown

MENTION_RE = re.compile(r"@(everyone|here|&[0-9]{17,21})")


def escape_mentions(text: str):
    return MENTION_RE.sub("@\u200b\\1", text)


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


async def delete_quietly(message: discord.Message):
    if message.channel.permissions_for(message.guild.me).manage_messages:
        try:
            await message.delete()
        except discord.HTTPException:
            pass


def guild_roughly_chunked(guild: discord.Guild) -> bool:
    return len(guild.members) / guild.member_count > 0.9


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
