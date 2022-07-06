from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from typing import Literal, Tuple, Union

import discord


# noinspection PyPep8Naming
class plural:
    """
    Formats a string to singular or plural based on the length objects it refers to.

    Examples
    --------
    - 'plural(len(data)):member'
    - 'plural(len(data)):entry|entries'
    """

    def __init__(self, value):
        self.value = value

    def __format__(self, format_spec) -> str:
        v = self.value
        singular, _, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        if abs(v) != 1:
            return f"{v} {plural}"
        return f"{v} {singular}"


MessageDaysT = Literal[0, 1, 2, 3, 4, 5, 6, 7]


def parse_delete_message_days(
    args: str,
) -> Tuple[str, Union[MessageDaysT, int]]:
    """
    A method to parse `delete_message_days` from 'reason' parameter in 'Ban' and 'Softban' commands.
    """
    parse_args = [v for v in args.split(" ")]
    days = parse_args[-1]
    reason = " ".join(v for v in parse_args[:-1])

    if days.startswith("--"):
        days = days.strip("--").strip(".")
        if days.isdigit():
            days = int(days)
            if days > 7:
                days = 7
            if not reason:
                reason = None
            return reason, days

    return args, 0


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
