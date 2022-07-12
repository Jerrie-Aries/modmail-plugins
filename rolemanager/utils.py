import discord


async def delete_quietly(message: discord.Message):
    if message.channel.permissions_for(message.guild.me).manage_messages:
        try:
            await message.delete()
        except discord.HTTPException:
            pass


def guild_roughly_chunked(guild: discord.Guild) -> bool:
    return len(guild.members) / guild.member_count > 0.9
