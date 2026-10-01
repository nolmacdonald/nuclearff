"""Thin wrapper around ``nflreadpy.load_injuries`` (issue #103).

Same posture as :mod:`nuclearff.nflverse.stats`: delegate to the matching
``nflreadpy`` loader, validate the columns depended on, and do the minimum
documented filtering.

Live finding (nflreadpy 0.1.5, seasons 2009, 2015, 2019, 2023-2025): the table
is keyed by ``gsis_id`` (no nulls in any sampled season), so it joins directly
to nflverse player tables and, via ``sleeper_player_id_map``
(:mod:`nuclearff.ids.crosswalk`), to Sleeper's player-ID space. It has one row
per player, week, and injury-report entry (a player-week can repeat, once per
practice-day report), with ``report_status`` in ``Out``, ``Doubtful``,
``Questionable``, ``Note`` or null, and ``game_type`` in ``REG``, ``WC``,
``DIV``, ``CON``, ``SB``.

Coverage caveat: a player placed on injured reserve drops off the weekly report.
Christian McCaffrey missed 13 games in 2024 but is listed ``Out`` in a single
week, so ``Out`` weeks undercount long-term absences. Treat counts derived from
this table as a floor on games missed, not the number itself.
"""

from __future__ import annotations

import logging

import nflreadpy
import polars as pl

from nuclearff.nflverse.stats import _require_columns

logger = logging.getLogger(__name__)

INJURIES_COVERAGE_START = 2009
"""First season nflreadpy's ``load_injuries`` covers."""

_INJURIES_REQUIRED_COLUMNS = ("season", "week", "gsis_id", "report_status", "game_type")


def load_injury_history(seasons: list[int]) -> pl.DataFrame:
    """Load the weekly NFL injury report, restricted to regular-season weeks.

    Delegates to ``nflreadpy.load_injuries(seasons=seasons)`` and keeps only
    ``game_type == "REG"`` rows, so playoff-week designations never count
    toward a regular-season availability history. Otherwise unfiltered: every
    position is kept and a player-week can appear more than once.

    Args:
        seasons: Seasons to load. Seasons before
            :data:`INJURIES_COVERAGE_START` return no rows (nflreadpy's own
            behavior).

    Returns:
        The injury-report rows, with ``season``, ``week``, ``gsis_id``,
        ``position``, ``report_status`` and the primary-injury columns
        nflreadpy publishes.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse injury reports for seasons %s", seasons)
    df = nflreadpy.load_injuries(seasons=seasons)
    _require_columns(df, _INJURIES_REQUIRED_COLUMNS, "load_injury_history")
    return df.filter(pl.col("game_type") == "REG")
