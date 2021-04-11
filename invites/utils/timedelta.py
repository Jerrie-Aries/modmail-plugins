from datetime import datetime, timezone


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


class DateTimeFormatter:
    """
    Datetime formatter. A class to convert and format datetime object.
    """

    @staticmethod
    def time(date_time: datetime, tzinfo: timezone = timezone.utc) -> str:
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
        convert = date_time.replace(tzinfo=tzinfo)  # convert to utc timezone
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
        now = datetime.utcnow()
        delta = (now - date_time).total_seconds()  # could be negative if the `date_time` provided is not from the past

        # add `abs` in case `delta` is negative
        months, remainder = divmod(int(abs(delta)), 2628000)
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

    @classmethod
    def time_age(cls, date_time: datetime, tzinfo: timezone = timezone.utc) -> str:
        """
        Formats the datetime to time and age combined together from `format_time` and `format_age`.

        Parameters
        ----------
        date_time : datetime
            A datetime object. Doesn't have to be from the past. This parameter is required
            to intantiate the class.
        tzinfo : timezone
            Timezone info. If not provided, defaults to UTC.

        Returns
        -------
        str : str
            A string of formatted value.
        """
        self = cls()
        fmt = self.time(date_time, tzinfo)
        fmt_age = self.age(date_time)
        fmt += f"\n{fmt_age if fmt_age else '.....'} ago"
        return fmt
