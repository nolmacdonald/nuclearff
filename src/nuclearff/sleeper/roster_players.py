"""Persist roster composition (players, starters, reserve, taxi) to DuckDB.

A roster's ``players`` field is the authoritative full roster; Sleeper never
returns a separate roster payload joined to player data, so nuclearff has
had no queryable record of "who was on this team this season" beyond
re-parsing raw snapshot JSON. This module categorizes each roster's players
by slot and persists them, combined with
:func:`nuclearff.sleeper.leagues.walk_league_chain` for multi-season
history, following the same plain-SQL DuckDB pattern as
:mod:`nuclearff.sleeper.standings`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nuclearff.duckdb_io import merge_table
from nuclearff.exceptions import SleeperAPIError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_roster_players"
"""Default table name, used by :func:`fetch_and_write_roster_players`."""

_COLUMNS = ("league_id", "season", "roster_id", "player_id", "slot")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    league_id VARCHAR,
    season INTEGER,
    roster_id INTEGER,
    player_id VARCHAR,
    slot VARCHAR,
    PRIMARY KEY (league_id, roster_id, player_id)
)
"""


def _id_set(values: Any) -> set[str]:
    """Coerce a roster field (``starters``/``reserve``/``taxi``) into a clean id set.

    Sleeper fills an empty starting slot with the literal string ``"0"``,
    confirmed against a real pre-draft league — that is not a player id and
    must never become a row. ``reserve``/``taxi`` are ``None`` when unused
    rather than an empty list.

    Args:
        values: The raw field value, expected to be a list of player ids.

    Returns:
        The real, non-empty, non-``"0"`` ids in ``values``.
    """
    if not isinstance(values, list):
        return set()
    return {v for v in values if isinstance(v, str) and v and v != "0"}


def roster_player_rows(
    league_id: str, season: int | None, rosters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Categorize each roster's players by slot.

    Args:
        league_id: The season's Sleeper league identifier.
        season: The season year.
        rosters: Raw roster objects, as returned by
            :meth:`SleeperClient.get_rosters`.

    Returns:
        One row per (roster, player). ``slot`` is ``"starter"``,
        ``"reserve"``, or ``"taxi"`` if the player appears in that roster
        field, checked in that priority order, else ``"bench"``. A
        ``starters`` entry is never also counted as ``bench``.
    """
    rows: list[dict[str, Any]] = []
    for roster in rosters:
        roster_id = roster.get("roster_id")
        if not isinstance(roster_id, int):
            continue

        players = roster.get("players")
        if not isinstance(players, list):
            continue
        starters = _id_set(roster.get("starters"))
        reserve = _id_set(roster.get("reserve"))
        taxi = _id_set(roster.get("taxi"))

        for player_id in players:
            if not isinstance(player_id, str) or not player_id:
                continue
            if player_id in starters:
                slot = "starter"
            elif player_id in reserve:
                slot = "reserve"
            elif player_id in taxi:
                slot = "taxi"
            else:
                slot = "bench"
            rows.append(
                {
                    "league_id": league_id,
                    "season": season,
                    "roster_id": roster_id,
                    "player_id": player_id,
                    "slot": slot,
                }
            )
    return rows


def fetch_and_write_roster_players(
    client: SleeperClient,
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
) -> int:
    """Fetch rosters for every league in a chain and persist roster composition.

    A season whose rosters fail to fetch is logged and skipped rather than
    aborting the whole walk, matching
    :func:`nuclearff.sleeper.standings.fetch_and_write_standings`'s posture.

    Only replaces rows for league_ids actually fetched this call (via
    :func:`nuclearff.duckdb_io.merge_table`) -- both every *other* league
    already in :data:`TABLE_NAME`, and a league in ``leagues`` whose own
    fetch failed this call, are left untouched: a transient failure on
    refetch must not erase that league's previously-good rows. See
    :func:`~nuclearff.sleeper.matchups.fetch_and_write_matchups`'s
    docstring for the cross-league data-loss bug this guards against.

    Args:
        client: A configured Sleeper client.
        leagues: Raw league payloads for every season to cover, as returned
            by :func:`nuclearff.sleeper.leagues.walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table.

    Returns:
        The number of rows written.
    """
    rows: list[dict[str, Any]] = []
    fetched_league_ids: list[str] = []

    for league in leagues:
        league_id = str(league["league_id"])
        season = int(league["season"]) if league.get("season") else None

        try:
            rosters = client.get_rosters(league_id)
        except SleeperAPIError as exc:
            logger.warning("Could not fetch rosters for league %s: %s", league_id, exc)
            continue

        fetched_league_ids.append(league_id)
        rows.extend(roster_player_rows(league_id, season, rosters))

    count = merge_table(
        db_path,
        table_name,
        _CREATE_TABLE_SQL,
        _COLUMNS,
        [[row[column] for column in _COLUMNS] for row in rows],
        key_column="league_id",
        key_values=fetched_league_ids,
    )

    logger.info("Wrote %d roster-player row(s) to %s", count, db_path)
    return count
