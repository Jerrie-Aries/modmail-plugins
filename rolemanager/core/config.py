from __future__ import annotations

from typing import Any, Dict, List, TYPE_CHECKING

from .vendors import Config

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from ..rolemanager import RoleManager
    from ..types import DefaultConfigRaw, ReactRoleConfigRaw


class ReactRules:
    NORMAL = "NORMAL"  # Allow multiple.
    UNIQUE = "UNIQUE"  # Remove existing role when assigning another role in group.
    VERIFY = "VERIFY"  # Not Implemented yet.


DEFAULT_CONFIG: DefaultConfigRaw = {
    "reactroles": {
        "message_cache": {},
        "channels": [],
        "enabled": True,
    },
    "autorole": {
        "roles": [],
        "enabled": False,
    },
}


REACTROLES_DEFAULT: ReactRoleConfigRaw = {
    "message": int(),
    "channel": int(),
    "emoji_role_groups": {},  # "emoji_string": "role_id"
    "rules": ReactRules.NORMAL,
}


class RoleManagerConfig(Config):

    cog: RoleManagerConfig
    cache: DefaultConfigRaw

    def __init__(self, cog: RoleManager, db: AsyncIOMotorCollection):
        super().__init__(cog, db, defaults=DEFAULT_CONFIG)

    @property
    def autorole(self) -> Dict[str, Any]:
        return self.cache["autorole"]

    @property
    def reactroles(self) -> ReactRoleConfigRaw:
        return self.cache["reactroles"]

    @property
    def reactrole_channels(self) -> List[str]:
        return self.reactroles.get("channels", [])

    def new_reactroles(self) -> ReactRoleConfigRaw:
        data = self.deepcopy(REACTROLES_DEFAULT)
        return data
