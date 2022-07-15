from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, ItemsView, List, TYPE_CHECKING


# <!-- Developer -->
from discord.ext import commands

# < -- ----- -->


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot


class BaseConfig:
    """
    Represents a dictionary-like base config to store and manage configurations.

    Parameters
    -----------
    cog : commands.Cog
        The instance of Cog this config belongs to.
    default : Dict[str, Any]
        A dictionary containing default config.
    """

    def __init__(self, cog: commands.Cog, **kwargs: Any):
        self.cog: commands.Cog = cog
        self.bot: ModmailBot = cog.bot
        self.default: Dict[str, Any] = deepcopy(kwargs.pop("default", {}))
        self._cache: Dict[str, Any] = {}

        # extras will be deleted
        del kwargs

    def __repr__(self) -> str:
        return repr(self._cache)

    def __str__(self) -> str:
        return f"<BaseConfig {self._cache}>"

    @property
    def cache(self) -> Dict[str, Any]:
        return self._cache

    def __setitem__(self, key: str, item: Any) -> None:
        if not isinstance(key, str):
            raise TypeError(f"Expected str object for parameter key, got {type(key).__name__} instead.")
        self._cache[key] = item

    def __getitem__(self, key: str) -> Any:
        return self._cache[key]

    def __delitem__(self, key: str) -> None:
        return self.remove(key)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Gets an item from config.
        """
        return self._cache.get(key, default)

    def set(self, key: str, item: Any) -> None:
        """
        Sets an item.
        """
        return self.__setitem__(key, item)

    def remove(self, key: str, *, restore_default: bool = False) -> None:
        """
        Removes item from config.
        """
        del self._cache[key]
        if restore_default:
            self._cache[key] = deepcopy(self.default[key])

    def keys(self) -> List[str]:
        """
        Returns the list of config keys.
        """
        return self._cache.keys()

    def values(self) -> List[Any]:
        """
        Returns the list of config values.
        """
        return self._cache.values()

    def items(self) -> ItemsView[str, Any]:
        """
        Returns a sequence of key value pair tuples.
        """
        return self._cache.items()


class Config(BaseConfig):
    """
    This class inherits from :class:`BaseConfig` with additional database supports.
    """

    def __init__(self, cog: commands.Cog, db: AsyncIOMotorCollection, **kwargs: Any):
        self._id: str = kwargs.pop("_id", "config")
        super().__init__(cog, **kwargs)
        self.db: AsyncIOMotorCollection = db

    def __str__(self) -> str:
        return f"<Config {self._cache}>"

    async def fetch(self) -> Dict[str, Any]:
        """
        Fetches the data from database.
        """
        data = await self.db.find_one({"_id": self._id})
        if data is None:
            data = deepcopy(self.default)
        self.refresh(data=data)
        return data

    async def update(self, *, data: Dict[str, Any] = None, refresh: bool = False) -> None:
        """
        Updates the database with the new data.
        """
        if data is None:
            data = self._cache
        new_data = await self.db.find_one_and_update(
            {"_id": self._id},
            {"$set": data},
            upsert=True,
            return_document=True,
        )
        if refresh:
            self.refresh(data=new_data)

    def refresh(self, *, data: Dict[str, Any]) -> None:
        """
        Refreshes config cache with the provided data.
        """
        for key, value in data.items():
            self._cache[key] = value
