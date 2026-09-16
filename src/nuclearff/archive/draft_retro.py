"""League history archive: draft retrospectives (issue #111, epic #116).

Grades a historical pick against how the player *actually* performed that
season — deliberately distinct from the draft-companion epic's #96
(live post-pick grading against pre-draft projections). This needs no
projection model at all, just real final season output, already available
via :func:`nuclearff.sleeper.performance.season_actuals` (built for
``report season-performance``). No new Sleeper fetching.

Manager identity is ``sleeper_standings.display_name``, joined by
``(league_id, roster_id)`` — the same convention
:func:`nuclearff.sleeper.draft.draft_order_stats` already uses for this
exact table.

**Known confound, not resolved here:** ``sleeper_draft_picks.is_keeper``
marks a keeper pick, whose draft slot reflects a league rule (e.g. "keep
your pick's original round"), not a real assessment of that player's
expected value at the time — a keeper league's steal/bust results may be
skewed by this. Left to the caller to filter on ``is_keeper`` if their
league uses them; not silently excluded here since not every league does.
"""

from __future__ import annotations

import polars as pl

_SCHEMA = {
    "league_id": pl.String,
    "draft_id": pl.String,
    "season": pl.Int64,
    "player_id": pl.String,
    "player_name": pl.String,
    "position": pl.String,
    "manager": pl.String,
    "round": pl.Int64,
    "pick_no": pl.Int64,
    "season_points": pl.Float64,
    "expected_rank": pl.UInt32,
    "actual_rank": pl.UInt32,
    "rank_delta": pl.Int64,
}


def grade_historical_picks(
    draft_picks: pl.DataFrame, season_actuals: pl.DataFrame, standings: pl.DataFrame
) -> pl.DataFrame:
    """Grade every historical pick against the player's real season output.

    Args:
        draft_picks: ``sleeper_draft_picks`` rows, spanning every season to
            grade — ``league_id``, ``draft_id``, ``season``, ``pick_no``,
            ``round``, ``roster_id``, ``player_id``, ``position``,
            ``first_name``, ``last_name``.
        season_actuals: :func:`nuclearff.sleeper.performance.season_actuals`
            output, concatenated across every season to grade — needs
            ``season``, ``player_id``, ``actual_points`` (per-week rows;
            summed to a season total here).
        standings: ``sleeper_standings`` rows, for ``display_name``.

    Returns:
        One row per pick: ``league_id``, ``draft_id``, ``season``,
        ``player_id``, ``player_name``, ``position``, ``manager``,
        ``round``, ``pick_no``, ``season_points`` (``0.0`` for a drafted
        player who never accrued real points that season — a real, if
        extreme, outcome, not a data gap to exclude), ``expected_rank``
        (rank by ``pick_no`` ascending, within that draft — 1 is the 1st
        overall pick), ``actual_rank`` (rank by ``season_points``
        descending, within that draft — 1 is the season's top real
        scorer), and ``rank_delta`` (``expected_rank - actual_rank``:
        strongly positive is a steal — drafted late, produced like an
        early pick; strongly negative is a bust — drafted early, produced
        like a late pick).

        Ranks (and so ``rank_delta``) are computed within each
        ``(league_id, draft_id)`` separately, since draft pool size varies
        by season/league — a ``rank_delta`` isn't perfectly comparable
        across two drafts of very different size, only within a "per
        season" or "all-time across this league's own history" view where
        that size is roughly stable.

        Sort by ``rank_delta`` descending for steals, ascending for busts;
        filter to one ``season`` for a season view, or leave unfiltered for
        an all-time view — both are the same table.
    """
    if draft_picks.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    points = season_actuals.group_by(["season", "player_id"]).agg(
        pl.col("actual_points").sum().alias("season_points")
    )

    names = standings.select("league_id", "roster_id", "display_name")

    joined = (
        draft_picks.join(points, on=["season", "player_id"], how="left")
        .with_columns(pl.col("season_points").fill_null(0.0))
        .join(names, on=["league_id", "roster_id"], how="left")
        .rename({"display_name": "manager"})
        .with_columns(
            player_name=pl.concat_str(
                [pl.col("first_name"), pl.col("last_name")], separator=" "
            )
        )
    )

    ranked = joined.with_columns(
        expected_rank=pl.col("pick_no")
        .rank(method="ordinal")
        .over(["league_id", "draft_id"])
        .cast(pl.UInt32),
        actual_rank=pl.col("season_points")
        .rank(method="ordinal", descending=True)
        .over(["league_id", "draft_id"])
        .cast(pl.UInt32),
    ).with_columns(
        rank_delta=(
            pl.col("expected_rank").cast(pl.Int64)
            - pl.col("actual_rank").cast(pl.Int64)
        )
    )

    return ranked.select(list(_SCHEMA.keys())).sort("rank_delta", descending=True)
