from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Union, TYPE_CHECKING

import discord
from discord.utils import MISSING

from core.models import getLogger

from .checks import my_role_hierarchy


if TYPE_CHECKING:
    from ..rolemanager import RoleManager
    from .views import ReactionRoleView, RoleManagerButton
    from .types import AutoRoleConfigPayload, ReactRolePayload, ReactRoleConfigPayload
else:
    ReactionRoleView = None


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


class TriggerType:
    REACTION = "REACTION"
    INTERACTION = "INTERACTION"


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
            "roles": list(self.roles),
            "enable": self.is_enabled(),
        }


class ReactionRole:
    """
    A ReactionRole object that is attached to a message.

    Parameters
    ----------
    message : Union[discord.PartialMessage, discord.Message]
        The message where the reactions are attached to.
    trigger_type: str
        The type of trigger that this reaction roles response to.
        Should be reaction or interaction.
    binds : List[Dict[str, Any]]
        List of bind data attached to the message.
    rules : str
        The rules applied for the reactions.
    """

    def __init__(
        self,
        message: Union[discord.PartialMessage, discord.Message],
        *,
        trigger_type: str = TriggerType.REACTION,
        binds: List[Dict[str, Any]],
        rules: str = ReactRules.NORMAL,
    ):
        self.message: Union[discord.PartialMessage, discord.Message] = message
        self.channel: discord.TextChannel = message.channel
        self.trigger_type: str = trigger_type
        self.binds: List[Dict[str, Any]] = binds
        self.rules: str = rules
        self.manager: ReactionRoleManager = MISSING
        self.view: ReactionRoleView = MISSING

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
        Instantiate this class from raw data. This will automatically add persistent
        view to the bot if the trigger type is interaction.
        """
        channel_id = data.pop("channel")
        channel = manager.cog.bot.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel with ID {channel_id} not found.")
        message = discord.PartialMessage(id=data.pop("message"), channel=channel)
        trigger_type = data.pop("type", TriggerType.REACTION)
        instance = cls(
            message,
            trigger_type=trigger_type,
            binds=data.pop("binds"),
            rules=data.pop("rules"),
        )
        if trigger_type == TriggerType.INTERACTION:
            instance.view = ReactionRoleView(manager.cog, message, model=instance)
            manager.cog.bot.add_view(instance.view, message_id=message.id)
        return instance

    def delete_set_roles(self, role_list: List[str]) -> None:
        for role_id in role_list:
            for bind in self.binds:
                if role_id == bind["role"]:
                    self.binds.remove(bind)

    def resolve_unique(self, member: discord.Member, role: discord.Role) -> List[discord.Role]:
        ret = []
        for bind in self.binds:
            role_id = bind["role"]
            if role_id == str(role.id):
                continue
            _role = member.guild.get_role(int(role_id))
            if _role is not None and _role in member.roles:
                ret.append(_role)
        return ret

    async def resolve_role(self, role_id: int) -> Option[discord.Role]:
        role = self.channel.guild.get_role(role_id)
        if not role:
            logger.error(f"Role with ID {role_id} was deleted.")
            self.delete_set_roles([str(role_id)])
            await self.manager.cog.config.update()
            if self.view:
                await self.view.update_view()
            return None

        if not my_role_hierarchy(self.channel.guild, role):
            logger.error(f"Role {role} outranks me.")
            return None
        return role

    def to_dict(self) -> ReactRolePayload:
        return {
            "message": self.message.id,
            "channel": self.channel.id,
            "binds": self.binds,
            "rules": self.rules,
            "type": self.trigger_type,
        }


class ReactionRoleManager:
    """
    A class to store and manage reaction roles.
    """

    def __init__(self, cog: RoleManager, *, data: ReactRoleConfigPayload):
        self.cog: RoleManager = cog
        self._enable: bool = data.pop("enable", True)
        self.entries: Set[ReactionRole] = set()

        self._unresolved: List[ReactRolePayload] = []
        self._populate_entries_from_data(data=data.pop("data"))

    def _populate_entries_from_data(self, *, data: List[ReactRolePayload]) -> None:
        global ReactionRoleView
        from .views import ReactionRoleView  # circular

        for entry in data:
            try:
                reactrole = ReactionRole.from_data(self, data=entry)
            except ValueError:
                self._unresolved.append(entry)
                continue
            self.add(reactrole)

    def get_unresolved(self) -> List[ReactRolePayload]:
        """
        Gets unresolved reaction role data.
        """
        return self._unresolved

    def resolve_broken(self) -> int:
        """
        A helper to resolve the unresolved data.
        """
        fixed = 0
        for data in self._unresolved:
            try:
                reactrole = ReactionRole.from_data(self, data=data)
            except ValueError:
                continue
            self._unresolved.remove(data)
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
        instance.manager = self

    def remove(self, message_id: int) -> None:
        """
        Removes a ReactionRole object that matches the message ID from entries.
        """
        entry = self.find_entry(message_id)
        if entry is None:
            raise ValueError(f"ReactionRole entry with message ID {message_id} not found.")
        view = entry.view
        if view:
            view.stop()
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
        trigger_type: str = TriggerType.REACTION,
        binds: List[Dict[str, Any]] = None,
        rules: str = ReactRules.NORMAL,
        add: bool = False,
    ) -> ReactionRole:
        """
        Create a new ReactionRole instance.

        Parameters
        ----------
        message : Union[discord.PartialMessage, discord.Message]
            The message where the reactions are attached to.
        trigger_type: str
            The type of trigger that this reaction roles response to.
            Should be reaction or interaction.
        binds : List[Dict[str, Any]]
            List of bind data attached to the message.
        rules : str
            The rules applied for the reactions.
        add : bool
            Whether or not the instance created should be added to the entries.
        """
        if binds is None:
            binds = []
        instance = ReactionRole(message, trigger_type=trigger_type, binds=binds, rules=rules)
        if add:
            self.add(instance)
        return instance

    async def handle_interaction(
        self,
        reactrole: ReactionRole,
        interaction: discord.Interaction,
        button: RoleManagerButton,
    ) -> None:
        if not self.is_enabled():
            embed = discord.Embed(
                color=self.cog.bot.error_color,
                description="Reaction roles feature is currently disabled.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if reactrole.trigger_type != TriggerType.INTERACTION or interaction.user.bot:
            return

        member = reactrole.channel.guild.get_member(interaction.user.id)
        role_id = button.custom_id.split("-")[-1]
        role = await reactrole.resolve_role(int(role_id))
        if role is None:
            return

        embed = discord.Embed(color=self.cog.bot.main_color)
        if role not in member.roles:
            await member.add_roles(role, reason="Reaction role.")
            embed.description = f"Role {role.mention} has been added to you.\n\n"
            if reactrole.rules == ReactRules.UNIQUE:
                to_remove = reactrole.resolve_unique(member, role)
                if to_remove:
                    await member.remove_roles(*to_remove, reason="Reaction role.")
                    embed.description += "__**Removed:**__\n" + "\n".join(r.mention for r in to_remove)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await member.remove_roles(role, reason="Reaction role.")
            embed.description = f"Role {role.mention} is now removed from you."
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def handle_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        if not self.is_enabled() or payload.guild_id is None:
            return

        reactrole = self.find_entry(payload.message_id)
        if not reactrole or reactrole.trigger_type != TriggerType.REACTION:
            return

        guild = self.cog.bot.get_guild(payload.guild_id)
        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot or not guild.me.guild_permissions.manage_roles:
            return

        role_id = None
        for bind in reactrole.binds:
            emoji = bind.get("emoji")
            if emoji and str(payload.emoji) == emoji:
                role_id = bind["role"]
                break
        else:
            return

        role = await reactrole.resolve_role(int(role_id))
        if role is None:
            return

        if payload.event_type == "REACTION_ADD":
            if role not in member.roles:
                await member.add_roles(role, reason="Reaction role.")
            if reactrole.rules == ReactRules.UNIQUE:
                to_remove = reactrole.resolve_unique(member, role)
                if to_remove:
                    await member.remove_roles(*to_remove, reason="Reaction role.")
        else:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role.")

    def to_dict(self) -> ReactRoleConfigPayload:
        data = [entry.to_dict() for entry in self.entries]
        # store the unresolved data back in the database
        # in case there were permissions issue that made the data couldn't be resolved
        # TODO: Timeout for unresolved, then purge
        unresolved = self.get_unresolved()
        if unresolved:
            data.extend(unresolved)
        return {
            "enable": self.is_enabled(),
            "data": data,
        }
