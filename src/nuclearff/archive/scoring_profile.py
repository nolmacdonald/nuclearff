"""League history archive: per-manager season scoring profile (issue #154).

``sleeper_standings`` keeps season-end totals only, and
:mod:`nuclearff.archive.records` reports league-wide single-game extremes.
Neither shows how one manager's per-game scoring compares with their own
record. :func:`scoring_profile` builds that from ``sleeper_matchups``, one row
per season and manager, using the pairing step the rest of the archive shares
(:func:`nuclearff.sleeper.wins.paired_weekly_matchups`).

Conventions, matching :mod:`nuclearff.archive.records`:

* A margin is ``points - opponent_points``. Margin statistics are reported as
  positive magnitudes: ``avg_margin_lost_by`` is how far a manager lost by on
  average, not a negative number.
* A tie is neither a win nor a loss, so it enters no margin statistic. It
  still counts as a game played and in the points averages.
* A bye week (a real score with no opponent) counts toward ``games_played``
  and ``avg_points_for`` but has no margin and no opponent score, so it is
  excluded from every margin statistic and from ``avg_points_against``.
* A week not yet played is dropped by
  :func:`nuclearff.sleeper.wins.drop_unplayed_weeks`.

Manager identity is ``sleeper_standings.display_name``, the same
cross-season-identity posture as :mod:`nuclearff.archive.records`.
"""

from __future__ import annotations

import polars as pl

from nuclearff.sleeper.wins import drop_unplayed_weeks, paired_weekly_matchups

_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "manager": pl.String,
    "games_played": pl.UInt32,
    "avg_points_for": pl.Float64,
    "avg_points_against": pl.Float64,
    "avg_margin_won_by": pl.Float64,
    "avg_margin_lost_by": pl.Float64,
    "largest_blowout_win": pl.Float64,
    "largest_blowout_loss": pl.Float64,
    "closest_win": pl.Float64,
    "closest_loss": pl.Float64,
}

_KEYS = ["league_id", "season", "roster_id"]


def scoring_profile(matchups: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """Per-manager, per-season scoring averages and margin extremes.

    Args:
        matchups: ``sleeper_matchups`` rows -- ``league_id``, ``season``,
            ``week``, ``roster_id``, ``matchup_id``, ``points``.
        standings: ``sleeper_standings`` rows, for ``display_name``.

    Returns:
        One row per ``(league_id, season, manager)``, ordered by season and
        then ``avg_points_for`` descending: ``games_played`` (every played
        week, byes included), ``avg_points_for``, ``avg_points_against``,
        ``avg_margin_won_by`` (mean margin in wins), ``avg_margin_lost_by``
        (mean margin in losses), ``largest_blowout_win``,
        ``largest_blowout_loss``, ``closest_win`` and ``closest_loss``. Margin
        values are positive and ``null`` when the manager has no win (or no
        loss) that season; ``avg_points_against`` is ``null`` when every
        played week was a bye. Rosters with no ``display_name`` are omitted.
    """
    played = drop_unplayed_weeks(matchups)
    if played.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    totals = played.group_by(_KEYS).agg(
        pl.len().alias("games_played"),
        pl.col("points").mean().alias("avg_points_for"),
    )

    paired = paired_weekly_matchups(matchups)
    wins = pl.col("margin").filter(pl.col("margin") > 0)
    losses = (-pl.col("margin")).filter(pl.col("margin") < 0)
    margins = paired.group_by(_KEYS).agg(
        pl.col("opponent_points").mean().alias("avg_points_against"),
        wins.mean().alias("avg_margin_won_by"),
        losses.mean().alias("avg_margin_lost_by"),
        wins.max().alias("largest_blowout_win"),
        losses.max().alias("largest_blowout_loss"),
        wins.min().alias("closest_win"),
        losses.min().alias("closest_loss"),
    )

    names = standings.select("league_id", "roster_id", "display_name")
    return (
        totals.join(margins, on=_KEYS, how="left")
        .join(names, on=["league_id", "roster_id"], how="inner")
        .filter(pl.col("display_name").is_not_null())
        .rename({"display_name": "manager"})
        .select(list(_SCHEMA.keys()))
        .sort(["season", "avg_points_for"], descending=[False, True])
    )
