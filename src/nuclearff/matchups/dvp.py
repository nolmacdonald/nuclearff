"""Defense-vs-position: real fantasy points allowed, by position, per team.

Built for issue #105 (the league-history and draft-companion epics' shared
strength-of-schedule foundation). Every real weekly skill-position stat line
already carries the real ``opponent_team`` it was scored against (confirmed
live in ``nflreadpy.load_player_stats``), so this module needs no join
against :mod:`nuclearff.nflverse.schedules` for the *historical* half of the
work — only the forward-looking remaining-schedule side
(:mod:`nuclearff.draft.sos`) needs the schedule loader, for weeks that
haven't been played yet and therefore have no stat rows at all.

Every row is scored through this league's own :class:`ScoringEngine` rather
than trusting a generic points column — the same posture every other
fantasy-points computation in this project already takes (see
``report performance``'s use of ``score_projection``).
"""

from __future__ import annotations

import logging

import polars as pl

from nuclearff.scoring.engine import ScoringEngine

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ("season", "week", "position", "opponent_team")


def points_allowed_by_position(
    weekly_skill_stats: pl.DataFrame, engine: ScoringEngine
) -> pl.DataFrame:
    """Aggregate real fantasy points allowed, by position, per defense.

    Args:
        weekly_skill_stats: Rows as returned by
            :func:`nuclearff.nflverse.stats.load_weekly_skill_stats` — one
            row per skill-position player per game, with ``opponent_team``
            already present.
        engine: Scores each row under this league's real rules via
            :meth:`ScoringEngine.score_frame`.

    Returns:
        ``season``, ``week``, ``opponent_team`` (the *defense*, i.e. the
        team that gave up these points), ``position``, ``points_allowed``
        (real fantasy points scored by every player at that position
        against that defense that week, summed), and
        ``season_avg_points_allowed`` (that defense/position's mean
        ``points_allowed`` across every week present in
        ``weekly_skill_stats`` for that season — reflects whatever weeks the
        caller passed in, not a true point-in-time "as of this week" figure;
        a caller wanting the latter should pass in only the weeks played so
        far).

    Raises:
        ValueError: If ``weekly_skill_stats`` is missing a required column.
    """
    missing = [c for c in _REQUIRED_COLUMNS if c not in weekly_skill_stats.columns]
    if missing:
        raise ValueError(
            f"points_allowed_by_position: input DataFrame is missing "
            f"expected column(s) {missing!r} (got "
            f"{weekly_skill_stats.columns!r})."
        )

    scored = engine.score_frame(weekly_skill_stats)

    weekly = (
        scored.group_by(["season", "week", "opponent_team", "position"])
        .agg(pl.col("fantasy_points").sum().alias("points_allowed"))
        .sort(["season", "opponent_team", "position", "week"])
    )

    return weekly.with_columns(
        pl.col("points_allowed")
        .mean()
        .over(["season", "opponent_team", "position"])
        .alias("season_avg_points_allowed")
    )
