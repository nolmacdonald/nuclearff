"""Compute season standings and playoff results, and persist them to DuckDB.

A roster's ``settings`` (via :meth:`SleeperClient.get_rosters`) already carries
its regular-season record, but nothing in nuclearff turns that into a ranked
standings table, resolves an owner's display name, or accounts for how the
playoffs actually re-ranked the league. This module does that per season,
combined with :func:`nuclearff.sleeper.leagues.walk_league_chain` for
multi-season history, and writes both a typed standings table and the raw
playoff bracket match data to DuckDB via :mod:`nuclearff.duckdb_io`.

**Final placement is only computed from the winners bracket.** A Sleeper
bracket match with a non-null ``p`` (placement) field unambiguously awards
rank ``p`` to the match's winner and ``p + 1`` to its loser — confirmed
against a real completed season (ranks 1-6 of 10). The losers bracket also
carries its own ``p`` field, but whether it continues that same overall
numbering or restarts within just the losers-bracket participants could not
be confirmed against real data. Rather than guess, :func:`resolve_final_ranks`
does not use it: a roster whose final bracket appearance was in the losers
bracket gets a ``NULL`` ``final_rank``, not a wrong number. The raw losers-
bracket ``placement`` value is still captured verbatim in
``sleeper_playoff_matches`` for anyone who wants to interpret it themselves.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from nuclearff.duckdb_io import replace_table
from nuclearff.exceptions import SleeperAPIError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

STANDINGS_TABLE_NAME = "sleeper_standings"
"""Default table name for per-season standings.

Used by :func:`fetch_and_write_standings`.
"""

MATCHES_TABLE_NAME = "sleeper_playoff_matches"
"""Default table name for raw playoff bracket matches."""

_STANDINGS_COLUMNS = (
    "league_id",
    "season",
    "roster_id",
    "owner_id",
    "display_name",
    "wins",
    "losses",
    "ties",
    "fpts",
    "fpts_against",
    "regular_season_rank",
    "final_rank",
)

_STANDINGS_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    league_id VARCHAR,
    season INTEGER,
    roster_id INTEGER,
    owner_id VARCHAR,
    display_name VARCHAR,
    wins INTEGER,
    losses INTEGER,
    ties INTEGER,
    fpts DOUBLE,
    fpts_against DOUBLE,
    regular_season_rank INTEGER,
    final_rank INTEGER,
    PRIMARY KEY (league_id, roster_id)
)
"""

_MATCHES_COLUMNS = (
    "league_id",
    "season",
    "bracket",
    "match",
    "round",
    "t1",
    "t2",
    "winner",
    "loser",
    "placement",
    "t1_from",
    "t2_from",
)

_MATCHES_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    league_id VARCHAR,
    season INTEGER,
    bracket VARCHAR,
    match INTEGER,
    round INTEGER,
    t1 INTEGER,
    t2 INTEGER,
    winner INTEGER,
    loser INTEGER,
    placement INTEGER,
    t1_from JSON,
    t2_from JSON,
    PRIMARY KEY (league_id, bracket, match)
)
"""


def _points(settings: dict[str, Any], base_key: str) -> float | None:
    """Combine Sleeper's split whole/decimal points fields into one float.

    Sleeper reports points as two integer fields, e.g. ``fpts=2055`` and
    ``fpts_decimal=82`` meaning ``2055.82``. The decimal field is absent on a
    roster that hasn't scored yet (a fresh, pre-draft league).

    Args:
        settings: A roster's raw ``settings`` object.
        base_key: ``"fpts"`` or ``"fpts_against"``.

    Returns:
        The combined value, or ``None`` if the whole-number field is absent
        or not numeric.
    """
    whole = settings.get(base_key)
    if not isinstance(whole, int | float):
        return None
    decimal = settings.get(f"{base_key}_decimal", 0)
    if not isinstance(decimal, int | float):
        decimal = 0
    return round(whole + decimal / 100, 2)


def roster_display_names(
    rosters: list[dict[str, Any]], users: list[dict[str, Any]]
) -> dict[int, str | None]:
    """Map each roster to its owner's display name.

    Args:
        rosters: Raw roster objects, as returned by
            :meth:`SleeperClient.get_rosters`.
        users: Raw user objects, as returned by
            :meth:`SleeperClient.get_users`.

    Returns:
        ``roster_id`` mapped to a display name, or ``None`` if the roster's
        ``owner_id`` has no matching entry in ``users`` (a data
        inconsistency, not fatal).
    """
    names_by_user_id = {
        user.get("user_id"): user.get("display_name")
        for user in users
        if isinstance(user, dict)
    }
    result: dict[int, str | None] = {}
    for roster in rosters:
        roster_id = roster.get("roster_id")
        if not isinstance(roster_id, int):
            continue
        result[roster_id] = names_by_user_id.get(roster.get("owner_id"))
    return result


def resolve_final_ranks(winners_bracket: list[dict[str, Any]]) -> dict[int, int]:
    """Resolve final league placement from the winners bracket's placement matches.

    See the module docstring for why the losers bracket is deliberately not
    used here.

    Args:
        winners_bracket: Raw match objects, as returned by
            :meth:`SleeperClient.get_winners_bracket`.

    Returns:
        ``roster_id`` mapped to final rank, for every roster that appears in
        a placement match (one with a non-null ``p``). A roster whose last
        bracket appearance was in the losers bracket, or in a winners-bracket
        match with no ``p``, is absent from the result.
    """
    ranks: dict[int, int] = {}
    for match in winners_bracket:
        placement = match.get("p")
        if not isinstance(placement, int):
            continue
        winner = match.get("w")
        loser = match.get("l")
        if isinstance(winner, int):
            ranks[winner] = placement
        if isinstance(loser, int):
            ranks[loser] = placement + 1
    return ranks


def standings_rows(
    league_id: str,
    season: int | None,
    rosters: list[dict[str, Any]],
    users: list[dict[str, Any]],
    winners_bracket: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build :data:`STANDINGS_TABLE_NAME` rows for one season.

    Args:
        league_id: The season's Sleeper league identifier.
        season: The season year.
        rosters: Raw roster objects.
        users: Raw user objects.
        winners_bracket: Raw winners-bracket match objects.

    Returns:
        One row per roster, ``regular_season_rank`` assigned by wins then
        points-for descending. This tiebreak is a reasonable standard, not a
        reproduction of Sleeper's own (unpublished) internal tiebreaker —
        treat ties as approximate.
    """
    names = roster_display_names(rosters, users)
    final_ranks = resolve_final_ranks(winners_bracket)

    rows: list[dict[str, Any]] = []
    for roster in rosters:
        roster_id = roster.get("roster_id")
        if not isinstance(roster_id, int):
            continue
        settings = roster.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        rows.append(
            {
                "league_id": league_id,
                "season": season,
                "roster_id": roster_id,
                "owner_id": roster.get("owner_id"),
                "display_name": names.get(roster_id),
                "wins": settings.get("wins"),
                "losses": settings.get("losses"),
                "ties": settings.get("ties"),
                "fpts": _points(settings, "fpts"),
                "fpts_against": _points(settings, "fpts_against"),
                "final_rank": final_ranks.get(roster_id),
            }
        )

    ranked = sorted(rows, key=lambda row: (-(row["wins"] or 0), -(row["fpts"] or 0.0)))
    for rank, row in enumerate(ranked, start=1):
        row["regular_season_rank"] = rank

    return rows


def bracket_match_rows(
    league_id: str, season: int | None, bracket: str, matches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Flatten raw bracket matches into :data:`MATCHES_TABLE_NAME` rows.

    Args:
        league_id: The season's Sleeper league identifier.
        season: The season year.
        bracket: ``"winners"`` or ``"losers"``.
        matches: Raw match objects, as returned by
            :meth:`SleeperClient.get_winners_bracket`/``get_losers_bracket``.

    Returns:
        One row per match. ``t1_from``/``t2_from`` are kept as JSON text
        (the dependency reference to an earlier match's winner/loser),
        ``None`` when the match's participants are already known roster ids.
    """
    rows: list[dict[str, Any]] = []
    for match in matches:
        t1_from = match.get("t1_from")
        t2_from = match.get("t2_from")
        rows.append(
            {
                "league_id": league_id,
                "season": season,
                "bracket": bracket,
                "match": match.get("m"),
                "round": match.get("r"),
                "t1": match.get("t1"),
                "t2": match.get("t2"),
                "winner": match.get("w"),
                "loser": match.get("l"),
                "placement": match.get("p"),
                "t1_from": json.dumps(t1_from) if t1_from is not None else None,
                "t2_from": json.dumps(t2_from) if t2_from is not None else None,
            }
        )
    return rows


def fetch_and_write_standings(
    client: SleeperClient,
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    table_name: str = STANDINGS_TABLE_NAME,
    matches_table_name: str = MATCHES_TABLE_NAME,
) -> tuple[int, int]:
    """Fetch rosters/users/brackets for every league in a chain and persist standings.

    Each of a season's four endpoints (rosters, users, winners bracket,
    losers bracket) is fetched independently: a failure on one is logged and
    that endpoint degrades to empty for the season rather than skipping the
    season entirely, matching :func:`nuclearff.sleeper.snapshot.fetch_league_snapshot`'s
    posture. An empty bracket (a season with no playoffs yet, or a league
    type that never generates one) is valid data, not a failure — it simply
    yields no placement rows for that season.

    Args:
        client: A configured Sleeper client.
        leagues: Raw league payloads for every season to cover, as returned
            by :func:`nuclearff.sleeper.leagues.walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table for standings.
        matches_table_name: Destination table for raw playoff matches.

    Returns:
        A ``(standings_rows_written, match_rows_written)`` tuple.
    """

    def _optional(league_id: str, label: str, fetch: Any, default: Any) -> Any:
        try:
            return fetch()
        except SleeperAPIError as exc:
            logger.warning(
                "Could not fetch %s for league %s: %s", label, league_id, exc
            )
            return default

    standings: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []

    for league in leagues:
        league_id = str(league["league_id"])
        season = int(league["season"]) if league.get("season") else None

        rosters = _optional(
            league_id, "rosters", lambda lid=league_id: client.get_rosters(lid), []
        )
        users = _optional(
            league_id, "users", lambda lid=league_id: client.get_users(lid), []
        )
        winners_bracket = _optional(
            league_id,
            "winners_bracket",
            lambda lid=league_id: client.get_winners_bracket(lid),
            [],
        )
        losers_bracket = _optional(
            league_id,
            "losers_bracket",
            lambda lid=league_id: client.get_losers_bracket(lid),
            [],
        )

        standings.extend(
            standings_rows(league_id, season, rosters, users, winners_bracket)
        )
        matches.extend(
            bracket_match_rows(league_id, season, "winners", winners_bracket)
        )
        matches.extend(bracket_match_rows(league_id, season, "losers", losers_bracket))

    standings_count = replace_table(
        db_path,
        table_name,
        _STANDINGS_CREATE_TABLE_SQL,
        _STANDINGS_COLUMNS,
        [[row[column] for column in _STANDINGS_COLUMNS] for row in standings],
    )
    matches_count = replace_table(
        db_path,
        matches_table_name,
        _MATCHES_CREATE_TABLE_SQL,
        _MATCHES_COLUMNS,
        [[row[column] for column in _MATCHES_COLUMNS] for row in matches],
    )

    logger.info(
        "Wrote %d standings row(s) and %d playoff match row(s) to %s",
        standings_count,
        matches_count,
        db_path,
    )
    return standings_count, matches_count
