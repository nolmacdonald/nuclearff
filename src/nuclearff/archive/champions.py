"""League history archive: season-level facts (champions, runners-up, records).

Built for issue #108 (league-history epic #116). ``sleeper_standings``
(written by :func:`nuclearff.sleeper.standings.fetch_and_write_standings`,
issue #21) already carries ``final_rank`` and ``regular_season_rank`` per
roster per season, resolved from the real winners bracket at capture time —
nothing has ever read it back out as a "who won this season" table until
now. This module does that read/aggregate only; no new Sleeper fetching.

Every other issue in this epic (#109-#113) joins against
:func:`championship_history`'s output as its shared foundation, the same
role :func:`nuclearff.sleeper.trades.load_trades` plays for the
trade-network epic (#40).
"""

from __future__ import annotations

import polars as pl

_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "champion_roster_id": pl.Int64,
    "champion_display_name": pl.String,
    "runner_up_roster_id": pl.Int64,
    "runner_up_display_name": pl.String,
    "regular_season_leader_roster_id": pl.Int64,
    "regular_season_leader_display_name": pl.String,
}


def _placement(
    standings: pl.DataFrame,
    rank_column: str,
    rank_value: int,
    roster_alias: str,
    name_alias: str,
) -> pl.DataFrame:
    """One row per ``league_id`` at a given rank, or none if unresolved.

    Sorting by ``roster_id`` before deduplicating makes the pick
    deterministic in the (unexpected) case of two rosters tied at the same
    rank for one league — real data should never produce this, but a
    reproducible pick beats an arbitrary one if it ever does.
    """
    return (
        standings.filter(pl.col(rank_column) == rank_value)
        .sort("roster_id")
        .unique(subset=["league_id"], keep="first")
        .select(
            "league_id",
            pl.col("roster_id").alias(roster_alias),
            pl.col("display_name").alias(name_alias),
        )
    )


def championship_history(standings: pl.DataFrame) -> pl.DataFrame:
    """One row per season: champion, runner-up, and regular-season leader.

    Args:
        standings: :data:`nuclearff.sleeper.standings.STANDINGS_TABLE_NAME`
            rows (or an equivalent frame) — one row per roster per season,
            with ``final_rank``, ``regular_season_rank``, and
            ``display_name`` already resolved at capture time.

    Returns:
        One row per distinct ``league_id`` in ``standings`` (Sleeper mints a
        new ``league_id`` each season, so this is already season-grain),
        sorted by season: ``league_id``, ``season``,
        ``champion_roster_id``/``champion_display_name``,
        ``runner_up_roster_id``/``runner_up_display_name``,
        ``regular_season_leader_roster_id``/
        ``regular_season_leader_display_name``. A season whose bracket never
        resolved a placement (``final_rank`` all ``NULL`` — an in-progress
        or bracket-less season) still gets a row, with the champion/runner-up
        columns left ``NULL`` rather than omitted or guessed.
        ``regular_season_leader`` is expected to always resolve, since
        :func:`nuclearff.sleeper.standings.standings_rows` assigns
        ``regular_season_rank`` to every roster.

        Co-owned rosters (issue #24) resolve to the primary owner's
        ``display_name`` only, the same simplification the trade-network
        epic (#40) already accepted.
    """
    seasons = (
        standings.select("league_id", "season")
        .sort("league_id")
        .unique(subset=["league_id"], keep="first")
    )

    champions = _placement(
        standings, "final_rank", 1, "champion_roster_id", "champion_display_name"
    )
    runners_up = _placement(
        standings, "final_rank", 2, "runner_up_roster_id", "runner_up_display_name"
    )
    leaders = _placement(
        standings,
        "regular_season_rank",
        1,
        "regular_season_leader_roster_id",
        "regular_season_leader_display_name",
    )

    history = (
        seasons.join(champions, on="league_id", how="left")
        .join(runners_up, on="league_id", how="left")
        .join(leaders, on="league_id", how="left")
        .sort("season")
    )
    return history.select(list(_SCHEMA.keys()))
