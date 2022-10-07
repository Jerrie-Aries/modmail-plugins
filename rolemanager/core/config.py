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
        "message_cache": {},
        "enable": True,
    },
}


# TODO: Deprecate
def _resolve_migration(data: Dict[str, Any]) -> bool:
    update = False
    for key, elems in list(data.items()):
        if not isinstance(elems, dict):
            continue
        for k, v in list(elems.items()):
            if k == "enabled":
                data[key]["enable"] = data[key].pop(k)
                update = True
            if key == "reactroles" and k == "message_cache":
                g = "emoji_role_groups"
                for msg_id in list(v.keys()):
                    if g in v[msg_id]:
                        v[msg_id]["binds"] = v[msg_id].pop(g)
                        update = True
                    trigger_type = v[msg_id].get("type")
                    if not trigger_type:
                        v[msg_id]["type"] = "REACTION"
        if key == "autorole":
            data["autoroles"] = data.pop("autorole")
        if key == "reactroles":
            if data[key].get("channels", None) is not None:
                data[key].pop("channels")
                update = True
    return update


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
        identifier = "autorole" in data or data["reactroles"].get("channels", None) is not None
        if identifier:
            _resolve_migration(data)
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
