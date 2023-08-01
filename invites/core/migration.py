from typing import Any, Dict

from core.models import getLogger


logger = getLogger(__name__)


def is_multiline(data: Dict[str, Any]) -> bool:
    fields = [
        str(data.get("invite_channel")),
        str(data.get("invite_code")),
        str(data.get("inviter", {}).get("id")),
    ]
    return any(("\n" in val for val in fields))


def _resolve_invite_data(data: Dict[str, Any]) -> Dict[str, Any]:
    invite = {
        "code": data["invite_code"],
        "inviter_id": str(data["inviter"]["id"]),
        "channel_id": data["invite_channel"].strip("<#>"),
        "created_at": None,
        "expires_at": None,
        "max_age": None,
        "max_uses": None,
    }
    return invite


async def db_migration(cog) -> None:
    """
    A helper to migrate documents in the database since our new data scheme is now a
    bit different from the old one.
    """
    db = cog.db
    search_query = {"$and": [{"user_id": {"$exists": True}}, {"inviter": {"$exists": True}}]}
    count = await db.count_documents(search_query)
    if not count:
        return

    to_insert = {}
    logger.debug(f"Migrating database documents in {db.name}.")
    async for old_doc in db.find(search_query):
        if str(old_doc["user_id"]) in to_insert:
            continue

        if old_doc.get("multi", False) or is_multiline(old_doc):
            # we just treat these as broken
            continue

        invite = _resolve_invite_data(old_doc)
        userdoc = {
            "_id": str(old_doc["user_id"]),
            "guilds": {str(old_doc["guild_id"]): {"invites": [invite]}},  # still supports multiple
        }
        # find dupe data for same user
        find_filter = {
            "user_id": old_doc["user_id"],
            "guild_id": {
                "$ne": old_doc["guild_id"],
            },
        }
        async for dupe_doc in db.find(find_filter):
            # only do if it does not have multiple invites
            if not is_multiline(dupe_doc):
                ginvite = _resolve_invite_data(dupe_doc)
                userdoc["guilds"][str(dupe_doc["guild_id"])] = {"invites": [ginvite]}

        to_insert[userdoc["_id"]] = userdoc

    try:
        await db.delete_many({"_id": {"$ne": "config"}})
    except Exception as exc:
        logger.error(f"{type(exc).__name__}: {str(exc)}", exc_info=True)

    try:
        await db.insert_many(to_insert.values())
    except Exception as exc:
        logger.error(f"{type(exc).__name__}: {str(exc)}", exc_info=True)

    cog.config.set("migrated", True)
    await cog.config.update()
    logger.debug("Migration is now complete.")
