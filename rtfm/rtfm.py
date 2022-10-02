from __future__ import annotations

import io
import os
import re
import zlib
from typing import Dict, Iterator, Optional, TYPE_CHECKING

import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel

from .core.utils import finder


if TYPE_CHECKING:
    from bot import ModmailBot

RTFM_PAGE_TYPES = {
    "stable": "https://discordpy.readthedocs.io/en/stable",
    "latest": "https://discordpy.readthedocs.io/en/latest",
    "python": "https://docs.python.org/3",
}


class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE: int = 16 * 1024

    def __init__(self, buffer: bytes):
        self.stream: io.BytesIO = io.BytesIO(buffer)

    def readline(self) -> str:
        return self.stream.readline().decode("utf-8")

    def skipline(self) -> None:
        self.stream.readline()

    def read_compressed_chunks(self) -> Iterator[bytes]:
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self) -> Iterator[bytes]:
        buf = b""
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b"\n")
            while pos != -1:
                yield buf[:pos].decode("utf-8")
                buf = buf[pos + 1 :]
                pos = buf.find(b"\n")


class RTFM(commands.Cog):
    """
    Python and discord.py documentation exclusive things.
    """

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot

    def parse_object_inv(self, stream: SphinxObjectFileReader, url: str) -> Dict[str, str]:
        # key: URL
        # n.b.: key doesn't have `discord` or `discord.ext.commands` namespaces
        result = {}

        # first line is version info
        inv_version = stream.readline().rstrip()

        if inv_version != "# Sphinx inventory version 2":
            raise RuntimeError("Invalid objects.inv file version.")

        # next line is "# Project: <name>"
        # then after that is "# Version: <version>"
        projname = stream.readline().rstrip()[11:]
        _ = stream.readline().rstrip()[11:]

        # next line says if it's a zlib header
        line = stream.readline()
        if "zlib" not in line:
            raise RuntimeError("Invalid objects.inv file, not z-lib compatible.")

        # This code mostly comes from the Sphinx repository.
        entry_regex = re.compile(r"(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)")
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(":")
            if directive == "py:module" and name in result:
                # From the Sphinx Repository:
                # due to a bug in 1.1 and below,
                # two inventory entries are created
                # for Python modules, and the first
                # one is correct
                continue

            # Most documentation pages have a label
            if directive == "std:doc":
                subdirective = "label"

            if location.endswith("$"):
                location = location[:-1] + name

            key = name if dispname == "-" else dispname
            prefix = f"{subdirective}:" if domain == "std" else ""

            if projname == "discord.py":
                key = key.replace("discord.ext.commands.", "").replace("discord.", "")

            result[f"{prefix}{key}"] = os.path.join(url, location)

        return result

    async def build_rtfm_lookup_table(self) -> None:
        cache = {}
        for key, page in RTFM_PAGE_TYPES.items():
            cache[key] = {}
            async with self.bot.session.get(page + "/objects.inv") as resp:
                if resp.status != 200:
                    raise RuntimeError("Cannot build rtfm lookup table, try again later.")

                stream = SphinxObjectFileReader(await resp.read())
                cache[key] = self.parse_object_inv(stream, page)

        self._rtfm_cache = cache

    async def do_rtfm(self, ctx: commands.Context, key: str, obj: Optional[str]) -> None:
        if obj is None:
            await ctx.send(RTFM_PAGE_TYPES[key])
            return

        if not hasattr(self, "_rtfm_cache"):
            await ctx.typing()
            await self.build_rtfm_lookup_table()

        obj = re.sub(r"^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)", r"\1", obj)

        if key.startswith("latest"):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == "_":
                    continue
                if q == name:
                    obj = f"abc.Messageable.{name}"
                    break

        cache = list(self._rtfm_cache[key].items())
        matches = finder(obj, cache, key=lambda t: t[0], lazy=False)[:8]

        embed = discord.Embed(colour=discord.Colour.blurple())
        if len(matches) == 0:
            raise commands.BadArgument("Could not find anything. Sorry.")

        embed.description = "\n".join(f"[`{key}`]({url})" for key, url in matches)
        await ctx.send(embed=embed)

    @checks.has_permissions(PermissionLevel.REGULAR)
    @commands.group(aliases=["rtfd"], invoke_without_command=True)
    async def rtfm(self, ctx: commands.Context, *, obj: str = None):
        """
        Gives you a documentation link for a `discord.py` entity.

        Events, objects, and functions are all supported through a cruddy fuzzy algorithm.
        """
        await self.do_rtfm(ctx, "stable", obj)

    @checks.has_permissions(PermissionLevel.REGULAR)
    @rtfm.command(name="python", aliases=["py"])
    async def rtfm_python(self, ctx: commands.Context, *, obj: str = None):
        """
        Gives you a documentation link for a Python entity.
        """
        await self.do_rtfm(ctx, "python", obj)

    @checks.has_permissions(PermissionLevel.REGULAR)
    @rtfm.command(name="master", aliases=["2.0", "latest"])
    async def rtfm_master(self, ctx: commands.Context, *, obj: str = None):
        """
        Gives you a documentation link for a discord.py entity (master branch).
        """
        await self.do_rtfm(ctx, "latest", obj)

    @checks.has_permissions(PermissionLevel.OWNER)
    @rtfm.command(name="refresh")
    @commands.is_owner()
    async def rtfm_refresh(self, ctx: commands.Context):
        """
        Refreshes the RTFM cache.
        """

        async with ctx.typing():
            await self.build_rtfm_lookup_table()

        await ctx.send("\N{THUMBS UP SIGN}")


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(RTFM(bot))
