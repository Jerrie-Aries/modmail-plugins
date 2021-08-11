import argparse
import re
from typing import Tuple, Union

import discord
from dateutil.parser import parse
from discord.ext import commands

from .checks import my_role_hierarchy


class AssignableRole(discord.Role):
    @classmethod
    async def convert(cls, ctx: commands.Context, argument: str) -> discord.Role:
        converter = commands.RoleConverter()
        try:
            role = await converter.convert(ctx, argument)
        except commands.BadArgument:
            raise commands.BadArgument(f'Role "{argument}" not found.')
        if role.managed:
            raise commands.BadArgument(
                f"`{role}` is an integrated role and cannot be assigned."
            )
        allowed = my_role_hierarchy(ctx.guild, role)
        if not allowed:
            raise commands.BadArgument(f"I am not higher than `{role}` in hierarchy.")
        return role


class UnionEmoji(discord.Emoji):
    @classmethod
    async def convert(
        cls, ctx: commands.Context, argument: str
    ) -> Union[discord.Emoji, str]:
        argument = re.sub("\ufe0f", "", argument)  # remove trailing whitespace
        try:
            emoji = await ctx.bot.convert_emoji(argument)  # method in `bot.py`
        except commands.BadArgument:
            raise commands.EmojiNotFound(argument)
        return emoji


class EmojiRoleGroup(commands.Converter):
    """
    A custom converter to convert arguments to :class:`UnionEmoji` and :class:`AssignableRole`.

    Returns
    --------
    Tuple
        A tuple of :class:`Emoji` and :class:`AssignableRole`.
    """

    async def convert(
        self, ctx: commands.Context, argument: str
    ) -> Tuple[Union[discord.Emoji, str], discord.Role]:
        split = argument.split(";")
        if len(split) < 2:
            raise commands.BadArgument

        emoji = await UnionEmoji.convert(ctx, split[0])
        role = await AssignableRole.convert(ctx, split[1])
        return emoji, role


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


class Args(commands.Converter):

    __slots__ = "vals"

    @classmethod
    async def convert(cls, ctx, argument):
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
        discs.add_argument("--disc", nargs="*", dest="disc", default=[])
        discs.add_argument("--not-disc", nargs="*", dest="ndisc", default=[])

        # Roles
        parser.add_argument("--roles", nargs="*", dest="roles", default=[])
        parser.add_argument("--any-role", nargs="*", dest="any-role", default=[])

        parser.add_argument("--not-roles", nargs="*", dest="not-roles", default=[])
        parser.add_argument(
            "--not-any-role", nargs="*", dest="not-any-role", default=[]
        )

        single = parser.add_mutually_exclusive_group()
        single.add_argument("--a-role", dest="a-role", action="store_true")
        single.add_argument("--no-role", dest="no-role", action="store_true")

        # Date stuff
        jd = parser.add_argument_group()
        jd.add_argument("--joined-on", nargs="*", dest="joined-on", default=[])
        jd.add_argument("--joined-before", nargs="*", dest="joined-be", default=[])
        jd.add_argument("--joined-after", nargs="*", dest="joined-af", default="")

        cd = parser.add_argument_group()
        cd.add_argument("--created-on", nargs="*", dest="created-on", default=[])
        cd.add_argument("--created-before", nargs="*", dest="created-be", default=[])
        cd.add_argument("--created-after", nargs="*", dest="created-af", default=[])

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
        parser.add_argument(
            "--not-any-perm", nargs="*", dest="not-any-perm", default=[]
        )

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
                        if (
                            not word.startswith('"')
                            and not word.endswith('"')
                            and not tmp
                        ):
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
                                if (
                                    word.startswith('"')
                                    and word.endswith('"')
                                    and len(word) > 1
                                ):
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
                        raise commands.BadArgument(
                            "A quote was started but never finished."
                        )
                    vals[key] = word_list
        except Exception as e:
            raise commands.BadArgument(str(e))

        if any(
            s
            for s in vals["status"]
            if not s.lower() in ["online", "dnd", "idle", "offline"]
        ):
            raise commands.BadArgument(
                "Invalid status.  Must be either `online`, `dnd`, `idle` or `offline`."
            )

        # Usernames (and Stuff)
        if vals["disc"]:
            new = []
            for disc in vals["disc"]:
                if len(disc) != 4:
                    raise commands.BadArgument(
                        "Discriminators must have the length of 4"
                    )
                try:
                    new.append(int(disc))
                except ValueError:
                    raise commands.BadArgument("Discriminators must be valid integers")
            vals["disc"] = new

        if vals["ndisc"]:
            new = []
            for disc in vals["ndisc"]:
                if len(disc) != 4:
                    raise commands.BadArgument(
                        "Discriminators must have the length of 4"
                    )
                try:
                    new.append(int(disc))
                except ValueError:
                    raise commands.BadArgument("Discriminators must be valid integers")
            vals["ndisc"] = new

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
                vals["joined-on"] = parse(" ".join(vals["joined-on"]))
            except:
                raise commands.BadArgument("Failed to parse --joined-on argument")

        if vals["joined-be"]:
            try:
                vals["joined-be"] = parse(" ".join(vals["joined-be"]))
            except:
                raise commands.BadArgument("Failed to parse --joined-be argument")

        if vals["joined-af"]:
            try:
                vals["joined-af"] = parse(" ".join(vals["joined-af"]))
            except:
                raise commands.BadArgument("Failed to parse --joined-after argument")

        if vals["created-on"]:
            try:
                vals["created-on"] = parse(" ".join(vals["created-on"]))
            except:
                raise commands.BadArgument("Failed to parse --created-on argument")

        if vals["created-be"]:
            try:
                vals["created-be"] = parse(" ".join(vals["created-be"]))
            except:
                raise commands.BadArgument("Failed to parse --created-be argument")

        if vals["created-af"]:
            try:
                vals["created-af"] = parse(" ".join(vals["created-af"]))
            except:
                raise commands.BadArgument("Failed to parse --created-af argument")

        # Activities
        if vals["device"]:
            if not all(d in ["desktop", "mobile", "web"] for d in vals["device"]):
                raise commands.BadArgument(
                    "Bad device.  Must be `desktop`, `mobile` or `web`."
                )

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

        if vals["format"]:
            if not vals["format"][0].lower() in ["menu"]:
                raise commands.BadArgument(
                    "Invalid format.  Must be `menu` for in an embed."
                )
            vals["format"] = vals["format"][0].lower()
        self = cls()
        self.vals = vals
        return vals

    def __getitem__(self, item):
        return self.vals[item]
