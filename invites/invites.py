import discord
from discord.ext import commands
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from core import checks
from core.models import getLogger, PermissionLevel
from core.paginator import EmbedPaginatorSession

from .utils.timedelta import DateTimeFormatter


logger = getLogger(__name__)


dt_formatter = DateTimeFormatter


class Invites(commands.Cog):
    """
    Track invites.

    __**About:**__
    The bot will check which invite is used when someone joins the server using the following methods:
    - Check if invite no longer exists.
    - Check invite's uses. If used invite is found with this method, it will overwrite any results from the previous method.
    """

    _id = "config"
    default_config = {
        "channel": str(int()),
        "enabled": False,
    }

    def __init__(self, bot):
        """
        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot = bot
        self.db = bot.api.get_plugin_partition(self)
        self._config_cache: Dict[str, Any] = {}
        self.invite_cache: Dict[int, Set[discord.Invite]] = {}
        self.vanity_invites: Dict[int, Optional[discord.Invite]] = {}

        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        """
        Initial tasks when loading the cog.
        """
        await self.populate_config_cache()
        await self.populate_invite_cache()

    async def populate_config_cache(self):
        """
        Populates the config cache with data from database.
        """
        db_config = await self.db.find_one({"_id": self._id})
        if db_config is None:
            db_config = {}  # empty dict, so we can use `.get` method without error

        to_update = False
        for guild in self.bot.guilds:
            config = db_config.get(str(guild.id))
            if config is None:
                config = {k: v for k, v in self.default_config.items()}
                to_update = True
            self._config_cache[str(guild.id)] = config

        if to_update:
            await self.config_update()

    def guild_config(self, guild_id: str):
        config = self._config_cache.get(guild_id)
        if config is None:
            config = {k: v for k, v in self.default_config.items()}
            self._config_cache[guild_id] = config

        return config

    async def config_update(self):
        """
        Updates the database with the data from config cache.

        This will update the database from the cache globally (not guild specific).
        """
        await self.db.find_one_and_update(
            {"_id": self._id},
            {"$set": self._config_cache},
            upsert=True,
        )

    async def populate_invite_cache(self):
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            config = self.guild_config(str(guild.id))
            if not config["enabled"]:
                continue

            logger.debug("Caching invites for guild (%s).", guild.name)
            self.invite_cache[guild.id] = {inv for inv in await guild.invites()}

            if "VANITY_URL" in guild.features:
                self.vanity_invites[guild.id] = await guild.vanity_invite()

    async def get_used_invite(self, member: discord.Member) -> List[Optional[discord.Invite]]:
        """
        Checks which invite is used in join via the following strategies:
        1. Check if invite doesn't exist anymore.
        2. Check invite uses. This will overwrite check 1.

        After the checks are done, it will store the new invites in cache automatically.

        Returns predicted invites in a list/array.

        Parameters
        ----------
        member : discord.Member
            Member object.
        """
        guild = member.guild
        new_invite_cache = {i for i in await guild.invites()}
        predicted_invites = []
        found = False

        for _inv in self.invite_cache[guild.id]:
            # 1. Check if invite doesn't exist anymore.
            if _inv not in new_invite_cache:
                predicted_invites.append(_inv)
                continue

            # 2. Check invite uses.
            used_inv = next(
                (inv for inv in new_invite_cache if inv.id == _inv.id and inv.uses > _inv.uses),
                None,
            )
            if used_inv is not None:
                # We found the used invite, the `for loop` will stop here and the value will be returned.
                found = True
                predicted_invites = [used_inv]
                break

        # 3. Check vanity invite
        if not found and "VANITY_URL" in guild.features:
            # still not found and this guild has vanity url enabled in guild.features
            # so we check if it's incremented
            vanity_invite = await guild.vanity_invite()
            _vanity_inv = self.vanity_invites.get(guild.id)
            if _vanity_inv is not None and vanity_invite.uses > _vanity_inv.uses:
                predicted_invites = [vanity_invite]
                found = True
            self.vanity_invites[guild.id] = vanity_invite

        # In case no invite found from check #2 and #3, there are possibly deleted or expired invites in the list
        # of 'predicted_invites'.
        # We'll try to filter them, remove any that meets those criteria.
        # In this case we check the values of '.uses', '.max_uses' and '.max_age' attributes and do the logics.
        if predicted_invites and not found:
            for inv in list(predicted_invites):
                if inv.max_age:
                    expired = (
                        datetime.timestamp(inv.created_at) + inv.max_age
                    ) < member.joined_at.timestamp()
                else:
                    expired = False  # never expires
                if not all((inv.max_uses == (inv.uses + 1), not expired)):
                    predicted_invites.remove(inv)

            if len(predicted_invites) == 1:
                predicted_invites[0].uses += 1

        self.invite_cache[guild.id] = new_invite_cache
        return predicted_invites

    async def save_user_data(
        self, member: discord.Member, predicted_invites: List[discord.Invite]
    ):
        """
        Saves user and invite data into the database.
        This will be used when the bot is on event: `on_member_join`.

        Parameters
        ----------
        member : discord.Member
            Member object, belongs to member that newly joined the guild.
        predicted_invites : List[discord.Invite]
            List of invites that was retrieved from `get_used_invite` method.
        """
        if not predicted_invites:
            return

        user_data = {
            "user_name": f"{member.name}#{member.discriminator}",
            "joined_at": member.joined_at,
            "inviter": {
                "mention": "\n".join(
                    getattr(invite.inviter, "mention", "None") for invite in predicted_invites
                ),
                "id": "\n".join(
                    str(getattr(invite.inviter, "id", "None")) for invite in predicted_invites
                ),
            },
            "invite_code": "\n".join(str(invite.code) for invite in predicted_invites),
            "invite_channel": "\n".join(
                getattr(invite.channel, "mention", "None") for invite in predicted_invites
            ),
            "multi": len(predicted_invites) > 1,
        }

        await self.db.find_one_and_update(
            {"guild_id": member.guild.id, "user_id": member.id}, {"$set": user_data}, upsert=True
        )

    async def remove_user_data(self, member: discord.Member):
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
        - `{prefix}invite config set channel <channel>`
        - `{prefix}invite config set enable True`
        """
        await ctx.send_help(ctx.command)

    @invites.group(name="config", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config(self, ctx: commands.Context):
        """
        Invites tracking configurations.
        """
        await ctx.send_help(ctx.command)

    @invites_config.group(name="set", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_set(self, ctx: commands.Context):
        """
        Set the configurations.
        """
        await ctx.send_help(ctx.command)

    @invites_config_set.command(name="channel")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_set_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """
        Set the channel where the logs for invites tracking should be posted.

        `<channel>` may be a channel ID, mention, or name.
        """
        config = self.guild_config(str(ctx.guild.id))

        new_config = dict(channel=str(channel.id))
        config.update(new_config)
        await self.config_update()

        embed = discord.Embed(
            description=f"Invite logs channel is now set to {channel.mention}",
            color=self.bot.main_color,
        )
        await ctx.send(embed=embed)

    @invites_config_set.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_set_enable(self, ctx: commands.Context, mode: bool):
        """
        Enable or disable the logging for invites tracking.

        **Usage:**
        - `{prefix}invite config set enable True`
        - `{prefix}invite config set enable False`
        """
        config = self.guild_config(str(ctx.guild.id))

        new_config = dict(enabled=mode)
        config.update(new_config)
        await self.config_update()

        embed = discord.Embed(
            description=("Enabled" if mode is True else "Disabled")
            + " the invites tracking logs.",
            color=self.bot.main_color,
        )
        await ctx.send(embed=embed)

        if mode:
            self.invite_cache[ctx.guild.id] = {inv for inv in await ctx.guild.invites()}

    @invites_config.command(name="get")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_get(self, ctx: commands.Context, *, key: str.lower = None):
        """
        Show the configuration variables that are currently set.

        Leave `key` empty to show all currently set configuration variables.

        **Usage:**
        - `{prefix}invite config get channel`
        - `{prefix}invite config get enabled`
        """
        config = self.guild_config(str(ctx.guild.id))

        if key:
            keys = [k for k in self.default_config]
            if key in keys:
                if key == "channel":
                    channel = ctx.guild.get_channel(int(config[key]))
                    desc = (
                        f"`{key}` is set to {channel.mention if channel is not None else 'None'}"
                    )
                else:
                    desc = f"`{key}` is set to `{config[key]}`"

                embed = discord.Embed(color=self.bot.main_color, description=desc)
                embed.set_author(name="Config variable", icon_url=self.bot.user.avatar_url)

            else:
                embed = discord.Embed(
                    title="Error",
                    color=self.bot.error_color,
                    description=f"`{key}` is an invalid key.",
                )
        else:
            channel = ctx.guild.get_channel(int(config["channel"]))
            enabled = config["enabled"]

            embed = discord.Embed(
                color=self.bot.main_color,
                description="Here is a list of currently set configurations.",
            )
            embed.set_author(name="Invite config:", icon_url=self.bot.user.avatar_url)

            embed.add_field(
                name="Channel",
                value=channel.mention if channel is not None else "None",
                inline=False,
            )
            embed.add_field(name="Enabled", value=f"`{enabled}`", inline=False)

        await ctx.send(embed=embed)

    @invites_config.command(name="reset")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_config_reset(self, ctx: commands.Context):
        """
        Reset the configuration settings to default value.
        """
        self._config_cache[str(ctx.guild.id)] = {k: v for k, v in self.default_config.items()}
        await self.config_update()

        embed = discord.Embed(
            description=f"Configuration settings has been reset to default.",
            color=self.bot.main_color,
        )
        embed.add_field(name="Channel", value="None", inline=False)
        embed.add_field(name="Enabled", value="False", inline=False)
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
        await self.populate_invite_cache()
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
                title=f"List of Invites",
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
                        title=f"List of Invites (Continued)",
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
        embed = discord.Embed(color=self.bot.main_color, title="__Invite info__")
        embed.set_thumbnail(url=str(invite.guild.icon_url))
        embed.description = f"**Server:**\n{invite.guild}\n" f"**Invite link:**\n{invite.url}\n"

        fetched_invites = await discord.Guild.invites(ctx.guild)
        try:
            embed.add_field(
                name="Inviter:",
                value=invite.inviter.mention
                if invite in fetched_invites
                else f"{invite.inviter.name}#{invite.inviter.discriminator}",
            )
            embed.add_field(name="Channel:", value=invite.channel.mention)
        except AttributeError:
            pass
        if invite in fetched_invites:
            local = False
            for inv in fetched_invites:
                if invite.id == inv.id:
                    invite = inv
                    local = True
                    break
            if local:
                invite_created = dt_formatter.time(invite.created_at)
                timestamp_expires = datetime.timestamp(invite.created_at) + int(invite.max_age)
                invite_expires = dt_formatter.time(datetime.fromtimestamp(timestamp_expires))
                if invite_created == invite_expires:
                    invite_expires = "Never"
                embed.add_field(name="Uses:", value=invite.uses)
                embed.add_field(name="Created at:", value=invite_created)
                embed.add_field(name="Expires at:", value=invite_expires)
        else:
            embed.description += f"**Member count:**\n{invite.approximate_member_count}"
        await ctx.send(embed=embed)

    @invites.command(name="delete", aliases=["revoke"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def invites_delete(self, ctx: commands.Context, invite: discord.Invite):
        """
        Delete an invite.

        `invite` may be an invite code, or full invite link.
        """
        fetched_invites = await ctx.guild.invites()
        if invite not in fetched_invites:
            raise commands.BadArgument('Invite "{}" is not from this guild.'.format(invite.code))

        for inv in fetched_invites:
            if inv.id == invite.id:
                invite = inv
                break
        embed = discord.Embed(
            color=discord.Color.blurple(), description=f"Deleted invite code: `{invite.code}`"
        )
        embed.add_field(name="Inviter:", value=invite.inviter.mention)
        embed.add_field(name="Channel:", value=invite.channel.mention)

        invite_created = dt_formatter.time(invite.created_at)
        timestamp_expires = datetime.timestamp(invite.created_at) + int(invite.max_age)
        invite_expires = dt_formatter.time(datetime.fromtimestamp(timestamp_expires))

        if invite_created == invite_expires:
            invite_expires = "Never"

        embed.add_field(name="Uses:", value=invite.uses)
        embed.add_field(name="Created at:", value=invite_created)
        embed.add_field(name="Expires at:", value=invite_expires)
        try:
            await invite.delete()
        except discord.Forbidden:
            raise commands.BadArgument("I don't have permissions to revoke invites.")

        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        config = self.guild_config(str(invite.guild.id))
        if not config["enabled"]:
            return

        cached_invites = self.invite_cache.get(invite.guild.id)
        if cached_invites is None:
            cached_invites = {inv for inv in await invite.guild.invites()}
        else:
            cached_invites.update({invite})
        self.invite_cache[invite.guild.id] = cached_invites
        logger.debug("Invite created. Updating invite cache for guild (%s).", invite.guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        config = self.guild_config(str(member.guild.id))

        if not config["enabled"]:
            return
        channel = member.guild.get_channel(int(config["channel"]))
        if channel is None:
            return

        embed = discord.Embed(color=discord.Color.green(), timestamp=datetime.utcnow())
        embed.set_thumbnail(url=member.avatar_url)
        embed.title = f"{member.name}#{member.discriminator} just joined."
        embed.set_footer(text=f"User ID: {member.id}")

        join_position = sorted(member.guild.members, key=lambda m: m.joined_at).index(member) + 1
        suffix = ["th", "st", "nd", "rd", "th"][min(join_position % 10, 4)]
        if 11 <= (join_position % 100) <= 13:
            suffix = "th"

        desc = f"{member.mention} is the {join_position}{suffix} to join."
        embed.description = desc + "\n"
        embed.add_field(name="Account created:", value=dt_formatter.time_age(member.created_at))

        predicted_invites = await self.get_used_invite(member)
        if predicted_invites:
            vanity_inv = self.vanity_invites.get(member.guild.id)  # could be None
            embed.add_field(
                name="Inviter:",
                value="\n".join(getattr(i.inviter, "mention", "None") for i in predicted_invites),
            )
            embed.add_field(
                name="Invite code:",
                value="\n".join(
                    i.code if i != vanity_inv else "Vanity URL"
                    for i in predicted_invites
                ),
            )
            embed.add_field(
                name="Invite channel:",
                value="\n".join(getattr(i.channel, "mention", "None") for i in predicted_invites),
            )

            if len(predicted_invites) == 1:
                invite = predicted_invites[0]
                if invite == vanity_inv:
                    embed.add_field(name="Vanity:", value="True")
                else:
                    embed.add_field(
                        name="Invite created:", value=f"{dt_formatter.time(invite.created_at)}"
                    )

                if invite.max_age:
                    tstamp_exp = datetime.timestamp(invite.created_at) + invite.max_age
                    expires = dt_formatter.time(datetime.fromtimestamp(tstamp_exp))
                else:
                    expires = "Never"

                embed.add_field(name="Invite expires:", value=f"{expires}")
                embed.add_field(name="Invite uses:", value=f"{invite.uses}")

            else:
                embed.description += "\n⚠️ *More than 1 used invites are predicted.*\n"

        else:
            embed.description += "\n⚠️ *Something went wrong, could not get invite info.*\n"

        await channel.send(embed=embed)
        await self.save_user_data(member, predicted_invites)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return

        config = self.guild_config(str(member.guild.id))

        if not config["enabled"]:
            return
        channel = member.guild.get_channel(int(config["channel"]))
        if channel is None:
            return

        embed = discord.Embed(color=discord.Color.red(), timestamp=datetime.utcnow())
        embed.set_thumbnail(url=member.avatar_url)
        embed.title = f"{member.name}#{member.discriminator} left."
        embed.set_footer(text=f"User ID: {member.id}")
        desc = f"{member.mention} just left the server."
        embed.description = desc + "\n"

        user_db = await self.db.find_one({"guild_id": member.guild.id, "user_id": member.id})
        if user_db:
            embed.add_field(name="Joined at:", value=dt_formatter.time(user_db["joined_at"]))
            embed.add_field(name="Time on server:", value=dt_formatter.age(user_db["joined_at"]))
            embed.add_field(name="Inviter:", value=user_db["inviter"]["mention"])
            embed.add_field(name="Invite code:", value=user_db["invite_code"])
            embed.add_field(name="Invite channel:", value=user_db["invite_channel"])
        else:
            embed.description += "\n*No invite info*.\n"
            embed.add_field(name="Joined at:", value=dt_formatter.time(member.joined_at))
            embed.add_field(name="Time on server:", value=dt_formatter.age(member.joined_at))

        if member.nick:
            embed.description += "\n**Nickname:**\n" + member.nick + "\n"

        role_list = [
            role.mention
            for role in reversed(member.roles)
            if role is not member.guild.default_role
        ]
        if role_list:
            embed.description += "\n**Roles:**\n" + (" ".join(role_list)) + "\n"

        if user_db and user_db.get("multi"):
            embed.description += "\n⚠️ *More than 1 used invites were retrieved.*\n"

        await channel.send(embed=embed)
        await self.remove_user_data(member)


def setup(bot):
    bot.add_cog(Invites(bot))
