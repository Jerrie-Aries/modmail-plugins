from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Union, TYPE_CHECKING

import discord

from core.models import getLogger

from .checks import my_role_hierarchy


if TYPE_CHECKING:
    from ..rolemanager import RoleManager
    from .types import AutoRoleConfigPayload, ReactRolePayload, ReactRoleConfigPayload


__all__ = [
    "AutoRoleManager",
    "ReactRules",
    "ReactionRole",
    "ReactionRoleManager",
]


logger = getLogger(__name__)


class ReactRules:
    NORMAL = "NORMAL"  # Allow multiple.
    UNIQUE = "UNIQUE"  # Remove existing role when assigning another role in group.
    VERIFY = "VERIFY"  # Not Implemented yet.


class AutoRoleManager:
    """
    A class to store and manage autoroles.
    """

    def __init__(self, cog: RoleManager, *, data: AutoRoleConfigPayload):
        self.cog: RoleManager = cog
        self.roles: List[str] = data.pop("roles", [])
        self._enable: bool = data.pop("enable", False)

    def is_enabled(self) -> bool:
        """
        Returns `True` if the autorole feature is enabled. Otherwise, `False`.
        """
        return self._enable

    def enable(self) -> None:
        """
        Enables the autorole feature.
        """
        if self._enable:
            raise ValueError("Auto role feature is already enabled.")
        self._enable = True

    def disable(self) -> None:
        """
        Disables the autorole feature.
        """
        if not self._enable:
            raise ValueError("Auto role feature is already disabled.")
        self._enable = False

    async def handle_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        if not self.is_enabled():
            return
        if not self.roles:
            return

        to_add = []
        for role_id in self.roles:
            role = member.guild.get_role(role_id)
            if role is None:
                continue
            to_add.append(role)
        if not to_add:
            return

        try:
            await member.add_roles(*to_add, reason="Autorole.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.error(f"Exception occured when trying to add roles to member {member}.")
            logger.error(f"{type(exc).__name__}: {str(exc)}")
            return

    def to_dict(self) -> AutoRoleConfigPayload:
        return {
            "roles": [r for r in self.roles],
            "enable": self.is_enabled(),
        }


class ReactionRole:
    """
    A ReactionRole object that is attached to a message.

    Parameters
    ----------
    message : Union[discord.PartialMessage, discord.Message]
        The message where the reactions are attached to.
    binds : Dict[str, str]
        Emoji role mapping.
    rules : str
        The rules applied for the reactions.
    """

    def __init__(
        self,
        message: Union[discord.PartialMessage, discord.Message],
        *,
        binds: Dict[str, str],
        rules: str = ReactRules.NORMAL,
    ):
        self.message: Union[discord.PartialMessage, discord.Message] = message
        self.channel: discord.TextChannel = message.channel
        self.binds: Dict[str, str] = binds
        self.rules: str = rules

    def __hash__(self):
        return hash((self.message.id, self.channel.id))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} message_id={self.message.id} binds={self.binds}>"

    def __eq__(self, other) -> bool:
        if not isinstance(other, ReactionRole):
            return False
        return self.message.id == other.message.id and self.channel.id == other.channel.id

    @classmethod
    def from_data(cls, manager: ReactionRoleManager, *, data: ReactRolePayload) -> ReactionRole:
        """
        Instantiate this class from raw data.
        """
        channel_id = data.pop("channel")
        channel = manager.cog.bot.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel with ID {channel_id} not found.")
        message = discord.PartialMessage(id=data.pop("message"), channel=channel)
        return cls(
            message,
            binds=data.pop("binds"),
            rules=data.pop("rules"),
        )

    def to_dict(self) -> ReactRolePayload:
        return {
            "message": self.message.id,
            "channel": self.channel.id,
            "binds": self.binds,
            "rules": self.rules,
        }


class ReactionRoleManager:
    """
    A class to store and manage reaction roles.
    """

    def __init__(self, cog: RoleManager, *, data: ReactRoleConfigPayload):
        self.cog: RoleManager = cog
        self.channels: List[str] = data.pop("channels", [])
        self._enable: bool = data.pop("enable", True)
        self.entries: Set[ReactionRole] = set()

        self._populate_entries_from_data(data=data.pop("message_cache"))
        self._unresolved: Dict[str, Any] = {}

    def _populate_entries_from_data(self, *, data: Dict[str, ReactRolePayload]) -> None:
        for key in list(data.keys()):
            entry = data.pop(key)
            try:
                reactrole = ReactionRole.from_data(self, data=entry)
            except ValueError:
                self._unresolved[key] = entry
                continue
            self.add(reactrole)

    def get_unresolved(self) -> Dict[str, ReactRolePayload]:
        """
        Gets unresolved reaction role data.
        """
        return self._unresolved

    def resolve_broken(self) -> int:
        """
        A helper to resolve the unresolved data.
        """
        fixed = 0
        for key, data in list(self._unresolved.items()):
            try:
                reactrole = ReactionRole.from_data(self, data=data)
            except ValueError:
                continue
            self._unresolved.pop(key)
            self.add(reactrole)
            fixed += 1
        return fixed

    def add(self, instance: ReactionRole) -> None:
        """
        Adds a ReactionRole object to entries.
        """
        if not isinstance(instance, ReactionRole):
            raise TypeError(
                f"Invalid type. Expected type ReactionRole, got {instance.__class__.__name__} instead."
            )
        self.entries.add(instance)

    def remove(self, message_id: int) -> None:
        """
        Removes a ReactionRole object that matches the message ID from entries.
        """
        entry = self.find_entry(message_id)
        if entry is None:
            raise ValueError(f"ReactionRole entry with message ID {message_id} not found.")
        self.entries.remove(entry)

    def is_enabled(self) -> bool:
        """
        Returns `True` if the reaction role feature is enabled. Otherwise, `False`.
        """
        return self._enable

    def enable(self) -> None:
        """
        Enables the reaction role feature.
        """
        if self._enable:
            raise ValueError("Reaction role feature is already enabled.")
        self._enable = True

    def disable(self) -> None:
        """
        Disabled the reaction role feature.
        """
        if not self._enable:
            raise ValueError("Reaction role feature is already disabled.")
        self._enable = False

    def find_entry(self, message_id: int) -> Optional[ReactionRole]:
        """
        Returns the ReactionRole object that matches the message ID provided, if found.
        Otherwise, returns `None`.
        """
        unresolved = self.get_unresolved()
        if unresolved:
            self.resolve_broken()
        return next(
            (e for e in self.entries if e.message.id == message_id),
            None,
        )

    def create_new(
        self,
        message: Union[discord.PartialMessage, discord.Message],
        *,
        binds: Dict[str, str] = None,
        rules: str = ReactRules.NORMAL,
        add: bool = False,
    ) -> ReactionRole:
        """
        Create a new ReactionRole instance.

        Parameters
        ----------
        message : Union[discord.PartialMessage, discord.Message]
            The message where the reactions are attached to.
        binds : Dict[str, str]
            Emoji role mapping.
        rules : str
            The rules applied for the reactions.
        add : bool
            Whether or not the instance created should be added to the entries.
        """
        if binds is None:
            binds = {}
        instance = ReactionRole(message, binds=binds, rules=rules)
        if add:
            self.add(instance)
        return instance

    def bulk_delete_set_roles(
        self,
        message: Union[discord.Message, discord.Object],
        emoji_list: List[str],
    ) -> None:
        reactrole = self.find_entry(message.id)
        if reactrole is None:
            raise ValueError(f'Message ID "{message.id}" is not in cache.')

        binds = reactrole.binds
        if not binds:
            self.remove(message.id)
            return
        for emoji_str in emoji_list:
            if emoji_str in binds:
                del binds[emoji_str]

    async def handle_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        if not self.is_enabled() or payload.guild_id is None:
            return

        reactrole = self.find_entry(payload.message_id)
        if not reactrole:
            return

        guild = self.cog.bot.get_guild(payload.guild_id)
        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot or not guild.me.guild_permissions.manage_roles:
            return

        emoji_str = str(payload.emoji)
        binds = reactrole.binds
        if emoji_str not in binds:
            return

        role_id = binds[emoji_str]
        role = guild.get_role(role_id)
        if not role:
            logger.error(f"Role with ID {role_id} was deleted.")
            self.bulk_delete_set_roles(discord.Object(payload.message_id), [emoji_str])
            await self.cog.config.update()
            return

        if not my_role_hierarchy(guild, role):
            logger.error(f"Role {role} outranks me.")
            return

        if payload.event_type == "REACTION_ADD":
            if role not in member.roles:
                await member.add_roles(role, reason="Reaction role.")
            if reactrole.rules == ReactRules.UNIQUE:
                to_remove = []
                for _id in binds.values():
                    if _id == role_id:
                        continue
                    _role = guild.get_role(_id)
                    if _role is not None and _role in member.roles:
                        to_remove.append(_role)
                if not to_remove:
                    return
                await member.remove_roles(*to_remove, reason="Reaction role.")
        else:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role.")

    def to_dict(self) -> ReactRoleConfigPayload:
        message_cache = {str(entry.message.id): entry.to_dict() for entry in self.entries}

        # store the unresolved data back in the database
        # in case there were permissions issue that made the data couldn't be resolved
        # TODO: Timeout for unresolved, then purge
        unresolved = self.get_unresolved()
        if unresolved:
            message_cache.update(unresolved)
        return {
            "channels": self.channels,
            "enable": self.is_enabled(),
            "message_cache": message_cache,
        }
