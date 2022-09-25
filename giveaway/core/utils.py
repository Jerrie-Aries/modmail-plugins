import math


# Parsing and Conversion
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


duration_syntax = (
    "Examples:\n"
    "`30m` or `30 minutes` = 30 minutes\n"
    "`2d` or `2days` or `2day` = 2 days\n"
    "`1mo` or `1 month` = 1 month\n"
    "`7 days 12 hours` or `7days12hours` (with/without spaces)\n"
    "`6d12h` (this syntax must be without spaces)\n"
)
