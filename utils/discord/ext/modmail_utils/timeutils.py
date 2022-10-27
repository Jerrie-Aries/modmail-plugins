from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from typing import Literal, Optional, SupportsInt

import discord

from .chat_formatting import human_join


__all__ = (
    "datetime_formatter",
    "human_timedelta",
    "humanize_timedelta",
)


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


def humanize_timedelta(
    *, timedelta: Optional[timedelta] = None, seconds: Optional[SupportsInt] = None
) -> str:
    """
    Get an aware human timedelta representation.

    This works with either a timedelta object or a number of seconds.

    Fractional values will be omitted, and values less than 1 second
    an empty string.

    Parameters
    ----------
    timedelta: Optional[timedelta]
        A timedelta object.
    seconds: Optional[SupportsInt]
        A number of seconds.

    Returns
    -------
    str
        A locale aware representation of the timedelta or seconds.

    Raises
    ------
    ValueError
        The function was called with neither a number of seconds nor a timedelta object.
    """

    try:
        obj = seconds if seconds is not None else timedelta.total_seconds()
    except AttributeError:
        raise ValueError("You must provide either a timedelta or a number of seconds")

    seconds = int(obj)
    periods = [
        ("year", "years", 60 * 60 * 24 * 365),
        ("month", "months", 60 * 60 * 24 * 30),
        ("day", "days", 60 * 60 * 24),
        ("hour", "hours", 60 * 60),
        ("minute", "minutes", 60),
        ("second", "seconds", 1),
    ]

    strings = []
    for period_name, plural_period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            if period_value == 0:
                continue
            unit = plural_period_name if period_value > 1 else period_name
            strings.append(f"{period_value} {unit}")

    return human_join(strings, final="and")


MONTHNAMES = {
    "01": "January",
    "02": "February",
    "03": "March",
    "04": "April",
    "05": "May",
    "06": "Jun",
    "07": "July",
    "08": "August",
    "09": "September",
    "10": "October",
    "11": "November",
    "12": "December",
}

DAYNAMES = {
    "0": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
}

# Abbreviated, takes only 3 initial letters
MONTHS_ABBRV = {k: v[:3] for k, v in MONTHNAMES.items()}
DAYS_ABBRV = {k: v[:3] for k, v in DAYNAMES.items()}


TimestampStyle = Literal["f", "F", "d", "D", "t", "T", "R"]


# noinspection PyPep8Naming
class datetime_formatter:
    """
    Datetime formatter. A class to convert and format datetime object.
    """

    @staticmethod
    def time_string(date_time: datetime, tzinfo: timezone = timezone.utc) -> str:
        """
        Converts the datetime object to formatted string with UTC timezone.

        Parameters
        ----------
        date_time : datetime
            A datetime object. Doesn't have to be from the past. This parameter is required.
        tzinfo : timezone
            Timezone info. If not provided, defaults to UTC.

        Returns
        -------
        str : str
            A string of formatted value, e.g. `Sun, 02 Sep 2020 12:56 PM UTC`.
        """
        convert = date_time.replace(tzinfo=tzinfo)
        year = convert.strftime("%Y")
        month = MONTHS_ABBRV.get(convert.strftime("%m"))
        day = convert.strftime("%d")  # use "%-d" to get without zero-padded number
        day_abbrv = DAYS_ABBRV.get(convert.strftime("%w"))
        hour = convert.strftime("%I")
        minute = convert.strftime("%M")
        am_pm = convert.strftime("%p")
        tz_name = convert.strftime("%Z")

        fmt = f"{day_abbrv}, {day} {month} {year}\n{hour}:{minute} {am_pm} {tz_name}"
        return fmt

    @staticmethod
    def age(date_time: datetime) -> str:
        """
        Converts the datetime to an age.

        Parameters
        ----------
        date_time : datetime
            A datetime object. Doesn't have to be from the past. This parameter is required.
            Note, the `date_time` provided here will be compared with `datetime.utcnow()`

        Returns
        -------
        str : str
            A string of formatted age or an empty string if there's no output,
            e.g. `1 year 6 months`.
        """
        if date_time.tzinfo is None:
            date_time = date_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # use `abs` in case the seconds is negative if the
        # `date_time` passed in is a future datetime
        delta = int(abs(now - date_time).total_seconds())

        months, remainder = divmod(delta, 2628000)
        hours, seconds = divmod(remainder, 3600)
        minutes, seconds = divmod(seconds, 60)
        days, hours = divmod(hours, 24)
        years, months = divmod(months, 12)

        attrs = ["years", "months", "days", "hours", "minutes", "seconds"]
        parsed = {
            "years": years,
            "months": months,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
        }

        for attr in attrs:
            value = parsed.get(attr)
            if value:
                value = f"{value} {attr if value != 1 else attr[:-1]}"
                parsed[attr] = value

        if years:
            output = [parsed.get(attr) for attr in attrs[0:3]]
        elif months:
            output = [parsed.get(attr) for attr in attrs[1:3]]
        elif days:
            output = [parsed.get(attr) for attr in attrs[2:4]]
        elif hours:
            output = [parsed.get(attr) for attr in attrs[3:5]]
        elif minutes:
            output = [parsed.get(attr) for attr in attrs[4:]]
        else:
            output = [parsed.get(attrs[-1])]
        output = [v for v in output if v]
        return " ".join(v for v in output if v)  # this could return an empty string

    @staticmethod
    def time_age(date_time: datetime) -> str:
        """
        Formats the datetime to time and age combined together from `format_time` and `format_age`.

        Parameters
        ----------
        date_time : datetime
            A datetime object. Doesn't have to be from the past. This parameter is required
            to intantiate the class.

        Returns
        -------
        str : str
            The formatted string.
        """
        fmt = discord.utils.format_dt(date_time, "F")
        fmt_age = datetime_formatter.age(date_time)
        fmt += f"\n{fmt_age if fmt_age else '.....'} ago"
        return fmt
