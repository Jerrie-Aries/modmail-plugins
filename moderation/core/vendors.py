from __future__ import annotations

from typing import Any, TYPE_CHECKING

from discord.utils import MISSING

if TYPE_CHECKING:
    from ...utils.utils import Config
else:
    Config = MISSING


def _set_globals(**kwargs: Any) -> None:
    global Config
    Config = kwargs.pop("Config")
