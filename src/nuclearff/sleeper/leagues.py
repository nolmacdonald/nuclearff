"""Walk a league's ``previous_league_id`` chain and persist history to DuckDB.

``nuclearff sleeper fetch-league`` captures a single league's current-season
payload. A league's ``previous_league_id`` links back to the same league in
its prior season, but nothing walks that chain — there is no durable,
queryable record of a league's history the way the Sleeper player map already
has one (:mod:`nuclearff.sleeper.players`). This module fetches every hop in
that chain and writes both the raw Sleeper payloads and their parsed
:class:`~nuclearff.config.league.LeagueConfig` fields to DuckDB via
:mod:`nuclearff.duckdb_io`, following the same plain-SQL pattern.

A hop that fails to fetch stops the walk (its own ``previous_league_id`` is
needed to keep going); a hop that fetches but fails to type into
``LeagueConfig`` keeps its raw row and simply has no config row. Neither
aborts the whole walk — see :func:`walk_league_chain` and
:func:`league_config_rows`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nuclearff.config.league import league_config_from_sleeper
from nuclearff.duckdb_io import replace_table
from nuclearff.exceptions import ConfigError, SleeperAPIError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_leagues"
"""Default table name for raw league payloads, used by :func:`write_league_tables`."""

CONFIG_TABLE_NAME = "sleeper_league_configs"
"""Default table name for parsed ``LeagueConfig`` rows.

Used by :func:`write_league_tables`.
"""

DEFAULT_MAX_SEASONS = 20
"""Default cap on chain length, guarding against an unexpectedly long walk."""

_LEAGUE_TYPE_NAMES = {0: "redraft", 1: "keeper", 2: "dynasty", 3: "chopped"}
"""Sleeper's numeric ``settings.type`` mapped to a readable label.

An unrecognized value falls back to ``"unknown"`` rather than raising —
Sleeper's own docs only document 0/1/2, but nothing guarantees that stays
exhaustive. ``3`` ("Chopped": lowest scorer eliminated weekly, no playoff
bracket) was confirmed live against a real league
(``1262207133378695168``) while investigating GitHub Issue 34 — see
:mod:`nuclearff.sleeper.standings` for how that league type's final
standing is derived without a bracket.
"""

_LEAGUE_COLUMNS = (
    "league_id",
    "previous_league_id",
    "season",
    "name",
    "status",
    "league_type",
    "total_rosters",
    "settings",
    "scoring_settings",
    "roster_positions",
    "fetched_at",
)
"""Columns for :data:`TABLE_NAME`, matching :data:`_LEAGUE_CREATE_TABLE_SQL`'s order."""

_LEAGUE_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    league_id VARCHAR PRIMARY KEY,
    previous_league_id VARCHAR,
    season INTEGER,
    name VARCHAR,
    status VARCHAR,
    league_type VARCHAR,
    total_rosters INTEGER,
    settings JSON,
    scoring_settings JSON,
    roster_positions JSON,
    fetched_at TIMESTAMP
)
"""
"""Schema for :data:`TABLE_NAME`. ``settings``/``scoring_settings``/``roster_positions``
carry the raw Sleeper payload verbatim, the same posture
:class:`nuclearff.config.league.ScoringSettings`/``RosterSlots`` take for tolerating
an unfixed key set — see that module's docstring."""

_CONFIG_COLUMNS = (
    "league_id",
    "season",
    "name",
    "num_teams",
    "league_type",
    "best_ball",
    "is_full_ppr",
    "has_te_premium",
    "draft_id",
    "previous_league_id",
    "roster_qb",
    "roster_rb",
    "roster_wr",
    "roster_te",
    "roster_flex",
    "roster_superflex",
    "roster_bench",
    "roster_ir",
)
"""Columns for :data:`CONFIG_TABLE_NAME`, in the same order as
:data:`_CONFIG_CREATE_TABLE_SQL`. Scoring is deliberately not flattened here —
Sleeper's scoring-key set is not fixed (see
:class:`nuclearff.config.league.ScoringSettings`), and the raw
``scoring_settings`` JSON already lives in :data:`TABLE_NAME`, joinable by
``league_id``."""

_CONFIG_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    league_id VARCHAR PRIMARY KEY,
    season INTEGER,
    name VARCHAR,
    num_teams INTEGER,
    league_type INTEGER,
    best_ball BOOLEAN,
    is_full_ppr BOOLEAN,
    has_te_premium BOOLEAN,
    draft_id VARCHAR,
    previous_league_id VARCHAR,
    roster_qb INTEGER,
    roster_rb INTEGER,
    roster_wr INTEGER,
    roster_te INTEGER,
    roster_flex INTEGER,
    roster_superflex INTEGER,
    roster_bench INTEGER,
    roster_ir INTEGER
)
"""
"""Schema for :data:`CONFIG_TABLE_NAME`."""


def _chain_previous_league_id(league_json: dict[str, Any]) -> str | None:
    """Return the next hop's league ID, or ``None`` if the chain ends here.

    Sleeper represents "no prior season" inconsistently across leagues:
    the key can be missing, ``null``, an empty string, or the string
    ``"0"``. All four are treated as chain termination.

    Args:
        league_json: A raw Sleeper league object.

    Returns:
        The prior season's league ID, or ``None`` if there is none.
    """
    raw = league_json.get("previous_league_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text and text != "0" else None


def walk_league_chain(
    client: SleeperClient,
    league_id: str,
    *,
    max_seasons: int = DEFAULT_MAX_SEASONS,
) -> list[dict[str, Any]]:
    """Fetch a league and every prior season reachable via ``previous_league_id``.

    Args:
        client: A configured Sleeper client.
        league_id: The starting (most recent) league identifier.
        max_seasons: Maximum number of hops to fetch, including the starting
            league. Guards against an unexpectedly long or malformed chain.

    Returns:
        Raw league payloads, most recent season first, in fetch order. Always
        has at least one entry.

    Raises:
        SleeperAPIError: If the starting league itself cannot be fetched. A
            failure on a later hop is logged and stops the walk instead of
            raising — see the module docstring.
    """
    leagues: list[dict[str, Any]] = [client.get_league(league_id)]
    seen = {league_id}

    while len(leagues) < max_seasons:
        next_id = _chain_previous_league_id(leagues[-1])
        if next_id is None:
            break
        if next_id in seen:
            logger.warning(
                "Sleeper league chain from %s revisits %s; stopping walk",
                league_id,
                next_id,
            )
            break

        try:
            next_league = client.get_league(next_id)
        except SleeperAPIError as exc:
            logger.warning(
                "Could not fetch league %s (previous_league_id of %s): %s",
                next_id,
                leagues[-1]["league_id"],
                exc,
            )
            break

        leagues.append(next_league)
        seen.add(next_id)

    if len(leagues) >= max_seasons:
        logger.info(
            "Sleeper league chain from %s hit max_seasons=%d; stopping walk",
            league_id,
            max_seasons,
        )

    return leagues


def league_rows(
    leagues: list[dict[str, Any]], *, fetched_at: datetime | None = None
) -> list[dict[str, Any]]:
    """Flatten raw league payloads into :data:`TABLE_NAME` rows.

    Args:
        leagues: Raw league payloads, as returned by :func:`walk_league_chain`.
        fetched_at: Timestamp to record for every row. Defaults to now (UTC).

    Returns:
        One row per league, keyed by :data:`_LEAGUE_COLUMNS`.
    """
    if fetched_at is None:
        fetched_at = datetime.now(tz=UTC)

    rows: list[dict[str, Any]] = []
    for league in leagues:
        settings = league.get("settings")
        if not isinstance(settings, dict):
            settings = {}

        previous_league_id = league.get("previous_league_id")
        rows.append(
            {
                "league_id": str(league["league_id"]),
                "previous_league_id": (
                    str(previous_league_id) if previous_league_id else None
                ),
                "season": int(league["season"]) if league.get("season") else None,
                "name": league.get("name"),
                "status": league.get("status"),
                "league_type": _LEAGUE_TYPE_NAMES.get(settings.get("type"), "unknown"),
                "total_rosters": league.get("total_rosters"),
                "settings": json.dumps(settings),
                "scoring_settings": json.dumps(league.get("scoring_settings") or {}),
                "roster_positions": json.dumps(league.get("roster_positions") or []),
                "fetched_at": fetched_at,
            }
        )
    return rows


def league_config_rows(leagues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse raw league payloads into :data:`CONFIG_TABLE_NAME` rows.

    A payload that does not type into :class:`~nuclearff.config.league.LeagueConfig`
    is logged and skipped rather than raising — its raw row (see
    :func:`league_rows`) is kept regardless.

    Args:
        leagues: Raw league payloads, as returned by :func:`walk_league_chain`.

    Returns:
        One row per league that parsed successfully, keyed by
        :data:`_CONFIG_COLUMNS`.
    """
    rows: list[dict[str, Any]] = []
    for league in leagues:
        try:
            cfg = league_config_from_sleeper(league)
        except ConfigError as exc:
            logger.warning(
                "Could not derive a LeagueConfig for league %s: %s",
                league.get("league_id"),
                exc,
            )
            continue

        rows.append(
            {
                "league_id": cfg.league_id,
                "season": cfg.season,
                "name": cfg.name,
                "num_teams": cfg.num_teams,
                "league_type": cfg.league_type,
                "best_ball": cfg.best_ball,
                "is_full_ppr": cfg.scoring.is_full_ppr,
                "has_te_premium": cfg.scoring.has_te_premium,
                "draft_id": cfg.draft_id,
                "previous_league_id": cfg.previous_league_id,
                "roster_qb": cfg.roster.qb,
                "roster_rb": cfg.roster.rb,
                "roster_wr": cfg.roster.wr,
                "roster_te": cfg.roster.te,
                "roster_flex": cfg.roster.flex,
                "roster_superflex": cfg.roster.superflex,
                "roster_bench": cfg.roster.bench,
                "roster_ir": cfg.roster.ir,
            }
        )
    return rows


def write_league_tables(
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
    config_table_name: str = CONFIG_TABLE_NAME,
    fetched_at: datetime | None = None,
) -> tuple[int, int]:
    """Write raw and parsed league history to DuckDB, replacing both tables wholesale.

    Each call replaces both tables rather than upserting, matching
    :func:`nuclearff.sleeper.players.write_players_table` — a chain walk is a
    full re-fetch, not an incremental feed.

    Args:
        leagues: Raw league payloads, as returned by :func:`walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table for raw payloads.
        config_table_name: Destination table for parsed ``LeagueConfig`` rows.
        fetched_at: Timestamp to record for every raw row. Defaults to now (UTC).

    Returns:
        A ``(raw_rows_written, config_rows_written)`` tuple. The second is
        less than or equal to the first when a hop failed to parse (see
        :func:`league_config_rows`).

    Raises:
        ValueError: If either table name is not a plain identifier.
    """
    raw_rows = league_rows(leagues, fetched_at=fetched_at)
    raw_values = [[row[column] for column in _LEAGUE_COLUMNS] for row in raw_rows]
    raw_count = replace_table(
        db_path, table_name, _LEAGUE_CREATE_TABLE_SQL, _LEAGUE_COLUMNS, raw_values
    )

    config_rows = league_config_rows(leagues)
    config_values = [[row[column] for column in _CONFIG_COLUMNS] for row in config_rows]
    config_count = replace_table(
        db_path,
        config_table_name,
        _CONFIG_CREATE_TABLE_SQL,
        _CONFIG_COLUMNS,
        config_values,
    )

    logger.info(
        "Wrote %d Sleeper league(s) (%d with a parsed config) to %s",
        raw_count,
        config_count,
        db_path,
    )
    return raw_count, config_count
