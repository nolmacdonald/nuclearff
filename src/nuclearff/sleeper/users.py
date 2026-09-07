"""Resolve a league's roster ids to their owning (and co-owning) users.

:func:`nuclearff.sleeper.standings.roster_display_names` already maps a
roster to its primary owner's display name, and both
:mod:`nuclearff.sleeper.standings` and :mod:`nuclearff.sleeper.transactions`
use it rather than each re-implementing that join. This module covers what
that simpler mapping doesn't: co-owned rosters, where ``roster.co_owners``
carries a second (or third) user alongside ``owner_id``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def roster_owners(
    rosters: list[dict[str, Any]], users: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Map each roster to its owner and co-owners' display names.

    Args:
        rosters: Raw roster objects, as returned by
            :meth:`SleeperClient.get_rosters`.
        users: Raw user objects, as returned by
            :meth:`SleeperClient.get_users`.

    Returns:
        ``roster_id`` mapped to a dict with ``owner_id``, ``display_name``
        (the owner's), and ``co_owners`` — a list of
        ``{"user_id": ..., "display_name": ...}`` dicts, empty when the
        roster has none. A ``user_id`` with no matching entry in ``users``
        (a data inconsistency) is logged and resolves to a ``None``
        display name rather than raising.
    """
    names_by_user_id = {
        user["user_id"]: user.get("display_name")
        for user in users
        if isinstance(user, dict) and isinstance(user.get("user_id"), str)
    }

    def _resolve(roster_id: int, user_id: str) -> str | None:
        if user_id not in names_by_user_id:
            logger.warning(
                "Roster %s references user %s with no matching entry in users",
                roster_id,
                user_id,
            )
        return names_by_user_id.get(user_id)

    result: dict[int, dict[str, Any]] = {}
    for roster in rosters:
        roster_id = roster.get("roster_id")
        if not isinstance(roster_id, int):
            continue

        owner_id = roster.get("owner_id")
        display_name = (
            _resolve(roster_id, owner_id) if isinstance(owner_id, str) else None
        )

        co_owner_ids = roster.get("co_owners")
        co_owners = [
            {"user_id": co_owner_id, "display_name": _resolve(roster_id, co_owner_id)}
            for co_owner_id in (co_owner_ids if isinstance(co_owner_ids, list) else [])
            if isinstance(co_owner_id, str)
        ]

        result[roster_id] = {
            "owner_id": owner_id,
            "display_name": display_name,
            "co_owners": co_owners,
        }

    return result
