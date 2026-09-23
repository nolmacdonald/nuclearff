"""Strength of schedule: how favorable a candidate's remaining games look.

Combines :mod:`nuclearff.matchups.dvp`'s real points-allowed-by-position
table (historical, from played games) with
:mod:`nuclearff.nflverse.schedules`'s real schedule (future games, which
have no stat rows yet) to rank how favorable a team's remaining schedule is
at a given position.

Built for issue #105; :func:`playoff_strength_of_schedule` (issue #106)
reuses :func:`strength_of_schedule` entirely over a narrower week window
(the league's real fantasy-playoff weeks,
:meth:`nuclearff.config.league.LeagueConfig.playoff_weeks`) rather than
recomputing anything -- a draft-time consideration distinct from the
whole-remaining-season average, since a pick's value matters most for
winning the fantasy playoffs specifically.
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


def playoff_strength_of_schedule(
    team: str,
    position: str,
    dvp: pl.DataFrame,
    schedules: pl.DataFrame,
    season: int,
    playoff_weeks: list[int] | None,
) -> float | None:
    """Average fantasy-*playoff*-week opponent points-allowed at ``position``.

    Identical to :func:`strength_of_schedule`, scoped to
    ``playoff_weeks`` instead of the whole remaining season — no new DvP
    computation, per issue #106's own instruction.

    Args:
        team: The candidate player's real NFL team abbreviation.
        position: The candidate's position.
        dvp: Rows as returned by
            :func:`nuclearff.matchups.dvp.points_allowed_by_position`.
        schedules: Rows as returned by
            :func:`nuclearff.nflverse.schedules.load_schedules`.
        season: The season to evaluate.
        playoff_weeks: The league's real fantasy-playoff week numbers, from
            :meth:`nuclearff.config.league.LeagueConfig.playoff_weeks`.
            ``None`` if that couldn't be resolved (missing
            ``playoff_week_start``/``playoff_teams``) — not guessed at a
            placeholder range like "15-17".

    Returns:
        Same semantics as :func:`strength_of_schedule`. ``None`` if
        ``playoff_weeks`` is ``None`` or empty, matching this project's
        "don't guess" posture for missing playoff-range data (see
        :meth:`~nuclearff.config.league.LeagueConfig.playoff_weeks`).
    """
    if not playoff_weeks:
        return None
    return strength_of_schedule(team, position, dvp, schedules, season, playoff_weeks)
