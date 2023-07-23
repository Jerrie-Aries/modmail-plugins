from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from discord.ext.modmail_utils import Config


from .models import AutoRoleManager, ReactionRoleManager

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


class RoleManagerConfig(Config):
    """
    Config class for RoleManager.
    """

    def __init__(self, cog: RoleManager, db: AsyncIOMotorCollection):
        super().__init__(cog, db, use_cache=False)

    async def fetch(self) -> ConfigPayload:
        data = await super().fetch()
        if not data:
            # empty dict returned from .fetch()
            data = self.deepcopy(_default_config)
        else:
            data = self._resolve_keys(data)
        return data

    def _resolve_keys(self, data: Dict[str, Any]) -> ConfigPayload:
        """
        This is to prevent unnecessarily updating the database with default config on startup
        mainly when first time loading this plugin.
        """
        keys = _default_config.keys()
        for key in keys:
            if key not in data:
                data[key] = self.deepcopy(_default_config[key])
        return data

    async def update(self, *, data: Dict[str, Any] = None) -> None:
        if not data:
            data = self.to_dict()
        await super().update(data=data)

    def to_dict(self) -> ConfigPayload:
        return {
            "autoroles": self.cog.autorole_manager.to_dict(),
            "reactroles": self.cog.reactrole_manager.to_dict(),
        }
