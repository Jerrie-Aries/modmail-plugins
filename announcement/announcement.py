from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import discord

from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel

from .core.models import AnnouncementModel
from .core.views import AnnouncementView


if TYPE_CHECKING:
    from bot import ModmailBot


info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


logger = getLogger(__name__)


class Announcement(commands.Cog):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce(self, ctx: commands.Context):
        """
        Base command to create announcements.
        """
        await ctx.send_help(ctx.command)

    @announce.command(name="create", aliases=["start"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce_create(self, ctx: commands.Context, *, channel: Optional[discord.TextChannel] = None):
        """
        Post an announcement in a channel specified.

        This will initiate a creation panel where you can choose and customise the output of the announcement.

        `channel` if specified may be a channel ID, mention, or name. Otherwise, fallbacks to current channel.

        __**Note:**__
        - If `channel` is not specified, to ensure cleaner output the creation message will automatically be deleted after the announcement is posted.
        """
        delete = False
        if channel is None:
            channel = ctx.channel
            delete = True
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                logger.warning(f"Missing `Manage Messages` permission in {channel} channel.")

        announcement = AnnouncementModel(ctx, channel)
        view = AnnouncementView(ctx, announcement)
        embed = discord.Embed(title="Announcement Creation Panel", color=self.bot.main_color)
        embed.description = (
            "Choose a type of announcement using the dropdown menu below.\n\n"
            "__**Available types:**__\n"
            "- **Normal** : Plain text announcement.\n"
            "- **Embed** : Embedded announcement. Image and thumbnail image are also supported."
        )
        embed.set_footer(text="This panel will timeout after 10 minutes.")
        view.message = message = await ctx.send(embed=embed, view=view)
        await view.wait(input_event=True)

        if not announcement.posted:
            await message.edit(view=view)
            return

        if delete:
            view.stop()
            await message.delete()
            return

        embed = message.embeds[0]
        description = f"Announcement has been posted in {channel.mention}.\n\n"
        if announcement.channel.type == discord.ChannelType.news:
            description += "Would you like to publish this announcement?\n\n"
            view.generate_buttons(confirmation=True)
        else:
            view.stop()

        embed.description = description
        await message.edit(embed=embed, view=view)
        if view.is_finished():
            return

        await view.wait()

        hyper_link = f"[announcement]({announcement.message.jump_url})"
        if view.confirm:
            await announcement.publish()
            embed.description = f"Successfully published this {hyper_link} to all subscribed channels.\n\n"
        if view.confirm is not None:
            if not view.confirm:
                embed.description += (
                    f"To manually publish this {hyper_link}, use command:\n"
                    f"```\n{ctx.prefix}publish {announcement.channel.id}-{announcement.message.id}\n```"
                )
            view = None

        await message.edit(embed=embed, view=view)

    @announce.command(name="quick")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce_quick(self, ctx: commands.Context, channel: discord.TextChannel, *, content: str):
        """
        Create a quick plain text announcement.

        `channel` may be a channel ID, mention, or name.
        """
        await channel.send(content)

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def publish(self, ctx: commands.Context, *, message: discord.Message):
        """
        Publish a message from announcement channel to all subscribed channels.

        `message` may be a message ID, format of `channel ID-message ID`, or message link.

        __**Notes:**__
        - If message ID is provided (without channel ID and not the message link), the bot will only look for the message in the current channel.
        - Only messages in [announcement](https://support.discord.com/hc/en-us/articles/360032008192-Announcement-Channels) channels can be published.
        """
        channel = message.channel
        if not channel.type == discord.ChannelType.news:
            raise commands.BadArgument(f"Channel {channel.mention} is not an announcement channel.")
        if message.flags.crossposted:
            raise commands.BadArgument(f"Message `{message.id}` is already published.")

        await message.publish()
        embed = discord.Embed(
            description=f"Successfully published this [message]({message.jump_url}) to all subscribed channels.",
            color=self.bot.main_color,
        )
        await ctx.reply(embed=embed)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Announcement(bot))
