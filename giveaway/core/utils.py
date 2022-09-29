from __future__ import annotations

import math

from typing import Optional, TYPE_CHECKING

from core.time import UserFriendlyTime

if TYPE_CHECKING:
    from datetime import datetime

    from discord.ext import commands


# Parsing and Conversion
duration_syntax = (
    "`30m` or `30 minutes` = 30 minutes\n"
    "`2d` or `2days` or `2day` = 2 days\n"
    "`1mo` or `1 month` = 1 month\n"
    "`7 days 12 hours` or `7days12hours` (with/without spaces)\n"
    "`6d12h` (this syntax must be without spaces)\n"
)


def format_time_remaining(giveaway_time: int) -> str:
    attrs = ["days", "hours", "minutes"]
    delta = {
        "days": math.floor(giveaway_time // 86400),
        "hours": math.floor(giveaway_time // 3600 % 24),
        "minutes": math.floor(giveaway_time // 60 % 60),
    }
    output = []
    for attr in attrs:
        value = delta.get(attr)
        if value:
            output.append(f"{value} {attr if value != 1 else attr[:-1]}")
    return " ".join(output) if output else "less than 1 minute"


async def time_converter(
    ctx: commands.Context, argument: str, *, now: Optional[datetime] = None
) -> UserFriendlyTime:
    return await UserFriendlyTime().convert(ctx, argument, now=now)
