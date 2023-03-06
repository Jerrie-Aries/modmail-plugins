from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Union, TYPE_CHECKING

import discord
from discord.utils import MISSING

from core.models import getLogger


if TYPE_CHECKING:
    from ..rolemanager import RoleManager
    from .views import Button, ReactionRoleView
    from .types import AutoRoleConfigPayload, ReactRolePayload, ReactRoleConfigPayload
else:
    Button = None
    ReactionRoleView = None


__all__ = [
    "AutoRoleManager",
    "Bind",
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

    async def update(self) -> None:
        await self.cog.config.update(data={"autoroles": self.to_dict()})

    def to_dict(self) -> AutoRoleConfigPayload:
        return {
            "roles": list(self.roles),
            "enable": self.is_enabled(),
        }


class Bind:
    """
    Represents role-emoji or role-button bind.

    This class can be constructed manually and the attributes also can be assigned
    manually. This is mainly to aid with constructing this class partially.
    """

    def __init__(
        self,
        model: ReactionRole,
        *,
        role: discord.Role = MISSING,
        emoji: Optional[Union[discord.Emoji, discord.PartialEmoji]] = None,
        button: Optional[Button] = None,
        trigger_type: str = TriggerType.REACTION,
    ):
        self.model: ReactionRole = model
        self.role: discord.Role = role
        self.emoji: Optional[Union[discord.Emoji, discord.PartialEmoji]] = emoji
        self.button: Optional[Button] = button

    @property
    def trigger_type(self) -> str:
        return self.model.trigger_type

    def is_set(self) -> bool:
        """
        Whether this bind is fully constructed.
        """
        if self.role is MISSING:
            return False
        if self.trigger_type == TriggerType.INTERACTION:
            return self.button is not None
        if self.trigger_type == TriggerType.REACTION:
            return self.emoji is not None
        return False

    @classmethod
    def from_data(cls, model: ReactionRole, *, data: Dict[str, Any]) -> Bind:
        """
        Instantiate this class from raw data.

        Raises
        -------
        ValueError
            Role with provided ID from the data not found.
        """
        role = model.message.guild.get_role(int(data["role"]))
        if role is None:
            raise ValueError(f"Role {data['role']} not found.")
        trigger_type = model.trigger_type
        kwargs = {"role": role, "trigger_type": trigger_type}
        if trigger_type == TriggerType.INTERACTION:
            payload = data["button"]
            emoji = payload.get("emoji")
            if emoji is not None:
                emoji = discord.PartialEmoji.from_str(payload["emoji"])
            kwargs["button"] = Button(
                label=payload["label"],
                emoji=emoji,
                callback=model.handle_interaction,
                style=discord.ButtonStyle[payload["style"]],
            )
        else:
            kwargs["emoji"] = discord.PartialEmoji.from_str(data["emoji"])
        return cls(model, **kwargs)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "role": str(self.role.id),
        }
        if self.emoji:
            data["emoji"] = str(self.emoji)
        if self.button:
            emoji = self.button.emoji
            data["button"] = {
                "label": self.button.label,
                "emoji": str(emoji) if emoji is not None else None,
                "style": self.button.style.name,
            }
        return data


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
    binds : List[Bind]
        List of bind data attached to the message.
    rules : str
        The rules applied for the reactions.
    """

    def __init__(
        self,
        *,
        message: Union[discord.PartialMessage, discord.Message] = MISSING,
        binds: List[Bind] = MISSING,
        trigger_type: str = TriggerType.REACTION,
        rules: str = ReactRules.NORMAL,
    ):
        self.message: Union[discord.PartialMessage, discord.Message] = message
        self.binds: List[Bind] = binds if binds is not MISSING else []
        self.trigger_type: str = trigger_type
        self.rules: str = rules
        self.manager: ReactionRoleManager = MISSING  # set from ReactionRoleManager.add
        self.view: ReactionRoleView = MISSING

    def __hash__(self):
        return hash((self.message.id, self.channel.id))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} message_id={self.message.id} binds={self.binds}>"

    def __eq__(self, other) -> bool:
        if not isinstance(other, ReactionRole):
            return False
        return self.message.id == other.message.id and self.channel.id == other.channel.id

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        """
        The channel where the reaction role message is. If the `.message` attribute
        is not set, `None` will be returned.
        """
        if self.message is MISSING:
            return None
        return self.message.channel

    @classmethod
    def from_data(cls, manager: ReactionRoleManager, *, data: ReactRolePayload) -> ReactionRole:
        """
        Instantiate this class from raw data. This will automatically add persistent
        view to the bot if the trigger type is interaction.

        Raises
        -------
        ValueError
            Channel with provided ID from the data not found.
        """
        global Button
        if Button is None:
            from .views import Button

        channel_id = data.pop("channel")
        channel = manager.cog.bot.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel with ID {channel_id} not found.")
        message = discord.PartialMessage(id=data.pop("message"), channel=channel)
        trigger_type = data.pop("type", TriggerType.REACTION)

        instance = cls(
            message=message,
            trigger_type=trigger_type,
            rules=data.pop("rules"),
        )

        binds = []
        for bind in data.pop("binds", []):
            try:
                binds.append(Bind.from_data(instance, data=bind))
            except ValueError as exc:
                logger.error(str(exc), exc_info=True)
                continue

        instance.binds = binds
        if trigger_type == TriggerType.INTERACTION:
            instance.view = ReactionRoleView(manager.cog, message, model=instance)
            manager.cog.bot.add_view(instance.view, message_id=message.id)
        return instance

    def delete_set_roles(self, role_list: List[str]) -> None:
        for role_id in role_list:
            for bind in self.binds:
                if role_id == str(bind.role.id):
                    self.binds.remove(bind)

    def resolve_unique(self, member: discord.Member, role: discord.Role) -> List[discord.Role]:
        ret = []
        for bind in self.binds:
            if role.id == bind.role.id:
                continue
            if bind.role in member.roles:
                ret.append(bind.role)
        return ret

    def get_bind_from(
        self,
        *,
        role: discord.Role = MISSING,
        button: Button = MISSING,
        emoji: Union[discord.Emoji, discord.PartialEmoji] = MISSING,
    ) -> Optional[Bind]:
        """
        Get Bind instance that matches the provided entity. If not found, `None` will be returned.
        """
        if role:
            return discord.utils.find(lambda bind: bind.role == role, self.binds)
        if button:
            return discord.utils.find(lambda bind: bind.button.custom_id == button.custom_id, self.binds)
        return discord.utils.find(lambda bind: bind.emoji == emoji, self.binds)

    async def handle_interaction(self, interaction: discord.Interaction, button: Button) -> None:
        await interaction.response.defer()
        if not self.manager.is_enabled():
            embed = discord.Embed(
                color=self.manager.cog.bot.error_color,
                description="Reaction roles feature is currently disabled.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if self.trigger_type != TriggerType.INTERACTION:
            return

        member = self.channel.guild.get_member(interaction.user.id)
        bind = self.get_bind_from(button=button)
        if bind is None:
            return

        role = bind.role
        embed = discord.Embed(color=self.manager.cog.bot.main_color)
        if role not in member.roles:
            await member.add_roles(role, reason="Reaction role.")
            embed.description = f"Role {role.mention} has been added to you.\n\n"
            if self.rules == ReactRules.UNIQUE:
                to_remove = self.resolve_unique(member, role)
                if to_remove:
                    await member.remove_roles(*to_remove, reason="Reaction role.")
                    embed.description += "__**Removed:**__\n" + "\n".join(r.mention for r in to_remove)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await member.remove_roles(role, reason="Reaction role.")
            embed.description = f"Role {role.mention} is now removed from you."
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def handle_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        guild = self.manager.cog.bot.get_guild(payload.guild_id)
        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot or not guild.me.guild_permissions.manage_roles:
            return

        bind = self.get_bind_from(emoji=payload.emoji)
        if bind is None:
            return

        role = bind.role
        if payload.event_type == "REACTION_ADD":
            if role not in member.roles:
                await member.add_roles(role, reason="Reaction role.")
            if self.rules == ReactRules.UNIQUE:
                to_remove = self.resolve_unique(member, role)
                if to_remove:
                    await member.remove_roles(*to_remove, reason="Reaction role.")
        else:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role.")

    def to_dict(self) -> ReactRolePayload:
        return {
            "message": self.message.id,
            "channel": self.channel.id,
            "binds": [bind.to_dict() for bind in self.binds],
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
        Adds a ReactionRole object to entries. Internally this will also assign
        the `.manager` attribute to the `ReactionRole` object.
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
        *,
        message: Union[discord.PartialMessage, discord.Message] = MISSING,
        trigger_type: str = TriggerType.REACTION,
        binds: List[Bind] = None,
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
        binds : List[Bind]
            List of bind data attached to the message.
        rules : str
            The rules applied for the reactions.
        add : bool
            Whether or not the instance created should be added to the entries.
        """
        if binds is None:
            binds = []
        instance = ReactionRole(message=message, trigger_type=trigger_type, binds=binds, rules=rules)
        if add:
            self.add(instance)
        return instance

    async def update(self) -> None:
        await self.cog.config.update(data={"reactroles": self.to_dict()})

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
