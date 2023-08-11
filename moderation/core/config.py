from __future__ import annotations

from typing import Any, Dict, Optional, Set, TYPE_CHECKING

import discord
from discord.utils import MISSING

from discord.ext.modmail_utils import BaseConfig, Config


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

    from ..moderation import Moderation


__all__ = ("GuildConfig", "ModConfig")


_default_config = {
    "log_channel": str(int()),
    "logging": False,
    "webhook": None,
    "channel_whitelist": [],
}


class GuildConfig(BaseConfig):
    """
    Config instance for a guild.
    """

    def __init__(self, cog: Moderation, guild: discord.Guild, data: Dict[str, Any]):
        super().__init__(cog, defaults=_default_config)
        self.guild: discord.Guild = guild
        self.webhook: discord.Webhook = MISSING
        self._cache = data
        self._recursive_resolve_keys(self.defaults, self._cache)

    def __hash__(self):
        return hash((self.guild.id,))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} guild_id={self.guild.id} cache={self.cache}>"

    def __eq__(self, other) -> bool:
        if not isinstance(other, GuildConfig):
            return False
        return self.guild.id == other.guild.id

    async def update(self) -> None:
        """
        Updates the database with config from cache.
        """
        await self.cog.config.update(data={str(self.guild.id): self._cache})

    @property
    def log_channel(self) -> Optional[discord.TextChannel]:
        """
        Returns the log channel.
        """
        channel_id = int(self.get("log_channel"))
        if not channel_id:
            return None
        return self.guild.get_channel(int(channel_id))

    def remove(self, key: str) -> Any:
        if key not in _default_config:
            raise KeyError(f'Configuration key "{key}" is invalid.')
        if key in self.cache:
            del self._cache[key]

        self._cache[key] = self.copy(_default_config[key])


# Configuration
class ModConfig(Config):
    """
    Handles all guild configs.

    Note:
    This instance is not using cache. Value for both `._cache` and `defaults` would be empty dictionary.
    """

    cog: Moderation

    def __init__(self, cog: Moderation, db: AsyncIOMotorCollection):
        super().__init__(cog, db, defaults={}, use_cache=False)
        self.__guild_configs: Set[GuildConfig] = set()

    async def fetch(self, *args, **kwargs) -> Dict[str, Any]:
        if not self.defaults:
            # we're not using cache for this instance, this is just temporary populated
            # to resolve default keys in fetched data.
            # this must be done before calling super().fetch().
            for guild in self.bot.guilds:
                self.defaults[str(guild.id)] = self.deepcopy(_default_config)
        data = await super().fetch(*args, **kwargs)
        self.defaults.clear()
        for guild in self.bot.guilds:
            config = GuildConfig(self.cog, guild, data=data.pop(str(guild.id), {}))
            self.__guild_configs.add(config)
        return data  # just leftovers or empty dict

    def get_config(self, guild: discord.Guild) -> GuildConfig:
        """Returns config for the guild specified."""
        config = next((c for c in self.__guild_configs if c.guild.id == guild.id), None)
        if config is None:
            config = GuildConfig(self.cog, guild, data={})
            self.__guild_configs.add(config)
        return config
