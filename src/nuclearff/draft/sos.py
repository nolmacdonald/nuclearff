"""Strength of schedule: how favorable a candidate's remaining games look.

Combines :mod:`nuclearff.matchups.dvp`'s real points-allowed-by-position
table (historical, from played games) with
:mod:`nuclearff.nflverse.schedules`'s real schedule (future games, which
have no stat rows yet) to rank how favorable a team's remaining schedule is
at a given position.

Built for issue #105; issue #106 (playoff schedule strength) reuses this
module's output over a narrower week window rather than recomputing
anything.
"""

from __future__ import annotations

import polars as pl

from nuclearff.nflverse.schedules import team_opponents


def strength_of_schedule(
    team: str,
    position: str,
    dvp: pl.DataFrame,
    schedules: pl.DataFrame,
    season: int,
    remaining_weeks: list[int],
) -> float | None:
    """Average remaining-opponent points-allowed at ``position`` for ``team``.

    Args:
        team: The candidate player's real NFL team abbreviation.
        position: The candidate's position.
        dvp: Rows as returned by
            :func:`nuclearff.matchups.dvp.points_allowed_by_position`.
        schedules: Rows as returned by
            :func:`nuclearff.nflverse.schedules.load_schedules`.
        season: The season to evaluate.
        remaining_weeks: Week numbers still ahead on ``team``'s schedule.

    Returns:
        The mean, across ``remaining_weeks``, of each real opponent's
        ``season_avg_points_allowed`` at ``position`` — higher means an
        easier remaining schedule (the opponents faced have historically
        given up more points at this position). ``None`` if none of
        ``remaining_weeks`` resolve to a real scheduled opponent (a bye
        week, or a week past the schedule's real coverage), or if none of
        those opponents have any ``dvp`` history at ``position`` yet (early
        season, thin sample) — callers should treat ``None`` as "not enough
        data," never as "easy schedule."
    """
    opponents = team_opponents(schedules).filter(
        (pl.col("season") == season)
        & (pl.col("team") == team)
        & (pl.col("week").is_in(remaining_weeks))
    )
    if opponents.height == 0:
        return None

    dvp_at_position = dvp.filter(
        (pl.col("season") == season) & (pl.col("position") == position)
    ).unique(subset=["opponent_team"], keep="first")

    joined = opponents.join(
        dvp_at_position.rename({"opponent_team": "opponent"}),
        on="opponent",
        how="inner",
    )
    if joined.height == 0:
        return None

    values = joined["season_avg_points_allowed"].drop_nulls().to_list()
    if not values:
        return None
    return sum(values) / len(values)
