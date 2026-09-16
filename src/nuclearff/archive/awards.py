"""League history archive: season awards and superlatives (issue #113, epic #116).

Scoped to what's honestly derivable from ``sleeper_standings`` alone —
``regular_season_rank`` and ``final_rank`` (``fetch-league --standings``,
issue #21) already carry everything these three superlatives need. No new
Sleeper fetching, and — unlike #110's original head-to-head split (#137) —
no dependency on ``LeagueConfig.playoff_week_start`` either:
``final_rank`` already resolves through the bracket, so "did this team win
it all despite a bad regular season" needs no week-level data at all.

**Deliberately excludes anything requiring a subjective judgment call this
project has no data to back** (e.g. "best trade," "most exciting week") —
if those are wanted later, they need a real, stated scoring rule from
outside this module, not a guessed heuristic invented here.

Manager identity is ``sleeper_standings.display_name``, the same
cross-season-identity posture every other multi-season aggregate in this
project accepts.
"""

from __future__ import annotations

import polars as pl

_SCHEMA = {
    "kind": pl.String,
    "season": pl.Int64,
    "manager": pl.String,
    "value": pl.Float64,
}

KIND_BEST_REGULAR_SEASON = "best_regular_season"
"""The most dominant #1 regular-season finish in league history, by fpts."""

KIND_CINDERELLA_RUN = "cinderella_run"
"""The champion who overcame the worst regular-season finish to win it all.

``value`` is the rank gap (``regular_season_rank - 1``, since every
champion's ``final_rank`` is 1 by definition) — bigger means a less likely
run to the title.
"""

KIND_BIGGEST_IMPROVEMENT = "biggest_improvement"
"""The single largest season-over-season fpts jump by the same manager.

``value`` is the fpts delta (current season minus the immediately prior
season for that same manager) — fpts rather than final_rank, since fpts is
always populated for a completed season while final_rank is null for a
non-bracket finisher.
"""


def _resolvable(standings: pl.DataFrame) -> pl.DataFrame:
    return standings.filter(pl.col("display_name").is_not_null())


def _best_regular_season(standings: pl.DataFrame) -> pl.DataFrame | None:
    leaders = standings.filter(pl.col("regular_season_rank") == 1)
    if leaders.height == 0:
        return None
    best = leaders.sort("fpts", descending=True).head(1)
    return best.select(
        kind=pl.lit(KIND_BEST_REGULAR_SEASON),
        season=pl.col("season"),
        manager=pl.col("display_name"),
        value=pl.col("fpts"),
    )


def _cinderella_run(standings: pl.DataFrame) -> pl.DataFrame | None:
    champions = standings.filter(pl.col("final_rank") == 1)
    if champions.height == 0:
        return None
    worst_seed = champions.sort("regular_season_rank", descending=True).head(1)
    return worst_seed.select(
        kind=pl.lit(KIND_CINDERELLA_RUN),
        season=pl.col("season"),
        manager=pl.col("display_name"),
        value=(pl.col("regular_season_rank") - 1).cast(pl.Float64),
    )


def _biggest_improvement(standings: pl.DataFrame) -> pl.DataFrame | None:
    prior = standings.select(
        "display_name",
        (pl.col("season") + 1).alias("season"),
        pl.col("fpts").alias("prior_fpts"),
    )
    joined = standings.join(prior, on=["display_name", "season"], how="inner")
    if joined.height == 0:
        return None

    joined = joined.with_columns((pl.col("fpts") - pl.col("prior_fpts")).alias("delta"))
    best = joined.sort("delta", descending=True).head(1)
    return best.select(
        kind=pl.lit(KIND_BIGGEST_IMPROVEMENT),
        season=pl.col("season"),
        manager=pl.col("display_name"),
        value=pl.col("delta"),
    )


def season_awards(standings: pl.DataFrame) -> pl.DataFrame:
    """Season-level superlatives: best regular season, Cinderella run, and biggest jump.

    Args:
        standings: ``sleeper_standings`` rows, spanning every season to
            consider — ``season``, ``display_name``, ``fpts``,
            ``regular_season_rank``, ``final_rank``.

    Returns:
        Up to three rows, one per :data:`KIND_BEST_REGULAR_SEASON`,
        :data:`KIND_CINDERELLA_RUN`, and :data:`KIND_BIGGEST_IMPROVEMENT` —
        ``season``, ``manager``, ``value`` (meaning depends on ``kind``; see
        each constant's own docstring). An award with no eligible season
        (e.g. no completed season has a resolved ``final_rank`` yet) is
        simply absent from the result rather than a row of nulls.
    """
    resolvable = _resolvable(standings)
    parts = [
        _best_regular_season(resolvable),
        _cinderella_run(resolvable),
        _biggest_improvement(resolvable),
    ]
    rows = [part for part in parts if part is not None]
    if not rows:
        return pl.DataFrame(schema=_SCHEMA)
    return pl.concat(rows).select(list(_SCHEMA.keys()))
