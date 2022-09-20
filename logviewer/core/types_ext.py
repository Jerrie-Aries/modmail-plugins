from __future__ import annotations

from typing import (
    Any,
    Dict,
    List,
    Optional,
    TypedDict,
)


RawPayload = Dict[str, Any]


class AuthorPayload(TypedDict):
    id: str
    name: str
    discriminator: str
    avatar_url: str
    mod: bool


class MessagePayload(TypedDict):
    message_id: str
    timestamp: str
    content: str
    attachments: List[AttachmentPayload]
    author: AuthorPayload
    type: str
    edited: Optional[bool]


class AttachmentPayload(TypedDict):
    id: str
    filename: str
    url: str
    is_image: bool
    size: int


class LogEntryPayload(TypedDict):
    key: str
    open: bool
    created_at: str
    closed_at: Optional[str]
    channel_id: str
    guild_id: str
    creator: AuthorPayload
    recipient: AuthorPayload
    closer: Optional[AuthorPayload]
    close_message: Optional[str]
    messages: List[MessagePayload]
