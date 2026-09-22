"""Fetch weekly matchups across a league's history and persist them to DuckDB.

``SleeperClient.get_matchups`` has existed since the client was first built,
but nothing calls it: no CLI command, no snapshot capture, no DuckDB table.
This module wires it up, combined with
:func:`nuclearff.sleeper.leagues.walk_league_chain` for multi-season history,
following the same plain-SQL DuckDB pattern as
:mod:`nuclearff.sleeper.standings`.

A week with no matchups yet (in-progress or future season) returns an empty
list from Sleeper, not an error — that's valid data, not a failure.

**Incremental fetch (issue #148).** A completed season's weeks cannot
change, so :func:`fetch_and_write_matchups` skips re-fetching any week
already persisted for a league whose raw payload reports
``status == "complete"``. A season that is not yet complete still skips
its older cached weeks, but always refetches the most recent
``trailing_refresh_weeks`` of them (Sleeper stat corrections land there)
plus any week never fetched at all. Weeks that are skipped keep their
already-cached row(s) rather than being dropped — :func:`merge_table`
replaces *all* of a league's rows on every call, so a skipped week's rows
must be re-supplied from the cache, not simply omitted.

A week with no matchups leaves no cached row at all (see above), so a
"complete" season's weeks past its real end are always re-probed on every
call — a small, bounded cost (at most ``max_week`` minus the season's real
length) rather than a growing one, since no row is ever written for them
either way.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import polars as pl

from nuclearff.duckdb_io import merge_table, read_table
from nuclearff.exceptions import SleeperAPIError, StorageError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_matchups"
"""Default table name, used by :func:`fetch_and_write_matchups`."""

DEFAULT_MAX_WEEK = 18
"""Default cap on weeks fetched per season (a full NFL regular + postseason)."""

DEFAULT_TRAILING_REFRESH_WEEKS = 2
"""Default number of a not-yet-complete season's most recent cached weeks to
always refetch (stat corrections), even though they were already cached."""

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


_EXISTING_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "roster_id": pl.Int64,
    "matchup_id": pl.Int64,
    "points": pl.Float64,
    "custom_points": pl.Float64,
    "players": pl.String,
    "starters": pl.String,
    "starters_points": pl.String,
    "players_points": pl.String,
}


def _read_existing(db_path: str | Path, table_name: str) -> pl.DataFrame:
    """Read already-cached :data:`TABLE_NAME` rows, tolerating a first-ever run.

    Args:
        db_path: Path to the DuckDB database file.
        table_name: Table to read.

    Returns:
        The table's rows, or an empty frame with the expected schema if the
        database file or the table doesn't exist yet.
    """
    try:
        return read_table(db_path, table_name)
    except StorageError:
        return pl.DataFrame(schema=_EXISTING_SCHEMA)


def weeks_to_fetch(
    cached_weeks: set[int],
    *,
    max_week: int,
    is_complete: bool,
    trailing_refresh_weeks: int,
) -> set[int]:
    """Decide which weeks need a live Sleeper call this run.

    Args:
        cached_weeks: Weeks already persisted for this league.
        max_week: Maximum week number to consider.
        is_complete: Whether the league's raw ``status`` reads ``"complete"``.
        trailing_refresh_weeks: For a season that isn't complete yet, how
            many of its most-recently-cached weeks to always refetch.

    Returns:
        Week numbers (1..``max_week``) that should be fetched live this run:
        every week never cached, plus — only when ``is_complete`` is
        ``False`` — the most recent ``trailing_refresh_weeks`` cached weeks.
    """
    never_cached = set(range(1, max_week + 1)) - cached_weeks
    if is_complete or not cached_weeks:
        return never_cached

    trailing_start = max(1, max(cached_weeks) - trailing_refresh_weeks + 1)
    trailing = {week for week in cached_weeks if week >= trailing_start}
    return never_cached | trailing


def fetch_and_write_matchups(
    client: SleeperClient,
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    max_week: int = DEFAULT_MAX_WEEK,
    table_name: str = TABLE_NAME,
    trailing_refresh_weeks: int = DEFAULT_TRAILING_REFRESH_WEEKS,
) -> int:
    """Fetch every week's matchups for every league in a chain and persist them.

    **Incremental** (issue #148): a week already cached for a league whose
    ``status`` is ``"complete"`` is never refetched — see the module
    docstring. A week that fails to fetch live keeps whatever was already
    cached for it, rather than losing that data to a transient error.

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
        trailing_refresh_weeks: For a not-yet-complete season, how many of
            its most recently cached weeks to always refetch live (stat
            corrections), even though they were already cached.

    Returns:
        The number of rows written.
    """
    existing = _read_existing(db_path, table_name)
    rows: list[dict[str, Any]] = []

    for league in leagues:
        league_id = str(league["league_id"])
        season = int(league["season"]) if league.get("season") else None
        is_complete = league.get("status") == "complete"

        league_existing = (
            existing.filter(pl.col("league_id") == league_id)
            if existing.height
            else existing
        )
        cached_weeks = (
            set(league_existing["week"].to_list()) if league_existing.height else set()
        )
        weeks_needed = weeks_to_fetch(
            cached_weeks,
            max_week=max_week,
            is_complete=is_complete,
            trailing_refresh_weeks=trailing_refresh_weeks,
        )

        if league_existing.height:
            kept = league_existing.filter(~pl.col("week").is_in(sorted(weeks_needed)))
            rows.extend(kept.to_dicts())

        for week in sorted(weeks_needed):
            try:
                matchups = client.get_matchups(league_id, week)
            except SleeperAPIError as exc:
                logger.warning(
                    "Could not fetch matchups for league %s week %d: %s",
                    league_id,
                    week,
                    exc,
                )
                if league_existing.height:
                    rows.extend(
                        league_existing.filter(pl.col("week") == week).to_dicts()
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
