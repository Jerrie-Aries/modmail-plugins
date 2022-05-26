import asyncio
import json
from typing import Dict, List, Optional, Union

import discord
from discord.ext import commands
from discord.ext.commands import (
    BadArgument,
    CheckFailure,
    Converter,
    MessageConverter,
    TextChannelConverter,
)


class StringToEmbed(Converter):
    def __init__(self, content: bool = False):
        self.conversion_type = "json"  # currently only supports JSON
        self.allow_content = content

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Embed:
        data = argument.strip("`")
        data = await self.load_from_json(ctx, data)
        content = self.get_content(data)

        if data.get("embed"):
            data = data["embed"]
        elif data.get("embeds"):
            data = data.get("embeds")[0]
        self.check_data_type(ctx, data)

        fields = await self.create_embed(ctx, data, content=content)
        embed = fields["embed"]
        return embed

    def check_data_type(self, ctx: commands.Context, data, *, data_type=(dict, list)):
        if not isinstance(data, data_type):
            raise BadArgument(
                f"This doesn't seem to be properly formatted embed {self.conversion_type.upper()}. "
                f"Use command `{ctx.bot.prefix}embed example` to see a JSON example."
            )

    async def load_from_json(self, ctx: commands.Context, data: str, **kwargs) -> dict:
        try:
            data = json.loads(data)
        except json.decoder.JSONDecodeError as error:
            return await self.embed_convert_error(ctx, "JSON Parse Error", error)
        self.check_data_type(ctx, data, **kwargs)
        return data

    def get_content(self, data: dict, *, content: str = None) -> Optional[str]:
        content = data.pop("content", content)
        if content is not None and not self.allow_content:
            raise BadArgument("The `content` field is not supported for this command.")
        return content

    async def create_embed(
        self, ctx: commands.Context, data: dict, *, content: str = None
    ) -> Dict[str, Union[discord.Embed, str]]:
        content = self.get_content(data, content=content)

        timestamp = data.get("timestamp")
        if timestamp:
            data["timestamp"] = timestamp.strip("Z")
        try:
            e = discord.Embed.from_dict(data)
            length = len(e)
            if length > 6000:
                raise BadArgument(
                    f"Embed size exceeds Discord limit of 6000 characters ({length})."
                )
        except BadArgument:
            raise
        except Exception as error:
            return await self.embed_convert_error(ctx, "Embed Parse Error", error)
        return {"embed": e, "content": content}

    @staticmethod
    async def embed_convert_error(
        ctx: commands.Context, error_type: str, error: Exception
    ):
        embed = discord.Embed(
            color=ctx.bot.main_color,
            title=f"{error_type}: `{type(error).__name__}`",
            description=f"```py\n{error}\n```",
        )
        embed.set_footer(text=f'Use "{ctx.prefix}embed example" to see an example')
        asyncio.create_task(ctx.send(embed=embed))
        raise CheckFailure


class ListStringToEmbed(StringToEmbed):
    async def convert(
        self, ctx: commands.Context, argument: str
    ) -> List[discord.Embed]:
        data = argument.strip("`")
        data = await self.load_from_json(ctx, data, data_type=(dict, list))

        if isinstance(data, list):
            pass
        elif data.get("embed"):
            data = [data["embed"]]
        elif data.get("embeds"):
            data = data.get("embeds")
            if isinstance(data, dict):
                data = list(data.values())
        else:
            data = [data]
        self.check_data_type(ctx, data, data_type=list)

        embeds = []
        for embed_data in data:
            fields = await self.create_embed(ctx, embed_data)
            embed = fields["embed"]
            embeds.append(embed)
        if embeds:
            return embeds
        else:
            raise BadArgument


class StoredEmbedConverter(Converter):
    async def convert(self, ctx: commands.Context, name: str) -> dict:
        cog = ctx.cog
        data = await cog.db_config()  # can only be used within this cog
        embeds = data.get("embeds", {})
        embed = embeds.get(name)
        if not embed:
            raise BadArgument(f'Embed "{name}" not found.')

        return embed

    def __getitem__(self, item):
        return self[item]


class BotMessage(discord.Message):
    @classmethod
    async def convert(cls, ctx: commands.Context, argument: str) -> discord.Message:
        converter = MessageConverter()
        message = await converter.convert(ctx, argument)
        if message.author.id != ctx.me.id:
            raise BadArgument(f"That is not a message sent by me.")
        elif not message.channel.permissions_for(ctx.me).send_messages:
            raise BadArgument(
                f"I do not have permissions to send/edit messages in {message.channel.mention}."
            )
        return message


class MessageableChannel(discord.TextChannel):
    @classmethod
    async def convert(cls, ctx: commands.Context, argument: str) -> discord.TextChannel:
        converter = TextChannelConverter()
        channel = await converter.convert(ctx, argument)
        my_perms = channel.permissions_for(ctx.me)
        if not (my_perms.send_messages and my_perms.embed_links):
            raise BadArgument(
                f"I do not have permissions to send embeds in {channel.mention}."
            )
        author_perms = channel.permissions_for(ctx.author)
        if not (author_perms.send_messages and author_perms.embed_links):
            raise BadArgument(
                f"You do not have permissions to send embeds in {channel.mention}."
            )
        return channel
