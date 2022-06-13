from discord.ext import commands
from core.models import PermissionLevel


def trivia_stop_check():
    async def predicate(ctx: commands.Context) -> bool:
        # noinspection PyProtectedMember
        session = ctx.cog._get_trivia_session(ctx.channel)
        if session is None:
            predicate.fail_msg = "There is no ongoing trivia session in this channel."
            return False

        author = ctx.author

        def is_mod(permission_level=PermissionLevel.MODERATOR) -> bool:
            if ctx.channel.permissions_for(author).administrator:
                return True

            checkables = {*author.roles, author}

            level_permissions = ctx.bot.config["level_permissions"]

            for level in PermissionLevel:
                if level >= permission_level and level.name in level_permissions:
                    # -1 is for @everyone
                    if -1 in level_permissions[level.name] or any(
                        str(check.id) in level_permissions[level.name] for check in checkables
                    ):
                        return True
            return False

        auth_checks = (
            await ctx.bot.is_owner(author),
            is_mod(),
            author == ctx.guild.owner,
            author == session.ctx.author,
        )
        return any(auth_checks)

    return commands.check(predicate)
