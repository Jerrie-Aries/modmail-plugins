from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

import discord
from discord.utils import MISSING

from core.models import getLogger


if TYPE_CHECKING:
    from bot import ModmailBot

    from ..invites import Invites

logger = getLogger(__name__)


class PartialInvite:
    """
    Partially constructed invite object.
    """

    def __init__(
        self,
        tracker: InviteTracker,
        code: str,
        *,
        inviter_id: Optional[int],
        channel_id: Optional[int],
        created_at: Optional[datetime],
        expires_at: Optional[datetime],
        max_age: Optional[int],
        max_uses: Optional[int],
    ):
        self.tracker: InviteTracker = tracker
        self.code: str = code
        self.inviter_id: Optional[int] = inviter_id
        self.inviter: discord.User = MISSING
        self.channel_id: Optional[int] = channel_id
        self.created_at: Optional[datetime] = created_at
        self.expires_at: Optional[datetime] = expires_at
        self.max_age: Optional[int] = max_age
        self.max_uses: Optional[int] = max_uses

    @property
    def link(self) -> str:
        return "https://discord.gg/" + self.code

    @property
    def channel(self) -> Optional[discord.GuildChannel]:
        if not self.channel_id:
            return None
        return self.tracker.bot.get_channel(self.channel_id)

    @classmethod
    async def from_data(cls, tracker: InviteTracker, *, data: Dict[str, Any]) -> PartialInvite:
        """
        Construct this class from raw data retrieved from database.
        All attributes should be resolved from here.
        """
        inviter_id = data["inviter"].get("id")
        if inviter_id:
            inviter_id = int(inviter_id)
        channel_id = data["channel"].get("id")
        if channel_id:
            channel_id = int(channel_id)
        created_at = data["created_at"]
        if created_at:
            created_at = datetime.fromtimestamp(created_at)
        expires_at = data["expires_at"]
        if expires_at:
            expires_at = datetime.fromtimestamp(expires_at)
        invite = cls(
            tracker,
            data["code"],
            inviter_id=inviter_id,
            channel_id=channel_id,
            created_at=created_at,
            expires_at=expires_at,
            max_age=data["max_age"],
            max_uses=data["max_uses"],
        )
        if inviter_id:
            invite.inviter = await tracker.get_or_fetch_user(inviter_id)

        return invite


class InviteTracker:
    """
    Represents invite tracking feature.
    """

    def __init__(self, cog: Invites):
        self.bot: ModmailBot = cog.bot
        self.cog: Invites = cog
        self.invite_cache: Dict[int, Set[discord.Invite]] = {}
        self.vanity_invites: Dict[int, Optional[discord.Invite]] = {}
        self._fetched_users: Dict[int, Any] = {}

    async def populate_invites(self) -> None:
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            config = self.cog.guild_config(guild.id)
            if not config["enable"]:
                continue

            logger.debug("Caching invites for guild (%s).", guild.name)
            self.invite_cache[guild.id] = set(await guild.invites())

            if "VANITY_URL" in guild.features:
                vanity_inv = await guild.vanity_invite()
                if vanity_inv is not None:
                    self.vanity_invites[guild.id] = vanity_inv

    async def get_used_invite(self, member: discord.Member) -> List[Optional[discord.Invite]]:
        """
        Checks which invite is used in join via the following strategies:
        1. Check if invite doesn't exist anymore.
        2. Check invite uses. This will overwrite check 1.

        After the checks are done, it will store the new invites in cache automatically.

        Returns a list of predicted invites.

        Parameters
        ----------
        member : discord.Member
            Member object.
        """
        guild = member.guild
        new_invs = set(await guild.invites())
        pred_invs = []
        found = False

        for old_inv in self.invite_cache[guild.id]:
            # 1. Check if invite doesn't exist anymore.
            if old_inv not in new_invs:
                # the invite could be deleted, expired or reached max usage
                # if it's the latter one, then this is the used invite so we add to the list first
                pred_invs.append(old_inv)
                continue

            # 2. Check invite uses.
            used_inv = next(
                (inv for inv in new_invs if inv.id == old_inv.id and inv.uses > old_inv.uses),
                None,
            )
            if used_inv is not None:
                # We found the used invite, the `for loop` will stop here and the value will be returned.
                found = True
                pred_invs = [used_inv]
                break

        # 3. Check vanity invite
        if not found and "VANITY_URL" in guild.features:
            # still not found and this guild has vanity url enabled in guild.features
            # so we check if it's incremented
            vanity_inv = await guild.vanity_invite()
            cached_vanity_inv = self.vanity_invites.get(guild.id)
            if vanity_inv and cached_vanity_inv and vanity_inv.uses > cached_vanity_inv.uses:
                pred_invs = [vanity_inv]
                found = True
            self.vanity_invites[guild.id] = vanity_inv

        # In case no invite found from check #2 and #3, there are possibly deleted or expired invites in the list
        # of 'pred_invs'.
        # We'll try to filter them, remove any that meets those criteria.
        # In this case we check the values of '.uses', '.max_uses' and '.max_age' attributes and do the logics.
        if pred_invs and not found:
            for inv in list(pred_invs):
                if inv.max_age:
                    expired = (
                        datetime.timestamp(inv.created_at) + inv.max_age
                    ) < member.joined_at.timestamp()
                else:
                    expired = False  # never expires
                if not all((inv.max_uses == (inv.uses + 1), not expired)):
                    pred_invs.remove(inv)

            if len(pred_invs) == 1:
                pred_invs[0].uses += 1

        self.invite_cache[guild.id] = new_invs
        return pred_invs

    @staticmethod
    def invite_to_dict(invite: discord.Invite) -> Dict[str, Any]:
        """Scheme of invite data."""
        created = invite.created_at.timestamp() if invite.created_at else None
        expires = invite.expires_at.timestamp() if invite.expires_at else None
        inviter = {}
        if invite.inviter:
            inviter["id"] = str(invite.inviter.id)
        channel = {}
        if invite.channel:
            channel["id"] = str(invite.channel.id)
        inv_data = {
            "code": invite.code,
            "inviter": inviter,
            "channel": channel,
            "created_at": created,
            "expires_at": expires,
            "max_age": invite.max_age,
            "max_uses": invite.max_uses,
        }
        return inv_data

    async def get_user_data(self, member: discord.Member) -> Optional[Dict[str, Any]]:
        """
        Fetches user data from database. If the data does not exist, `None` will be returned.

        Parameters
        ----------
        member : discord.Member
            Member object.

        Returns
        -------
        Dict[str, Any]
            The data that of the user if any. Otherwise returns `None`.
        """
        return await self.cog.db.find_one({"_id": str(member.id)})

    async def get_invite_used_by(self, member: discord.Member) -> Optional[PartialInvite]:
        """
        Fetches invite data used by user from database.

        Parameters
        ----------
        member : discord.Member
            Member object.

        Returns
        -------
        Optional[PartialInvite]
            PartialInvite object constructed from raw data if exists. Otherwise returns `None`.
        """
        data = await self.get_user_data(member)
        if data:
            invdata = data["guilds"].get(str(member.guild.id))
            if invdata:
                return await PartialInvite.from_data(self, data=invdata)
        return None

    async def save_user_data(self, member: discord.Member, invite: List[discord.Invite]) -> Dict[str, Any]:
        """
        Saves user and invite data into the database.

        Parameters
        ----------
        member : discord.Member
            Member object.
        invite : discord.Invite
            Invite object that was retrieved from `get_used_invite` method.

        Returns
        -------
        Dict[str, Any]
            The data that has been saved.
        """
        user_data = {
            f"guilds.{member.guild.id}": self.invite_to_dict(invite),
        }

        return await self.cog.db.find_one_and_update(
            {"_id": str(member.id)},
            {"$set": user_data},
            upsert=True,
            return_document=True,
        )

    async def update_user_data(self, member: discord.Member, *, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Updates user data with new data provided.

        Parameters
        ----------
        member : discord.Member
            Member object.
        data : Dict[str, Any]
            The new data to update.

        Returns
        -------
        Dict[str, Any]
            The updated data.
        """
        return await self.cog.db.find_one_and_update(
            {"_id": str(member.id)}, {"$set": data}, upsert=True, return_document=True
        )

    async def remove_user_data(self, user_id: int) -> Dict[str, Any]:
        """
        Removes user and invite data from the database.

        Unlike `.save_user_data` and `update_user_data`, this method only takes user ID
        as parameter just in case we do not have `discord.Member` object to pass.

        Parameters
        ----------
        user_id : int
            The ID of user to delete the data.

        Returns
        -------
        Dict[str, Any]
            The data that was deleted if any. Otherwise returns `None`.
        """
        return await self.cog.db.find_one_and_delete({"_id": str(user_id)})

    async def clear_all_data(self) -> None:
        """
        Clear all data of all users in the database.
        """
        await self.cog.db.delete_many({"_id": {"$ne": "config"}})

    async def get_or_fetch_user(self, user_id: int) -> Optional[discord.User]:
        """
        A helper to aid with resolving user who created the invite.
        This is to solve issue when the inviter already left the server and not in
        bot's internal cache.

        The lookup strategy, in order:
        - Cache (._fetched_users). This is to prevent repeated API requests
        to fetch same user.
        - Bot's internal cache
        - Fetch from discord

        Parameters
        ----------
        user_id : int
            The ID of user to look up.
        """
        user = self._fetched_users.get(user_id)
        if isinstance(user, discord.User):
            return user
        if user is MISSING:
            return None

        user = self.bot.get_user(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                # prabably deleted account, so we store as MISSING
                user = MISSING
        self._fetched_users[user_id] = user
        return user if user else None
