from typing import Tuple

import discord


async def is_allowed_by_hierarchy(
    bot, mod: discord.Member, member: discord.Member
) -> bool:
    return (
        mod.guild.owner_id == mod.id
        or mod.top_role >= member.top_role
        or await bot.is_owner(mod)
    )


async def is_allowed_by_role_hierarchy(
    bot,
    bot_me: discord.Member,
    mod: discord.Member,
    role: discord.Role,
) -> Tuple[bool, str]:
    if role >= bot_me.top_role and bot_me.id != mod.guild.owner_id:
        return False, f"I am not higher than `{role}` in hierarchy."
    else:
        return (
            mod.top_role > role
            or mod.id == mod.guild.owner_id
            or await bot.is_owner(mod),
            f"You are not higher than `{role}` in hierarchy.",
        )


def my_role_hierarchy(guild: discord.Guild, role: discord.Role) -> bool:
    return guild.me.top_role > role
