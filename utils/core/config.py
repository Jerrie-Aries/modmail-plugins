from __future__ import annotations

import copy as copylib
from typing import Any, Dict, ItemsView, List, TypeVar, Union, TYPE_CHECKING


# <!-- Developer -->
from discord.ext import commands

# <-- ----- -->


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot


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
    defaults : Dict[str, Any]
        A dictionary containing the default key value pairs.
    """

    def __init__(self, cog: CogT, **kwargs: Any):
        self.cog: CogT = cog
        self.bot: ModmailBot = cog.bot
        self.defaults: DataT = self.deepcopy(kwargs.pop("defaults", {}))
        self._cache: DataT = {}

        # extras will be deleted
        del kwargs

    def __repr__(self) -> str:
        return repr(self._cache)

    def __str__(self) -> str:
        return f"<BaseConfig cache={self._cache}>"

    @property
    def cache(self) -> DataT:
        return self._cache

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

    def __setitem__(self, key: KT, item: VT) -> None:
        if not isinstance(key, str):
            raise TypeError(f"Expected str object for parameter key, got {type(key).__name__} instead.")
        self._cache[key] = item

    def __getitem__(self, key: KT) -> VT:
        return self._cache[key]

    def __delitem__(self, key: KT) -> None:
        return self.remove(key)

    def get(self, key: KT, default: TypeT = None) -> Union[VT, TypeT]:
        """
        Gets an item from config.
        """
        return self._cache.get(key, default)

    def set(self, key: KT, item: VT) -> None:
        """
        Sets an item.
        """
        return self.__setitem__(key, item)

    def remove(self, key: KT, *, restore_default: bool = False) -> None:
        """
        Removes item from config.
        """
        del self._cache[key]
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


class Config(BaseConfig):
    """
    This class inherits from :class:`BaseConfig` with additional database supports.
    """

    def __init__(self, cog: CogT, db: AsyncIOMotorCollection, **kwargs: Any):
        self._id: str = kwargs.pop("_id", "config")
        super().__init__(cog, **kwargs)
        self.db: AsyncIOMotorCollection = db

    def __str__(self) -> str:
        return f"<Config _id={self._id} cache={self._cache}>"

    async def fetch(self) -> DataT:
        """
        Fetches the data from database and refresh the cache. If the response data is `None` default data
        will be returned.
        """
        data = await self.db.find_one({"_id": self._id})
        if data is None:
            data = self.deepcopy(self.defaults)
        self.refresh(data=data)
        return data

    async def update(self, *, data: DataT = None, refresh: bool = False) -> None:
        """
        Updates the database with the new data.

        By default if data parameter is not provided, this will insert the data
        from cache into the database. If you want to change the behaviour consider
        overriding this method.

        Parameters
        -----------
        data : DataT
            The data to be saved into the database. Defaults to`None`.
        refresh : bool
            Whether to refresh the cache after the operation. Defaults to  `False`.
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

    def refresh(self, *, data: DataT) -> None:
        """
        Refreshes config cache with the provided data.

        Parameters
        -----------
        data : DataT
            The data to cache.
        """
        for key, value in data.items():
            self._cache[key] = value
