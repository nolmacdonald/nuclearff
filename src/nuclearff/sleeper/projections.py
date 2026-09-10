"""Fetch and persist weekly player projections, and score them under a league's rules.

Sleeper exposes per-player weekly projections at an endpoint not listed in
the documented public API (https://docs.sleeper.com covers ``/v1/...``
only): :meth:`~nuclearff.sleeper.client.SleeperClient.get_projections`. See
that method's docstring for the live verification behind using it anyway.

Each projection's ``stats`` dict is keyed with Sleeper's own scoring
vocabulary already (``rec``, ``rec_yd``, ``pass_td``, ``fum_lost``, ...) —
the *same* vocabulary a league's ``scoring_settings`` uses. This is
different from :mod:`nuclearff.nflverse.stats`, which needs
:class:`nuclearff.scoring.engine.ScoringEngine` to translate from
nflverse's own, different column names. :func:`score_projection` scores a
projection line under a league's own
:class:`~nuclearff.config.league.ScoringSettings` directly, with no
translation layer needed — deliberately more accurate than trusting
Sleeper's own generic ``pts_ppr``/``pts_half_ppr``/``pts_std`` fields, which
assume a stock scoring format rather than this league's exact rules. This
is the same "compute in the league's own scoring rather than assume a
generic format" posture :mod:`nuclearff.nflverse.rankings` explicitly
declines to take for FantasyPros ECR, applied here instead.

Threshold bonuses (``bonus_rec_yd_100``, ...) and position-conditional
reception bonuses (``bonus_rec_wr``, ...) are **not** scored here: a
fractional projected yardage total (e.g. 62.3 receiving yards) can't
cleanly answer "did this cross 100 yards" the way a completed game's real
total can, and scoring an expected-value fraction of a flat bonus would
invent precision this data doesn't support. A league leaning on these for
meaningful points will see a projected score that runs a little low —
:func:`unscored_projection_keys` names exactly which of a league's nonzero
scoring keys aren't covered, so this is never a silent gap, the same
diagnostic posture :meth:`nuclearff.scoring.engine.ScoringEngine.unscored_keys`
already takes for the nflverse-backed scoring path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import polars as pl

from nuclearff.config.league import ScoringSettings
from nuclearff.duckdb_io import read_table, replace_table
from nuclearff.exceptions import StorageError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_projections"
"""Default table name, used by :func:`fetch_and_write_projections`.

Scoped by ``(season, week, player_id)``, not ``league_id`` — a projection
is a platform-wide fact about a player's expected week, the same
"not tied to any one league" posture :mod:`nuclearff.sleeper.players`
already takes for the player map, so leagues sharing a season/week never
duplicate or refetch each other's rows.
"""

DEFAULT_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")
"""Positions fetched by default — the same skill-position scope this
project's valuation/metrics modules already focus on."""

_SCHEMA = {
    "season": pl.Int64,
    "week": pl.Int64,
    "player_id": pl.Utf8,
    "team": pl.Utf8,
    "opponent": pl.Utf8,
    "position": pl.Utf8,
    "company": pl.Utf8,
    "updated_at": pl.Int64,
    "stats": pl.Utf8,
}
_COLUMNS: tuple[str, ...] = tuple(_SCHEMA)

_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    season INTEGER,
    week INTEGER,
    player_id VARCHAR,
    team VARCHAR,
    opponent VARCHAR,
    position VARCHAR,
    company VARCHAR,
    updated_at BIGINT,
    stats JSON,
    PRIMARY KEY (season, week, player_id)
)
"""

_SCORABLE_KEYS: tuple[str, ...] = (
    "rec",
    "rec_yd",
    "rec_td",
    "rec_fd",
    "rec_2pt",
    "rush_att",
    "rush_yd",
    "rush_td",
    "rush_fd",
    "rush_2pt",
    "pass_att",
    "pass_cmp",
    "pass_yd",
    "pass_td",
    "pass_int",
    "pass_2pt",
    "fum",
    "fum_lost",
)
"""Sleeper stat-category keys a projection's ``stats`` dict carries that
also appear directly in a league's own scoring vocabulary. Deliberately
excludes threshold/position-conditional bonus keys — see the module
docstring."""


def projection_rows(
    season: int, week: int, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Flatten raw Sleeper projection entries into :data:`TABLE_NAME` rows.

    Args:
        season: Season these projections are for.
        week: Week these projections are for.
        entries: Raw entries, as returned by
            :meth:`~nuclearff.sleeper.client.SleeperClient.get_projections`.

    Returns:
        One row per entry with a real ``player_id``. Sleeper's team-level
        defense rows use a team abbreviation in place of a numeric player
        id and are kept (defenses are a legitimate ``player_id`` in
        Sleeper's own vocabulary); an entry missing ``player_id`` entirely
        is skipped rather than guessed at.
    """
    rows: list[dict[str, Any]] = []
    for entry in entries:
        player_id = entry.get("player_id")
        if not player_id:
            continue
        player = entry.get("player") or {}
        rows.append(
            {
                "season": season,
                "week": week,
                "player_id": str(player_id),
                "team": entry.get("team") or player.get("team"),
                "opponent": entry.get("opponent"),
                "position": entry.get("position") or player.get("position"),
                "company": entry.get("company"),
                "updated_at": entry.get("updated_at"),
                "stats": json.dumps(entry.get("stats") or {}),
            }
        )
    return rows


def fetch_and_write_projections(
    client: SleeperClient,
    season: int,
    week: int,
    db_path: str | Path,
    *,
    positions: tuple[str, ...] = DEFAULT_POSITIONS,
    season_type: str = "regular",
    table_name: str = TABLE_NAME,
) -> int:
    """Fetch one week's projections and merge them into :data:`TABLE_NAME`.

    Unlike :func:`nuclearff.duckdb_io.replace_table`'s normal wholesale-
    replace contract, this only replaces rows for this specific
    ``(season, week)`` — every other week already persisted survives.
    Wholesale-replacing the whole table on every call would repeat the
    exact bug class GitHub Issue 85 found and fixed for
    ``sleeper_draft_picks`` (a per-season write silently erasing every
    earlier season already written): existing rows are read back first,
    rows for this ``(season, week)`` are dropped, the freshly fetched rows
    are appended, and the merged result is written in one
    :func:`~nuclearff.duckdb_io.replace_table` call.

    Args:
        client: A configured Sleeper client.
        season: Season to fetch.
        week: Week to fetch.
        db_path: Path to the DuckDB database file, created if absent.
        positions: Positions to request from Sleeper.
        season_type: Sleeper season type, e.g. ``"regular"``.
        table_name: Destination table.

    Returns:
        The number of rows now stored in the whole table after the merge.
    """
    entries = client.get_projections(
        season, week, positions=positions, season_type=season_type
    )
    new_rows = projection_rows(season, week, entries)
    logger.info(
        "Fetched %d projection rows for season %s week %s", len(new_rows), season, week
    )

    try:
        existing = read_table(db_path, table_name).with_columns(
            pl.col(name).cast(dtype) for name, dtype in _SCHEMA.items()
        )
    except StorageError:
        existing = pl.DataFrame(schema=_SCHEMA)

    kept = existing.filter(~((pl.col("season") == season) & (pl.col("week") == week)))
    new_frame = (
        pl.DataFrame(new_rows, schema=_SCHEMA)
        if new_rows
        else pl.DataFrame(schema=_SCHEMA)
    )
    merged = pl.concat([kept, new_frame], how="vertical")

    return replace_table(
        db_path, table_name, _CREATE_TABLE_SQL, _COLUMNS, merged.select(_COLUMNS).rows()
    )


def score_projection(stats: dict[str, Any], scoring: ScoringSettings) -> float:
    """Score one player's projected stat line under a league's own rules.

    Args:
        stats: A projection's ``stats`` dict, keyed by Sleeper's own stat
            category vocabulary (e.g. ``{"rec_yd": 62.3, "rec": 4.8}``).
        scoring: The league's scoring rules.

    Returns:
        Projected fantasy points. See the module docstring for which
        scoring keys this does and does not account for.
    """
    return sum(scoring.get(key) * (stats.get(key) or 0.0) for key in _SCORABLE_KEYS)


def unscored_projection_keys(scoring: ScoringSettings) -> list[str]:
    """Nonzero scoring keys :func:`score_projection` cannot account for.

    Mirrors :meth:`nuclearff.scoring.engine.ScoringEngine.unscored_keys`'s
    diagnostic role, but against :data:`_SCORABLE_KEYS` rather than
    nflverse's stat vocabulary.

    Args:
        scoring: The league's scoring rules.

    Returns:
        Sorted nonzero keys in ``scoring.values`` that projected points
        will not reflect.
    """
    return sorted(
        key
        for key, value in scoring.values.items()
        if value != 0 and key not in _SCORABLE_KEYS
    )
