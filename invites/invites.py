from __future__ import annotations

import json

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, TypedDict, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import MISSING

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot

    class GuildConfigData(TypedDict):
        channel: str
        webhook: Optional[str]
        enable: bool


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)

logger = getLogger(__name__)

# <!-- Developer -->
if TYPE_CHECKING:
    from ..utils.utils import Config, datetime_formatter as dt_formatter
else:
    Config = MISSING
    dt_formatter = MISSING


def _set_globals(cog: Invites) -> None:
    required = __plugin_info__["cogs_required"][0]
    utils_cog = cog.bot.get_cog(required)
    if not utils_cog:
        raise RuntimeError(f"{required} plugin is required for {cog.qualified_name} plugin to function.")

    global Config, dt_formatter

    Config = utils_cog.config["Config"]
    dt_formatter = utils_cog.timeutils["datetime_formatter"]


# <-- ----- -->


class Invites(commands.Cog):
    __doc__ = __description__

    default_config: GuildConfigData = {
        "channel": str(int()),
        "webhook": None,
        "enable": False,
    }

    def __init__(self, bot):
        """
        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot = bot
        self.db: AsyncIOMotorCollection = bot.api.get_plugin_partition(self)
        self.config: Config = MISSING
        self.invite_cache: Dict[int, Set[discord.Invite]] = {}
        self.vanity_invites: Dict[int, Optional[discord.Invite]] = {}

    async def cog_load(self) -> None:
        """
        Initial tasks when loading the cog.
        """
        self.bot.loop.create_task(self.initialize())

    async def initialize(self) -> None:
        # Ensure everything is ready and all extensions are loaded
        await self.bot.wait_for_connected()
        _set_globals(self)
        await self.populate_config()
        await self.populate_invites()

    async def populate_config(self) -> None:
        """
        Populates the config cache with data from database.
        """
        config = Config(self, self.db)
        await config.fetch()
        update = False
        for guild in self.bot.guilds:
            gconfig = config.get(str(guild.id))
            if gconfig is None:
                gconfig = {k: v for k, v in self.default_config.items()}
                update = True
            config.cache[str(guild.id)] = gconfig

        if update:
            await config.update()

        self.config = config

    def guild_config(self, guild_id: str) -> GuildConfigData:
        config = self.config.get(guild_id)
        if config is None:
            config = {k: v for k, v in self.default_config.items()}
            self.config[guild_id] = config

        return config

    async def populate_invites(self) -> None:
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            config = self.guild_config(str(guild.id))
            if not config["enable"]:
                continue

            logger.debug("Caching invites for guild (%s).", guild.name)
            self.invite_cache[guild.id] = {inv for inv in await guild.invites()}

            if "VANITY_URL" in guild.features:
                vanity_inv = await guild.vanity_invite()
                if vanity_inv is not None:
                    self.vanity_invites[guild.id] = vanity_inv

    async def get_used_invite(self, member: discord.Member) -> List[Optional[discord.Invite]]:
        """
        Checks which invite is used in join via the following strategies:
        1. Check if invite doesn't exist anymore.
        2. Check invite uses. This will overwrite check 1.

        After the checks are done, it will store the new invites in cache automatically.

        Returns a list of predicted invites.

        Parameters
        ----------
        member : discord.Member
            Member object.
        """
        guild = member.guild
        new_invs = {i for i in await guild.invites()}
        pred_invs = []
        found = False

        for old_inv in self.invite_cache[guild.id]:
            # 1. Check if invite doesn't exist anymore.
            if old_inv not in new_invs:
                # the invite could be deleted, expired or reached max usage
                # if it's the latter one, then this is the used invite so we add to the list first
                pred_invs.append(old_inv)
                continue

            # 2. Check invite uses.
            used_inv = next(
                (inv for inv in new_invs if inv.id == old_inv.id and inv.uses > old_inv.uses),
                None,
            )
            if used_inv is not None:
                # We found the used invite, the `for loop` will stop here and the value will be returned.
                found = True
                pred_invs = [used_inv]
                break

        # 3. Check vanity invite
        if not found and "VANITY_URL" in guild.features:
            # still not found and this guild has vanity url enabled in guild.features
            # so we check if it's incremented
            vanity_inv = await guild.vanity_invite()
            cached_vanity_inv = self.vanity_invites.get(guild.id)
            if vanity_inv and cached_vanity_inv and vanity_inv.uses > cached_vanity_inv.uses:
                pred_invs = [vanity_inv]
                found = True
            self.vanity_invites[guild.id] = vanity_inv

        # In case no invite found from check #2 and #3, there are possibly deleted or expired invites in the list
        # of 'pred_invs'.
        # We'll try to filter them, remove any that meets those criteria.
        # In this case we check the values of '.uses', '.max_uses' and '.max_age' attributes and do the logics.
        if pred_invs and not found:
            for inv in list(pred_invs):
                if inv.max_age:
                    expired = (
                        datetime.timestamp(inv.created_at) + inv.max_age
                    ) < member.joined_at.timestamp()
                else:
                    expired = False  # never expires
                if not all((inv.max_uses == (inv.uses + 1), not expired)):
                    pred_invs.remove(inv)

            if len(pred_invs) == 1:
                pred_invs[0].uses += 1

        self.invite_cache[guild.id] = new_invs
        return pred_invs

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        """
        An internal method to retrieve an existing webhook from the channel if any, otherwise a new one
        will be created.

        Parameters
        -----------
        channel : discord.TextChannel
            The channel to get or create the webhook from.
        """
        # check bot permissions first
        bot_me = channel.guild.get_member(self.bot.user.id)
        if not bot_me or not channel.permissions_for(bot_me).manage_webhooks:
            return None

        wh = None
        webhooks = await channel.webhooks()
        if webhooks:
            # find any webhook that has token which means that belongs to the client
            wh = discord.utils.find(lambda x: x.token is not None, webhooks)

        if not wh:
            avatar = await self.bot.user.display_avatar.read()
            try:
                wh = await channel.create_webhook(
                    name=self.bot.user.name,
                    avatar=avatar,
                    reason="Webhook for invite logs.",
                )
            except Exception as e:
                logger.error(f"{type(e).__name__}: {str(e)}")
                wh = None

        return wh

    async def save_user_data(self, member: discord.Member, invites: List[discord.Invite]) -> None:
        """
        Saves user and invite data into the database.
        This will be used when the bot is on event: `on_member_join`.

        Parameters
        ----------
        member : discord.Member
            Member object, belongs to member that newly joined the guild.
        invites : List[discord.Invite]
            List of invites that was retrieved from `get_used_invite` method.
        """
        if not invites:
            return

        user_data = {
            "user_name": str(member),
            "inviter": {
                "mention": "\n".join(getattr(invite.inviter, "mention", "None") for invite in invites),
                "id": "\n".join(str(getattr(invite.inviter, "id", "None")) for invite in invites),
            },
            "invite_code": "\n".join(str(invite.code) for invite in invites),
            "invite_channel": "\n".join(getattr(invite.channel, "mention", "None") for invite in invites),
            "multi": len(invites) > 1,
        }

        await self.db.find_one_and_update(
            {"guild_id": member.guild.id, "user_id": member.id},
            {"$set": user_data},
            upsert=True,
        )

    async def remove_user_data(self, member: discord.Member) -> None:
        """
        Removes user and invite data from the database.
        This will be used when the bot is on event: `on_member_remove`.

        Parameters
        ----------
        member : discord.Member
            Member object, belongs to member that leaves the guild.
        """
        await self.db.find_one_and_delete({"guild_id": member.guild.id, "user_id": member.id})

    @commands.group(aliases=["invite"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def invites(self, ctx: commands.Context):
        """
        Set up invites tracking logs.

        **For initial setup, use commands:**
        - `{prefix}invite config channel <channel>`
        - `{prefix}invite config enable True`
        """
        await ctx.send_help(ctx.command)

    @invites.group(name="config", usage="<command> [argument]", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config(self, ctx: commands.Context):
        """
        Invites tracking configurations.

        Run this command without argument to see current set configurations.
        """
        config = self.guild_config(str(ctx.guild.id))

        channel = ctx.guild.get_channel(int(config["channel"]))
        embed = discord.Embed(
            title="Invites Config",
            color=self.bot.main_color,
            description="Current set configurations.",
        )

        embed.add_field(
            name="Channel",
            value=f'{getattr(channel, "mention", "`None`")}',
            inline=False,
        )
        embed.add_field(name="Enabled", value=f"`{config['enable']}`", inline=False)
        embed.add_field(name="Webhook URL", value=f'`{config["webhook"]}`', inline=False)
        await ctx.send(embed=embed)

    @invites_config.command(name="channel")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_channel(
        self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None
    ):
        """
        Set the channel where the logs for invites tracking should be posted.

        `channel` may be a channel ID, mention, or name.

        Leave `channel` empty to see the current set channel.
        """
        config = self.guild_config(str(ctx.guild.id))
        if channel is None:
            channel = self.bot.get_channel(int(config.get("channel")))
            if channel:
                description = f"Invites logging channel is currently set to {channel.mention}."
            else:
                description = "Invites logging channel is not set."
        else:
            new_config = dict(channel=str(channel.id), webhook=None)
            config.update(new_config)
            await self.config.update()
            description = f"Log channel is now set to {channel.mention}."

        embed = discord.Embed(description=description, color=self.bot.main_color)
        await ctx.send(embed=embed)

    @invites_config.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_enable(self, ctx: commands.Context, *, mode: Optional[bool] = None):
        """
        Enable or disable the logging for invites tracking.

        `mode` is a boolean value, may be `True` or `False` (case insensitive).

        **Examples:**
        - `{prefix}invite config enable True`
        - `{prefix}invite config enable False`

        Leave `mode` empty to see the current set value.
        """
        config = self.guild_config(str(ctx.guild.id))
        if mode is None:
            mode = config.get("enable")
            description = (
                "Invites tracking logging is currently " + ("`enabled`" if mode else "`disabled`") + "."
            )
        else:
            new_config = dict(enable=mode)
            config.update(new_config)
            description = ("Enabled " if mode else "Disabled ") + "the logging for invites tracking."
            await self.config.update()

        embed = discord.Embed(description=description, color=self.bot.main_color)
        await ctx.send(embed=embed)

        if mode:
            self.invite_cache[ctx.guild.id] = {inv for inv in await ctx.guild.invites()}

    @invites_config.command(name="reset")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_reset(self, ctx: commands.Context):
        """
        Reset the configuration settings to default value.
        """
        self.config[str(ctx.guild.id)] = {k: v for k, v in self.default_config.items()}
        await self.config.update()

        embed = discord.Embed(
            description="Configuration settings has been reset to default.",
            color=self.bot.main_color,
        )
        embed.add_field(name="Channel", value="`None`", inline=False)
        embed.add_field(name="Enabled", value="`False`", inline=False)
        embed.add_field(name="Webhook URL", value="`None`", inline=False)
        await ctx.send(embed=embed)

    @invites.command(name="refresh")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def invites_refresh(self, ctx: commands.Context):
        """
        Manually fetch the invites and store them in cache.

        **Note:**
        Invites are automatically fetched and stored in cache everytime:
         - A new member joining the server.
         - An invite being created.
        There is no need to manually fetch the invites using this command to store them in cache.
        """
        await self.populate_invites()
        embed = discord.Embed(
            description="Successfully refreshed the invite cache.",
            color=self.bot.main_color,
        )
        await ctx.send(embed=embed)

    @invites.command(name="list")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def invites_list(self, ctx: commands.Context):
        """
        Get the list of invites on this server.
        """
        invites_list = await ctx.guild.invites()

        embeds = [
            discord.Embed(
                title="List of Invites",
                color=discord.Color.dark_theme(),
                description="",
            )
        ]
        entries = 0

        if invites_list:
            embed = embeds[0]

            for invite in reversed(sorted(invites_list, key=lambda invite: invite.uses)):
                line = f"{invite.uses} - {invite.inviter.name}#{invite.inviter.discriminator} - `{invite.code}`\n"
                if entries == 25:
                    embed = discord.Embed(
                        title="List of Invites (Continued)",
                        color=discord.Color.dark_theme(),
                        description=line,
                    )
                    embeds.append(embed)
                    entries = 1
                else:
                    embed.description += line
                    entries += 1
        else:
            embeds[0].description = "Currently there are no list of invites available."

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @invites.command(name="info")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def invites_info(self, ctx: commands.Context, invite: discord.Invite):
        """
        Get an info of a specific invite.
        """
        embed = discord.Embed(color=self.bot.main_color, title="__Invite Info__")
        embed.description = f"**Server:**\n{invite.guild}\n\n" f"**Invite link:**\n{invite.url}\n\n"

        fetched_invites = await ctx.guild.invites()
        embed.add_field(
            name="Inviter:",
            value=f"`{invite.inviter}`",
        )
        embed.add_field(name="Channel:", value=invite.channel.mention)
        if invite in fetched_invites:
            local = False
            for inv in fetched_invites:
                if invite.id == inv.id:
                    invite = inv
                    local = True
                    break
            if local:
                if invite.max_age:
                    tstamp_exp = datetime.timestamp(invite.created_at) + invite.max_age
                    expires = discord.utils.format_dt(datetime.fromtimestamp(tstamp_exp), "F")
                else:
                    expires = "Never"
                created = discord.utils.format_dt(invite.created_at, "F")
                embed.add_field(name="Uses:", value=invite.uses)
                embed.add_field(name="Created at:", value=created)
                embed.add_field(name="Expires at:", value=expires)
        else:
            embed.description += f"**Member count:**\n{invite.approximate_member_count}\n\n"

        # could be None if the invite is from a group DM
        if invite.guild is not None:
            embed.set_thumbnail(url=str(invite.guild.icon))
            embed.set_footer(text=f"Server ID: {invite.guild.id}")

        await ctx.send(embed=embed)

    @invites.command(name="delete", aliases=["revoke"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_delete(self, ctx: commands.Context, *, invite: discord.Invite):
        """
        Delete an invite.

        `invite` may be an invite code, or full invite link.
        """
        if not invite.guild or invite.guild != ctx.guild:
            raise commands.BadArgument('Invite "{}" is not from this guild.'.format(invite.code))

        embed = discord.Embed(
            color=discord.Color.blurple(),
            description=f"Deleted invite code: `{invite.code}`",
        )
        embed.add_field(name="Inviter:", value=f"`{invite.inviter}`")
        embed.add_field(name="Channel:", value=invite.channel.mention)

        if invite.max_age:
            tstamp_exp = datetime.timestamp(invite.created_at) + invite.max_age
            expires = discord.utils.format_dt(datetime.fromtimestamp(tstamp_exp), "F")
        else:
            expires = "Never"

        created = discord.utils.format_dt(invite.created_at, "F")

        embed.add_field(name="Uses:", value=invite.uses)
        embed.add_field(name="Created at:", value=created)
        embed.add_field(name="Expires at:", value=expires)
        try:
            await invite.delete()
        except discord.Forbidden:
            raise commands.BadArgument("I do not have permissions to revoke invites.")

        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        config = self.guild_config(str(invite.guild.id))
        if not config["enable"]:
            return

        cached_invites = self.invite_cache.get(invite.guild.id)
        if cached_invites is None:
            cached_invites = {inv for inv in await invite.guild.invites()}
        else:
            cached_invites.update({invite})
        self.invite_cache[invite.guild.id] = cached_invites
        logger.debug("Invite created. Updating invite cache for guild (%s).", invite.guild)

    async def send_log_embed(self, channel: discord.TextChannel, embed: discord.Embed) -> None:
        """
        Sends the log embed to the designated channel. If a webhook is available, the embed will
        be sent using the webhook instead.

        Parameters
        -----------
        channel : discord.TextChannel
            The channel to send the embed.
        embed : discord.Embed
            The embed object.
        """
        config = self.guild_config(str(channel.guild.id))
        wh_url = config.get("webhook")
        if wh_url is None:
            webhook = await self._get_or_create_webhook(channel)
            if webhook:
                config["webhook"] = webhook.url
                await self.config.update()
        else:
            webhook = discord.Webhook.from_url(wh_url, session=self.bot.session)

        kwargs = {"embed": embed}
        if webhook:
            kwargs["username"] = self.bot.user.name
            kwargs["avatar_url"] = self.bot.user.display_avatar.url
            kwargs["wait"] = True
            send_func = webhook.send
        else:
            send_func = channel.send
        await send_func(**kwargs)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        config = self.guild_config(str(member.guild.id))

        if not config["enable"]:
            return
        channel = member.guild.get_channel(int(config["channel"]))
        if channel is None:
            return

        embed = discord.Embed(
            title=f"{member} just joined.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"User ID: {member.id}")

        join_position = sorted(member.guild.members, key=lambda m: m.joined_at).index(member) + 1
        suffix = ["th", "st", "nd", "rd", "th"][min(join_position % 10, 4)]
        if 11 <= (join_position % 100) <= 13:
            suffix = "th"

        desc = f"{member.mention} is the {join_position}{suffix} to join."
        embed.description = desc + "\n"
        embed.add_field(name="Account created:", value=dt_formatter.time_age(member.created_at))

        pred_invs = await self.get_used_invite(member)
        if pred_invs:
            vanity_inv = self.vanity_invites.get(member.guild.id)
            embed.add_field(
                name="Inviter:",
                value="\n".join(getattr(i.inviter, "mention", "`None`") for i in pred_invs),
            )
            embed.add_field(
                name="Invite code:",
                value="\n".join(i.code if i != vanity_inv else "Vanity URL" for i in pred_invs),
            )
            embed.add_field(
                name="Invite channel:",
                value="\n".join(getattr(i.channel, "mention", "`None`") for i in pred_invs),
            )

            if len(pred_invs) == 1:
                invite = pred_invs[0]
                if invite == vanity_inv:
                    embed.add_field(name="Vanity:", value="True")
                else:
                    embed.add_field(
                        name="Invite created:",
                        value=f"{discord.utils.format_dt(invite.created_at, 'F')}",
                    )

                if invite.max_age:
                    tstamp_exp = datetime.timestamp(invite.created_at) + invite.max_age
                    expires = discord.utils.format_dt(datetime.fromtimestamp(tstamp_exp), "F")
                else:
                    expires = "Never"

                embed.add_field(name="Invite expires:", value=f"{expires}")
                embed.add_field(name="Invite uses:", value=f"{invite.uses}")

            else:
                embed.description += "\n⚠️ *More than 1 used invites are predicted.*\n"

        else:
            embed.description += "\n⚠️ *Something went wrong, could not get invite info.*\n"

        await self.send_log_embed(channel, embed)
        await self.save_user_data(member, pred_invs)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return

        config = self.guild_config(str(member.guild.id))

        if not config["enable"]:
            return
        channel = member.guild.get_channel(int(config["channel"]))
        if channel is None:
            return

        embed = discord.Embed(color=discord.Color.red(), timestamp=datetime.utcnow())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.title = f"{member} left."
        embed.set_footer(text=f"User ID: {member.id}")
        desc = f"{member.mention} just left the server."
        embed.description = desc + "\n"

        embed.add_field(name="Joined at:", value=discord.utils.format_dt(member.joined_at, "F"))
        embed.add_field(name="Time on server:", value=dt_formatter.age(member.joined_at))

        user_db = await self.db.find_one({"guild_id": member.guild.id, "user_id": member.id})
        if user_db:
            embed.add_field(name="Inviter:", value=user_db["inviter"]["mention"])
            embed.add_field(name="Invite code:", value=user_db["invite_code"])
            embed.add_field(name="Invite channel:", value=user_db["invite_channel"])
        else:
            embed.description += "\n*No invite info*.\n"

        if member.nick:
            embed.description += "\n**Nickname:**\n" + member.nick + "\n"

        role_list = [role.mention for role in reversed(member.roles) if role is not member.guild.default_role]
        if role_list:
            embed.description += "\n**Roles:**\n" + (" ".join(role_list)) + "\n"

        if user_db and user_db.get("multi"):
            embed.description += "\n⚠️ *More than 1 used invites were retrieved.*\n"

        await self.send_log_embed(channel, embed)
        await self.remove_user_data(member)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Invites(bot))
