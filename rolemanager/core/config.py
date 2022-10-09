from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from .models import AutoRoleManager, ReactionRoleManager
from .vendors import Config

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from ..rolemanager import RoleManager
    from .types import ConfigPayload


_default_config: ConfigPayload = {
    "autoroles": {
        "roles": [],
        "enable": False,
    },
    "reactroles": {
        "data": [],
        "enable": True,
    },
}


# TODO: Deprecate
def _resolve_migration(data: Dict[str, Any]) -> Dict[str, Any]:
    # step 1
    if data.get("autorole"):
        data["autoroles"] = data.pop("autorole")
    for key in data:
        if not isinstance(data[key], dict):
            continue
        if "enabled" in data[key]:
            data[key]["enable"] = data[key].pop("enabled")
    # step 2
    rr_data = data["reactroles"]
    if rr_data.get("channels") is not None:
        rr_data.pop("channels")

    # step 3, this will be the data type conversion and reorder
    if rr_data.get("message_cache") is not None:
        # handle msg cache
        msg_cache = rr_data.pop("message_cache")
        # very old key name, the current one would be 'binds'
        g = "emoji_role_groups"
        for msg_id in list(msg_cache):
            if g in msg_cache[msg_id]:
                msg_cache[msg_id]["binds"] = msg_cache[msg_id].pop(g)
                update = True
            trigger_type = msg_cache[msg_id].get("type")
            if not trigger_type:
                msg_cache[msg_id]["type"] = "REACTION"
        # loop again
        for msg_id in list(msg_cache):
            # handle old 'emoji':'role_id' format
            binds = msg_cache[msg_id].get("binds", {})
            flipped = None
            if any(not k.isdigit() for k, _ in binds.items()):
                flipped = {str(v): {"emoji": k} for k, v in binds.items()}
            if flipped is not None:
                msg_cache[msg_id]["binds"] = flipped

        # the last part of migration, here we'll update the type of data
        # and reorder for easier access later on
        for msg_id in list(msg_cache):
            binds = msg_cache[msg_id].get("binds", {})
            if not binds:
                continue
            updated_binds = []
            trigger_type = msg_cache[msg_id].get("type")
            if trigger_type == "INTERACTION":
                # buttons stuff
                for role_id in list(binds):
                    bind_data = {
                        "role": role_id,
                        "button": {k: v for k, v in binds[role_id].items()},
                    }
                    updated_binds.append(bind_data)
            else:
                # normal reaction
                for role_id in list(binds):
                    bind_data = {
                        "role": role_id,
                        "emoji": binds[role_id]["emoji"],
                    }
                updated_binds.append(bind_data)
            msg_cache[msg_id]["binds"] = updated_binds

        # finally convert this to a list
        rr_data["data"] = [msg_cache.pop(msg_id) for msg_id in list(msg_cache)]
    data["reactroles"] = rr_data

    return data


class RoleManagerConfig(Config):
    """
    Config class for RoleManager.
    """

    def __init__(self, cog: RoleManager, db: AsyncIOMotorCollection):
        super().__init__(cog, db, use_cache=False)
        self._autoroles: AutoRoleManager = None
        self._reactroles: ReactionRoleManager = None

    async def fetch(self) -> None:
        data = await super().fetch()
        if data is None:
            data = self.deepcopy(_default_config)
        if data["reactroles"].get("message_cache") is not None:
            data = _resolve_migration(data)
            await self.update(data=data)

        self._resolve_attributes(data)

    def _resolve_attributes(self, data: ConfigPayload) -> None:
        self._autoroles = AutoRoleManager(self.cog, data=data.pop("autoroles"))
        reactroles = data.pop("reactroles")
        self._reactroles = ReactionRoleManager(self.cog, data=reactroles)

    async def update(self, *, data: Dict[str, Any] = None) -> None:
        if not data:
            data = self.to_dict()
        await super().update(data=data)

    @property
    def autoroles(self) -> AutoRoleManager:
        return self._autoroles

    @property
    def reactroles(self) -> ReactionRoleManager:
        return self._reactroles

    def to_dict(self) -> ConfigPayload:
        return {
            "autoroles": self.autoroles.to_dict(),
            "reactroles": self.reactroles.to_dict(),
        }
