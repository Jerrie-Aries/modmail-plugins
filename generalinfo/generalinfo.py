from __future__ import annotations

import json
import platform
import sys
import unicodedata
from pathlib import Path
from typing import Union, TYPE_CHECKING

import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel
from core.paginator import EmbedPaginatorSession, MessagePaginatorSession

from .core.utils import code_block, plural


if TYPE_CHECKING:
    from bot import ModmailBot


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


verif = {
    "none": "0 - None",
    "low": "1 - Low",
    "medium": "2 - Medium",
    "high": "3 - High",
    "extreme": "4 - Extreme",
}

# These are subject to arbitrary change by Discord.
# Go to: https://discord.com/developers/docs/resources/guild#guild-object-guild-features
features = {
    "ANIMATED_BANNER": "Animated Banner",
    "ANIMATED_ICON": "Animated Icon",
    "AUTO_MODERATION": "Auto Moderation",
    "BANNER": "Banner Image",
    "COMMERCE": "Commerce",
    "COMMUNITY": "Community",
    "CREATOR_MONETIZABLE_PROVISIONAL": "Creator Monetization",
    "CREATOR_STORE_PAGE": "Creator Store Page",
    "DEVELOPER_SUPPORT_SERVER": "Developer Supoort Server",
    "DISCOVERABLE": "Server Discovery",
    "FEATURABLE": "Featurable",
    "INVITES_DISABLED": "Invites Disabled",
    "INVITE_SPLASH": "Splash Invite",
    "MEMBER_LIST_DISABLED": "Member List Disabled",
    "MEMBER_VERIFICATION_GATE_ENABLED": "Member Verification Gate",
    "MORE_EMOJI": "More Emojis",
    "MORE_STICKERS": "More Stickers",
    "NEWS": "News Channels",
    "PARTNERED": "Partnered",
    "PREVIEW_ENABLED": "Preview",
    "PUBLIC_DISABLED": "Public Disabled",
    "RAID_ALERTS_DISABLED": "Raid Alerts Disabled",
    "ROLE_ICONS": "Role Icons",
    "ROLE_SUBSCRIPTIONS_AVAILABLE_FOR_PURCHASE": "Role Subscription Purchase",
    "ROLE_SUBSCRIPTIONS_ENABLED": "Role Subscription",
    "TICKETED_EVENTS_ENABLED": "Ticketed Events",
    "VANITY_URL": "Vanity URL",
    "VERIFIED": "Verified",
    "VIP_REGIONS": "VIP Voice Servers",
    "WELCOME_SCREEN_ENABLED": "Welcome Screen",
}


def _size(num: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
        if abs(num) < 1024.0:
            return "{0:.1f}{1}".format(num, unit)
        num /= 1024.0
    return "{0:.1f}{1}".format(num, "YB")


def _bitsize(num: Union[int, float]) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
        if abs(num) < 1000.0:
            return "{0:.1f}{1}".format(num, unit)
        num /= 1000.0
    return "{0:.1f}{1}".format(num, "YB")


class GeneralInfo(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def botinfo(self, ctx: commands.Context):
        """
        Information of this Modmail bot.
        """
        bot_me = ctx.me

        embed = discord.Embed(title="Bot info", color=bot_me.color)
        embed.set_author(name=f"{bot_me}")
        embed.add_field(name="Prefix:", value=f"`{self.bot.prefix}` or {self.bot.user.mention}")
        embed.add_field(
            name="Created:",
            value=(
                f"{discord.utils.format_dt(self.bot.user.created_at, 'F')}\n"
                f"{discord.utils.format_dt(self.bot.user.created_at, 'R')}"
            ),
        )
        embed.add_field(name="Latency:", value=code_block(f"{self.bot.latency * 1000:.2f} ms", "py"))
        embed.add_field(name="Uptime:", value=code_block(self.bot.uptime, "cs"))
        v = sys.version_info
        python_version = "{0.major}.{0.minor}.{0.micro} {1}".format(v, v.releaselevel.title())
        versions = f"Bot: {self.bot.version}\ndiscord.py: {discord.__version__}\nPython: {python_version}"
        embed.add_field(name="Version info:", value=code_block(versions, "py"))
        embed.add_field(
            name="Hosting method:",
            value=code_block(self.bot.hosting_method.name, "fix"),
        )
        uname = platform.uname()
        system_info = (
            f"System: {uname.system}\n"
            f"Architecture: {uname.machine}\n"
            f"Version: {uname.version}\n"
            f"Release: {uname.release}\n"
        )
        embed.add_field(name="System info", value=code_block(system_info, "py"))
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Bot ID: {self.bot.user.id}")

        await ctx.send(embed=embed)

    @commands.command(aliases=["userpfp"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def useravatar(self, ctx: commands.Context, *, member: discord.Member = None):
        """
        Avatar (profile picture) of a member.

        `member` if specified, may be a user ID, mention, or name.
        """
        if member is None:
            member = ctx.author

        embed = discord.Embed(title="Avatar", color=member.color)
        embed.set_author(name=str(member))
        embed.add_field(name="Avatar URL", value=f"[Link]({member.display_avatar.url})")
        embed.set_image(url=member.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(aliases=["whois", "memberinfo"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def userinfo(self, ctx: commands.Context, *, user: Union[discord.Member, discord.User] = None):
        """
        Information of a guild member or Discord user.

        `user` if specified, may be a user ID, mention, or name.
        """
        if user is None:
            user = ctx.author

        entity = "Member" if isinstance(user, discord.Member) else "User"
        embed = discord.Embed(title=f"{entity} info", color=user.color)
        embed.set_author(name=str(user))
        embed.add_field(name="Created:", value=discord.utils.format_dt(user.created_at, "F"))
        embed.add_field(name="Avatar URL:", value=f"[Link]({user.display_avatar.url})")
        embed.add_field(name="Mention:", value=user.mention)

        if isinstance(user, discord.Member):
            embed.add_field(name="Joined:", value=discord.utils.format_dt(user.joined_at, "F"))
            join_position = sorted(user.guild.members, key=lambda m: m.joined_at).index(user) + 1
            embed.add_field(name="Join Position:", value=f"{join_position}")
            embed.add_field(name="Nickname:", value=user.nick)
            if user.activity is not None:
                activitytype = user.activity.type.name.title()  # type: ignore
                activitytype += " to" if activitytype == "Listening" else ""
                embed.add_field(name="Activity:", value=f"{activitytype} {user.activity.name}")
            embed.add_field(name="Status:", value=user.status.name.title())  # type: ignore
            role_list = [role.mention for role in reversed(user.roles) if role is not ctx.guild.default_role]
            embed.add_field(name="Roles:", value=" ".join(role_list) if role_list else "None.")

        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"User ID: {user.id}")

        await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def roleinfo(self, ctx: commands.Context, *, role: discord.Role):
        """
        Information of a role specified.

        `role` may be a ID, mention, or name.
        """

        rolecolor = str(role.color).upper()
        embed = discord.Embed(title="Role info", color=role.color)
        embed.set_author(name=f"{role.name}")

        embed.add_field(name="Role Name:", value=f"{role.name}")
        embed.add_field(name="Color:", value=rolecolor)
        embed.add_field(name="Members:", value=f"{len(role.members)}")
        embed.add_field(name="Created at:", value=discord.utils.format_dt(role.created_at, "F"))
        embed.add_field(name="Role Position:", value=role.position)
        embed.add_field(name="Mention:", value=role.mention)
        embed.add_field(name="Hoisted:", value=role.hoist)
        embed.add_field(name="Mentionable:", value=role.mentionable)
        embed.add_field(name="Managed:", value=role.managed)
        embed.set_thumbnail(url=f"https://placehold.it/100/{str(rolecolor)[1:]}?text=+")
        embed.set_footer(text=f"Role ID: {role.id}")

        await ctx.send(embed=embed)

    @commands.command(aliases=["inrole"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def rolemembers(self, ctx: commands.Context, *, role: discord.Role):
        """
        List of members in a role specified.

        `role` may be a role ID, mention, or name.
        """

        member_list = role.members.copy()

        def base_embed(continued=False, description=None):
            embed = discord.Embed(
                title=f"Members in {discord.utils.escape_markdown(role.name).title()}",
                description=description if description is not None else "",
                color=role.color,
            )

            if continued:
                embed.title += " (Continued)"

            embed.set_thumbnail(url=f"https://placehold.it/100/{str(role.color)[1:]}?text=+")
            footer_text = f"Found {plural(len(member_list)):member}"
            embed.set_footer(text=footer_text)
            return embed

        embeds = [base_embed()]
        entries = 0
        if member_list:
            embed = embeds[0]
            for member in sorted(member_list, key=lambda m: m.name.lower()):
                line = f"{member}\n"
                if entries == 25:
                    embed = base_embed(continued=True, description=line)
                    embeds.append(embed)
                    entries = 1
                else:
                    embed.description += line
                    entries += 1
        else:
            embeds[0].description = "Currently there are no members with that role."

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def allroles(self, ctx: commands.Context):
        """
        List of roles on this server.
        """
        roles_list = [
            role for role in ctx.guild.roles if role is not ctx.guild.default_role  # @everyone not included
        ]

        def base_embed(continued=False, description=None):
            embed = discord.Embed(title="All roles", color=self.bot.main_color)
            if continued:
                embed.title += " (Continued)"
            embed.description = description if description is not None else ""
            embed.set_footer(text=f"Found {plural(len(roles_list)):role}")
            return embed

        embeds = [base_embed()]
        entries = 0
        if roles_list:
            embed = embeds[0]
            for role in reversed(sorted(roles_list, key=lambda role: role.position)):
                line = f"{role.mention} : {plural(len(role.members)):member}\n"
                if entries == 25:
                    embed = base_embed(True, line)
                    embeds.append(embed)
                    entries = 1
                else:
                    embed.description += line
                    entries += 1
        else:
            embeds[0].description = "There is no role in this server."

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @commands.command(aliases=["guildinfo"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def serverinfo(self, ctx: commands.Context):
        """
        Information of this server.
        """
        guild = ctx.guild
        bots = len([m for m in guild.members if m.bot])
        humans = len([m for m in guild.members if not m.bot])
        online = len([m for m in guild.members if m.status != discord.Status.offline])

        embed = discord.Embed(title="Server info", color=self.bot.main_color)
        embed.set_author(name=f"{guild.name}")
        embed.description = f"Created {discord.utils.format_dt(guild.created_at, 'R')}."
        embed.add_field(
            name="__Member Count:__",
            value=(
                f"**Online** - {online}\n"
                f"**Humans** - {humans}\n"
                f"**Bots** - {bots}\n"
                f"**All** - {guild.member_count}"
            ),
        )
        embed.add_field(
            name="__Channels:__",
            value=(
                f"**Category** - {len(guild.categories)}\n"
                f"**Text** - {len(guild.text_channels)}\n"
                f"**Voice** - {len(guild.voice_channels)}"
            ),
        )
        embed.add_field(name="__Roles:__", value=f"{len(guild.roles)}")
        embed.add_field(
            name="__Verification Level:__",
            value=f"{(verif[str(guild.verification_level)])}",
        )

        if guild.premium_tier != 0:
            nitro_boost = (
                f"**Tier {str(guild.premium_tier)} with {guild.premium_subscription_count} boosters**\n"
                f"**File size limit** - {_size(guild.filesize_limit)}\n"
                f"**Emoji limit** - {str(guild.emoji_limit)}\n"
                f"**VC's max bitrate** - {_bitsize(guild.bitrate_limit)}"
            )
            embed.add_field(name="__Nitro Boost:__", value=nitro_boost)

        embed.add_field(
            name="__Misc:__",
            value=(
                f"**AFK channel** - {str(guild.afk_channel) if guild.afk_channel else 'Not set'}\n"
                f"**AFK timeout** - {guild.afk_timeout}\n"
                f"**Custom emojis** - {len(guild.emojis)}"
            ),
            inline=False,
        )

        guild_features_list = [
            f"\N{WHITE HEAVY CHECK MARK} {name}"
            for feature, name in features.items()
            if feature in guild.features
        ]
        if guild_features_list:
            embed.add_field(name="__Server features:__", value="\n".join(guild_features_list))

        embed.add_field(name="__Server Owner:__", value=guild.owner.mention, inline=False)

        if guild.splash:
            embed.set_image(url=str(guild.splash.replace(format="png").url))

        embed.set_thumbnail(url=str(guild.icon.url))
        embed.set_footer(text=f"Server ID: {guild.id}")

        await ctx.send(embed=embed)

    # https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/meta.py
    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def charinfo(self, ctx: commands.Context, *, characters: str):
        """
        Information about a number of characters.

        Only up to 25 characters at a time.
        """

        def to_string(c):
            digit = f"{ord(c):x}"
            name = unicodedata.name(c, "Name not found.")
            return f"`\\U{digit:>08}` : `{name}` - {c} \N{EM DASH}\n<http://www.fileformat.info/info/unicode/char/{digit}>"

        msg = "\n\n".join(map(to_string, characters))
        if len(msg) > 2000:
            return await ctx.send("Output too long to display.")
        await ctx.send(msg)

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def guildemojis(self, ctx: commands.Context):
        """
        List of custom emojis in this server with their IDs.
        """
        pages = []
        page = "### Server emojis:\n\n"
        emojis = ctx.guild.emojis
        for i, e in enumerate(emojis):
            elem = f"{e} - `{e}`\n"
            if len(page) + len(elem) > 2000:
                pages.append(page)
                page = elem
            else:
                page += elem
            if i == len(emojis) - 1:
                pages.append(page)
        if not pages:
            raise commands.BadArgument("There is no custom emoji in this server.")

        session = MessagePaginatorSession(ctx, *pages)
        await session.run()


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(GeneralInfo(bot))
