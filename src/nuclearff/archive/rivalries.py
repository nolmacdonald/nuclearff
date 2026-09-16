"""League history archive: head-to-head rivalries (issue #110, epic #116).

``sleeper/wins.py::paired_weekly_matchups`` (added for #109) already pairs
each week's two rosters with real points and margin — this module joins
that against manager identity and collapses it to one row per manager pair
across every persisted season. No new Sleeper fetching.

**Deliberately scoped down from issue #110's original proposal: no
regular-season/playoff split in this pass.** That split needs a real
playoff-week boundary, which needs ``LeagueConfig.playoff_week_start`` — a
field that doesn't exist yet (the same gap the draft-companion epic's #106
already surfaced). Rather than block this issue on that fix, or hardcode a
guessed week range, this ships the combined all-time head-to-head record
only; the playoff/regular-season breakdown is deferred to a follow-up once
that config gap is fixed elsewhere.

Manager identity is ``sleeper_standings.display_name``, the same
cross-season-identity posture every other multi-season aggregate in this
project accepts.
"""

from __future__ import annotations

import polars as pl

from nuclearff.sleeper.wins import paired_weekly_matchups

_SCHEMA = {
    "manager_a": pl.String,
    "manager_b": pl.String,
    "games": pl.UInt32,
    "wins_a": pl.UInt32,
    "wins_b": pl.UInt32,
    "ties": pl.UInt32,
    "avg_margin": pl.Float64,
    "biggest_blowout_margin": pl.Float64,
    "biggest_blowout_winner": pl.String,
    "biggest_blowout_season": pl.Int64,
    "biggest_blowout_week": pl.Int64,
}


def head_to_head(matchups: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """All-time combined head-to-head record for every manager pair that has met.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows, for ``display_name``.

    Returns:
        One row per manager pair (``manager_a`` < ``manager_b``
        alphabetically, so a pair is never double-counted in both
        directions): ``games``, ``wins_a``/``wins_b``/``ties``,
        ``avg_margin`` (mean absolute point differential across their
        meetings — a competitiveness measure, not signed toward either
        manager), and the pair's own ``biggest_blowout_margin`` /
        ``biggest_blowout_winner`` / ``biggest_blowout_season`` /
        ``biggest_blowout_week`` (``biggest_blowout_winner`` is ``None``
        only if every meeting between this pair has been a tie).

        A roster pair that shares the same resolved ``display_name`` (data
        anomaly, not expected in real data) is excluded rather than
        collapsed into a self-matchup — same posture as excluding an
        unresolvable manager entirely.
    """
    paired = paired_weekly_matchups(matchups)
    if paired.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    names = standings.select("league_id", "roster_id", "display_name")
    joined = (
        paired.join(names, on=["league_id", "roster_id"], how="inner")
        .rename({"display_name": "manager"})
        .join(
            names.rename(
                {"roster_id": "opponent_roster_id", "display_name": "opponent"}
            ),
            on=["league_id", "opponent_roster_id"],
            how="inner",
        )
        .filter(
            pl.col("manager").is_not_null()
            & pl.col("opponent").is_not_null()
            & (pl.col("manager") != pl.col("opponent"))
        )
    )
    if joined.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    # Each real matchup appears twice in `joined` (once per side) -- keeping
    # only the alphabetically-earlier direction gives one row per matchup.
    canonical = joined.filter(pl.col("manager") < pl.col("opponent")).rename(
        {
            "manager": "manager_a",
            "opponent": "manager_b",
            "points": "manager_a_points",
            "opponent_points": "manager_b_points",
        }
    )
    # `joined` always contains both directions of a surviving pair (the
    # `manager != opponent` filter above already excludes a self-matchup),
    # so `canonical` can't be empty here if `joined` wasn't.
    canonical = canonical.with_columns(abs_margin=pl.col("margin").abs())

    summary = canonical.group_by(["manager_a", "manager_b"]).agg(
        pl.len().cast(pl.UInt32).alias("games"),
        (pl.col("margin") > 0).sum().cast(pl.UInt32).alias("wins_a"),
        (pl.col("margin") < 0).sum().cast(pl.UInt32).alias("wins_b"),
        (pl.col("margin") == 0).sum().cast(pl.UInt32).alias("ties"),
        pl.col("abs_margin").mean().alias("avg_margin"),
    )

    blowouts = (
        canonical.sort("abs_margin", descending=True)
        .group_by(["manager_a", "manager_b"], maintain_order=True)
        .first()
        .select(
            "manager_a",
            "manager_b",
            "season",
            "week",
            "margin",
            biggest_blowout_margin=pl.col("abs_margin"),
            biggest_blowout_winner=pl.when(pl.col("margin") > 0)
            .then(pl.col("manager_a"))
            .when(pl.col("margin") < 0)
            .then(pl.col("manager_b"))
            .otherwise(pl.lit(None)),
        )
        .rename({"season": "biggest_blowout_season", "week": "biggest_blowout_week"})
    )

    result = summary.join(blowouts, on=["manager_a", "manager_b"], how="inner")
    return result.select(list(_SCHEMA.keys())).sort("games", descending=True)
