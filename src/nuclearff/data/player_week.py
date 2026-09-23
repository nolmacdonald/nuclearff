"""Fetch and build the ``player_week`` curated dataset from nflverse.

Guide §10's worked example (GitHub Issue 182): ``player_week`` is nflverse's
own weekly player statistics, narrowed to
:data:`~nuclearff.data.datasets.PLAYER_WEEK_SCHEMA`'s five required columns
and cast to its exact dtypes. The source is ``nflreadpy.load_player_stats``
(confirmed against the real package -- not the ``load_weekly_skill_stats``
name an earlier draft assumed, which does not exist in this dependency).
It already covers every position nflverse tracks, including K and DEF, so
there is no skill-position filter here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

import polars as pl

from nuclearff.data.datasets import PLAYER_WEEK_SCHEMA
from nuclearff.data.schema import validate_frame

PlayerStatsLoader = Callable[[Sequence[int]], pl.DataFrame]
"""A ``load_player_stats``-shaped callable: seasons -> raw weekly stats frame."""


def current_season(today: date | None = None) -> int:
    """The NFL season currently in progress, inferred from today's date.

    A season is named for the year its regular season starts, but runs into
    the following calendar year through the Super Bowl (early-to-mid
    February) -- so January and February belong to the *previous* year's
    season. By March 1st the prior season is unambiguously over (free
    agency/the draft, not games, follow), so that's the cutover point.

    Args:
        today: Date to infer from. Defaults to the real current date --
            injectable for tests.

    Returns:
        The season year, e.g. ``2025`` for any date from September 2025
        through February 2026, or ``2026`` from March 2026 through August
        2026.
    """
    today = today or date.today()
    return today.year - 1 if today.month < 3 else today.year


def _default_loader(seasons: Sequence[int]) -> pl.DataFrame:
    import nflreadpy

    return nflreadpy.load_player_stats(list(seasons), summary_level="week")


def fetch_player_week(
    seasons: Sequence[int], *, loader: PlayerStatsLoader = _default_loader
) -> pl.DataFrame:
    """Fetch raw nflverse weekly player statistics for one or more seasons.

    Args:
        seasons: Seasons to fetch, e.g. ``[2026]``.
        loader: Injectable in place of ``nflreadpy.load_player_stats`` --
            the same seam :class:`nuclearff.sleeper.SleeperClient` injection
            uses elsewhere in this project, so a test never needs a live
            network call.

    Returns:
        The unmodified frame ``nflreadpy`` returns: every column it
        publishes (150+, as of ``nflreadpy`` 0.1.x), ``season``/``week`` as
        ``Int32``. Narrowing and casting to ``PLAYER_WEEK_SCHEMA`` happens in
        :func:`build_player_week`, kept separate so a caller can inspect the
        raw frame before it's discarded.
    """
    return loader(seasons)


def build_player_week(
    raw: pl.DataFrame, *, through_week: int | None = None
) -> pl.DataFrame:
    """Narrow and cast raw nflverse stats to ``PLAYER_WEEK_SCHEMA``'s shape.

    Rows with a null ``player_id`` are dropped before validation: nflverse's
    weekly stats include team-level aggregate rows (no individual player
    attached -- confirmed against real 2024 data, one such row per team per
    week) that would otherwise fail ``PLAYER_WEEK_SCHEMA``'s non-nullable
    ``player_id``/``player_name`` columns.

    Args:
        raw: :func:`fetch_player_week`'s output.
        through_week: If given, drop rows with ``week`` greater than this
            (inclusive bound, matching
            :func:`nuclearff.data.repository.load_player_week`'s own filter
            shape). ``None`` keeps every week ``raw`` contains, regular
            season and postseason alike (nflverse numbers postseason weeks
            19-22, matching ``PLAYER_WEEK_SCHEMA``'s declared range -- they
            do not collide with regular-season week numbers).

    Returns:
        A frame with exactly ``PLAYER_WEEK_SCHEMA``'s five columns,
        ``season``/``week`` cast to ``Int64``, already validated against
        that schema.

    Raises:
        DataQualityError: If the built frame fails
            :data:`~nuclearff.data.datasets.PLAYER_WEEK_SCHEMA`'s checks.
    """
    frame = raw.filter(pl.col("player_id").is_not_null()).select(
        pl.col("season").cast(pl.Int64),
        pl.col("week").cast(pl.Int64),
        pl.col("player_id").cast(pl.String),
        pl.col("player_name").cast(pl.String),
        pl.col("team").cast(pl.String),
    )
    if through_week is not None:
        frame = frame.filter(pl.col("week") <= through_week)

    validate_frame(PLAYER_WEEK_SCHEMA, frame)
    return frame
