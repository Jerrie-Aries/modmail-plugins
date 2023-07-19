from enum import Enum


class ReactRules(Enum):

    NORMAL = "NORMAL"  # Allow multiple.
    UNIQUE = "UNIQUE"  # Remove existing role when assigning another role in group.
    VERIFY = "VERIFY"  # Not Implemented yet.
    INVALID = "INVALID"

    @classmethod
    def from_value(cls, value: str) -> "ReactRules":
        try:
            return cls(value)
        except ValueError:
            return cls.INVALID


class TriggerType(Enum):
    REACTION = "REACTION"
    INTERACTION = "INTERACTION"
    INVALID = "INVALID"

    @classmethod
    def from_value(cls, value: str) -> "TriggerType":
        try:
            return cls(value)
        except ValueError:
            return cls.INVALID
