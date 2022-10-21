from __future__ import annotations

import copy as copylib
from typing import Any, Dict, ItemsView, List, TypeVar, Union, TYPE_CHECKING

from discord.ext import commands


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot


__all__ = (
    "BaseConfig",
    "Config",
)


CogT = TypeVar("CogT", bound=commands.Cog)
TypeT = TypeVar("TypeT")
VT = TypeVar("VT")
KT = TypeVar("KT")
DataT = Dict[KT, VT]


class BaseConfig:
    """
    Represents a dictionary-like base config to store and manage configurations.

    Parameters
    -----------
    cog : CogT
        The instance of Cog this config belongs to.
    defaults : DataT
        A dictionary containing the default key value pairs.
    use_cache : bool
        Whether should use cache mapping to store config data. If this were set to `False`,
        the dictionary-like methods (e.g `.set()` and `.get()`) cannot be used.
        Defaults to `True`.
    """

    def __init__(self, cog: CogT, *, defaults: DataT = None, use_cache: bool = True):
        self.cog: CogT = cog
        self.bot: ModmailBot = cog.bot
        if defaults is not None:
            if not isinstance(defaults, dict):
                raise TypeError(
                    f"Invalid type for defaults parameter. Expected dict, got {defaults.__class__.__name__} instead."
                )
        self.defaults: DataT = self.deepcopy(defaults) if defaults is not None else None
        self._use_cache: bool = use_cache
        self._cache: DataT = {}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} cog='{self.cog.qualified_name}' cache={self._cache}>"

    def cache_enabled(self) -> bool:
        """
        Returns `True` if this instance is using cache mapping to store data. Otherwise, `False`.
        """
        return self._use_cache

    @property
    def cache(self) -> DataT:
        return self._cache

    def __setitem__(self, key: KT, item: VT) -> None:
        if not self.cache_enabled():
            raise NotImplementedError("Method is not allowed due to disabled cache.")
        if not isinstance(key, str):
            raise TypeError(f"Expected str object for parameter key, got {type(key).__name__} instead.")
        self._cache[key] = item

    def __getitem__(self, key: KT) -> VT:
        if not self.cache_enabled():
            raise NotImplementedError("Method is not allowed due to disabled cache.")
        return self._cache[key]

    def __delitem__(self, key: KT) -> None:
        if not self.cache_enabled():
            raise NotImplementedError("Method is not allowed due to disabled cache.")
        del self._cache[key]

    def set(self, key: KT, item: VT) -> None:
        """
        Sets an item.
        """
        return self.__setitem__(key, item)

    def get(self, key: KT, default: TypeT = None) -> Union[VT, TypeT]:
        """
        Gets an item from config.
        """
        return self._cache.get(key, default)

    def remove(self, key: KT, *, restore_default: bool = False) -> None:
        """
        Removes item from config.
        """
        self.__delitem__(key)
        if restore_default:
            self._cache[key] = self.deepcopy(self.defaults[key])

    def keys(self) -> List[KT]:
        """
        Returns the list of config keys.
        """
        return self._cache.keys()

    def values(self) -> List[VT]:
        """
        Returns the list of config values.
        """
        return self._cache.values()

    def items(self) -> ItemsView[KT, VT]:
        """
        Returns a sequence of key value pair tuples.
        """
        return self._cache.items()

    @staticmethod
    def copy(obj: TypeT) -> TypeT:
        """
        Returns a shallow copy of object.
        """
        return copylib.copy(obj)

    @staticmethod
    def deepcopy(obj: TypeT) -> TypeT:
        """
        Returns a deep copy of object.
        """
        return copylib.deepcopy(obj)


class Config(BaseConfig):
    """
    This class inherits from :class:`BaseConfig` with additional of database support.
    """

    def __init__(self, cog: CogT, db: AsyncIOMotorCollection, **kwargs: Any):
        self._id: str = kwargs.pop("_id", "config")
        super().__init__(cog, **kwargs)
        self.db: AsyncIOMotorCollection = db

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} cog='{self.cog.qualified_name}' id='{self._id}' cache={self._cache}>"
        )

    async def fetch(self) -> DataT:
        """
        Fetches the data from database. If the response data is `None` default data will be returned.

        By default if cache is enabled, this will automatically refresh the cache after the data is retrieved.

        Returns
        -------
        DataT
            The data retrieved or empty dictionary if no data received from the database.
        """
        data = await self.db.find_one({"_id": self._id})
        if data is None:
            if self.defaults is not None:
                data = self.deepcopy(self.defaults)
            else:
                # empty dict to resolve AttributeError in `.refresh`
                data = {}
        if self.cache_enabled():
            self.refresh(data=data)
        return data

    async def update(self, *, data: DataT = None, refresh: bool = False) -> None:
        """
        Updates the database with the new data.

        By default if the data parameter is not provided and cache is enabled, this will insert the data
        from the cache into the database. If you want to change the behaviour consider overriding this method.

        Parameters
        -----------
        data : DataT
            The data to be saved into the database. Defaults to`None`.
        refresh : bool
            Whether to refresh the cache after the operation. Defaults to  `False`.
        """
        if data is None:
            if not self.cache_enabled() or not self.cache:
                # kind of security to prevent data lost
                raise ValueError("Cache is disabled or empty, data parameter must be provided.")
            data = self._cache
        new_data = await self.db.find_one_and_update(
            {"_id": self._id},
            {"$set": data},
            upsert=True,
            return_document=True,
        )
        if refresh:
            self.refresh(data=new_data)

    def refresh(self, *, data: DataT) -> None:
        """
        Refreshes config cache with the provided data.

        Parameters
        -----------
        data : DataT
            The data to cache.
        """
        if not self.cache_enabled():
            raise NotImplementedError("Method is not allowed due to disabled cache.")
        for key, value in data.items():
            self._cache[key] = value
