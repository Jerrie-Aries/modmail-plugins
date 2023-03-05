from __future__ import annotations

import asyncio
import functools
import json
from collections import defaultdict
from colorsys import rgb_to_hsv
from datetime import timezone
from pathlib import Path
from typing import Dict, List, Optional, Union, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import MISSING

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession
from core.utils import human_join


if TYPE_CHECKING:
    from .motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot
    from .core.types import ArgsParserRawData


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)

logger = getLogger(__name__)


# <!-- Developer -->
try:
    from discord.ext.modmail_utils import ConfirmView, Limit, humanize_roles, paginate
except ImportError as exc:
    required = __plugin_info__["cogs_required"][0]
    raise RuntimeError(
        f"`modmail_utils` package is required for {__plugin_name__} plugin to function.\n"
        f"Install {required} plugin to resolve this issue."
    ) from exc

from .core.checks import is_allowed_by_role_hierarchy, my_role_hierarchy
from .core.config import RoleManagerConfig
from .core.converters import (
    Args,
    AssignableRole,
    ObjectConverter,
    PERMS,
)
from .core.models import AutoRoleManager, ReactionRoleManager, ReactRules, TriggerType
from .core.utils import (
    bind_string_format,
    get_audit_reason,
    guild_roughly_chunked,
)
from .core.views import ReactionRoleCreationPanel, ReactionRoleView


# <!-- ----- -->


# these probably will be used in couple of places so we define them outside
_type_session = (
    "Choose a trigger type for this reaction roles.\n\n"
    "__**Available options:**__\n"
    "- `Reaction` - Legacy reaction with emojis.\n"
    "- `Interaction` - Interaction with new Discord buttons.\n"
)
_rule_session = (
    "What is the rule for this reaction roles you want to set?\n\n"
    "__**Available options:**__\n"
    "- `Normal` - Allow users to have multiple roles in group.\n"
    "- `Unique` - Remove existing role when assigning another role in group.\n"
)
_bind_session = (
    "__**Buttons:**__\n"
    "- **Add** - Add a role-button or role-emoji bind to internal list. The bind can only be added if "
    "there were **no errors** when the values were submitted.\n"
    "- **Set** - Set or edit the current set values.\n"
    "- **Clear** - Reset all binds.\n\n"
    "__**Available fields:**__\n"
    "- `Role` - The role to bind to the emoji or button. May be a role ID, name, or format of `<@&roleid>`.\n"
    "- `Emoji` - Emoji to bind (reaction), or shown on the button (interaction). May be a unicode emoji, "
    "format of `:name:`, `<:name:id>` or `<a:name:id>` (animated emoji).\n"
    f"- `Label` - Button label (only available for button). Must not exceed {Limit.button_label} characters.\n"
)


class RoleManager(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot) -> None:
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.db: AsyncIOMotorCollection = bot.api.get_plugin_partition(self)

        self.config = RoleManagerConfig(self, self.db)
        self.reactrole_manager: ReactionRoleManager = MISSING
        self.autorole_manager: AutoRoleManager = MISSING

    async def cog_load(self) -> None:
        """
        Initial tasks when loading the cog.
        """
        self.bot.loop.create_task(self.initialize())

    async def cog_unload(self) -> None:
        for entry in self.reactrole_manager.entries:
            entry.view.stop()
        self.reactrole_manager.entries.clear()

    async def initialize(self) -> None:
        await self.bot.wait_for_connected()
        await self.populate_config()

    async def populate_config(self):
        data = await self.config.fetch()
        self.autorole_manager = AutoRoleManager(self, data=data.pop("autoroles"))
        reactroles = data.pop("reactroles")
        self.reactrole_manager = ReactionRoleManager(self, data=reactroles)

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
    def get_hsv(role: discord.Role):
        return rgb_to_hsv(*role.color.to_rgb())

    def base_embed(self, description: str) -> discord.Embed:
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
        Show a list of members in a role.

        `role` may be a role ID, mention, or name.
        """
        if not guild_roughly_chunked(role.guild) and self.bot.intents.members:
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

            embed.set_thumbnail(url=f"https://placehold.it/100/{str(role.color)[1:]}?text=+")

            footer_text = f"Found {len(member_list)} " + ("member" if len(member_list) == 1 else "members")
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
        Show a list of server's roles, ordered by color.
        """
        roles = defaultdict(list)
        for r in ctx.guild.roles:
            roles[str(r.color)].append(r)
        roles = dict(sorted(roles.items(), key=lambda v: self.get_hsv(v[1][0])))

        lines = [f"**{color}**\n{' '.join(r.mention for r in rs)}\n" for color, rs in roles.items()]
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
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def role_create(
        self,
        ctx: commands.Context,
        color: Optional[discord.Color] = discord.Color.default(),
        hoist: Optional[bool] = False,
        *,
        name: str = None,
    ):
        """
        Create a role.

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
            return await ctx.send("This server has reached the maximum role limit (250).")

        role = await ctx.guild.create_role(name=name, colour=color, hoist=hoist)
        await ctx.send(f"**{role}** created!", embed=await self.get_role_info(role))

    @role_.command(name="color")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_color(self, ctx: commands.Context, role: discord.Role, color: discord.Color):
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
    async def role_add(self, ctx: commands.Context, member: discord.Member, *, role: AssignableRole):
        """
        Add a role to a member.

        `member` may be a member ID, mention, or name.
        `role` may be a role ID, mention, or name.
        """
        if role in member.roles:
            await ctx.send(f"**{member}** already has the role **{role}**. Maybe try removing it instead.")
            return
        reason = get_audit_reason(ctx.author)
        await member.add_roles(role, reason=reason)
        await ctx.send(f"Added **{role.name}** to **{member}**.")

    @role_.command(name="remove")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def role_remove(self, ctx: commands.Context, member: discord.Member, *, role: AssignableRole):
        """
        Remove a role from a member.

        `member` may be a member ID, mention, or name.
        `role` may be a role ID, mention, or name.
        """
        if role not in member.roles:
            await ctx.send(f"**{member}** doesn't have the role **{role}**. Maybe try adding it instead.")
            return
        reason = get_audit_reason(ctx.author)
        await member.remove_roles(role, reason=reason)
        await ctx.send(f"Removed **{role.name}** from **{member}**.")

    @role_.command(require_var_positional=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def addmulti(self, ctx: commands.Context, role: AssignableRole, *members: discord.Member):
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
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def removemulti(self, ctx: commands.Context, role: AssignableRole, *members: discord.Member):
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
    async def multirole_add(self, ctx: commands.Context, member: discord.Member, *roles: AssignableRole):
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
            allowed = await is_allowed_by_role_hierarchy(self.bot, ctx.me, ctx.author, role)
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
            msg.append(f"You do not have permission to assign the roles {humanize_roles(not_allowed)}.")
        await ctx.send("\n".join(msg))

    @multirole.command(name="remove", require_var_positional=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def multirole_remove(self, ctx: commands.Context, member: discord.Member, *roles: AssignableRole):
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
            allowed = await is_allowed_by_role_hierarchy(self.bot, ctx.me, ctx.author, role)
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
            msg.append(f"You do not have permission to assign the roles {humanize_roles(not_allowed)}.")
        await ctx.send("\n".join(msg))

    @role_.command(name="all")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def role_all(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Add a role to all members of the server.

        `role` may be a role ID, mention, or name.
        """
        await self.super_massrole(ctx, ctx.guild.members, role)

    @role_.command(name="rall", aliases=["removeall"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def role_rall(self, ctx: commands.Context, *, role: AssignableRole):
        """
        Remove a role from all members of the server.

        `role` may be a role ID, mention, or name.
        """
        member_list = self.get_member_list(ctx.guild.members, role, False)
        await self.super_massrole(ctx, member_list, role, "No one on the server has this role.", False)

    @role_.command(name="humans")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
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
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
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
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
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
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
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
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
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
            list(target_role.members),
            add_role,
            f"Every member of **{target_role}** has this role.",
        )

    @role_.command(name="rin")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
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
            list(target_role.members),
            remove_role,
            f"No one in **{target_role}** has this role.",
            False,
        )

    @role_.group(name="target", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def role_target(self, ctx: commands.Context):
        """
        Modify roles using 'targeting' args.

        An explanation of Targeter and test commands to preview the members affected can be found with `{prefix}target`.
        """
        await ctx.send_help(ctx.command)

    @role_target.command(name="add")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def target_add(self, ctx: commands.Context, role: AssignableRole, *, args: str):
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
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def target_remove(self, ctx: commands.Context, role: AssignableRole, *, args: str):
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
        await ctx.send(f"Beginning to {verb} **{role.name}** {word} **{len(member_list)}** members.")
        async with ctx.typing():
            result = await self.massrole(member_list, [role], get_audit_reason(ctx.author), adding)
            result_text = (
                f"{verb.title()[:5]}ed **{role.name}** {word} **{len(result['completed'])}** members."
            )
            if result["skipped"]:
                result_text += f"\nSkipped {verb[:5]}ing roles for **{len(result['skipped'])}** members."
            if result["failed"]:
                result_text += f"\nFailed {verb[:5]}ing roles for **{len(result['failed'])}** members."
        await ctx.send(result_text)

    @staticmethod
    def get_member_list(
        members: List[discord.Member], role: discord.Role, adding: bool = True
    ) -> List[discord.Member]:
        if adding:
            members = [member for member in members if role not in member.roles]
        else:
            members = [member for member in members if role in member.roles]
        return members

    @staticmethod
    async def massrole(
        members: List[discord.Member],
        roles: List[discord.Role],
        reason: str,
        adding: bool = True,
    ) -> Dict[str, List[discord.Member]]:
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
                        logger.exception(f"Failed to remove roles from {member}", exc_info=e)
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
        manager = self.autorole_manager
        if role.id in manager.roles:
            raise commands.BadArgument(f'Role "{role}" is already in autorole list.')

        manager.roles.append(role.id)
        await manager.update()
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"On member join, role {role.mention} will be added to the member.",
        )
        await ctx.send(embed=embed)

    @_autorole.command(name="remove", aliases=["delete"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def autorole_remove(self, ctx: commands.Context, *, role: Union[AssignableRole, int]):
        """
        Remove an autorole.
        """
        manager = self.autorole_manager

        if isinstance(role, discord.Role):
            role_id = role.id
        else:
            # to support removing id of role that already got deleted from server
            role_id = role

        if role_id not in manager.roles:
            raise commands.BadArgument(f'Role "{role}" is not in autorole list.')

        manager.roles.remove(role_id)
        await manager.update()

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
        manager = self.autorole_manager
        if mode is None:
            em = discord.Embed(
                color=self.bot.main_color,
                description=f"The autorole is currently set to `{manager.is_enabled()}`.",
            )
            return await ctx.send(embed=em)

        try:
            if mode:
                manager.enable()
            else:
                manager.disable()
        except ValueError as exc:
            raise commands.BadArgument(str(exc)) from exc

        await manager.update()

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
        manager = self.autorole_manager
        if not manager.roles:
            raise commands.BadArgument("There are no roles set for the autorole.")

        autorole_roles = []
        for role_id in manager.roles:
            role = ctx.guild.get_role(role_id)
            if role is None:
                # in case the role already got deleted from server, show anyway
                autorole_roles.append(role_id)
                continue
            autorole_roles.append(role.mention)
        if not autorole_roles:
            raise commands.BadArgument("There are no roles set for the autorole on this server.")

        embed = discord.Embed(title="Autorole", color=self.bot.main_color, description="")
        for i, role_fmt in enumerate(autorole_roles, start=1):
            embed.description += f"{i}. {role_fmt}\n"

        embed.set_footer(
            text=f"Total: {len(autorole_roles)}" + (" role" if len(autorole_roles) == 1 else "roles")
        )
        await ctx.send(embed=embed)

    @_autorole.command(name="clear")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def autorole_clear(self, ctx: commands.Context):
        """
        Clear the autorole data.
        """
        view = ConfirmView(bot=self.bot, user=ctx.author)
        view.message = await ctx.send(
            embed=self.base_embed(description="Are you sure you want to clear all autorole data?"),
            view=view,
        )

        await view.wait()

        if view.value is None:
            msg = "Time out. Action cancelled."
        elif view.value:
            manager = self.autorole_manager
            manager.roles.clear()
            if manager.is_enabled():
                manager.disable()
            await manager.update()
            msg = "Data cleared."
        else:
            msg = "Action cancelled."

        await ctx.send(msg)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.autorole_manager.handle_member_join(member)

    # ###################### #
    #     REACTION ROLES     #
    # ###################### #

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def reactrole(self, ctx: commands.Context):
        """
        Base command for Reaction Roles management.
        """
        await ctx.send_help(ctx.command)

    @reactrole.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def reactrole_enable(self, ctx: commands.Context, mode: bool = None):
        """
        Toggle reaction roles on or off.

        Run this command without argument to get the current set configuration.
        """
        manager = self.reactrole_manager
        if mode is None:
            em = discord.Embed(
                color=self.bot.main_color,
                description=f"The reaction roles is currently set to `{manager.is_enabled()}`.",
            )
            return await ctx.send(embed=em)

        try:
            if mode:
                manager.enable()
            else:
                manager.disable()
        except ValueError as exc:
            raise commands.BadArgument(str(exc)) from exc

        await manager.update()

        embed = discord.Embed(
            color=self.bot.main_color,
            description=("Enabled " if mode else "Disabled ") + "the reaction roles.",
        )
        await ctx.send(embed=embed)

    @reactrole.command(name="create")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def reactrole_create(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        *,
        title: str = None,
    ):
        """
        Create a new reaction roles menu.

        `channel` if specified, may be a channel ID, mention, or name.
        If not specified, will be the channel where the command is ran from.
        `title` is the title for the embed. Must not exceed 256 characters.

        __**Notes:**__
        - This command will initiate the button and text input interactive session.
        """
        done_session = "The reaction roles {} has been posted."  # format hyperlink message.jump_url
        input_sessions = [
            {"key": "type", "description": _type_session},
            {"key": "rule", "description": _rule_session},
            {"key": "bind", "description": _bind_session},
            {"key": "done", "description": done_session},
        ]
        reactrole = self.reactrole_manager.create_new()
        view = ReactionRoleCreationPanel(ctx, reactrole, input_sessions=input_sessions)
        if title is None:
            title = "Reaction Roles"
        view.output_embed = discord.Embed(
            title=title,
            color=self.bot.main_color,
        )
        view.preview_description = "Press the following buttons to receive the corresponding roles:\n\n"

        embed = discord.Embed(
            title="Reaction Roles Creation",
            color=self.bot.main_color,
            description=view.session_description,
        )
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if not view.value:
            return

        if channel is None:
            channel = ctx.channel
        reactrole.message = message = await channel.send(embed=view.output_embed)
        trigger_type = reactrole.trigger_type
        self.reactrole_manager.add(reactrole)
        if trigger_type == TriggerType.REACTION:
            for bind in reactrole.binds:
                await message.add_reaction(bind.emoji)
                await asyncio.sleep(0.2)
        else:
            output_view = ReactionRoleView(self, message, model=reactrole)
            await message.edit(view=output_view)

        hyperlink = f"[message]({message.jump_url})"
        description = view.session_description.format(hyperlink)
        embed = view.message.embeds[0]
        embed.description = description
        await view.message.edit(embed=embed, view=view)
        await reactrole.manager.update()

    @reactrole.command(name="edit", aliases=["add"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def reactrole_edit(self, ctx: commands.Context, message: discord.Message):
        """
        Edit by adding role-button or role-emoji binds to a message specified.
        This can be used if you want to create a reaction roles menu on a pre-existing message.

        `message` may be a message ID, message link, or format of `channelid-messageid`.

        __**Notes:**__
        - This command will initiate the button and text input interactive session.
        - Buttons can only be added on messages that were sent from this bot.
        """
        new = False
        reactrole = self.reactrole_manager.find_entry(message.id)
        input_sessions = []
        if reactrole is None:
            new = True
            reactrole = self.reactrole_manager.create_new(message=message)
            if message.author.id == self.bot.user.id:
                input_sessions.append({"key": "type", "description": _type_session})
            input_sessions.append({"key": "rule", "description": _rule_session})

        done_session = "Updated and linked {} to reaction roles {}."
        abs_sessions = [
            {"key": "bind", "description": _bind_session},
            {"key": "done", "description": done_session},
        ]
        input_sessions.extend(abs_sessions)
        view = ReactionRoleCreationPanel(
            ctx,
            reactrole,
            input_sessions=input_sessions,
        )
        view.preview_description = "__**Note:**__\nThis embed is not from the original message.\n\n"
        embed = discord.Embed(
            title="Reaction Roles Add",
            color=self.bot.main_color,
            description=view.session_description,
        )
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if not view.value:
            return

        roles_fmt = ", ".join(f"<@&{bind.role.id}>" for bind in reactrole.binds)
        hyperlink = f"[message]({message.jump_url})"
        description = view.session_description.format(roles_fmt, hyperlink)
        embed = view.message.embeds[0]
        embed.description = description
        await view.message.edit(embed=embed, view=view)

        if reactrole.trigger_type == TriggerType.REACTION:
            reactions = [str(r) for r in message.reactions]
            for bind in reactrole.binds:
                if str(bind.emoji) in reactions:
                    continue
                await message.add_reaction(bind.emoji)
                await asyncio.sleep(0.2)
        else:
            if new:
                ReactionRoleView(self, message, model=reactrole)
            reactrole.view.rebind()
            await reactrole.view.update_view()

        if new:
            self.reactrole_manager.add(reactrole)
        await reactrole.manager.update()

    @reactrole.command(name="rule")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def reactrole_rule(
        self,
        ctx: commands.Context,
        message: Union[discord.Message, ObjectConverter, int],
        rules: str.upper = None,
    ):
        """
        Set a rule for an existing reaction roles message.

        `message` may be a message ID, message link, or format of `channelid-messageid`.

        Available options for `rule`:
        `Normal` - Allow users to have multiple roles in group.
        `Unique` - Remove existing role when assigning another role in group.

        Leave the `rule` empty to get the current set configuration.
        """
        if isinstance(message, int):
            message_id = message
        else:
            message_id = message.id

        reactrole = self.reactrole_manager.find_entry(message_id)
        if reactrole is None or not reactrole.binds:
            raise commands.BadArgument("There are no reaction roles set up for that message.")

        old_rules = reactrole.rules
        if rules is None:
            return await ctx.send(
                embed=self.base_embed(
                    f"Reaction role rules for that message is currently set to `{old_rules}`."
                )
            )

        if rules not in (ReactRules.NORMAL, ReactRules.UNIQUE):
            raise commands.BadArgument(f"`{rules}` is not a valid option for reaction role's rule.")

        if rules == old_rules:
            raise commands.BadArgument(
                f"Reaction role's rule for that message is already set to `{old_rules}`."
            )

        reactrole.rules = rules
        await reactrole.manager.update()
        await ctx.send(
            embed=self.base_embed(f"Reaction role's rule for that message is now set to `{rules}`.")
        )

    @reactrole.group(name="delete", aliases=["remove"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def reactrole_delete(
        self,
        ctx: commands.Context,
        message: Union[discord.Message, ObjectConverter, int],
    ):
        """
        Delete reaction roles data from a message.
        If the set trigger type is `Interaction` (i.e. buttons), the buttons will be cleared from the message.

        `message` may be a message ID, message link, or format of `channelid-messageid`.
        """
        if isinstance(message, int):
            message_id = message
        else:
            message_id = message.id

        reactrole = self.reactrole_manager.find_entry(message_id)
        if reactrole is None or not reactrole.binds:
            raise commands.BadArgument("There are no reaction roles set up for that message.")

        view = ConfirmView(bot=self.bot, user=ctx.author)
        view.message = await ctx.send(
            embed=self.base_embed("Are you sure you want to remove all reaction roles for that message?"),
            view=view,
        )

        await view.wait()

        if not view.value:
            # cancelled or timed out
            raise commands.BadArgument("Action cancelled.")

        message = reactrole.message
        self.reactrole_manager.remove(message.id)
        if reactrole.view:
            await message.edit(view=None)
        await reactrole.manager.update()
        await ctx.send(embed=self.base_embed("Reaction roles cleared for that message."))

    @reactrole_delete.command(name="bind", aliases=["link"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def delete_bind(
        self,
        ctx: commands.Context,
        message: Union[discord.Message, ObjectConverter, int],
        *,
        role: discord.Role,
    ):
        """
        Delete a role-button bind from reaction roles message.

        `message` may be a message ID, message link, or format of `channelid-messageid`.
        `role` may be a role ID, mention, or name.
        """
        if isinstance(message, int):
            message_id = message
        else:
            message_id = message.id

        reactrole = self.reactrole_manager.find_entry(message_id)
        if reactrole is None:
            raise commands.BadArgument("There are no reaction roles set up for that message.")

        # we just do this manually here
        for bind in reactrole.binds:
            if bind.role == role:
                reactrole.binds.remove(bind)
                break
        else:
            raise commands.BadArgument(
                f"Role {role.mention} is not binded to any button or emoji on that message."
            )
        if reactrole.view:
            await reactrole.view.update_view()
        await reactrole.manager.update()
        await ctx.send(
            embed=self.base_embed("That role bind to a button or emoji on that message is now deleted.")
        )

    @reactrole.command(name="list")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def react_list(self, ctx: commands.Context):
        """
        Show a list of reaction roles set on this server.
        """
        manager = self.reactrole_manager
        manager.resolve_broken()
        entries = manager.entries
        if not entries:
            raise commands.BadArgument("There are no reaction roles set up here!")

        react_roles = []
        for index, entry in enumerate(entries, start=1):
            message = entry.message
            rules = entry.rules
            trigger_type = entry.trigger_type
            output = [f"[Reaction Role #{index}]({message.jump_url}) - `{trigger_type}`, `{rules}`"]
            for bind in entry.binds:
                emoji = bind.emoji or getattr(bind.button, "emoji", None)
                if trigger_type == TriggerType.INTERACTION:
                    label = bind.button.label
                else:
                    label = None
                output.append(
                    f"- {bind_string_format(str(emoji) if emoji else None, label, str(bind.role.id))}"
                )

            if len(output) == 1:
                output.append("- `None`")
            react_roles.append("\n".join(output))
        if not react_roles:
            raise commands.BadArgument("There are no reaction roles set up here!")

        color = self.bot.main_color
        description = "\n\n".join(react_roles)
        embeds = []
        pages = paginate(description, delims=["\n\n", "\n"])
        base_embed = discord.Embed(color=color)
        base_embed.set_author(name="Reaction Roles", icon_url=ctx.guild.icon.url)
        for page in pages:
            embed = base_embed.copy()
            embed.description = page
            embeds.append(embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @reactrole.command(name="refresh")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def reactrole_refresh(self, ctx: commands.Context):
        """
        Refresh all reaction roles data.
        This will look for non-existing linked roles and remove the necessary binds linked to the roles.
        This will also remove buttons with broken data from the reaction roles messages.

        __**Notes:**__
        - The reaction roles binds are considered broken if they are linked to deleted roles.
        """
        manager = self.reactrole_manager
        manager.resolve_broken()
        entries = manager.entries
        if not entries:
            raise commands.BadArgument("There are no reaction roles set up here!")

        to_remove = {}
        for entry in entries:
            roles = []
            for bind in entry.binds:
                role_id = bind.role.id
                if entry.channel.guild.get_role(role_id) is None:
                    roles.append(role_id)
            if roles:
                to_remove[entry] = roles
        embed = discord.Embed(
            title="Refresh",
            color=self.bot.main_color,
        )
        if to_remove:
            n = 1
            output = "__**Resolved:**__\n"
            for entry, roles in to_remove.items():
                output += f"{n}. [Message]({entry.message.jump_url}) - " + ", ".join(
                    f"`{role}`" for role in roles
                )
                output += "\n"
                entry.delete_set_roles(roles)
                if entry.view:
                    await entry.view.update_view()
                n += 1
            await manager.update()
        else:
            output = "No broken data or components."
        embed.description = output
        await ctx.send(embed=embed)

    @reactrole.command(name="repair")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def reactrole_repair(self, ctx: commands.Context):
        """
        Repair unresolved reaction roles data.

        __**Notes:**__
        - Usually the data cannot be resolved due to the bot is missing permissions, or the message or channel was deleted.
        """
        manager = self.reactrole_manager
        unresolved = manager.get_unresolved()
        if not unresolved:
            raise commands.BadArgument("There is no unresolved data.")
        total = len(unresolved)
        fixed = manager.resolve_broken()
        embed = discord.Embed(color=self.bot.main_color, description=f"Fixed {fixed}/{total} broken data.")
        if fixed < total:
            unresolved = manager.get_unresolved()
            embed.add_field(
                name="Unresolved", value="\n".join(f"- {message_id}" for message_id in unresolved.keys())
            )
        await ctx.send(embed=embed)

    @reactrole.command(name="clear")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def reactrole_clear(self, ctx: commands.Context):
        """
        Clear all reaction roles data.
        This will wipe all reaction roles data from the database, and the buttons attached to the messages will be removed.

        This operation **cannot** be undone.
        """
        view = ConfirmView(bot=self.bot, user=ctx.author)
        view.message = await ctx.send(
            embed=self.base_embed("Are you sure you want to clear all reaction role data?"),
            view=view,
        )

        await view.wait()

        if not view.value:
            # cancelled or timed out
            raise commands.BadArgument("Action cancelled.")
        for entry in self.reactrole_manager.entries:
            if entry.view:
                entry.view.stop()
                await entry.message.edit(view=None)
        self.reactrole_manager.entries.clear()
        await self.reactrole_manager.update()
        await ctx.send(embed=self.base_embed("Data cleared."))

    @commands.Cog.listener("on_raw_reaction_add")
    @commands.Cog.listener("on_raw_reaction_remove")
    async def on_raw_reaction_add_or_remove(self, payload: discord.RawReactionActionEvent):
        manager = self.reactrole_manager
        if not manager.is_enabled():
            return
        reactrole = manager.find_entry(payload.message_id)
        if reactrole is None:
            return
        await reactrole.handle_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return

        if not self.reactrole_manager.find_entry(payload.message_id):
            return

        self.reactrole_manager.remove(payload.message_id)
        await self.reactrole_manager.update()

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        if payload.guild_id is None:
            return
        update_db = False
        for message_id in payload.message_ids:
            reactrole = self.reactrole_manager.find_entry(message_id)
            if reactrole:
                self.reactrole_manager.entries.remove(reactrole)
                update_db = True

        if update_db:
            await self.reactrole_manager.update()

    # #################### #
    #       Targeter       #
    # #################### #

    @staticmethod
    def lookup(ctx: commands.Context, args: ArgsParserRawData):
        matched: List[discord.Member] = ctx.guild.members
        passed = []
        # --- Go through each possible argument ---

        # -- Nicknames/Usernames --

        if args["nick"]:
            matched_here = []
            for user in matched:
                if any([user.nick and piece.lower() in user.nick.lower() for piece in args["nick"]]):
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
                if any([piece.lower() in user.display_name.lower() for piece in args["name"]]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-nick"]:
            matched_here = []
            for user in matched:
                if not any([user.nick and piece.lower() in user.nick.lower() for piece in args["not-nick"]]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-user"]:
            matched_here = []
            for user in matched:
                if not any([piece.lower() in user.name.lower() for piece in args["not-user"]]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-name"]:
            matched_here = []
            for user in matched:
                if not any([piece.lower() in user.display_name.lower() for piece in args["not-name"]]):
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

        if args["discrim"]:
            matched_here = []
            for user in matched:
                if any([disc == int(user.discriminator) for disc in args["discrim"]]):
                    matched_here.append(user)
            passed.append(matched_here)

        if args["not-discrim"]:
            matched_here = []
            for user in matched:
                if not any([disc == int(user.discriminator) for disc in args["not-discrim"]]):
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
                    j = user.joined_at.replace(tzinfo=None)
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
                    j = user.joined_at.replace(tzinfo=None)
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
                    j = user.joined_at.replace(tzinfo=None)
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
                    c = user.created_at.replace(tzinfo=None)
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
                    c = user.created_at.replace(tzinfo=None)
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
                    c = user.created_at.replace(tzinfo=None)
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
                if user.activity and (user.activity.name.lower() in [a.lower() for a in args["a"]]):
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
    async def target(self, ctx: commands.Context, *, args: Args):
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
                    description="Found no matches.",
                    color=self.bot.error_color,
                )
                m = False
        if not m:
            await ctx.send(embed=embed)
        else:
            session = EmbedPaginatorSession(ctx, *embed_list)
            await session.run()

    @target.command(name="help")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def target_help(self, ctx: commands.Context):
        """
        Returns a menu that has a list of arguments you can pass to `target` command.
        """
        embed_list = []

        names = discord.Embed(title="Target Arguments - Names", color=self.bot.main_color)
        desc = (
            "`--nick <nickone> <nicktwo>` - Users must have one of the passed nicks in their nickname. "
            "If they don't have a nickname, they will instantly be excluded.\n"
            "`--user <userone> <usertwo>` - Users must have one of the passed usernames in their real username. "
            "This will not look at nicknames.\n"
            "`--name <nameone> <nametwo>` - Users must have one of the passed names in their nickname, and if they don't have one, their username.\n"
            "`--discrim <discrimone> <discrimtwo>` - Users must have one of the passed discriminators in their name.\n"
            "\n"
            "`--not-nick <nickone> <nicktwo>` - Users must not have one of the passed nicks in their nickname. "
            "If they don't have a nickname, they will instantly be excluded.\n"
            "`--not-user <userone> <usertwo>` - Users must not have one of the passed usernames in their real username. "
            "This will not look at nicknames.\n"
            "`--not-name <nameone> <nametwo>` - Users must not have one of the passed names in their username, and if they don't have one, their username.\n"
            "`--not-discrim <discrimone> <discrimtwo>` - Users must not have one of the passed discriminators in their name.\n"
            "\n"
            "`--a-nick` - Users must have a nickname in the server.\n"
            "`--no-nick` - Users cannot have a nickname in the server."
        )
        names.description = desc
        names.set_footer(text="Target Arguments - Names")
        embed_list.append(names)

        roles = discord.Embed(title="Target Arguments - Roles", color=self.bot.main_color)
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

        status = discord.Embed(title="Target Arguments - Profile", color=self.bot.main_color)
        desc = (
            "`--status <offline> <online> <dnd> <idle>` - Users' status must have at least one of the statuses passed.\n"
            "`--device <mobile> <web> <desktop>` - Filters by their device statuses. "
            "If they are not offline on any of the ones specified, they are included.\n"
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

        dates = discord.Embed(title="Target Arguments - Dates", color=self.bot.main_color)
        desc = (
            "`--joined-on YYYY MM DD` - Users must have joined on the day specified.\n"
            "`--joined-before YYYY MM DD` - Users must have joined before the day specified. The day specified is not counted.\n"
            "`--joined-after YYYY MM DD` - Users must have joined after the day specified. The day specified is not counted.\n"
            "\n"
            "`--created-on YYYY MM DD` - Users must have created their account on the day specified.\n"
            "`--created-before YYYY MM DD` - Users must have created their account before the day specified. The day specified is not counted.\n"
            "`--created-after YYYY MM DD` - Users must have created their account after the day specified. The day specified is not counted."
        )
        dates.description = desc
        dates.set_footer(text="Target Arguments - Dates")
        embed_list.append(dates)

        perms = discord.Embed(title="Target Arguments - Permissions", color=self.bot.main_color)
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

        special = discord.Embed(title="Target Arguments - Special Notes", color=self.bot.main_color)
        desc = (
            "`--format` - How to display results. At the moment, defaults to `menu` for showing the results in Discord.\n"
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
    async def target_permissions(self, ctx: commands.Context):
        """
        Returns a list of permissions that can be passed to `target` command.
        """
        perms = [p.replace("_", " ").title() for p in PERMS]
        embed = discord.Embed(title="Permissions that can be passed to Targeter")
        embed.description = human_join(perms, final=", and")
        await ctx.send(embed=embed)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(RoleManager(bot))
