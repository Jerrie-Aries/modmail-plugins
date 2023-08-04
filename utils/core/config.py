from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

import discord

from core.models import getLogger
from core.utils import tryint

from ..discord.ext.modmail_utils import Config


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

    from ..utils import ExtendedUtils


logger = getLogger(__name__)


_default_config: Dict[str, Any] = {
    "confirm_button_accept_label": None,
    "confirm_button_accept_emoji": None,
    "confirm_button_accept_style": discord.ButtonStyle.green.value,
    "confirm_button_deny_label": None,
    "confirm_button_deny_emoji": None,
    "confirm_button_deny_style": discord.ButtonStyle.red.value,
}

_enums = {
    "confirm_button_accept_style": discord.ButtonStyle,
    "confirm_button_deny_style": discord.ButtonStyle,
}

# keys that accept `None` as value
_optional = {
    "confirm_button_accept_label",
    "confirm_button_accept_emoji",
    "confirm_button_deny_label",
    "confirm_button_deny_emoji",
}


class UtilsConfig(Config):
    def __init__(self, cog: ExtendedUtils, db: AsyncIOMotorCollection):
        super().__init__(cog, db, defaults=_default_config)

    def set(self, key: str, item: Any) -> None:
        """
        Sets an item.
        """
        if key in _enums:
            if isinstance(item, (_enums[key])):
                # value is an enum type
                item = item.value
            else:
                item = tryint(item)
        if key in _optional:
            if isinstance(item, str) and item.lower() == "none":
                item = None

        return self.__setitem__(key, item)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Gets an item from config.
        """
        key = key.lower()
        if key not in _default_config:
            raise KeyError(f"{key} is invalid key.")
        if key not in self._cache:
            self._cache[key] = self.deepcopy(_default_config[key])
        value = self._cache[key]

        if key in _enums:
            if value is None:
                return None
            try:
                value = _enums[key](tryint(value))
            except ValueError:
                logger.warning(f"{value} is invalid for key {key}.")
                value = self.remove(key)
        return value
