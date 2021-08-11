import asyncio
import contextlib
import functools
from collections import defaultdict
from colorsys import rgb_to_hsv
from copy import deepcopy
from datetime import timezone
from typing import Iterable, List, Optional, Union

import discord
from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession
from .checks import is_allowed_by_role_hierarchy, my_role_hierarchy
from .converters import (
    Args,
    AssignableRole,
    EmojiRoleGroup,
    ObjectConverter,
    PERMS,
    UnionEmoji,
)
from .utils import (
    delete_quietly,
    guild_roughly_chunked,
    human_join,
    humanize_roles,
    paginate,
)

logger = getLogger(__name__)


def get_audit_reason(moderator: discord.Member):
    return f"Moderator: {moderator}."


class ReactRules:
    NORMAL = "NORMAL"  # Allow multiple.
    UNIQUE = "UNIQUE"  # Remove existing role when assigning another role in group.
    VERIFY = "VERIFY"  # Not Implemented yet.


YES_EMOJI = "✅"
NO_EMOJI = "❌"


class RoleManager(commands.Cog, name="Role Manager"):
    """
    Useful role commands to manage roles on your server.

    This plugin includes Auto Role, Mass Roling, Reaction Roles, and Targeter.

    __**About:**__
    This plugin is a combination and modified version of:
    - `roleutils` cog made by [PhenoM4n4n](https://github.com/phenom4n4n).
    Source repository can be found [here](https://github.com/phenom4n4n/phen-cogs/tree/master/roleutils).
    - `targeter` cog made by [NeuroAssassin](https://github.com/NeuroAssassin).
    Source repository can be found [here](https://github.com/NeuroAssassin/Toxic-Cogs/tree/master/targeter).

    __**Note:**__
    In order for any of the features in this plugin to work, the bot must have `Manage Roles` permission on your server.
    """

    _id = "config"
    default_config = {
        "reactroles": {
            "message_cache": {},
            "channels": [],
            "enabled": True,
        },
        "autorole": {
            "roles": [],
            "enabled": False,
        },
    }

    reactroles_default_config = {
        "message": int(),
        "channel": int(),
        "emoji_role_groups": {},  # "emoji_string": "role_id"
        "rules": ReactRules.NORMAL,
    }

    def __init__(self, bot) -> None:
        """
        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot = bot
        self.db = bot.api.get_plugin_partition(self)
        self.config_cache = {}
        self.method = "build"

        self.bot.loop.create_task(self.populate_cache())

    async def populate_cache(self):
        """
        Initial tasks when loading the cog.
        """
        await self.bot.wait_for_connected()

        config = await self.db.find_one({"_id": self._id})
        if config is None:
            config = deepcopy(self.default_config)
        self.config_cache = config

    @property
    def config(self):
        if not self.config_cache:
            self.config_cache = deepcopy(self.default_config)
        return self.config_cache

    async def update_db(self):
        """
        Updates the database with config from the cache.
        """
        await self.db.find_one_and_update(
            {"_id": self._id},
            {"$set": self.config_cache},
            upsert=True,
        )

    async def get_role_info(self, role: discord.Role) -> discord.Embed:
        if guild_roughly_chunked(role.guild) is False and self.bot.intents.members:
            await role.guild.chunk()
        description = [
            f"{role.mention}",
            f"Members: {len(role.members)} | Position: {role.position}",
            f"Color: {role.color}",
            f"Hoisted: {role.hoist}",
            f"Mentionable: {role.mentionable}",
        ]
        if role.managed:
            description.append(f"Managed: {role.managed}")

        embed = discord.Embed(
            color=role.color,
            title=role.name,
            description="\n".join(description),
            timestamp=role.created_at,
        )

        rolecolor = str(role.color).upper()
        embed.set_thumbnail(url=f"https://placehold.it/100/{str(rolecolor)[1:]}?text=+")
        embed.set_footer(text=f"Role ID: {role.id}")
        return embed

    @staticmethod
    def add_multiple_reactions(
        message: discord.Message,
        emojis: Iterable[Union[discord.Emoji, discord.Reaction, str]],
    ) -> asyncio.Task:
        """
        Add multiple reactions to the message.

        `asyncio.sleep()` is used to prevent the client from being rate limited when
        adding multiple reactions to the message.

        This is a non-blocking operation - calling this will schedule the
        reactions being added, but the calling code will continue to
        execute asynchronously. There is no need to await this function.

        This is particularly useful if you wish to start waiting for a
        reaction whilst the reactions are still being added.

        Parameters
        ----------
        message: discord.Message
            The message to add reactions to.
        emojis : Iterable[discord.Emoji or discord.Reaction or  str]
            Emojis to add.

        Returns
        -------
        asyncio.Task
            The task for the coroutine adding the reactions.
        """

        async def task():
            # The task should exit silently if the message is deleted
            with contextlib.suppress(discord.NotFound):
                for emoji in emojis:
                    try:
                        await message.add_reaction(emoji)
                    except (discord.HTTPException, discord.InvalidArgument) as e:
                        logger.warning("Failed to add reaction %s: %s.", emoji, e)
                        return
                    await asyncio.sleep(0.2)

        return asyncio.create_task(task())

    @staticmethod
    def get_hsv(role: discord.Role):
        return rgb_to_hsv(*role.color.to_rgb())

    def base_embed(self, description: str):
        embed = discord.Embed(color=self.bot.main_color, description=description)
        return embed

    @commands.group(name="roleutil", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_(self, ctx: commands.Context):
        """
        Base command for modifying roles.
        """
        await ctx.send_help(ctx.command)

    @role_.command(name="info")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_info(self, ctx: commands.Context, *, role: discord.Role):
        """
        Get information about a role.

        `role` may be a role ID, mention, or name.
        """
        await ctx.send(embed=await self.get_role_info(role))

    @role_.command(name="members", aliases=["dump"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_members(self, ctx: commands.Context, *, role: discord.Role):
        """
        Sends a list of members in a role.

        `role` may be a role ID, mention, or name.
        """
        if guild_roughly_chunked(role.guild) is False and self.bot.intents.members:
            await role.guild.chunk()

        member_list = role.members.copy()

        def base_embed(continued=False, description=None):
            embed = discord.Embed(
                description=description if description is not None else "",
                color=role.color,
            )

            embed.title = f"Members in {discord.utils.escape_markdown(role.name)}"
            if continued:
                embed.title += " (Continued)"

            embed.set_thumbnail(
                url=f"https://placehold.it/100/{str(role.color)[1:]}?text=+"
            )

            footer_text = f"Found {len(member_list)} " + (
                "member" if len(member_list) == 1 else "members"
            )
            embed.set_footer(text=footer_text)
            return embed

        embeds = [base_embed()]
        entries = 0

        if member_list:
            embed = embeds[0]

            for member in sorted(member_list, key=lambda m: m.name.lower()):
                line = f"{member} - {member.id}\n"
                if entries == 25:
                    embed = base_embed(continued=True, description=line)
                    embeds.append(embed)
                    entries = 1
                else:
                    embed.description += line
                    entries += 1
        else:
            embeds[0].description = f"Role **{role}** has no members."

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @role_.command(name="colors")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_colors(self, ctx: commands.Context):
        """
        Sends the server's roles, ordered by color.
        """
        roles = defaultdict(list)
        for r in ctx.guild.roles:
            roles[str(r.color)].append(r)
        roles = dict(sorted(roles.items(), key=lambda v: self.get_hsv(v[1][0])))

        lines = [
            f"**{color}**\n{' '.join(r.mention for r in rs)}\n"
            for color, rs in roles.items()
        ]
        embeds = [discord.Embed(color=self.bot.main_color)]
        embed = embeds[0]
        embed.description = ""
        for line in lines:
            if len(line) + len(embed.description) > 2000:
                embed = discord.Embed(color=self.bot.main_color)
                embeds.append(embed)
                embed.description = line
            else:
                embed.description += line

        session = EmbedPaginatorSession(ctx, *embeds)
        return await session.run()

    @role_.command(name="create")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_create(
        self,
        ctx: commands.Context,
        color: Optional[discord.Color] = discord.Color.default(),
        hoist: Optional[bool] = False,
        *,
        name: str = None,
    ):
        """
        Creates a role.

        Color and whether it is hoisted can be specified.

        `color` if specified, the following formats are accepted:
        - `0x<hex>`
        - `#<hex>`
        - `0x#<hex>`
        - `rgb(<number>,<number>,<number>)`
        Like CSS, `<number>` can be either 0-255 or 0-100%.
        `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).

        __**Note:**__
        - All parameters are optional.
        If they're not specified, a role with default name `new role` and gray color will be created.
        """
        if len(ctx.guild.roles) >= 250:
            return await ctx.send(
                "This server has reached the maximum role limit (250)."
            )

        role = await ctx.guild.create_role(name=name, colour=color, hoist=hoist)
        await ctx.send(f"**{role}** created!", embed=await self.get_role_info(role))

    @role_.command(name="color")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_color(
        self, ctx: commands.Context, role: discord.Role, color: discord.Color
    ):
        """
        Change a role's color.

        `role` may be a role ID, mention, or name.

        For `color`, the following formats are accepted:
        - `0x<hex>`
        - `#<hex>`
        - `0x#<hex>`
        - `rgb(<number>,<number>,<number>)`
        Like CSS, `<number>` can be either 0-255 or 0-100%.
        `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).
        """
        if not my_role_hierarchy(ctx.guild, role):
            raise commands.BadArgument(f"I am not higher than `{role}` in hierarchy.")
        await role.edit(color=color)
        await ctx.send(
            f"**{role}** color changed to **{color}**.",
            embed=await self.get_role_info(role),
        )

    @role_.command(name="name")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_name(self, ctx: commands.Context, role: discord.Role, *, name: str):
        """
        Change a role's name.

        `role` may be a role ID, mention, or name.
        """
        if not my_role_hierarchy(ctx.guild, role):
            raise commands.BadArgument(f"I am not higher than `{role}` in hierarchy.")
        old_name = role.name
        await role.edit(name=name)
        await ctx.send(
            f"Changed **{old_name}** to **{name}**.",
            embed=await self.get_role_info(role),
        )

    @role_.command(name="add")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_add(
        self, ctx: commands.Context, member: discord.Member, *, role: AssignableRole
    ):
        """
        Add a role to a member.

        `member` may be a member ID, mention, or name.
        `role` may be a role ID, mention, or name.
        """
        if role in member.roles:
            await ctx.send(
                f"**{member}** already has the role **{role}**. Maybe try removing it instead."
            )
            return
        reason = get_audit_reason(ctx.author)
        await member.add_roles(role, reason=reason)
        await ctx.send(f"Added **{role.name}** to **{member}**.")

    @role_.command(name="remove")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_remove(
        self, ctx: commands.Context, member: discord.Member, *, role: AssignableRole
    ):
        """
        Remove a role from a member.

        `member` may be a member ID, mention, or name.
        `role` may be a role ID, mention, or name.
        """
        if role not in member.roles:
            await ctx.send(
                f"**{member}** doesn't have the role **{role}**. Maybe try adding it instead."
            )
            return
        reason = get_audit_reason(ctx.author)
        await member.remove_roles(role, reason=reason)
        await ctx.send(f"Removed **{role.name}** from **{member}**.")

    @role_.command(require_var_positional=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def addmulti(
        self, ctx: commands.Context, role: AssignableRole, *members: discord.Member
    ):
        """
        Add a role to multiple members.

        `role` may be a role ID, mention, or name.
        `members` may be member IDs, mentions, or names.

        __**Note:**__
        - You can specify multiple members with single command, just separate the arguments with space.
        Typically the ID is easiest to use.
        """
        reason = get_audit_reason(ctx.author)
        already_members = []
        success_members = []
        for member in members:
            if role not in member.roles:
                await member.add_roles(role, reason=reason)
                success_members.append(member)
            else:
                already_members.append(member)
        msg = []
        if success_members:
            msg.append(f"Added **{role}** to {humanize_roles(success_members)}.")
        if already_members:
            msg.append(f"{humanize_roles(already_members)} already had **{role}**.")
        await ctx.send("\n".join(msg))

    @role_.command(require_var_positional=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def removemulti(
        self, ctx: commands.Context, role: AssignableRole, *members: discord.Member
    ):
        """
        Remove a role from multiple members.

        `role` may be a role ID, mention, or name.
        `members` may be member IDs, mentions, or names.

        __**Note:**__
        - You can specify multiple members with single command, just separate the arguments with space.
        Typically the ID is easiest to use.
        """
        reason = get_audit_reason(ctx.author)
        already_members = []
        success_members = []
        for member in members:
            if role in member.roles:
                await member.remove_roles(role, reason=reason)
                success_members.append(member)
            else:
                already_members.append(member)
        msg = []
        if success_members:
            msg.append(f"Removed **{role}** from {humanize_roles(success_members)}.")
        if already_members:
            msg.append(f"{humanize_roles(already_members)} didn't have **{role}**.")
        await ctx.send("\n".join(msg))

    @commands.group(invoke_without_command=True, usage="<add/remove>")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def multirole(self, ctx: commands.Context):
        """
        Add/Remove multiple roles to/from a member.
        """
        await ctx.send_help(ctx.command)

    @multirole.command(name="add", require_var_positional=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def multirole_add(
        self, ctx: commands.Context, member: discord.Member, *roles: AssignableRole
    ):
        """
        Add multiple roles to a member.

        `member` may be a member ID, mention, or name.
        `roles` may be role IDs, mentions, or names.

        __**Note:**__
        - You can specify multiple roles with single command, just separate the arguments with space.
        Typically the ID is easiest to use.
        """
        not_allowed = []
        already_added = []
        to_add = []
        for role in roles:
            allowed = await is_allowed_by_role_hierarchy(
                self.bot, ctx.me, ctx.author, role
            )
            if not allowed[0]:
                not_allowed.append(role)
            elif role in member.roles:
                already_added.append(role)
            else:
                to_add.append(role)
        reason = get_audit_reason(ctx.author)
        msg = []
        if to_add:
            await member.add_roles(*to_add, reason=reason)
            msg.append(f"Added {humanize_roles(to_add)} to **{member}**.")
        if already_added:
            msg.append(f"**{member}** already had {humanize_roles(already_added)}.")
        if not_allowed:
            msg.append(
                f"You do not have permission to assign the roles {humanize_roles(not_allowed)}."
            )
        await ctx.send("\n".join(msg))

    @multirole.command(name="remove", require_var_positional=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def multirole_remove(
        self, ctx: commands.Context, member: discord.Member, *roles: AssignableRole
    ):
        """
        Remove multiple roles from a member.

        `member` may be a member ID, mention, or name.
        `roles` may be role IDs, mentions, or names.

        __**Note:**__
        - You can specify multiple roles with single command, just separate the arguments with space.
        Typically the ID is easiest to use.
        """
        not_allowed = []
        not_added = []
        to_rm = []
        for role in roles:
            allowed = await is_allowed_by_role_hierarchy(
                self.bot, ctx.me, ctx.author, role
            )
            if not allowed[0]:
                not_allowed.append(role)
            elif role not in member.roles:
                not_added.append(role)
            else:
                to_rm.append(role)
        reason = get_audit_reason(ctx.author)
        msg = []
        if to_rm:
            await member.remove_roles(*to_rm, reason=reason)
            msg.append(f"Removed {humanize_roles(to_rm)} from **{member}**.")
        if not_added:
            msg.append(f"**{member}** didn't have {humanize_roles(not_added)}.")
        if not_allowed:
            msg.append(
                f"You do not have permission to assign the roles {humanize_roles(not_allowed)}."
            )
        await ctx.send("\n".join(msg))

    @role_.command(name="all")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_all(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Add a role to all members of the server.

        `role` may be a role ID, mention, or name.
        """
        await self.super_massrole(ctx, ctx.guild.members, role)

    @role_.command(name="rall", aliases=["removeall"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_rall(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Remove a role from all members of the server.

        `role` may be a role ID, mention, or name.
        """
        member_list = self.get_member_list(ctx.guild.members, role, False)
        await self.super_massrole(
            ctx, member_list, role, "No one on the server has this role.", False
        )

    @role_.command(name="humans")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_humans(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Add a role to all humans (non-bots) in the server.

        `role` may be a role ID, mention, or name.
        """
        await self.super_massrole(
            ctx,
            [member for member in ctx.guild.members if not member.bot],
            role,
            "Every human in the server has this role.",
        )

    @role_.command(name="rhumans")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_rhumans(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Remove a role from all humans (non-bots) in the server.

        `role` may be a role ID, mention, or name.
        """
        await self.super_massrole(
            ctx,
            [member for member in ctx.guild.members if not member.bot],
            role,
            "None of the humans in the server have this role.",
            False,
        )

    @role_.command(name="bots")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_bots(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Add a role to all bots in the server.

        `role` may be a role ID, mention, or name.
        """
        await self.super_massrole(
            ctx,
            [member for member in ctx.guild.members if member.bot],
            role,
            "Every bot in the server has this role.",
        )

    @role_.command(name="rbots")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_rbots(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Remove a role from all bots in the server.

        `role` may be a role ID, mention, or name.
        """
        await self.super_massrole(
            ctx,
            [member for member in ctx.guild.members if member.bot],
            role,
            "None of the bots in the server have this role.",
            False,
        )

    @role_.command(name="in")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_in(
        self,
        ctx: commands.Context,
        target_role: discord.Role,
        *,
        add_role: AssignableRole,
    ):
        """
        Add a role to all members of a another role.

        `target_role` and `add_role` may be a role ID, mention, or name.
        """
        await self.super_massrole(
            ctx,
            [member for member in target_role.members],
            add_role,
            f"Every member of **{target_role}** has this role.",
        )

    @role_.command(name="rin")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_rin(
        self,
        ctx: commands.Context,
        target_role: discord.Role,
        *,
        remove_role: AssignableRole,
    ):
        """
        Remove a role from all members of a another role.

        `target_role` and `remove_role` may be a role ID, mention, or name.
        """
        await self.super_massrole(
            ctx,
            [member for member in target_role.members],
            remove_role,
            f"No one in **{target_role}** has this role.",
            False,
        )

    @role_.group(name="target", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_target(self, ctx: commands.Context):
        """
        Modify roles using 'targeting' args.

        An explanation of Targeter and test commands to preview the members affected can be found with `{prefix}target`.
        """
        await ctx.send_help(ctx.command)

    @role_target.command(name="add")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def target_add(
        self, ctx: commands.Context, role: AssignableRole, *, args: str
    ):
        """
        Add a role to members using targeting args.

        `role` may be a role ID, mention, or name.

        An explanation of Targeter and test commands to preview the members affected can be found with `{prefix}target`.
        """
        args = await self.args_to_list(ctx, args)
        await self.super_massrole(
            ctx,
            args,
            role,
            f"No one was found with the given args that was eligible to recieve **{role}**.",
        )

    @role_target.command(name="remove")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def target_remove(
        self, ctx: commands.Context, role: AssignableRole, *, args: str
    ):
        """
        Remove a role from members using targeting args.

        `role` may be a role ID, mention, or name.

        An explanation of Targeter and test commands to preview the members affected can be found with `{prefix}target`.
        """
        args = await self.args_to_list(ctx, args)
        await self.super_massrole(
            ctx,
            args,
            role,
            f"No one was found with the given args that was eligible have **{role}** removed from them.",
            False,
        )

    async def super_massrole(
        self,
        ctx: commands.Context,
        members: list,
        role: discord.Role,
        fail_message: str = "Everyone in the server has this role.",
        adding: bool = True,
    ):
        if guild_roughly_chunked(ctx.guild) is False and self.bot.intents.members:
            await ctx.guild.chunk()
        member_list = self.get_member_list(members, role, adding)
        if not member_list:
            await ctx.send(fail_message)
            return
        verb = "add" if adding else "remove"
        word = "to" if adding else "from"
        await ctx.send(
            f"Beginning to {verb} **{role.name}** {word} **{len(member_list)}** members."
        )
        async with ctx.typing():
            result = await self.massrole(
                member_list, [role], get_audit_reason(ctx.author), adding
            )
            result_text = f"{verb.title()[:5]}ed **{role.name}** {word} **{len(result['completed'])}** members."
            if result["skipped"]:
                result_text += f"\nSkipped {verb[:5]}ing roles for **{len(result['skipped'])}** members."
            if result["failed"]:
                result_text += f"\nFailed {verb[:5]}ing roles for **{len(result['failed'])}** members."
        await ctx.send(result_text)

    @staticmethod
    def get_member_list(members: list, role: discord.Role, adding: bool = True):
        if adding:
            members = [member for member in members if role not in member.roles]
        else:
            members = [member for member in members if role in member.roles]
        return members

    @staticmethod
    async def massrole(members: list, roles: list, reason: str, adding: bool = True):
        completed = []
        skipped = []
        failed = []
        for member in members:
            if adding:
                to_add = [role for role in roles if role not in member.roles]
                if to_add:
                    try:
                        await member.add_roles(*to_add, reason=reason)
                    except Exception as e:
                        failed.append(member)
                        logger.exception(f"Failed to add roles to {member}", exc_info=e)
                    else:
                        completed.append(member)
                else:
                    skipped.append(member)
            else:
                to_remove = [role for role in roles if role in member.roles]
                if to_remove:
                    try:
                        await member.remove_roles(*to_remove, reason=reason)
                    except Exception as e:
                        failed.append(member)
                        logger.exception(
                            f"Failed to remove roles from {member}", exc_info=e
                        )
                    else:
                        completed.append(member)
                else:
                    skipped.append(member)
        return {"completed": completed, "skipped": skipped, "failed": failed}

    # ################ #
    #     AUTOROLE     #
    # ################ #

    @commands.group(name="autorole", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _autorole(self, ctx: commands.Context):
        """
        Manage autoroles.
        """
        await ctx.send_help(ctx.command)

    @_autorole.command(name="add")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def autorole_add(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Add a role to be added to all new members on join.
        """
        autorole_config = self.config["autorole"]
        roles = autorole_config.get("roles", [])
        if role.id in roles:
            raise commands.BadArgument(f'Role "{role}" is already in autorole list.')

        roles.append(role.id)
        await self.update_db()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"On member join, role {role.mention} will be added to the member.",
        )
        await ctx.send(embed=embed)

    @_autorole.command(name="remove", aliases=["delete"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def autorole_remove(
        self, ctx: commands.Context, *, role: Union[AssignableRole, int]
    ):
        """
        Remove an autorole.
        """
        autorole_config = self.config["autorole"]
        roles = autorole_config.get("roles", [])

        if isinstance(role, discord.Role):
            role_id = role.id
        else:
            role_id = role  # to support removing id of role that already got deleted from server

        if role_id not in roles:
            raise commands.BadArgument(f'Role "{role}" is not in autorole list.')

        roles.remove(role_id)
        await self.update_db()

        embed = discord.Embed(
            color=self.bot.main_color,
            description=f'Successfully removed role "{role}" from autorole list.',
        )
        await ctx.send(embed=embed)

    @_autorole.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def autorole_enable(self, ctx: commands.Context, mode: bool = None):
        """
        Enable the autorole on member join.

        Run this command without argument to get the current set configuration.
        """
        autorole_config = self.config["autorole"]
        enabled = autorole_config.get("enabled", False)
        if mode is None:
            em = discord.Embed(
                color=self.bot.main_color,
                description=f"The autorole is currently set to `{enabled}`.",
            )
            return await ctx.send(embed=em)

        if mode == enabled:
            raise commands.BadArgument(
                f'Autorole is already {"enabled" if mode else "disabled"}.'
            )

        autorole_config.update({"enabled": mode})
        await self.update_db()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=("Enabled " if mode else "Disabled ") + "the autorole.",
        )
        await ctx.send(embed=embed)

    @_autorole.command(name="list")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def autorole_list(self, ctx: commands.Context):
        """
        List of roles set for the autorole on this server.
        """
        autorole_config = self.config["autorole"]
        roles = autorole_config.get("roles", [])
        if not roles:
            raise commands.BadArgument("There are no roles set for the autorole.")

        autorole_roles = []
        for role_id in roles:
            role = ctx.guild.get_role(role_id)
            if role is None:
                autorole_roles.append(
                    role_id
                )  # show anyway in case the role already got deleted from server
                continue
            autorole_roles.append(role.mention)
        if not autorole_roles:
            raise commands.BadArgument(
                "There are no roles set for the autorole on this server."
            )

        embed = discord.Embed(
            title="Autorole", color=self.bot.main_color, description=""
        )
        for i, role_fmt in enumerate(autorole_roles, start=1):
            embed.description += f"{i}. {role_fmt}\n"

        embed.set_footer(
            text=f"Total: {len(autorole_roles)}"
            + (" role" if len(autorole_roles) == 1 else "roles")
        )
        await ctx.send(embed=embed)

    @_autorole.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def autorole_clear(self, ctx: commands.Context):
        """
        Clear the autorole data.
        """

        confirm = await ctx.send(
            embed=discord.Embed(
                color=self.bot.main_color,
                description="Are you sure you want to clear all autorole data?",
            ).set_footer(
                text=f"React with {YES_EMOJI} to proceed, {NO_EMOJI} to cancel"
            )
        )
        self.add_multiple_reactions(confirm, [YES_EMOJI, NO_EMOJI])

        def reaction_check(reaction, user):
            return (
                user.id == ctx.author.id
                and reaction.message.id == confirm.id
                and reaction.emoji in [YES_EMOJI, NO_EMOJI]
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", check=reaction_check, timeout=60
            )
        except asyncio.TimeoutError:
            try:
                await confirm.clear_reactions()
            except (discord.Forbidden, discord.HTTPException):
                pass
            raise commands.BadArgument("Time out. Action cancelled.")

        if reaction.emoji == YES_EMOJI:
            autorole_config = self.config["autorole"]
            autorole_config.update({"roles": [], "enabled": False})
            await self.update_db()
            final_msg = "Data cleared."
        else:
            final_msg = "Action cancelled."

        try:
            await confirm.clear_reactions()
        except (discord.Forbidden, discord.HTTPException):
            pass

        await ctx.send(final_msg)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        autorole_config = self.config["autorole"]
        enabled = autorole_config.get("enabled", False)
        if not enabled:
            return
        roles = autorole_config.get("roles", [])
        if not roles:
            return

        to_add = []
        for role_id in roles:
            role = member.guild.get_role(role_id)
            if role is None:
                continue
            to_add.append(role)
        if not to_add:
            return

        try:
            await member.add_roles(*to_add, reason="Autorole.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.error(
                f"Exception occured when trying to add roles to member {member}."
            )
            logger.error(f"{type(exc).__name__}: {str(exc)}")
            return

    # ###################### #
    #     REACTION ROLES     #
    # ###################### #

    def _check_payload_to_cache(self, payload):
        """
        Returns `True` if the 'payload.message_id' is in the reaction roles config.
        """
        return str(payload.message_id) in self.config["reactroles"]["message_cache"]

    def _udpate_reactrole_cache(
        self, message_id: int, remove: bool = False, config: dict = None
    ):
        """
        Updates config cache.
        """
        if remove:
            self.config["reactroles"]["message_cache"].pop(str(message_id))
        else:
            self.config["reactroles"]["message_cache"].update({str(message_id): config})

    async def bulk_delete_set_roles(
        self,
        message: Union[discord.Message, discord.Object],
        emoji_list: List[str],
    ):
        message_config = self.config["reactroles"]["message_cache"].get(str(message.id))
        if message_config is None:
            raise ValueError(f'Message ID "{message.id}" is not in cache.')

        react_to_role_config = message_config.get("emoji_role_groups", {})
        if not react_to_role_config:
            self._udpate_reactrole_cache(message.id, remove=True)
            return
        for emoji_str in emoji_list:
            if emoji_str in react_to_role_config:
                del react_to_role_config[emoji_str]

    @staticmethod
    def emoji_string(emoji: Union[discord.Emoji, discord.PartialEmoji, str]) -> str:
        """
        Returns a formatted string of an emoji.
        """
        if isinstance(emoji, (discord.Emoji, discord.PartialEmoji)):
            if emoji.id is None:
                emoji_fmt = emoji.name
            elif emoji.animated:
                emoji_fmt = f"<a:{emoji.name}:{emoji.id}>"
            else:
                emoji_fmt = f"<:{emoji.name}:{emoji.id}>"
            return emoji_fmt
        else:
            return emoji

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def reactrole(self, ctx: commands.Context):
        """
        Base command for Reaction Role management.
        """
        await ctx.send_help(ctx.command)

    @reactrole.command(name="enable")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def reactrole_enable(self, ctx: commands.Context, mode: bool = None):
        """
        Toggle reaction roles on or off.

        Run this command without argument to get the current set configuration.
        """
        reactroles_config = self.config.get("reactroles")
        enabled = reactroles_config.get("enabled", False)
        if mode is None:
            em = discord.Embed(
                color=self.bot.main_color,
                description=f"The reaction roles is currently set to `{enabled}`.",
            )
            return await ctx.send(embed=em)

        if mode == enabled:
            raise commands.BadArgument(
                f'Reaction roles is already {"enabled" if mode else "disabled"}.'
            )

        embed = discord.Embed(
            color=self.bot.main_color,
            description=("Enabled " if mode else "Disabled ") + "the reaction roles.",
        )
        await ctx.send(embed=embed)

        reactroles_config["enabled"] = mode
        await self.update_db()

    @reactrole.command(name="create")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def reactrole_create(
        self,
        ctx: commands.Context,
        emoji_role_groups: commands.Greedy[EmojiRoleGroup],
        channel: Optional[discord.TextChannel] = None,
        color: Optional[discord.Color] = None,
        *,
        name: str = None,
    ):
        """
        Create a new reaction role menu.

        Emoji and role groups should be seperated by a `;` and have no space.

        `channel` if specified, may be a channel ID, mention, or name.
        If not specified, will be the channel where the command is ran from.

        `color` if specified, the following formats are accepted:
        - `0x<hex>`
        - `#<hex>`
        - `0x#<hex>`
        - `rgb(<number>,<number>,<number>)`
        Like CSS, `<number>` can be either 0-255 or 0-100%.
        `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).

        __**Example:**__
        - `{prefix}reactrole create 🎃;@SpookyRole 🅱️;MemeRole #role_channel rgb(120,85,255)`
        """
        if not emoji_role_groups:
            raise commands.BadArgument("Failed to parse emoji and role groups.")
        channel = channel or ctx.channel
        if not channel.permissions_for(ctx.me).send_messages:
            raise commands.BadArgument(
                f"I do not have permission to send messages in {channel.mention}."
            )
        if color is None:
            color = self.bot.main_color

        def check(msg: discord.Message):
            return (
                ctx.author == msg.author
                and ctx.channel == msg.channel
                and (len(msg.content) < 2000)
            )

        def cancel_check(msg: discord.Message):
            return msg.content == "cancel" or msg.content == f"{ctx.prefix}cancel"

        rules_msg = await ctx.send(
            embed=self.base_embed(
                "What is the rule for this reaction role you want to set?\n\n"
                "Available options:\n"
                "`Normal` - Allow users to have multiple roles in group.\n"
                "`Unique` - Remove existing role when assigning another role in group.\n"
            )
        )

        try:
            rules_resp = await self.bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await delete_quietly(rules_msg)
            raise commands.BadArgument("Time out. Reaction Role creation cancelled.")
        else:
            await delete_quietly(rules_resp)
            await delete_quietly(rules_msg)
            if cancel_check(rules_resp) is True:
                raise commands.BadArgument("Reaction Role creation cancelled.")

        rules = rules_resp.content.upper()
        if rules not in (ReactRules.NORMAL, ReactRules.UNIQUE):
            raise commands.BadArgument(
                f"`{rules}` is not a valid option. Reaction Role creation cancelled."
            )

        if name is None:
            m = await ctx.send(
                embed=self.base_embed(
                    "What would you like the reaction role menu name to be?"
                )
            )
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=60)
            except asyncio.TimeoutError:
                await delete_quietly(m)
                raise commands.BadArgument(
                    "Time out. Reaction Role creation cancelled."
                )
            else:
                await delete_quietly(msg)
                await delete_quietly(m)
                if cancel_check(msg) is True:
                    raise commands.BadArgument("Reaction Role creation cancelled.")
                name = msg.content

        description = (
            f"React to the following emoji to receive the corresponding role:\n"
        )
        for (emoji, role) in emoji_role_groups:
            description += f"{emoji} - {role.mention}\n"
        embed = discord.Embed(title=name[:256], color=color, description=description)
        message = await channel.send(embed=embed)

        duplicates = {}
        message_config = deepcopy(self.reactroles_default_config)
        message_config["message"] = message.id
        message_config["channel"] = message.channel.id
        message_config["rules"] = rules
        binds = {}
        for (emoji, role) in emoji_role_groups:
            emoji_str = self.emoji_string(emoji)
            if emoji_str in binds or role.id in binds.values():
                duplicates[emoji] = role
            else:
                binds[emoji_str] = role.id
                await message.add_reaction(emoji)
        message_config["emoji_role_groups"] = binds
        if duplicates:
            dupes = "The following groups were duplicates and weren't added:\n"
            for emoji, role in duplicates.items():
                dupes += f"{emoji};{role}\n"
            await ctx.send(embed=self.base_embed(dupes))

        await ctx.message.add_reaction(YES_EMOJI)

        self._udpate_reactrole_cache(message.id, config=message_config)
        await self.update_db()

    @reactrole.command(name="add", aliases=["bind", "link"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def reactrole_add(
        self,
        ctx: commands.Context,
        message: discord.Message,
        emoji: UnionEmoji,
        role: AssignableRole,
    ):
        """
        Bind a reaction role to an emoji on a message that already exists.

        `message` may be a message ID or message link.

        __**Note:**__
        - This could be used if you want to create a reaction roles menu on a pre-existing message.
        """
        message_config = self.config["reactroles"]["message_cache"].get(str(message.id))
        if message_config is None:
            message_config = deepcopy(self.reactroles_default_config)

        for emo_id, role_id in message_config["emoji_role_groups"].items():
            if role.id == role_id:
                raise commands.BadArgument(
                    f"Role {role.mention} is already binded to emoji {emo_id} on that message."
                )

        emoji_str = self.emoji_string(emoji)
        old_role = ctx.guild.get_role(
            message_config["emoji_role_groups"].get(emoji_str)
        )
        if old_role:
            msg = await ctx.send(
                embed=self.base_embed(
                    f"Emoji {emoji} is already binded to role {old_role.mention} on that message.\n"
                    "Would you like to override it?"
                )
            )
            self.add_multiple_reactions(msg, [YES_EMOJI, NO_EMOJI])

            def reaction_check(reaction, user):
                return (
                    user.id == ctx.author.id
                    and reaction.message.id == msg.id
                    and reaction.emoji in [YES_EMOJI, NO_EMOJI]
                )

            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add", check=reaction_check, timeout=60
                )
            except asyncio.TimeoutError:
                raise commands.BadArgument("Time out. Bind cancelled.")

            if reaction.emoji == NO_EMOJI:
                raise commands.BadArgument("Bind cancelled.")

        rules = message_config.get("rules", ReactRules.NORMAL)
        message_config["emoji_role_groups"][emoji_str] = role.id
        message_config["channel"] = message.channel.id
        message_config["rules"] = rules

        if str(emoji) not in [str(e) for e in message.reactions]:
            await message.add_reaction(emoji)
        await ctx.send(
            embed=self.base_embed(
                f"Role {role.mention} has been binded to emoji {emoji} on [this message]({message.jump_url})."
            )
        )

        self._udpate_reactrole_cache(message.id, config=message_config)
        await self.update_db()

    @reactrole.command(name="rule")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def reactrole_rule(
        self,
        ctx: commands.Context,
        message: Union[discord.Message, ObjectConverter, int],
        rules: str.upper = None,
    ):
        """
        Set rule for an existing reaction role message.

        `message` may be a message ID or message link.

        Available options for `rule`:
        `Normal` - Allow users to have multiple roles in group.
        `Unique` - Remove existing role when assigning another role in group.

        Leave the `rule` empty to get the current set configuration.
        """
        if isinstance(message, int):
            message_id = message
        else:
            message_id = message.id

        message_config = self.config["reactroles"]["message_cache"].get(str(message_id))
        if message_config is None or not message_config.get("emoji_role_groups"):
            raise commands.BadArgument(
                "There are no reaction roles set up for that message."
            )

        old_rules = message_config["rules"]
        if rules is None:
            return await ctx.send(
                embed=self.base_embed(
                    f"Reaction role rules for that message is currently set to `{old_rules}`."
                )
            )

        if rules not in (ReactRules.NORMAL, ReactRules.UNIQUE):
            raise commands.BadArgument(
                f"`{rules}` is not a valid option for reaction role's rule."
            )

        old_rules = message_config["rules"]
        if rules == old_rules:
            raise commands.BadArgument(
                f"Reaction role's rule for that message is already set to `{old_rules}`."
            )

        message_config["rules"] = rules
        await self.update_db()
        await ctx.send(
            embed=self.base_embed(
                f"Reaction role's rule for that message is now set to `{rules}`."
            )
        )

    @reactrole.group(name="delete", aliases=["remove"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def reactrole_delete(
        self,
        ctx: commands.Context,
        message: Union[discord.Message, ObjectConverter, int],
    ):
        """
        Delete an entire reaction role for a message.

        `message` may be a message ID or message link.
        """
        if isinstance(message, int):
            message_id = message
        else:
            message_id = message.id

        message_config = self.config["reactroles"]["message_cache"].get(str(message_id))
        if message_config is None or not message_config.get("emoji_role_groups"):
            raise commands.BadArgument(
                "There are no reaction roles set up for that message."
            )

        msg = await ctx.send(
            embed=self.base_embed(
                "Are you sure you want to remove all reaction roles for that message?"
            )
        )
        self.add_multiple_reactions(msg, [YES_EMOJI, NO_EMOJI])

        def reaction_check(reaction, user):
            return (
                user.id == ctx.author.id
                and reaction.message.id == msg.id
                and reaction.emoji in [YES_EMOJI, NO_EMOJI]
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", check=reaction_check, timeout=60
            )
        except asyncio.TimeoutError:
            raise commands.BadArgument("Time out. Action cancelled.")

        if reaction.emoji == YES_EMOJI:
            self._udpate_reactrole_cache(message.id, remove=True)
            await self.update_db()
            await ctx.send(
                embed=self.base_embed("Reaction roles cleared for that message.")
            )
        else:
            raise commands.BadArgument("Action cancelled.")

    @reactrole_delete.command(name="bind", aliases=["link"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def delete_bind(
        self,
        ctx: commands.Context,
        message: Union[discord.Message, ObjectConverter, int],
        emoji: Union[UnionEmoji, ObjectConverter],
    ):
        """
        Delete an emoji-role bind for a reaction role.

        `message` may be a message ID or message link.
        """
        if isinstance(message, int):
            message_id = message
        else:
            message_id = message.id

        message_config = self.config["reactroles"]["message_cache"].get(str(message_id))
        if message_config is None:
            raise commands.BadArgument(
                "There are no reaction roles set up for that message."
            )

        emoji_str = self.emoji_string(emoji)
        try:
            del message_config["emoji_role_groups"][emoji_str]
        except KeyError:
            raise commands.BadArgument("That wasn't a valid emoji for that message.")
        await self.update_db()
        await ctx.send(embed=self.base_embed(f"That emoji role bind was deleted."))

    @reactrole.command(name="list")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def react_list(self, ctx: commands.Context):
        """
        View the reaction roles on this server.
        """
        data = self.config["reactroles"]["message_cache"]
        if not data:
            raise commands.BadArgument("There are no reaction roles set up here!")

        guild: discord.Guild = ctx.guild
        to_delete_message_emojis = {}
        react_roles = []
        for index, (message_id, message_data) in enumerate(data.items(), start=1):
            channel: discord.TextChannel = guild.get_channel(message_data["channel"])
            if channel is None:
                # TODO: handle deleted channels
                continue
            if self.method == "fetch":
                try:
                    message: discord.Message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    # TODO: handle deleted messages
                    continue
                link = message.jump_url
            elif self.method == "build":
                link = f"https://discord.com/channels/{ctx.guild.id}/{channel.id}/{message_id}"
            else:
                link = ""

            to_delete_emojis = []
            rules = message_data["rules"]
            reactions = [f"[Reaction Role #{index}]({link}) - `{rules}`"]
            for emoji_str, role in message_data["emoji_role_groups"].items():
                role = ctx.guild.get_role(role)
                if role:
                    reactions.append(f"{emoji_str}: {role.mention}")
                else:
                    to_delete_emojis.append(emoji_str)
            if to_delete_emojis:
                to_delete_message_emojis[message_id] = to_delete_emojis
            if len(reactions) > 1:
                react_roles.append("\n".join(reactions))
        if not react_roles:
            raise commands.BadArgument("There are no reaction roles set up here!")

        color = self.bot.main_color
        description = "\n\n".join(react_roles)
        embeds = []
        pages = paginate(description, delims=["\n\n", "\n"])
        base_embed = discord.Embed(color=color)
        base_embed.set_author(name="Reaction Roles", icon_url=ctx.guild.icon_url)
        for page in pages:
            embed = base_embed.copy()
            embed.description = page
            embeds.append(embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

        if to_delete_message_emojis:
            for message_id, emojis in to_delete_message_emojis.items():
                await self.bulk_delete_set_roles(discord.Object(message_id), emojis)
            await self.update_db()

    @reactrole.command(name="clear")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def reactrole_clear(self, ctx: commands.Context):
        """
        Clear all Reaction Role data.
        """
        confirm = await ctx.send(
            embed=discord.Embed(
                color=self.bot.main_color,
                description="Are you sure you want to clear all reaction role data?",
            ).set_footer(
                text=f"React with {YES_EMOJI} to proceed, {NO_EMOJI} to cancel"
            )
        )
        self.add_multiple_reactions(confirm, [YES_EMOJI, NO_EMOJI])

        def reaction_check(reaction, user):
            return (
                user.id == ctx.author.id
                and reaction.message.id == confirm.id
                and reaction.emoji in [YES_EMOJI, NO_EMOJI]
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", check=reaction_check, timeout=60
            )
        except asyncio.TimeoutError:
            raise commands.BadArgument("Time out. Action cancelled.")

        if reaction.emoji == YES_EMOJI:
            self.config["reactroles"]["message_cache"].clear()
            await ctx.send(embed=self.base_embed("Data cleared."))
            await self.update_db()
        else:
            raise commands.BadArgument("Action cancelled.")

    @commands.Cog.listener("on_raw_reaction_add")
    @commands.Cog.listener("on_raw_reaction_remove")
    async def on_raw_reaction_add_or_remove(
        self, payload: discord.RawReactionActionEvent
    ):
        if payload.guild_id is None:
            return

        if not self.config["reactroles"].get(
            "enabled"
        ) or not self._check_payload_to_cache(payload):
            return

        guild = self.bot.get_guild(payload.guild_id)
        if payload.event_type == "REACTION_ADD":
            member = payload.member
        else:
            member = guild.get_member(payload.user_id)

        if member is None or member.bot:
            return
        if not guild.me.guild_permissions.manage_roles:
            return

        message_config = self.config["reactroles"]["message_cache"].get(
            str(payload.message_id)
        )
        if not message_config:
            return

        emoji_str = self.emoji_string(payload.emoji)

        reacts = message_config.get("emoji_role_groups")
        if emoji_str not in reacts:
            return

        role_id = reacts.get(emoji_str)
        if not role_id:
            logger.debug("No matched role id.")
            return

        role = guild.get_role(role_id)
        if not role:
            logger.debug("Role was deleted.")
            await self.bulk_delete_set_roles(
                discord.Object(payload.message_id), [emoji_str]
            )
            await self.update_db()
            return

        if not my_role_hierarchy(guild, role):
            logger.debug("Role outranks me.")
            return

        rules = message_config.get("rules", ReactRules.NORMAL)
        if payload.event_type == "REACTION_ADD":
            if role not in member.roles:
                await member.add_roles(role, reason="Reaction role.")
            if rules == ReactRules.UNIQUE:
                to_remove = []
                for _, _id in reacts.items():
                    if _id == role_id:
                        continue
                    _role = guild.get_role(_id)
                    if _role is not None:
                        to_remove.append(_role)
                to_remove = [r for r in to_remove if r in member.roles]
                if not to_remove:
                    return
                await member.remove_roles(*to_remove, reason="Reaction role.")
        else:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role.")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return

        if not self._check_payload_to_cache(payload):
            return

        self._udpate_reactrole_cache(payload.message_id, True)
        await self.update_db()

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
        self, payload: discord.RawBulkMessageDeleteEvent
    ):
        if payload.guild_id is None:
            return
        update_db = False
        for message_id in payload.message_ids:
            if message_id in self.config["reactroles"]["message_cache"]:
                self._udpate_reactrole_cache(message_id, True)
                update_db = True

        if update_db:
            await self.update_db()

    # #################### #
    #       Targeter       #
    # #################### #

    @staticmethod
    def lookup(ctx, args):
        matched = ctx.guild.members
        passed = []
        # --- Go through each possible argument ---

        # -- Nicknames/Usernames --

        if args["nick"]:
            matched_here = []
            for user in matched:
                if any(
                    [
                        user.nick and piece.lower() in user.nick.lower()
                        for piece in args["nick"]
                    ]
                ):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["user"]:
            matched_here = []
            for user in matched:
                if any([piece.lower() in user.name.lower() for piece in args["user"]]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["name"]:
            matched_here = []
            for user in matched:
                if any(
                    [
                        piece.lower() in user.display_name.lower()
                        for piece in args["name"]
                    ]
                ):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-nick"]:
            matched_here = []
            for user in matched:
                if not any(
                    [
                        user.nick and piece.lower() in user.nick.lower()
                        for piece in args["not-nick"]
                    ]
                ):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-user"]:
            matched_here = []
            for user in matched:
                if not any(
                    [piece.lower() in user.name.lower() for piece in args["not-user"]]
                ):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-name"]:
            matched_here = []
            for user in matched:
                if not any(
                    [
                        piece.lower() in user.display_name.lower()
                        for piece in args["not-name"]
                    ]
                ):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["a-nick"]:
            matched_here = []
            for user in matched:
                if user.nick:
                    matched_here.append(user)
            passed.append(matched_here)

        if args["no-nick"]:
            matched_here = []
            for user in matched:
                if not user.nick:
                    matched_here.append(user)
            passed.append(matched_here)

        if args["disc"]:
            matched_here = []
            for user in matched:
                if any([disc == int(user.discriminator) for disc in args["disc"]]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["ndisc"]:
            matched_here = []
            for user in matched:
                if not any([disc == int(user.discriminator) for disc in args["ndisc"]]):
                    matched_here.append(user)
            passed.append(matched_here)

        # -- End Nicknames/Usernames --

        # -- Roles --

        if args["roles"]:
            matched_here = []
            for user in matched:
                ur = [role.id for role in user.roles]
                if all(role.id in ur for role in args["roles"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["any-role"]:
            matched_here = []
            for user in matched:
                ur = [role.id for role in user.roles]
                if any(role.id in ur for role in args["any-role"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-roles"]:
            matched_here = []
            for user in matched:
                ur = [role.id for role in user.roles]
                if not all(role.id in ur for role in args["not-roles"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-any-role"]:
            matched_here = []
            for user in matched:
                ur = [role.id for role in user.roles]
                if not any(role.id in ur for role in args["not-any-role"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["a-role"]:
            matched_here = []
            for user in matched:
                if len(user.roles) > 1:  # Since all members have the @everyone role
                    matched_here.append(user)
            passed.append(matched_here)

        if args["no-role"]:
            matched_here = []
            for user in matched:
                if len(user.roles) == 1:  # Since all members have the @everyone role
                    matched_here.append(user)
            passed.append(matched_here)

        # -- End Roles --

        # -- Dates --

        if args["joined-on"]:
            a = args["joined-on"]
            matched_here = []
            for user in matched:
                if a.tzinfo:
                    j = user.joined_at.replace(tzinfo=timezone.utc)
                else:
                    j = user.joined_at
                if j.date() == a.date():
                    matched_here.append(user)
                else:
                    pass
            passed.append(matched_here)

        if args["joined-be"]:
            a = args["joined-be"]
            matched_here = []
            for user in matched:
                if a.tzinfo:
                    j = user.joined_at.replace(tzinfo=timezone.utc)
                else:
                    j = user.joined_at
                if j < a:
                    matched_here.append(user)
                else:
                    pass
            passed.append(matched_here)

        if args["joined-af"]:
            a = args["joined-af"]
            matched_here = []
            for user in matched:
                if a.tzinfo:
                    j = user.joined_at.replace(tzinfo=timezone.utc)
                else:
                    j = user.joined_at
                if j > a:
                    matched_here.append(user)
                else:
                    pass
            passed.append(matched_here)

        if args["created-on"]:
            a = args["created-on"]
            matched_here = []
            for user in matched:
                if a.tzinfo:
                    c = user.created_at.replace(tzinfo=timezone.utc)
                else:
                    c = user.created_at
                if c.date() == a.date():
                    matched_here.append(user)
                else:
                    pass
            passed.append(matched_here)

        if args["created-be"]:
            a = args["created-be"]
            matched_here = []
            for user in matched:
                if a.tzinfo:
                    c = user.created_at.replace(tzinfo=timezone.utc)
                else:
                    c = user.created_at
                if c < a:
                    matched_here.append(user)
                else:
                    pass
            passed.append(matched_here)

        if args["created-af"]:
            a = args["created-af"]
            matched_here = []
            for user in matched:
                if a.tzinfo:
                    c = user.created_at.replace(tzinfo=timezone.utc)
                else:
                    c = user.created_at
                if c > a:
                    matched_here.append(user)
                else:
                    pass
            passed.append(matched_here)

        # -- End Dates --

        # -- Statuses / Activities --

        if args["status"]:
            matched_here = []
            statuses = [s for s in discord.Status if s.name.lower() in args["status"]]
            for user in matched:
                if user.status in statuses:
                    matched_here.append(user)
            passed.append(matched_here)

        if args["device"]:
            matched_here = []
            for user in matched:
                for d in args["device"]:
                    s = getattr(user, f"{d}_status")
                    if str(s) != "offline":
                        matched_here.append(user)
            passed.append(matched_here)

        if args["bots"]:
            matched_here = []
            for user in matched:
                if user.bot:
                    matched_here.append(user)
            passed.append(matched_here)

        if args["nbots"]:
            matched_here = []
            for user in matched:
                if not user.bot:
                    matched_here.append(user)
            passed.append(matched_here)

        if args["at"]:
            matched_here = []
            for user in matched:
                if user.activity and (user.activity.type in args["at"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["a"]:
            matched_here = []
            for user in matched:
                if user.activity and (
                    user.activity.name.lower() in [a.lower() for a in args["a"]]
                ):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["na"]:
            matched_here = []
            for user in matched:
                if not user.activity:
                    matched_here.append(user)
            passed.append(matched_here)

        if args["aa"]:
            matched_here = []
            for user in matched:
                if user.activity:
                    matched_here.append(user)
            passed.append(matched_here)

        # -- End Statuses / Activities --

        # -- Permissions --
        if args["perms"]:
            matched_here = []
            for user in matched:
                up = user.guild_permissions
                if all(getattr(up, perm) for perm in args["perms"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["any-perm"]:
            matched_here = []
            for user in matched:
                up = user.guild_permissions
                if any(getattr(up, perm) for perm in args["any-perm"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-perms"]:
            matched_here = []
            for user in matched:
                up = user.guild_permissions
                if not all(getattr(up, perm) for perm in args["not-perms"]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-any-perm"]:
            matched_here = []
            for user in matched:
                up = user.guild_permissions
                if not any(getattr(up, perm) for perm in args["not-any-perm"]):
                    matched_here.append(user)
            passed.append(matched_here)

        # --- End going through possible arguments ---
        try:
            all_passed = set(passed.pop())
        except IndexError:
            return []
        return all_passed.intersection(*passed)

    async def args_to_list(self, ctx: commands.Context, args: str):
        """
        Returns a list of members from the given args, which are
        expected to follow the style in the Args converter above.
        """
        args = await Args().convert(ctx, args)
        compact = functools.partial(self.lookup, ctx, args)
        matched = await self.bot.loop.run_in_executor(None, compact)
        if not matched:
            raise commands.BadArgument(
                f"No one was found with the given args.\nCheck out `{self.bot.prefix}target help` for an explanation."
            )
        return matched

    @commands.group(usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def target(self, ctx, *, args: Args):
        """
        Targets users based on the passed arguments.

        Run `{prefix}target help` to see a list of valid arguments.
        """
        async with ctx.typing():
            compact = functools.partial(self.lookup, ctx, args)
            matched = await self.bot.loop.run_in_executor(None, compact)

            if len(matched) != 0:
                color = self.bot.main_color
                string = " ".join([m.mention for m in matched])
                embed_list = []
                for page in paginate(string, delims=[" "], page_length=750):
                    embed = discord.Embed(
                        title=f"Targeting complete.  Found {len(matched)} matches.",
                        color=color,
                    )
                    embed.description = page
                    embed_list.append(embed)
                m = True
            else:
                embed = discord.Embed(
                    title="Targeting complete",
                    description=f"Found no matches.",
                    color=0xFF0000,
                )
                m = False
        if not m:
            await ctx.send(embed=embed)
        else:
            session = EmbedPaginatorSession(ctx, *embed_list)
            await session.run()

    @target.command(name="help")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def target_help(self, ctx):
        """
        Returns a menu that has a list of arguments you can pass to `target` command.
        """
        embed_list = []

        names = discord.Embed(
            title="Target Arguments - Names", color=self.bot.main_color
        )
        desc = (
            "`--nick <nickone> <nicktwo>` - Users must have one of the passed nicks in their nickname.  If they don't have a nickname, they will instantly be excluded.\n"
            "`--user <userone> <usertwo>` - Users must have one of the passed usernames in their real username.  This will not look at nicknames.\n"
            "`--name <nameone> <nametwo>` - Users must have one of the passed names in their username, and if they don't have one, their username.\n"
            "\n"
            "`--not-nick <nickone> <nicktwo>` - Users must not have one of the passed nicks in their nickname.  If they don't have a nickname, they will instantly be excluded.\n"
            "`--not-user <userone> <usertwo>` - Users must not have one of the passed usernames in their real username.  This will not look at nicknames.\n"
            "`--not-name <nameone> <nametwo>` - Users must not have one of the passed names in their username, and if they don't have one, their username.\n"
            "\n"
            "`--a-nick` - Users must have a nickname in the server.\n"
            "`--no-nick` - Users cannot have a nickname in the server."
        )
        names.description = desc
        names.set_footer(text="Target Arguments - Names")
        embed_list.append(names)

        roles = discord.Embed(
            title="Target Arguments - Roles", color=self.bot.main_color
        )
        desc = (
            "`--roles <roleone> <roletwo>` - Users must have all of the roles provided.\n"
            "`--any-role <roleone> <roletwo>` - Users must have at least one of the roles provided.\n"
            "`--a-role` - Users must have at least one role\n"
            "\n"
            "`--not-roles <roleone> <roletwo>` - Users cannot have all of the roles provided.\n"
            "`--not-any-role <roleone> <roletwo>` - Users cannot have any of the roles provided.\n"
            "`--no-role` - Users cannot have any roles."
        )
        roles.description = desc
        roles.set_footer(text="Target Arguments - Roles")
        embed_list.append(roles)

        status = discord.Embed(
            title="Target Arguments - Profile", color=self.bot.main_color
        )
        desc = (
            "`--status <offline> <online> <dnd> <idle>` - Users' status must have at least one of the statuses passed.\n"
            "`--device <mobile> <web> <desktop>` - Filters by their device statuses.  If they are not offline on any of the ones specified, they are included.\n"
            "`--only-bots` - Users must be a bot.\n"
            "`--no-bots` - Users cannot be a bot.\n"
            "\n"
            '`--activity "name of activity" "another one"` - Users\' activity must contain one of the activities passed.\n'
            "`--activity-type <playing> <streaming> <watching> <listening>` - Users' activity types must be one of the ones passed.\n"
            "`--an-activity` - Users must be in an activity.\n"
            "`--no-activity` - Users cannot be in an activity.\n"
        )
        status.description = desc
        status.set_footer(text="Target Arguments - Profile")
        embed_list.append(status)

        dates = discord.Embed(
            title="Target Arguments - Dates", color=self.bot.main_color
        )
        desc = (
            "`--joined-on YYYY MM DD` - Users must have joined on the day specified.\n"
            "`--joined-before YYYY MM DD` - Users must have joined before the day specified.  The day specified is not counted.\n"
            "`--joined-after YYYY MM DD` - Users must have joined after the day specified.  The day specified is not counted.\n"
            "\n"
            "`--created-on YYYY MM DD` - Users must have created their account on the day specified.\n"
            "`--created-before YYYY MM DD` - Users must have created their account before the day specified.  The day specified is not counted.\n"
            "`--created-after YYYY MM DD` - Users must have created their account after the day specified.  The day specified is not counted."
        )
        dates.description = desc
        dates.set_footer(text="Target Arguments - Dates")
        embed_list.append(dates)

        perms = discord.Embed(
            title="Target Arguments - Permissions", color=self.bot.main_color
        )
        desc = (
            "`--perms` - Users must have all of the permissions passed.\n"
            "`--any-perm` - Users must have at least one of the permissions passed.\n"
            "\n"
            "`--not-perms` - Users cannot have all of the permissions passed.\n"
            "`--not-any-perm` - Users cannot have any of the permissions passed.\n"
            "\n"
            f"Run `{self.bot.prefix}target permissions` to see a list of permissions that can be passed."
        )
        perms.description = desc
        perms.set_footer(text="Target Arguments - Permissions")
        embed_list.append(perms)

        special = discord.Embed(
            title="Target Arguments - Special Notes", color=self.bot.main_color
        )
        desc = (
            "`--format` - How to display results.  At the moment, defaults to `menu` for showing the results in Discord.\n"
            "\n"
            "If at any time you need to include quotes at the beginning or ending of something (such as a nickname or a role), "
            "include a slash `\\` right before it."
        )
        special.description = desc
        special.set_footer(text="Target Arguments - Special Notes")
        embed_list.append(special)

        session = EmbedPaginatorSession(ctx, *embed_list)
        await session.run()

    @target.command(name="permissions", aliases=["perms"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def target_permissions(self, ctx):
        """
        Returns a list of permissions that can be passed to `target` command.
        """
        perms = [p.replace("_", " ").title() for p in PERMS]
        embed = discord.Embed(title="Permissions that can be passed to Targeter")
        embed.description = human_join(perms, final=", and")
        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(RoleManager(bot))
