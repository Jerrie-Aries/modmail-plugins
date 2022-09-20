from typing import Literal, Tuple, Union


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
