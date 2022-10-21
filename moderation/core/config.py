from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

import discord
from discord.utils import MISSING

from discord.ext.modmail_utils import Config


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

    from ..moderation import Moderation


DEFAULT_CONFIG = {
    "log_channel": str(int()),
    "logging": False,
    "webhook": None,
}


# Configuration
class ModConfig(Config):
    """
    A class to handle Mod's configurations per guild.
    """

    cog: Moderation

    def __init__(
        self,
        cog: Moderation,
        db: AsyncIOMotorCollection,
        guild: discord.Guild,
        *,
        data: Dict[str, Any],
    ):
        self.guild: discord.Guild = guild
        defaults = {k: v for k, v in DEFAULT_CONFIG.items()}
        super().__init__(cog, db, defaults=defaults)
        self._cache: Dict[str, Any] = data if data else self.copy(self.defaults)
        self.webhook: discord.Webhook = MISSING

    async def update(self) -> None:
        """
        Updates the database with config from cache.
        """
        await super().update(data={str(self.guild.id): self._cache})

    @property
    def log_channel(self) -> Optional[discord.TextChannel]:
        """
        Returns the log channel.
        """
        channel_id = self.get("log_channel")
        return self.guild.get_channel(int(channel_id))

    def remove(self, key: str) -> Any:
        if key not in DEFAULT_CONFIG:
            raise KeyError(f'Configuration key "{key}" is invalid.')
        if key in self.cache:
            del self._cache[key]

        self._cache[key] = self.copy(DEFAULT_CONFIG[key])
