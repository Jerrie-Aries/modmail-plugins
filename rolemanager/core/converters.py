from __future__ import annotations

import argparse
import re
from typing import (
    Optional,
    TYPE_CHECKING,  # need the TYPE_CHECKING to create some lies for type hinting
    Union,
)

import discord
from dateutil.parser import parse as parse_datetime
from emoji import EMOJI_DATA

from .checks import my_role_hierarchy

# <-- Developer -->
from discord.ext import commands

# <-- ----- -->

if TYPE_CHECKING:
    from bot import ModmailBot


__all__ = [
    "Args",
    "AssignableRole",
    "EmojiRoleGroup",
    "ObjectConverter",
    "UnionEmoji",
    "PERMS",
]


class _AssignableRoleConverter(commands.RoleConverter):
    async def convert(self, ctx: commands.Context, argument: str) -> discord.Role:
        try:
            role = await super().convert(ctx, argument)
        except commands.BadArgument:
            raise commands.BadArgument(f'Role "{argument}" not found.')
        if role.managed:
            raise commands.BadArgument(f"`{role}` is an integrated role and cannot be assigned.")
        allowed = my_role_hierarchy(ctx.guild, role)
        if not allowed:
            raise commands.BadArgument(f"I am not higher than `{role}` in hierarchy.")
        return role


class _UnionEmojiConverter(commands.Converter):
    async def convert(
        self, ctx: commands.Context, argument: str
    ) -> Union[discord.Emoji, discord.PartialEmoji]:
        try:
            return self._convert_emoji(ctx.bot, argument)
        except commands.BadArgument:
            raise commands.EmojiNotFound(argument)

    # TODO: PR to implement this in `bot.py`
    @staticmethod
    def _convert_emoji(bot: ModmailBot, name: str) -> Union[discord.Emoji, discord.PartialEmoji]:
        """
        A method to convert the provided string to a :class:`discord.Emoji`, :class:`discord.PartialEmoji`.

        If the parsed emoji has an ID (a custom emoji) and cannot be found, or does not have an ID and
        cannot be found in :class:`EMOJI_DATA` dictionary keys, :class:`commands.EmojiNotFound`
        will be raised.

        Parameters
        -----------
        name : str
            The emoji string or a unicode emoji.

        Returns
        -------
        :class:`discord.Emoji` or :class:`discord.PartialEmoji`
            The converted emoji.
        """
        # remove trailing whitespace
        name = re.sub("\ufe0f", "", name)
        emoji = discord.PartialEmoji.from_str(name)
        if emoji.is_unicode_emoji():
            if emoji.name not in EMOJI_DATA:
                raise ValueError(f"{name} is not a valid unicode emoji.")
        else:
            # custom emoji
            emoji = bot.get_emoji(emoji.id)
            if emoji is None:
                raise commands.EmojiNotFound(name)
        return emoji


if TYPE_CHECKING:

    class AssignableRole(discord.Role):
        async def convert(self, ctx: commands.Context, argument: str) -> AssignableRole:
            ...

    class UnionEmoji(discord.Emoji, discord.PartialEmoji):
        async def convert(self, ctx: commands.Context, argument: str) -> UnionEmoji:
            ...

else:
    AssignableRole = _AssignableRoleConverter
    UnionEmoji = _UnionEmojiConverter


class EmojiRoleGroup(commands.Converter):
    """
    A custom converter to convert arguments to :class:`UnionEmoji`
    and :class:`ManageableRole` that is inherited from discord :class:`Role`.

    Returns
    --------
    Tuple
        A tuple of :class:`Emoji` and :class:`ManageableRole`.
    """

    def __init__(self):
        self.emoji: Optional[UnionEmoji] = None
        self.role: Optional[AssignableRole] = None

    async def convert(self, ctx: commands.Context, argument: str) -> EmojiRoleGroup:
        split = argument.split(";")
        if len(split) < 2:
            raise commands.BadArgument

        self.emoji = await UnionEmoji().convert(ctx, split[0])
        self.role = await AssignableRole().convert(ctx, split[1])
        return self


class ObjectConverter(commands.IDConverter):
    async def convert(self, ctx: commands.Context, argument: str) -> discord.Object:
        match = self._get_id_match(argument)
        if not match:
            raise commands.BadArgument
        return discord.Object(int(match.group(0)))


PERMS = [
    "add_reactions",
    "administrator",
    "attach_files",
    "ban_members",
    "change_nickname",
    "connect",
    "create_instant_invite",
    "deafen_members",
    "embed_links",
    "external_emojis",
    "kick_members",
    "manage_channels",
    "manage_emojis",
    "manage_guild",
    "manage_messages",
    "manage_nicknames",
    "manage_roles",
    "manage_webhooks",
    "mention_everyone",
    "move_members",
    "mute_members",
    "priority_speaker",
    "read_message_history",
    "read_messages",
    "send_messages",
    "send_tts_messages",
    "speak",
    "stream",
    "use_voice_activation",
    "view_audit_log",
]


class NoExitParser(argparse.ArgumentParser):
    def error(self, message):
        raise commands.BadArgument(f"Failed to parse, {message}.")


if TYPE_CHECKING:
    from .types import ArgsParserRawData


class Args(commands.Converter):

    __slots__ = "vals"

    @classmethod
    async def convert(cls, ctx: commands.Context, argument: str) -> ArgsParserRawData:
        argument = argument.replace("—", "--")
        parser = NoExitParser(description="Targeter argument parser", add_help=False)

        # Nicknames / Usernames
        names = parser.add_argument_group()
        names.add_argument("--nick", nargs="*", dest="nick", default=[])
        names.add_argument("--user", nargs="*", dest="user", default=[])
        names.add_argument("--name", nargs="*", dest="name", default=[])

        names.add_argument("--not-nick", nargs="*", dest="not-nick", default=[])
        names.add_argument("--not-user", nargs="*", dest="not-user", default=[])
        names.add_argument("--not-name", nargs="*", dest="not-name", default=[])

        names.add_argument("--a-nick", dest="a-nick", action="store_true")
        names.add_argument("--no-nick", dest="no-nick", action="store_true")

        discs = parser.add_mutually_exclusive_group()
        discs.add_argument("--discrim", nargs="*", dest="discrim", default=[])
        discs.add_argument("--not-discrim", nargs="*", dest="not-discrim", default=[])

        # Roles
        parser.add_argument("--roles", nargs="*", dest="roles", default=[])
        parser.add_argument("--any-role", nargs="*", dest="any-role", default=[])

        parser.add_argument("--not-roles", nargs="*", dest="not-roles", default=[])
        parser.add_argument("--not-any-role", nargs="*", dest="not-any-role", default=[])

        single = parser.add_mutually_exclusive_group()
        single.add_argument("--a-role", dest="a-role", action="store_true")
        single.add_argument("--no-role", dest="no-role", action="store_true")

        # Date stuff
        jd = parser.add_argument_group()
        jd.add_argument("--joined-on", nargs="*", dest="joined-on", default=None)
        jd.add_argument("--joined-before", nargs="*", dest="joined-be", default=None)
        jd.add_argument("--joined-after", nargs="*", dest="joined-af", default=None)

        cd = parser.add_argument_group()
        cd.add_argument("--created-on", nargs="*", dest="created-on", default=None)
        cd.add_argument("--created-before", nargs="*", dest="created-be", default=None)
        cd.add_argument("--created-after", nargs="*", dest="created-af", default=None)

        # Status / Activity / Device / Just Basically Profile Stuff
        parser.add_argument("--status", nargs="*", dest="status", default=[])
        parser.add_argument("--device", nargs="*", dest="device", default=[])

        bots = parser.add_mutually_exclusive_group()
        bots.add_argument("--only-bots", dest="bots", action="store_true")
        bots.add_argument("--no-bots", dest="nbots", action="store_true")

        parser.add_argument("--activity-type", nargs="*", dest="at", default=[])
        parser.add_argument("--activity", nargs="*", dest="a", default=[])

        at = parser.add_mutually_exclusive_group()
        at.add_argument("--no-activity", dest="na", action="store_true")
        at.add_argument("--an-activity", dest="aa", action="store_true")

        # Permissions
        parser.add_argument("--perms", nargs="*", dest="perms", default=[])
        parser.add_argument("--any-perm", nargs="*", dest="any-perm", default=[])

        parser.add_argument("--not-perms", nargs="*", dest="not-perms", default=[])
        parser.add_argument("--not-any-perm", nargs="*", dest="not-any-perm", default=[])

        # Extra
        parser.add_argument("--format", nargs="*", dest="format", default=["menu"])

        try:
            vals = vars(parser.parse_args(argument.split(" ")))
        except Exception as exc:
            raise commands.BadArgument(str(exc)) from exc

        try:
            for key, value in vals.items():
                if type(value) == list:
                    split_words = value
                    word_list = []
                    tmp = ""
                    for word in split_words:
                        if not word.startswith('"') and not word.endswith('"') and not tmp:
                            if word.startswith(r"\""):
                                word = word[1:]
                            word_list.append(word)
                        else:
                            echanged = False
                            if word.endswith(r"\""):
                                word = word[:-2] + '"'
                                echanged = True

                            schanged = False
                            if word.startswith(r"\""):
                                word = word[1:]
                                schanged = True
                            if word.startswith('"') and not schanged:
                                if word.startswith('"') and word.endswith('"') and len(word) > 1:
                                    word_list.append(word)
                                else:
                                    if tmp.endswith(" "):
                                        word_list.append(tmp)
                                        tmp = ""
                                        continue
                                    tmp += word[1:] + " "
                            elif word.endswith('"') and not echanged:
                                tmp += word[:-1]
                                word_list.append(tmp)
                                tmp = ""
                            else:
                                if schanged or echanged:
                                    word_list.append(word)
                                    continue
                                tmp += word + " "
                    if tmp:
                        raise commands.BadArgument("A quote was started but never finished.")
                    vals[key] = word_list
        except Exception as e:
            raise commands.BadArgument(str(e))

        if any(s for s in vals["status"] if not s.lower() in ["online", "dnd", "idle", "offline"]):
            raise commands.BadArgument(
                "Invalid status.  Must be either `online`, `dnd`, `idle` or `offline`."
            )

        # Usernames (and Stuff)

        if vals["discrim"]:
            new = []
            for disc in vals["discrim"]:
                if len(disc) != 4:
                    raise commands.BadArgument("Discriminators must have the length of 4")
                try:
                    new.append(int(disc))
                except ValueError:
                    raise commands.BadArgument("Discriminators must be valid integers")
            vals["discrim"] = new

        if vals["not-discrim"]:
            new = []
            for disc in vals["not-discrim"]:
                if len(disc) != 4:
                    raise commands.BadArgument("Discriminators must have the length of 4")
                try:
                    new.append(int(disc))
                except ValueError:
                    raise commands.BadArgument("Discriminators must be valid integers")
            vals["not-discrim"] = new

        # Roles

        rc = commands.RoleConverter()
        new = []
        for role in vals["roles"]:
            r = await rc.convert(ctx, role)
            if not r:
                raise commands.BadArgument(f"Couldn't find a role matching: {role}")
            new.append(r)
        vals["roles"] = new

        new = []
        for role in vals["any-role"]:
            r = await rc.convert(ctx, role)
            if not r:
                raise commands.BadArgument(f"Couldn't find a role matching: {role}")
            new.append(r)
        vals["any-role"] = new

        new = []
        for role in vals["not-roles"]:
            r = await rc.convert(ctx, role)
            if not r:
                raise commands.BadArgument(f"Couldn't find a role matching: {role}")
            new.append(r)
        vals["not-roles"] = new

        new = []
        for role in vals["not-any-role"]:
            r = await rc.convert(ctx, role)
            if not r:
                raise commands.BadArgument(f"Couldn't find a role matching: {role}")
            new.append(r)
        vals["not-any-role"] = new

        # Dates

        if vals["joined-on"]:
            try:
                vals["joined-on"] = parse_datetime(" ".join(vals["joined-on"]))
            except:
                raise commands.BadArgument("Failed to parse --joined-on argument")

        if vals["joined-be"]:
            try:
                vals["joined-be"] = parse_datetime(" ".join(vals["joined-be"]))
            except:
                raise commands.BadArgument("Failed to parse --joined-be argument")

        if vals["joined-af"]:
            try:
                vals["joined-af"] = parse_datetime(" ".join(vals["joined-af"]))
            except:
                raise commands.BadArgument("Failed to parse --joined-after argument")

        if vals["created-on"]:
            try:
                vals["created-on"] = parse_datetime(" ".join(vals["created-on"]))
            except:
                raise commands.BadArgument("Failed to parse --created-on argument")

        if vals["created-be"]:
            try:
                vals["created-be"] = parse_datetime(" ".join(vals["created-be"]))
            except:
                raise commands.BadArgument("Failed to parse --created-be argument")

        if vals["created-af"]:
            try:
                vals["created-af"] = parse_datetime(" ".join(vals["created-af"]))
            except:
                raise commands.BadArgument("Failed to parse --created-af argument")

        # Activities

        if vals["device"]:
            if not all(d in ["desktop", "mobile", "web"] for d in vals["device"]):
                raise commands.BadArgument("Bad device.  Must be `desktop`, `mobile` or `web`.")

        if vals["at"]:
            at = discord.ActivityType
            switcher = {
                "unknown": at.unknown,
                "playing": at.playing,
                "streaming": at.streaming,
                "listening": at.listening,
                "watching": at.watching,
                "competing": at.competing,
            }
            if not all([a.lower() in switcher for a in vals["at"]]):
                raise commands.BadArgument(
                    "Invalid Activity Type.  Must be either `unknown`, `playing`, `streaming`, `listening`, `competing` or `watching`."
                )
            new = [switcher[name.lower()] for name in vals["at"]]
            vals["at"] = new

        # Permissions

        new = []
        for perm in vals["perms"]:
            perm = perm.replace(" ", "_")
            if not perm.lower() in PERMS:
                raise commands.BadArgument(
                    f"Invalid permission.  Run `{ctx.bot.prefix}target permissions` to see a list of valid permissions."
                )
            new.append(perm)
        vals["perms"] = new

        new = []
        for perm in vals["any-perm"]:
            perm = perm.replace(" ", "_")
            if not perm.lower() in PERMS:
                raise commands.BadArgument(
                    f"Invalid permission.  Run `{ctx.bot.prefix}target permissions` to see a list of valid permissions."
                )
            new.append(perm)
        vals["any-perm"] = new

        new = []
        for perm in vals["not-perms"]:
            perm = perm.replace(" ", "_")
            if not perm.lower() in PERMS:
                raise commands.BadArgument(
                    f"Invalid permission.  Run `{ctx.bot.prefix}target permissions` to see a list of valid permissions."
                )
            new.append(perm)
        vals["not-perms"] = new

        new = []
        for perm in vals["not-any-perm"]:
            perm = perm.replace(" ", "_")
            if not perm.lower() in PERMS:
                raise commands.BadArgument(
                    f"Invalid permission.  Run `{ctx.bot.prefix}target permissions` to see a list of valid permissions."
                )
            new.append(perm)
        vals["not-any-perm"] = new

        # Formats

        if vals["format"]:
            if not vals["format"][0].lower() in ["menu"]:
                raise commands.BadArgument("Invalid format.  Must be `menu` for in an embed.")
            vals["format"] = vals["format"][0].lower()
        self = cls()
        self.vals = vals
        return vals

    def __getitem__(self, item: str):
        return self.vals[item]
