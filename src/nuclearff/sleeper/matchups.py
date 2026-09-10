"""Fetch weekly matchups across a league's history and persist them to DuckDB.

``SleeperClient.get_matchups`` has existed since the client was first built,
but nothing calls it: no CLI command, no snapshot capture, no DuckDB table.
This module wires it up, combined with
:func:`nuclearff.sleeper.leagues.walk_league_chain` for multi-season history,
following the same plain-SQL DuckDB pattern as
:mod:`nuclearff.sleeper.standings`.

A week with no matchups yet (in-progress or future season) returns an empty
list from Sleeper, not an error — that's valid data, not a failure.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from nuclearff.duckdb_io import merge_table
from nuclearff.exceptions import SleeperAPIError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_matchups"
"""Default table name, used by :func:`fetch_and_write_matchups`."""

DEFAULT_MAX_WEEK = 18
"""Default cap on weeks fetched per season (a full NFL regular + postseason)."""

_COLUMNS = (
    "league_id",
    "season",
    "week",
    "roster_id",
    "matchup_id",
    "points",
    "custom_points",
    "players",
    "starters",
    "starters_points",
    "players_points",
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    league_id VARCHAR,
    season INTEGER,
    week INTEGER,
    roster_id INTEGER,
    matchup_id INTEGER,
    points DOUBLE,
    custom_points DOUBLE,
    players JSON,
    starters JSON,
    starters_points JSON,
    players_points JSON,
    PRIMARY KEY (league_id, week, roster_id)
)
"""


def matchup_rows(
    league_id: str, season: int | None, week: int, matchups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Flatten one week's raw matchup entries into :data:`TABLE_NAME` rows.

    Args:
        league_id: The season's Sleeper league identifier.
        season: The season year.
        week: The week these matchups are for.
        matchups: Raw entries, as returned by
            :meth:`SleeperClient.get_matchups` — one per roster.

    Returns:
        One row per roster entry.
    """
    rows: list[dict[str, Any]] = []
    for entry in matchups:
        rows.append(
            {
                "league_id": league_id,
                "season": season,
                "week": week,
                "roster_id": entry.get("roster_id"),
                "matchup_id": entry.get("matchup_id"),
                "points": entry.get("points"),
                "custom_points": entry.get("custom_points"),
                "players": json.dumps(entry.get("players") or []),
                "starters": json.dumps(entry.get("starters") or []),
                "starters_points": json.dumps(entry.get("starters_points") or []),
                "players_points": json.dumps(entry.get("players_points") or {}),
            }
        )
    return rows


def fetch_and_write_matchups(
    client: SleeperClient,
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    max_week: int = DEFAULT_MAX_WEEK,
    table_name: str = TABLE_NAME,
) -> int:
    """Fetch every week's matchups for every league in a chain and persist them.

    A week that fails to fetch is logged and skipped; it does not abort the
    rest of the season or the walk, matching
    :func:`nuclearff.sleeper.standings.fetch_and_write_standings`'s posture.

    Only replaces rows for the league_ids in ``leagues`` (via
    :func:`nuclearff.duckdb_io.merge_table`) -- every other league already
    in :data:`TABLE_NAME` is left untouched. A prior version of this
    function called ``replace_table``, which drops the whole table first:
    fetching league B after league A had already been fetched silently
    erased league A's rows entirely, confirmed live 2026-09-10 across three
    real, unrelated leagues on the same account.

    Args:
        client: A configured Sleeper client.
        leagues: Raw league payloads for every season to cover, as returned
            by :func:`nuclearff.sleeper.leagues.walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        max_week: Maximum week number to fetch per season.
        table_name: Destination table.

    Returns:
        The number of rows written.
    """
    rows: list[dict[str, Any]] = []

    for league in leagues:
        league_id = str(league["league_id"])
        season = int(league["season"]) if league.get("season") else None

        for week in range(1, max_week + 1):
            try:
                matchups = client.get_matchups(league_id, week)
            except SleeperAPIError as exc:
                logger.warning(
                    "Could not fetch matchups for league %s week %d: %s",
                    league_id,
                    week,
                    exc,
                )
                continue
            if not matchups:
                continue
            rows.extend(matchup_rows(league_id, season, week, matchups))

    count = merge_table(
        db_path,
        table_name,
        _CREATE_TABLE_SQL,
        _COLUMNS,
        [[row[column] for column in _COLUMNS] for row in rows],
        key_column="league_id",
        key_values=[str(league["league_id"]) for league in leagues],
    )

    logger.info("Wrote %d Sleeper matchup row(s) to %s", count, db_path)
    return count
