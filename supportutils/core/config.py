from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from discord.ext.modmail_utils import Config


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot
    from ..supportutils import SupportUtility


_default_config: Dict[str, Any] = {
    "contact": {
        "message": None,
        "channel": None,
        "embed": {
            "title": "Contact Staff",
            "description": "Use button or dropdown below to contact our staff.",
            "footer": None,
        },
        "button": {},
        "select": {
            "options": [],
            "placeholder": "Choose a category",
        },
        "override_dmdisabled": False,
        "confirmation": {
            "enable": True,  # not used for now
            "embed": {
                "title": "Confirm thread creation",
                "description": "Use the button below to confirm thread creation which will directly contact the moderators.",
                "footer": None,
            },
        },
    },
    "feedback": {
        "enable": False,
        "channel": None,
        "embed": {
            "title": "Feedback",
            "description": "Press the button below to give a feedback.",
            "footer": None,
        },
        "button": {},
        "response": "Thanks for your time. Your feedback has been submitted to our staff team.",
        "active_sessions": [],
        "rating": {"enable": False, "placeholder": "Choose a rating"},
    },
    "thread_move": {
        "enable": False,
        "responded": {
            "category": None,
            "embed": {
                "title": None,
                "description": "This thread has been moved from {old_category} to {new_category}.",
                "footer": None,
            },
        },
        "inactive": {
            "timeout": None,
            "category": None,
            "embed": {
                "title": None,
                "description": "This thread has been moved from {old_category} to {new_category} due to inactivity.",
                "footer": None,
            },
            "tasks": {},
        },
    },
}


class SupportUtilityConfig(Config):
    def __init__(self, cog: SupportUtility, db: AsyncIOMotorCollection):
        super().__init__(cog, db, defaults=_default_config)

    @property
    def contact(self) -> Dict[str, Any]:
        return self["contact"]

    @property
    def feedback(self) -> Dict[str, Any]:
        return self["feedback"]

    @property
    def thread_move(self) -> Dict[str, Any]:
        return self["thread_move"]
