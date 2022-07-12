from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import discord


def human_timedelta(dt: datetime, *, source: datetime = None) -> str:
    """
    Convert datetime object to human readable string.

    All the provided parameters could be datetime objects whether timezone naive or aware,
    conversion will be done inside this function.
    """
    if source is not None:
        if source.tzinfo is None:
            source = source.replace(tzinfo=timezone.utc)
        now = source
    else:
        now = discord.utils.utcnow()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    if dt > now:
        delta = relativedelta(dt, now)
        suffix = ""
    else:
        delta = relativedelta(now, dt)
        suffix = " ago"

    if delta.microseconds and delta.seconds:
        delta = delta + relativedelta(seconds=+1)

    attrs = ["years", "months", "days", "hours", "minutes", "seconds"]

    output = []
    for attr in attrs:
        elem = getattr(delta, attr)
        if not elem:
            continue

        if elem > 1:
            output.append(f"{elem} {attr}")
        else:
            output.append(f"{elem} {attr[:-1]}")

    if not output:
        return "now"
    if len(output) == 1:
        return output[0] + suffix
    if len(output) == 2:
        return f"{output[0]} and {output[1]}{suffix}"
    return f"{output[0]}, {output[1]} and {output[2]}{suffix}"
