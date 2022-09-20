from __future__ import annotations

from datetime import datetime

import dateutil.parser
from natural.date import duration
from typing import List, Optional, Union, TYPE_CHECKING

from .formatter import format_content_html

if TYPE_CHECKING:
    from .types_ext import (
        AttachmentPayload,
        AuthorPayload,
        LogEntryPayload,
        MessagePayload,
    )


class LogEntry:
    def __init__(self, data: LogEntryPayload):
        self.key: str = data["key"]
        self.open: bool = data["open"]

        self.created_at: datetime = dateutil.parser.parse(data["created_at"])
        if self.created_at.tzinfo is not None:
            self.created_at = self.created_at.replace(tzinfo=None)

        self.human_created_at: str = duration(self.created_at, now=datetime.utcnow())
        self.closed_at: Optional[datetime] = (
            dateutil.parser.parse(data["closed_at"]) if not self.open else None
        )
        if self.closed_at is not None and self.closed_at.tzinfo is not None:
            self.closed_at = self.closed_at.replace(tzinfo=None)

        self.channel_id: int = int(data["channel_id"])
        self.guild_id: int = int(data["guild_id"])
        self.creator: Author = Author(data["creator"])
        self.recipient: Author = Author(data["recipient"])
        self.closer: Author = Author(data["closer"]) if not self.open else None
        self.close_message: str = format_content_html(data.get("close_message") or "")
        self.messages: List[Message] = [Message(m) for m in data["messages"]]
        self.internal_messages: List[Message] = [m for m in self.messages if m.type == "internal"]
        self.thread_messages: List[Message] = [
            m for m in self.messages if m.type not in ("internal", "system")
        ]

    @property
    def system_avatar_url(self) -> str:
        return "https://i.imgur.com/2fMgWZT.png"

    @property
    def human_closed_at(self) -> str:
        return duration(self.closed_at, now=datetime.utcnow())

    @property
    def message_groups(self) -> List[MessageGroup]:
        groups = []

        if not self.messages:
            return groups

        curr = MessageGroup(self.messages[0].author)

        for index, message in enumerate(self.messages):
            next_index = index + 1 if index + 1 < len(self.messages) else index
            next_message = self.messages[next_index]

            curr.messages.append(message)

            if message.is_different_from(next_message):
                groups.append(curr)
                curr = MessageGroup(next_message.author)

        groups.append(curr)
        return groups

    def plain_text(self) -> str:
        messages = self.messages
        thread_create_time = self.created_at.strftime("%d %b %Y - %H:%M UTC")
        out = f"Thread created at {thread_create_time}\n"

        if self.creator == self.recipient:
            out += f"[R] {self.creator} "
            out += f"({self.creator.id}) created a Modmail thread. \n"
        else:
            out += f"[M] {self.creator} "
            out += "created a thread with [R] "
            out += f"{self.recipient} ({self.recipient.id})\n"

        out += "────────────────────────────────────────────────\n"

        if messages:
            for index, message in enumerate(messages):
                next_index = index + 1 if index + 1 < len(messages) else index
                curr, next_ = message.author, messages[next_index].author

                author = curr
                user_type = "M" if author.mod else "R"
                create_time = message.created_at.strftime("%d/%m %H:%M")

                base = f"{create_time} {user_type} "
                base += f"{author}: {message.raw_content}\n"

                for attachment in message.attachments:
                    base += f"Attachment: {attachment}\n"

                out += base

                if curr != next_:
                    out += "────────────────────────────────\n"
                    # current_author = author

        if not self.open:
            if messages:  # only add if at least 1 message was sent
                out += "────────────────────────────────────────────────\n"

            out += f"[M] {self.closer} ({self.closer.id}) "
            out += "closed the Modmail thread. \n"

            closed_time = self.closed_at.strftime("%d %b %Y - %H:%M UTC")
            out += f"Thread closed at {closed_time} \n"

        return out


class Author:
    def __init__(self, data: AuthorPayload):
        self.id: int = int(data.get("id"))
        self.name: str = data["name"]
        self.discriminator: str = data["discriminator"]
        self.avatar_url: str = data["avatar_url"]
        self.mod: bool = data["mod"]

    @property
    def default_avatar_url(self) -> str:
        return "https://cdn.discordapp.com/embed/avatars/{}.png".format(int(self.discriminator) % 5)

    def __str__(self) -> str:
        return f"{self.name}#{self.discriminator}"

    def __eq__(self, other: Author) -> bool:
        return self.id == other.id and self.mod is other.mod


class MessageGroup:
    def __init__(self, author: Author):
        self.author: Author = author
        self.messages: List[Message] = []

    @property
    def created_at(self) -> str:
        return self.messages[0].human_created_at

    @property
    def type(self) -> str:
        return self.messages[0].type


class Attachment:
    def __init__(self, data: Union[str, AttachmentPayload]):
        if isinstance(data, str):  # Backwards compatibility
            self.id: int = 0
            self.filename: str = "attachment"
            self.url: str = data
            self.is_image: bool = True
            self.size: int = 0
        else:
            self.id = int(data["id"])
            self.filename: str = data["filename"]
            self.url: str = data["url"]
            self.is_image: bool = data["is_image"]
            self.size: int = data["size"]


class Message:
    def __init__(self, data: MessagePayload):
        self.id: int = int(data["message_id"])

        self.created_at: datetime = dateutil.parser.parse(data["timestamp"])
        if self.created_at.tzinfo is not None:
            self.created_at = self.created_at.replace(tzinfo=None)

        self.human_created_at: str = duration(self.created_at, now=datetime.utcnow())
        self.raw_content: str = data["content"]
        self.content: str = self.format_html_content(self.raw_content)
        self.attachments: List[Attachment] = [Attachment(a) for a in data["attachments"]]
        self.author: Author = Author(data["author"])
        self.type: str = data.get("type", "thread_message")
        self.edited: bool = data.get("edited", False)

    def is_different_from(self, other: Message) -> bool:
        return (
            (other.created_at - self.created_at).total_seconds() > 60
            or other.author != self.author
            or other.type != self.type
        )

    @staticmethod
    def format_html_content(content: str) -> str:
        return format_content_html(content)
