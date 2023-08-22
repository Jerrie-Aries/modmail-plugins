from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from discord.ext import commands

from core import checks
from core.models import getLogger, PermissionLevel

from .core.models import AnnouncementModel
from .core.views import AnnouncementView


if TYPE_CHECKING:
    from bot import ModmailBot


logger = getLogger(__name__)

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


news_channel_hyperlink = (
    "[announcement](https://support.discord.com/hc/en-us/articles/360032008192-Announcement-Channels)"
)
type_desc = (
    "Choose a type of announcement.\n\n"
    "__**Available types:**__\n"
    "- **Plain** : Plain text announcement.\n"
    "- **Embed** : Embedded announcement. Image and thumbnail image are also supported.\n"
)
embed_desc = (
    "Click the `Edit` button below to set/edit the embed values.\n\n"
    "__**Available fields:**__\n"
    "- **Description** : The content of the announcement. Must not exceed 4000 characters.\n"
    "- **Thumbnail URL** : URL of the image shown at the top right of the embed.\n"
    "- **Image URL** : URL of the large image shown at the bottom of the embed.\n"
    "- **Color** : The color code of the embed. If not specified, fallbacks to bot main color. "
    "The following formats are accepted:\n - `0x<hex>`\n - `#<hex>`\n - `0x#<hex>`\n - `rgb(<number>, <number>, <number>)`\n"
    "Like CSS, `<number>` can be either 0-255 or 0-100% and `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).\n"
)
plain_desc = "Click the `Edit` button below to set/edit the content.\n"
mention_desc = (
    "If nothing is selected, the announcement will be posted without any mention.\n"
    "To mention Users or Roles, select `Others` in the first dropdown, then in second dropdown select Users or Roles you want to mention.\n"
)
channel_desc = (
    "The destination channel. If nothing is selected, the announcement will be posted "
    "in the current channel.\n"
    f"The announcement can be published if the type of destination channel is {news_channel_hyperlink} channel.\n"
)


class Announcement(commands.Cog):
    __doc__ = __description__

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce(self, ctx: commands.Context):
        """
        Post an announcement.

        Run this command without argument to initiate a creation panel where you can choose and customise the output of the announcement.
        """
        announcement = AnnouncementModel(ctx)
        sessions = [
            ("type", type_desc),
            ("embed", embed_desc),
            ("plain", plain_desc),
            ("mention", mention_desc),
            ("channel", channel_desc),
            ("publish", None),
        ]
        view = AnnouncementView(ctx, announcement, input_sessions=sessions)
        await view.create_base()

        await view.wait()
        if not announcement.ready_to_post():
            # cancelled or timed out
            return

        await announcement.send()

        if announcement.channel == ctx.channel:
            view.stop()
            try:
                await ctx.message.delete()
                await view.message.delete()
            except discord.Forbidden:
                logger.warning(f"Missing `Manage Messages` permission in {ctx.channel} channel.")
            return

        embed = view.message.embeds[0]
        description = f"Announcement has been posted in {announcement.channel.mention}.\n\n"
        if announcement.channel.type == discord.ChannelType.news:
            description += "Would you like to publish the announcement?\n\n"
            view.fill_items(confirmation=True)
        else:
            view.stop()

        embed.description = description
        await view.message.edit(embed=embed, view=view)
        if view.is_finished():
            return

        await view.wait()

        if view.confirmed is not None:
            hyper_link = f"[announcement]({announcement.message.jump_url})"
            if view.confirmed:
                await announcement.publish()
                embed.description = f"Successfully published this {hyper_link} to all following servers.\n\n"
            else:
                embed.description = (
                    f"To manually publish this {hyper_link}, use command:\n"
                    f"```\n{ctx.prefix}publish {announcement.channel.id}-{announcement.message.id}\n```"
                )
            await view.message.edit(embed=embed, view=None)

    @announce.command(name="quick")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def announce_quick(self, ctx: commands.Context, channel: discord.TextChannel, *, content: str):
        """
        Create a quick plain text announcement.

        `channel` may be a channel ID, mention, or name.
        """
        await channel.send(content)

    @commands.command(
        help=(
            "Publish a message from announcement channel to all channels in other servers that are "
            "following the channel.\n\n"
            "`message` may be a message ID, format of `channel_id`-`message_id` "
            "(e.g. `1079077919915266210-1079173422967439360`), or message link.\n\n"
            "__**Notes:**__\n"
            "- If message ID is provided (without channel ID and not the message link), the bot will only "
            "look for the message in the current channel.\n"
            f"- Only messages in {news_channel_hyperlink} channels can be published."
        ),
    )
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def publish(self, ctx: commands.Context, *, message: discord.Message):
        """
        Publish a message from announcement channel.
        """
        channel = message.channel
        if not channel.type == discord.ChannelType.news:
            raise commands.BadArgument(f"Channel {channel.mention} is not an announcement channel.")
        if message.flags.crossposted:
            raise commands.BadArgument(f"Message `{message.id}` is already published.")

        await message.publish()
        embed = discord.Embed(
            description=f"Successfully published this [message]({message.jump_url}) to all following servers.",
            color=self.bot.main_color,
        )
        await ctx.reply(embed=embed)


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(Announcement(bot))
