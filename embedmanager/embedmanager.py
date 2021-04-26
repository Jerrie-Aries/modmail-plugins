import io
import json
from typing import Dict, Optional

import discord
from discord.ext import commands

from core import checks
from core.paginator import EmbedPaginatorSession
from core.models import PermissionLevel

from .converters import (
    MessageableChannel,
    BotMessage,
    StoredEmbedConverter,
    StringToEmbed,
)
from .utils import inline, human_join, paginate

JSON_CONVERTER = StringToEmbed()
JSON_CONTENT_CONVERTER = StringToEmbed(content=True)


JSON_EXAMPLE = """
{
    "title": "JSON Example",
    "description": "This embed is an example to show various features that can be used in a rich embed.",
    "url": "https://example.com",
    "color": 2616205,
    "fields": [
        {
            "name": "Field 1",
            "value": "This field is not within a line."
       },
        {
            "name": "Field 2",
            "value": "This is also not inline."
        },
        {
            "name": "Field 3",
            "value": "This field will be inline.",
            "inline": true
        },
        {
            "name": "Field 4",
            "value": "This field is also within a line.",
            "inline": true
        }
    ],
    "author": {
            "name": "Author Name",
            "url": "https://example.com",
            "icon_url": "https://link.to/some/image.png"
    },
    "footer": {
        "text": "Footer text",
        "icon_url": "https://link.to/some/image.png"
    },
    "image": {
        "url": "https://link.to/some/image.png"
    },
    "thumbnail": {
        "url": "https://link.to/some/image.png"
    }
}
"""


YES_EMOJI = "✅"
NO_EMOJI = "❌"


class EmbedManager(commands.Cog, name="Embed Manager"):
    """
    Create, post, and store embeds.

    __**About:**__
    This plugin is a modified version of `embedutils` cog made by [PhenoM4n4n](https://github.com/phenom4n4n).
    Original repository can be found [here](https://github.com/phenom4n4n/phen-cogs/tree/master/embedutils).
    Any credits must go to original developer of this cog.

    __**Note:**__
    The JSON must be in the format expected by this [Discord documentation](https://discord.com/developers/docs/resources/channel#embed-object).
    """

    _id = "config"
    default_config = {"embeds": {}}

    def __init__(self, bot):
        """
        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot = bot
        self.db = bot.plugin_db.get_partition(self)

    async def db_config(self) -> Dict:
        # No need to store in cache when initializing the plugin.
        # Only fetch from db when needed.
        config = await self.db.find_one({"_id": self._id})
        if config is None:
            config = {k: v for k, v in self.default_config.items()}
        return config

    async def update_db(self, data: dict):
        await self.db.find_one_and_update(
            {"_id": self._id},
            {"$set": data},
            upsert=True,
        )

    @staticmethod
    async def get_embed_from_message(message: discord.Message, index: int = 0):
        embeds = message.embeds
        if not embeds:
            raise commands.BadArgument("That message has no embeds.")
        index = max(min(index, len(embeds)), 0)
        embed = message.embeds[index]
        if embed.type == "rich":
            return embed
        raise commands.BadArgument("That is not a rich embed.")

    @staticmethod
    async def get_file_from_message(ctx: commands.Context, *, file_types=("json", "txt")) -> str:
        if not ctx.message.attachments:
            raise commands.BadArgument(
                f"Run `{ctx.bot.prefix}{ctx.command.qualified_name}` again, but this time attach an embed file."
            )
        attachment = ctx.message.attachments[0]
        if not any(attachment.filename.endswith("." + ft) for ft in file_types):
            raise commands.BadArgument(
                f"Invalid file type. The file name must end with one of {human_join([inline(ft) for ft in file_types])}."
            )

        content = await attachment.read()
        try:
            data = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise commands.BadArgument("Failed to read embed file contents.") from exc
        return data

    @commands.group(name="embed", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def _embed(self, ctx: commands.Context):
        """
        Base command for Embed Manager.

        __**Note:**__
        The JSON must be in the format expected by this [Discord documentation](https://discord.com/developers/docs/resources/channel#embed-object).
        - Use command `{prefix}embed example` to see a JSON example.
        """
        await ctx.send_help(ctx.command)

    @_embed.command(name="example")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_example(self, ctx: commands.Context):
        """
        Show an example of embed in JSON.
        """
        embed = discord.Embed(color=self.bot.main_color, title="JSON Example")
        embed.description = f"```py\n{JSON_EXAMPLE}\n```"
        await ctx.send(embed=embed)

    @_embed.command(name="simple")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_simple(
        self,
        ctx: commands.Context,
        channel: Optional[MessageableChannel],
        color: Optional[discord.Color],
        title: str,
        *,
        description: str,
    ):
        """
        Post a simple embed.

        Put the title in quotes if it is multiple words.
        """
        channel = channel or ctx.channel
        color = color or self.bot.main_color
        embed = discord.Embed(color=color, title=title, description=description)
        await channel.send(embed=embed)

    @_embed.command(name="json", aliases=["fromjson", "fromdata"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_json(self, ctx: commands.Context, *, data: JSON_CONTENT_CONVERTER):
        """
        Post an embed from valid JSON.
        """
        embed = data
        await ctx.send(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @_embed.command(name="fromfile", aliases=["fromjsonfile", "fromdatafile"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_fromfile(self, ctx: commands.Context):
        """
        Post an embed from a valid JSON file.
        """
        data = await self.get_file_from_message(ctx, file_types=("json", "txt"))
        embed = await JSON_CONTENT_CONVERTER.convert(ctx, data)
        await ctx.send(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @_embed.command(name="message", aliases=["frommsg", "frommessage"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_message(self, ctx: commands.Context, message: discord.Message, index: int = 0):
        """
        Post an embed from a message.

        `message` may be a message ID or message link of the embed.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(message, index)
        await ctx.send(embed=embed)

    @_embed.command(name="download")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_download(self, ctx: commands.Context, message: discord.Message, index: int = 0):
        """
        Download a JSON file for a message's embed.

        `message` may be a message ID or message link of the embed.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(message, index)
        data = embed.to_dict()
        data = json.dumps(data, indent=4)
        fp = io.BytesIO(bytes(data, "utf-8"))
        await ctx.send(file=discord.File(fp, "embed.json"))

    @_embed.command(name="post", aliases=["view", "drop", "show"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_post(
        self, ctx: commands.Context, name: StoredEmbedConverter, channel: MessageableChannel = None
    ):
        """
        Post a stored embed.

        `name` must be a name that was used when storing the embed.
        `channel` may be a channel name, ID, or mention.

        Use command `{prefix}embed store list` to get the list of stored embeds.
        """
        channel = channel or ctx.channel
        await channel.send(embed=discord.Embed.from_dict(name["embed"]))

    @_embed.command(name="info")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_info(self, ctx: commands.Context, name: StoredEmbedConverter):
        """
        Get info about an embed that is stored.

        `name` must be a name that was used when storing the embed.

        Use command `{prefix}embed store list` to get the list of stored embeds.
        """
        embed = discord.Embed(
            title=f"`{name['name']}` Info",
            description=(
                f"Author: <@!{name['author']}>\n"
                f"Length: {len(discord.Embed.from_dict(name['embed']))}"
            ),
        )
        await ctx.send(embed=embed)

    @_embed.group(name="edit", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit(
        self,
        ctx: commands.Context,
        message: BotMessage,
        color: Optional[discord.Color],
        title: str,
        *,
        description: str,
    ):
        """
        Edit a message sent by Bot's embeds.

        `message` may be a message ID or message link of the bot's embed.
        """
        color = color or self.bot.main_color
        embed = discord.Embed(color=color, title=title, description=description)
        await message.edit(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_edit.command(name="json", aliases=["fromjson", "fromdata"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit_json(
        self, ctx: commands.Context, message: BotMessage, *, data: JSON_CONVERTER
    ):
        """
        Edit a message's embed using valid JSON.

        `message` may be a message ID or message link of the bot's embed.
        """
        await message.edit(embed=data)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_edit.command(name="fromfile", aliases=["fromjsonfile", "fromdatafile"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit_fromfile(self, ctx: commands.Context, message: BotMessage):
        """
        Edit a message's embed using a valid JSON file.

        `message` may be a message ID or message link of the bot's embed.
        """
        data = await self.get_file_from_message(ctx, file_types=("json", "txt"))
        embed = await JSON_CONVERTER.convert(ctx, data)
        await message.edit(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_edit.command(name="message", aliases=["frommsg", "frommessage"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit_message(
        self,
        ctx: commands.Context,
        source: discord.Message,
        target: BotMessage,
        index: int = 0,
    ):
        """
        Edit a message's embed using another message's embed.

        `source` may be a message ID or message link of the source embed.
        `target` may be a message ID or message link of the bot's embed you want to edit.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(source, index)
        await target.edit(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @_embed.group(name="store", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store(self, ctx: commands.Context):
        """
        Store commands to store embeds for later use.
        """
        await ctx.send_help(ctx.command)

    @embed_store.command(name="simple")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_simple(
        self,
        ctx: commands.Context,
        name: str,
        color: Optional[discord.Color],
        title: str,
        *,
        description: str,
    ):
        """
        Store a simple embed.

        Put the title in quotes if it has multiple words.
        """
        if not color:
            color = self.bot.main_color
        embed = discord.Embed(color=color, title=title, description=description)
        await ctx.send(embed=embed)
        await self.store_embed(ctx, name, embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="json", aliases=["fromjson", "fromdata"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_json(self, ctx: commands.Context, name: str, *, data: JSON_CONVERTER):
        """
        Store an embed from valid JSON.
        """
        await self.store_embed(ctx, name, data)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="fromfile", aliases=["fromjsonfile", "fromdatafile"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_fromfile(self, ctx: commands.Context, name: str):
        """
        Store an embed from a valid JSON file.
        """
        data = await self.get_file_from_message(ctx, file_types=("json", "txt"))
        embed = await JSON_CONVERTER.convert(ctx, data)
        await self.store_embed(ctx, name, embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="message", aliases=["frommsg", "frommessage"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_message(self, ctx: commands.Context, name: str, message: discord.Message, index: int = 0):
        """
        Store an embed from a message.

        `message` may be a message ID or message link of the embed you want to store.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(message, index)
        await self.store_embed(ctx, name, embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="remove", aliases=["delete", "rm", "del"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_remove(self, ctx: commands.Context, name: str):
        """
        Remove a stored embed.
        """
        db_config = await self.db_config()
        embeds = db_config.get("embeds", {})
        try:
            del embeds[name]
        except KeyError:
            await ctx.send("This is not a stored embed.")
        else:
            await self.update_db(db_config)
            await ctx.send(f"Embed `{name}` is now deleted.")

    @embed_store.command(name="download")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_download(self, ctx: commands.Context, embed: StoredEmbedConverter):
        """
        Download a JSON file from a stored embed.
        """
        data = json.dumps(embed["embed"], indent=4)
        fp = io.BytesIO(bytes(data, "utf-8"))
        await ctx.send(file=discord.File(fp, "embed.json"))

    @embed_store.command(name="list")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_list(self, ctx: commands.Context):
        """
        View stored embeds.
        """
        db_config = await self.db_config()
        _embeds = db_config.get("embeds")
        if not _embeds:
            raise commands.BadArgument("There are no stored embeds.")

        description = [f"{index}. `{embed}`" for index, embed in enumerate(_embeds, start=1)]
        description = "\n".join(description)

        color = self.bot.main_color
        em = discord.Embed(color=color, title=f"Stored Embeds")

        if len(description) > 2048:
            embeds = []
            pages = list(paginate(description, page_length=1024))
            for page in pages:
                embed = em.copy()
                embed.description = page
                embeds.append(embed)
            session = EmbedPaginatorSession(ctx, *embeds)
            await session.run()
        else:
            em.description = description
            await ctx.send(embed=em)

    async def store_embed(self, ctx: commands.Context, name: str, embed: discord.Embed):
        embed = embed.to_dict()
        db_config = await self.db_config()
        embeds = db_config.get("embeds", {})
        embeds[name] = {"author": ctx.author.id, "embed": embed, "name": name}
        await self.update_db(db_config)
        await ctx.send(
            f"Embed stored under the name `{name}`. To post this embed, use command:\n"
            f"`{self.bot.prefix}embed post {name}`"
        )

    async def get_stored_embed(self, ctx: commands.Context, name: str):
        db_config = await self.db_config()
        embeds = db_config.get("embeds")
        try:
            data = embeds[name]
            embed = data["embed"]
        except KeyError:
            await ctx.send("This is not a stored embed.")
            return
        embed = discord.Embed.from_dict(embed)
        return embed, data["author"], data["uses"]


def setup(bot):
    bot.add_cog(EmbedManager(bot))
