from datetime import datetime
from typing import Dict, List, TypedDict, Optional

import discord


class ReactRoleConfigRaw(TypedDict):
    message: int
    channel: int
    emoji_role_groups: Dict[str, int]
    rules: str


MessageCacheRaw = Dict[str, ReactRoleConfigRaw]


class ReactRoleGlobalRaw(TypedDict):
    message_cache: MessageCacheRaw
    channels: List[int]
    enabled: bool


class AutoRoleConfigRaw(TypedDict):
    roles: List[int]
    enabled: bool


class DefaultConfigRaw(TypedDict):
    reactroles: ReactRoleGlobalRaw
    autorole: AutoRoleConfigRaw


_ArgsRawUserName = TypedDict(
    "_ArgsRawUserName",
    {
        "nick": List[str],
        "user": List[str],
        "name": List[str],
        "not-nick": List[str],
        "not-user": List[str],
        "not-name": List[str],
        "a-nick": bool,
        "no-nick": bool,
        "discrim": List[str],
        "not-discrim": List[str],
    },
)

_ArgsRawRole = TypedDict(
    "_ArgsRawRole",
    {
        "roles": List[discord.Role],
        "any-role": List[discord.Role],
        "not-roles": List[discord.Role],
        "not-any-role": List[discord.Role],
        "a-role": bool,
        "no-role": bool,
    },
)

_ArgsRawDateTime = TypedDict(
    "_ArgsRawDateTime",
    {
        "joined-on": Optional[datetime],
        "joined-be": Optional[datetime],
        "joined-af": Optional[datetime],
        "created-on": Optional[datetime],
        "created-be": Optional[datetime],
        "created-af": Optional[datetime],
    },
)

_ArgsRawStatusActivity = TypedDict(
    "_ArgsRawStatusActivity",
    {
        "status": List[str],
        "device": List[str],
        "bots": bool,
        "nbots": bool,
        "at": List[discord.ActivityType],
        "a": List[str],
        "na": bool,
        "aa": bool,
    },
)

_ArgsRawPermissions = TypedDict(
    "_ArgsRawPermissions",
    {
        "perms": List[str],
        "any-perm": List[str],
        "not-perms": List[str],
        "not-any-perm": List[str],
    },
)


class ArgsParserRawData(
    _ArgsRawUserName,
    _ArgsRawRole,
    _ArgsRawDateTime,
    _ArgsRawStatusActivity,
    _ArgsRawPermissions,
):
    format: List[str]
