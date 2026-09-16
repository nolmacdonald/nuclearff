"""Thin wrapper around ``nflreadpy`` for real NFL game schedules.

Added for issue #105 (defense-vs-position strength of schedule) and reused
by #106 (playoff schedule strength) and #114 (date-anchored "on this day"
matchup callbacks) rather than each adding its own wrapper.

Verified live against the installed nflreadpy version's
``load_schedules(seasons=[2025])``: returns ``game_id``, ``season``,
``game_type``, ``week``, ``gameday`` (a real calendar date string),
``home_team``, ``away_team``, plus scores/odds/weather columns this project
has no use for yet. Do not assume this holds for a far-future nflreadpy
version without re-checking, per this project's standing habit for every
nflreadpy loader (see the "nflreadpy" gotcha entries in ``brain/gotchas.md``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import nflreadpy
import polars as pl

logger = logging.getLogger(__name__)

_SCHEDULES_REQUIRED_COLUMNS = (
    "season",
    "week",
    "game_type",
    "gameday",
    "home_team",
    "away_team",
)


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing a column ``fn_name`` needs.

    Args:
        df: The DataFrame passed to ``fn_name``.
        required: Column names ``fn_name`` depends on.
        fn_name: Name of the calling function, included in the error message.

    Raises:
        ValueError: If any column in ``required`` is absent from ``df``.
    """
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{fn_name}: input DataFrame is missing expected column(s) "
            f"{missing!r} (got {df.columns!r})."
        )


def load_schedules(seasons: list[int]) -> pl.DataFrame:
    """Load real NFL game schedules for ``seasons``.

    Args:
        seasons: Seasons to load.

    Returns:
        One row per game (real or scheduled), with at least ``season``,
        ``week``, ``game_type``, ``gameday``, ``home_team``, and
        ``away_team``.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse schedules for seasons %s", seasons)
    df = nflreadpy.load_schedules(seasons=seasons)
    _require_columns(df, _SCHEDULES_REQUIRED_COLUMNS, "load_schedules")
    return df


def team_opponents(schedules: pl.DataFrame) -> pl.DataFrame:
    """Flatten ``schedules`` into one row per ``(season, week, team, opponent)``.

    Every real game contributes two rows, one from each side's perspective,
    so a downstream lookup can filter on a single team without re-deriving
    home/away logic itself.

    Args:
        schedules: Rows as returned by :func:`load_schedules`.

    Returns:
        ``season``, ``week``, ``team``, ``opponent`` — one row per team per
        game.
    """
    home = schedules.select(
        "season",
        "week",
        pl.col("home_team").alias("team"),
        pl.col("away_team").alias("opponent"),
    )
    away = schedules.select(
        "season",
        "week",
        pl.col("away_team").alias("team"),
        pl.col("home_team").alias("opponent"),
    )
    return pl.concat([home, away])
