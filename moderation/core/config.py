from __future__ import annotations

from typing import Any, Dict, ItemsView, Optional, TYPE_CHECKING

import discord


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot


# Configuration
class ModConfig:
    """
    A class to handle Mod's configurations per guild.
    """

    default_config = {
        "log_channel": str(int()),
        "logging": False,
    }

    __slots__ = ("_cache", "bot", "db", "guild", "id")

    @classmethod
    def populate(
        cls, bot: ModmailBot, db: AsyncIOMotorCollection, guild: discord.Guild, data: Dict[str, Any]
    ) -> "ModConfig":
        """
        Parameters
        -----------
        bot: ModmailBot
            The Modmail Bot.
        db: AsyncIOMotorCollection
            The database collection for this cog.
        guild: discord.Guild
            Guild object. The guild that owns this configurations.
        data: Dict[str, Any]
            A dictionary that stores the configurations.
            If an empty dictionary is passed in, the default keys and values will be added into it.
        """
        self = cls()

        # ## the basic configuration
        self.bot = bot
        self.db = db
        self.guild = guild
        self._cache = data if data else {k: v for k, v in self.default_config.items()}
        self.id = str(guild.id)
        return self

    def __repr__(self) -> str:
        return repr(self._cache)

    @property
    def cache(self) -> Dict[str, Any]:
        return self._cache

    async def update_db(self) -> None:
        """
        Updates the database with config from cache.
        """
        _id = "config"
        await self.db.find_one_and_update(
            {"_id": _id},
            {"$set": {str(self.id): self._cache}},
            upsert=True,
        )

    @property
    def log_channel(self) -> Optional[discord.TextChannel]:
        channel_id = self.get("log_channel")
        return self.guild.get_channel(int(channel_id))

    def __setitem__(self, key: str, item: Any) -> None:
        key = key.lower()
        self._cache[key] = item

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __delitem__(self, key: str) -> None:
        return self.remove(key)

    def get(self, key: str) -> Any:
        value = self.cache[key]
        return value

    def set(self, key: str, item: Any) -> None:
        return self.__setitem__(key, item)

    def remove(self, key: str) -> Any:
        if key not in self.default_config:
            raise KeyError(f'Configuration "{key}" is invalid.')
        if key in self.cache:
            del self._cache[key]

        self._cache[key] = {k: v for k, v in self.default_config.items()}
        return self._cache[key]

    def items(self) -> ItemsView:
        return self.cache.items()
