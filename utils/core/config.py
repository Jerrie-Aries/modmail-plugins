from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

import discord

from discord.ext import commands

from core.models import getLogger
from core.utils import tryint

from ..discord.ext.modmail_utils import Config


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

    from ..utils import ExtendedUtils


__all__ = ("UtilsConfig",)


logger = getLogger(__name__)


_default_config: Dict[str, Any] = {
    "confirm_button_accept_label": None,
    "confirm_button_accept_emoji": None,
    "confirm_button_accept_style": discord.ButtonStyle.green.value,
    "confirm_button_deny_label": None,
    "confirm_button_deny_emoji": None,
    "confirm_button_deny_style": discord.ButtonStyle.red.value,
    "developer_channel": None,
}

_enums = {
    "confirm_button_accept_style": discord.ButtonStyle,
    "confirm_button_deny_style": discord.ButtonStyle,
}

_converters = {
    "developer_channel": commands.TextChannelConverter,
}

# keys that accept `None` as value
_optional = {
    "confirm_button_accept_label",
    "confirm_button_accept_emoji",
    "confirm_button_deny_label",
    "confirm_button_deny_emoji",
}

_config_info = {
    "confirm_button_accept_label": {
        "description": "Label for accept confirmation button.",
        "examples": ["`{prefix}{config_set} {key} Confirm`"],
    },
    "confirm_button_accept_emoji": {
        "description": "Emoji for accept confirmation button.",
        "examples": ["`{prefix}{config_set} {key} ✅`"],
    },
    "confirm_button_accept_style": {
        "description": "Button color style. The only available options are as follows:\n- `1` - Blurple\n- `2` - Grey\n- `3` - Green\n- `4` - Red",
        "examples": ["`{prefix}{config_set} {key} 1`"],
    },
    "confirm_button_deny_label": {
        "description": "Label for deny confirmation button.",
        "examples": ["`{prefix}{config_set} {key} Cancel`"],
    },
    "confirm_button_deny_emoji": {
        "description": "Emoji for accept confirmation button.",
        "examples": ["`{prefix}{config_set} {key} ❌`"],
    },
    "confirm_button_deny_style": {
        "description": "Button color style. The only available options are as follows:\n- `1` - Blurple\n- `2` - Grey\n- `3` - Green\n- `4` - Red",
        "examples": ["`{prefix}{config_set} {key} 1`"],
    },
    "developer_channel": {
        "description": (
            "Represents developer channel.\n\n"
            "What is developer channel?\n"
            "- For ease of developing, in the developer channel "
            "bot owners no longer need to use prefixes to execute commands.\n\n"
            "**Notes:**\n"
            "- The channel type for developer channel must only be discord [TextChannel](https://discordpy.readthedocs.io/en/latest/api.html#discord.TextChannel). "
            "Other type of channels are not supported.\n"
            "- It is encouraged to not chat in the developer channel."
        ),
        "examples": [
            "`{prefix}{config_set} {key} {ctx.channel.id}`",
            "`{prefix}{config_set} {key} {ctx.channel.mention}`",
            "`{prefix}{config_set} {key} {ctx.channel.name}`",
        ],
    },
}


class UtilsConfig(Config):
    def __init__(self, cog: ExtendedUtils, db: AsyncIOMotorCollection):
        super().__init__(cog, db, defaults=_default_config)
        self.config_info: Dict[str, str] = _config_info

    async def resolve_conversion(self, ctx: commands.Context, key: str, value: str) -> str:
        """
        A helper to resolve async conversion.
        """
        if key in _converters:
            converter = _converters[key]
            entity = await converter().convert(ctx, value)
            value = str(entity.id)
        return value

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
            if default is not None:
                # do nothing
                pass
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
