import argparse

from typing import Optional

import discord

from discord.ext import commands

from .errors import BanEntryNotFound


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise RuntimeError(message)


class ActionReason(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: Optional[str]) -> str:
        if argument is None:
            return None
        reason_max = 512
        if len(argument) > reason_max:
            raise commands.BadArgument(f"Reason is too long ({len(argument)}/{reason_max})")
        return argument


class BannedMember:
    def __init__(self, user: discord.User, ban_reason: Optional[str], guild: discord.Guild):
        self.user: discord.User = user
        self.ban_reason: Optional[str] = ban_reason
        self.guild: discord.Guild = guild

    @classmethod
    async def convert(cls, ctx: commands.Context, argument: str) -> "BannedMember":
        if argument.isdigit():
            member_id = int(argument, base=10)
            try:
                entity = await ctx.guild.fetch_ban(discord.Object(id=member_id))
            except discord.NotFound:
                raise BanEntryNotFound from None
        else:
            ban_list = [entry async for entry in ctx.guild.bans(limit=None)]
            entity = discord.utils.find(lambda e: str(e.user) == argument, ban_list)

            if entity is None:
                raise BanEntryNotFound

        self = cls(user=entity.user, ban_reason=entity.reason, guild=ctx.guild)

        return self

    def __str__(self) -> str:
        return str(self.user)

    async def unban(self, *, reason: Optional[str] = None) -> None:
        """
        Unbans this user from the server.
        """
        await self.guild.unban(self.user, reason=reason)
