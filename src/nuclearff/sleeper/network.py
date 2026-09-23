"""Discover the wider Sleeper network reachable from a seed user.

Every other Sleeper-facing entry point in this project starts from an
already-known ``league_id`` or a single user's own leagues
(:mod:`nuclearff.sleeper.leagues`, :func:`SleeperClient.get_user_leagues`).
Neither answers "who else is out there": a seed user's leagues have
co-members, and those co-members have leagues of their own the seed user has
never touched. :func:`crawl_user_network` walks that graph -- a bounded,
explicit-opt-in discovery tool (see issue #209), not a replacement for the
per-league fetch (:func:`nuclearff.sleeper.leagues.walk_league_chain`,
:mod:`nuclearff.sleeper.transactions`, etc.) still needed to pull a chosen
league's real data.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

import polars as pl

from nuclearff.exceptions import SleeperAPIError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

DEFAULT_MAX_HOPS = 1
"""Reproduces the motivating example exactly: a user's leagues, then every
co-member of those leagues, then those co-members' own leagues."""

DEFAULT_MAX_USERS = 300
"""Hard cap on discovered users. Each additional hop multiplies the API call
count by roughly the branching factor seen so far, so this -- not
``max_hops`` -- is the real guard against a runaway crawl."""

_MEMBERSHIPS_SCHEMA = {"user_id": pl.String, "league_id": pl.String, "hop": pl.Int64}
_USERS_SCHEMA = {
    "user_id": pl.String,
    "display_name": pl.String,
    "hop": pl.Int64,
    "league_count": pl.Int64,
}
_LEAGUES_SCHEMA = {
    "league_id": pl.String,
    "name": pl.String,
    "season": pl.String,
    "sport": pl.String,
    "status": pl.String,
    "total_rosters": pl.Int64,
    "hop": pl.Int64,
}


class UserNetwork(NamedTuple):
    """The result of :func:`crawl_user_network`.

    Attributes:
        seed_user_id: The starting user's Sleeper id.
        leagues: One row per unique discovered league: ``league_id``,
            ``name``, ``season``, ``sport``, ``status``, ``total_rosters``
            (Sleeper's own count), ``hop`` (the earliest hop it was reached
            at), and ``known_member_count`` -- how many *discovered* users
            are known, from their own fetched leagues, to be in it. Exact
            for hop-0 leagues (their full membership was fetched to find
            co-members in the first place); a lower bound on
            ``total_rosters`` for later hops.
        users: One row per discovered user, including the seed:
            ``user_id``, ``display_name``, ``hop`` (first discovered),
            ``league_count`` (their total leagues that season), and
            ``shared_league_count`` (how many of those leagues are also in
            the seed's own hop-0 league set).
        memberships: The raw edge list the other two are aggregated from:
            ``user_id``, ``league_id``, ``hop`` -- one row per league in
            every fetched user's own league list.
    """

    seed_user_id: str
    leagues: pl.DataFrame
    users: pl.DataFrame
    memberships: pl.DataFrame


def crawl_user_network(
    client: SleeperClient,
    username_or_id: str,
    season: int | str,
    *,
    sport: str = "nfl",
    max_hops: int = DEFAULT_MAX_HOPS,
    max_users: int = DEFAULT_MAX_USERS,
) -> UserNetwork:
    """Crawl the Sleeper network reachable from a seed user.

    Hop 0 is the seed user's own leagues. Each further hop looks up every
    co-member of the leagues found so far (:meth:`SleeperClient.get_users`),
    then that co-member's own leagues
    (:meth:`SleeperClient.get_user_leagues`) -- exactly the motivating
    example from issue #209 when ``max_hops=1``, the default: a user's
    leagues, then all users in those leagues, then all of *those* users'
    leagues.

    A failure fetching one co-member's own leagues, or one league's member
    list, is logged and that user or league is skipped rather than aborting
    the whole crawl -- the same robustness precedent as
    :func:`nuclearff.sleeper.leagues.walk_league_chain`'s later-hop
    handling. A failure resolving the seed user or the seed's own leagues
    does raise: nothing useful can happen without either.

    Args:
        client: A configured Sleeper client.
        username_or_id: The seed user's Sleeper username or user id.
        season: Season year, e.g. ``2026``.
        sport: Sport key, such as ``"nfl"``.
        max_hops: How many rounds of user -> co-members -> their leagues to
            run beyond the seed's own leagues. Each additional hop
            multiplies the API call count by roughly the branching factor
            seen so far -- not a free knob.
        max_users: Hard cap on discovered users. Once expanding a hop would
            exceed it, expansion stops (logged, not raised) rather than
            growing without bound.

    Returns:
        A :class:`UserNetwork`.

    Raises:
        SleeperAPIError: If the seed user or the seed's own leagues can't be
            fetched.
    """
    seed_user = client.get_user(username_or_id)
    seed_user_id = seed_user["user_id"]

    users_seen: dict[str, dict[str, Any]] = {seed_user_id: seed_user}
    leagues_seen: dict[str, dict[str, Any]] = {}
    league_first_hop: dict[str, int] = {}
    membership_rows: list[dict[str, Any]] = []
    user_rows: list[dict[str, Any]] = []

    frontier = {seed_user_id: seed_user}
    hop = 0
    while frontier:
        newly_found_league_ids: set[str] = set()
        for user_id, user in frontier.items():
            try:
                leagues = client.get_user_leagues(user_id, season, sport=sport)
            except SleeperAPIError:
                if user_id == seed_user_id:
                    raise
                logger.warning(
                    "Could not fetch leagues for user %s (%s); skipping",
                    user_id,
                    user.get("display_name"),
                    exc_info=True,
                )
                continue

            for league in leagues:
                league_id = league.get("league_id")
                if not isinstance(league_id, str):
                    continue
                membership_rows.append(
                    {"user_id": user_id, "league_id": league_id, "hop": hop}
                )
                if league_id not in leagues_seen:
                    leagues_seen[league_id] = league
                    league_first_hop[league_id] = hop
                    newly_found_league_ids.add(league_id)

            user_rows.append(
                {
                    "user_id": user_id,
                    "display_name": user.get("display_name"),
                    "hop": hop,
                    "league_count": len(leagues),
                }
            )

        if hop >= max_hops or not newly_found_league_ids:
            break

        next_frontier = _expand_co_members(client, newly_found_league_ids, users_seen)
        if not next_frontier:
            break

        if len(users_seen) + len(next_frontier) > max_users:
            room = max(max_users - len(users_seen), 0)
            trimmed = dict(sorted(next_frontier.items())[:room])
            logger.warning(
                "User network crawl hit max_users=%d; discovered %d more "
                "users this hop, only expanding %d",
                max_users,
                len(next_frontier),
                len(trimmed),
            )
            next_frontier = trimmed

        users_seen.update(next_frontier)
        frontier = next_frontier
        hop += 1

    memberships = pl.DataFrame(membership_rows, schema=_MEMBERSHIPS_SCHEMA)
    return UserNetwork(
        seed_user_id=seed_user_id,
        leagues=_build_leagues_frame(leagues_seen, league_first_hop, memberships),
        users=_build_users_frame(user_rows, memberships, seed_user_id),
        memberships=memberships,
    )


def _expand_co_members(
    client: SleeperClient,
    league_ids: set[str],
    users_seen: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return not-yet-seen co-members of ``league_ids``, keyed by user id."""
    next_frontier: dict[str, dict[str, Any]] = {}
    for league_id in league_ids:
        try:
            members = client.get_users(league_id)
        except SleeperAPIError:
            logger.warning(
                "Could not fetch members of league %s; skipping",
                league_id,
                exc_info=True,
            )
            continue
        for member in members:
            member_id = member.get("user_id")
            if not isinstance(member_id, str):
                continue
            if member_id not in users_seen and member_id not in next_frontier:
                next_frontier[member_id] = member
    return next_frontier


def _build_users_frame(
    user_rows: list[dict[str, Any]], memberships: pl.DataFrame, seed_user_id: str
) -> pl.DataFrame:
    """Attach ``shared_league_count`` (overlap with the seed's own leagues).

    Unlike :func:`_build_leagues_frame`, ``user_rows`` is never empty here:
    the seed itself always contributes at least one row before this is
    called, even when it has zero leagues (see the ``max_hops=0`` no-leagues
    test) -- so there is no empty-frame branch to short-circuit.
    """
    users = pl.DataFrame(user_rows, schema=_USERS_SCHEMA)
    seed_league_ids = memberships.filter(pl.col("user_id") == seed_user_id)[
        "league_id"
    ].to_list()
    shared = (
        memberships.filter(pl.col("league_id").is_in(seed_league_ids))
        .group_by("user_id")
        .agg(pl.len().alias("shared_league_count"))
    )
    return (
        users.join(shared, on="user_id", how="left")
        .with_columns(pl.col("shared_league_count").fill_null(0))
        .sort(["hop", "user_id"])
    )


def _build_leagues_frame(
    leagues_seen: dict[str, dict[str, Any]],
    league_first_hop: dict[str, int],
    memberships: pl.DataFrame,
) -> pl.DataFrame:
    """Attach ``known_member_count`` (discovered users known to be members)."""
    rows = [
        {
            "league_id": league_id,
            "name": league.get("name"),
            "season": league.get("season"),
            "sport": league.get("sport"),
            "status": league.get("status"),
            "total_rosters": league.get("total_rosters"),
            "hop": league_first_hop[league_id],
        }
        for league_id, league in leagues_seen.items()
    ]
    leagues = pl.DataFrame(rows, schema=_LEAGUES_SCHEMA)
    if leagues.is_empty():
        return leagues.with_columns(
            pl.lit(0, dtype=pl.Int64).alias("known_member_count")
        )

    known_member_counts = memberships.group_by("league_id").agg(
        pl.col("user_id").n_unique().alias("known_member_count")
    )
    return (
        leagues.join(known_member_counts, on="league_id", how="left")
        .with_columns(pl.col("known_member_count").fill_null(0))
        .sort(["hop", "known_member_count"], descending=[False, True])
    )
