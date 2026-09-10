"""Join actual vs. projected weekly fantasy points into an over/underperformer table.

Actual points come straight from ``sleeper_matchups.players_points``
(:mod:`nuclearff.sleeper.matchups`) — Sleeper's own per-player fantasy
points, already computed under this league's exact scoring rules, the same
"already exact, no rescoring needed" posture this project takes for
``sleeper_standings``' win/loss totals. Projected points come from
``sleeper_projections`` (:mod:`nuclearff.sleeper.projections`), rescored
under the league's own :class:`~nuclearff.config.league.ScoringSettings`
via :func:`~nuclearff.sleeper.projections.score_projection` rather than
trusting Sleeper's generic ``pts_ppr``, so both sides of the comparison are
apples-to-apples: this league's real scoring rules, not a generic format.

Only players with **both** a real result and a real Sleeper projection are
included (an inner join) — a player Sleeper didn't project at all gets no
row here rather than a misleading "beat a 0-point projection by their whole
total," the same "never guess, never silently include" posture the rest of
this project's identity-resolution code takes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import polars as pl

from nuclearff.config.league import ScoringSettings
from nuclearff.sleeper.projections import score_projection

logger = logging.getLogger(__name__)

_ACTUALS_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "roster_id": pl.Int64,
    "manager": pl.String,
    "player_id": pl.String,
    "is_starter": pl.Boolean,
    "actual_points": pl.Float64,
}

_PERFORMANCE_SCHEMA = {
    **_ACTUALS_SCHEMA,
    "player_name": pl.String,
    "position": pl.String,
    "team": pl.String,
    "projected_points": pl.Float64,
    "delta": pl.Float64,
}

_SEASON_SCHEMA = {
    "player_id": pl.String,
    "player_name": pl.String,
    "position": pl.String,
    "team": pl.String,
    "manager": pl.String,
    "games": pl.UInt32,
    "avg_projected_points": pl.Float64,
    "avg_actual_points": pl.Float64,
    "avg_delta": pl.Float64,
}


def _explode_players_points(
    matchups: pl.DataFrame, standings: pl.DataFrame, league_id: str
) -> pl.DataFrame:
    """Explode every week of one league's ``sleeper_matchups`` into per-player rows.

    Shared by :func:`weekly_actuals` and :func:`season_actuals` — the only
    difference between "one week" and "a whole season" is which rows the
    caller keeps afterward.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows — needs ``league_id``,
            ``roster_id``, ``display_name``.
        league_id: The specific season's league id to restrict to.

    Returns:
        One row per (roster, player, week) rostered that season: matches
        :data:`_ACTUALS_SCHEMA`. A roster with no resolvable manager name
        contributes no rows.
    """
    names = dict(
        standings.filter(pl.col("league_id") == league_id)
        .select("roster_id", "display_name")
        .iter_rows()
    )

    filtered = matchups.filter(pl.col("league_id") == league_id)

    rows: list[dict[str, Any]] = []
    for row in filtered.iter_rows(named=True):
        manager = names.get(row["roster_id"])
        if not manager:
            continue
        starters = set(json.loads(row["starters"] or "[]"))
        players_points: dict[str, Any] = json.loads(row["players_points"] or "{}")
        for player_id, points in players_points.items():
            rows.append(
                {
                    "league_id": league_id,
                    "season": row["season"],
                    "week": row["week"],
                    "roster_id": row["roster_id"],
                    "manager": manager,
                    "player_id": str(player_id),
                    "is_starter": player_id in starters,
                    "actual_points": float(points) if points is not None else 0.0,
                }
            )

    if not rows:
        return pl.DataFrame(schema=_ACTUALS_SCHEMA)
    return pl.DataFrame(rows, schema=_ACTUALS_SCHEMA)


def weekly_actuals(
    matchups: pl.DataFrame, standings: pl.DataFrame, league_id: str, week: int
) -> pl.DataFrame:
    """Explode one week's ``sleeper_matchups`` rows into one row per rostered player.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows — needs ``league_id``,
            ``roster_id``, ``display_name``.
        league_id: The specific season's league id to restrict to.
        week: The week to restrict to.

    Returns:
        One row per (roster, player) rostered that week: ``league_id``,
        ``season``, ``week``, ``roster_id``, ``manager``, ``player_id``,
        ``is_starter``, ``actual_points``. A roster with no resolvable
        manager name contributes no rows.
    """
    return _explode_players_points(matchups, standings, league_id).filter(
        pl.col("week") == week
    )


def season_actuals(
    matchups: pl.DataFrame, standings: pl.DataFrame, league_id: str
) -> pl.DataFrame:
    """Explode every week of ``sleeper_matchups`` for one league into per-player rows.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows — needs ``league_id``,
            ``roster_id``, ``display_name``.
        league_id: The specific season's league id to restrict to.

    Returns:
        One row per (roster, player, week) rostered that season, matching
        :func:`weekly_actuals`'s schema but spanning every week present in
        ``matchups`` for ``league_id`` rather than one.
    """
    return _explode_players_points(matchups, standings, league_id)


def weekly_performance(
    actuals: pl.DataFrame,
    projections: pl.DataFrame,
    players: pl.DataFrame,
    scoring: ScoringSettings,
    *,
    starters_only: bool = True,
) -> pl.DataFrame:
    """Combine actual and projected points into one ranked over/underperformer table.

    Despite the name, this isn't inherently single-week: the join is keyed
    on ``(season, week, player_id)``, so passing :func:`season_actuals`'s
    multi-week output (alongside a ``projections`` table covering every one
    of those weeks) computes one row per player *per week* across a whole
    season just as well — :func:`season_summary` is what collapses that
    into one row per player.

    Args:
        actuals: Output of :func:`weekly_actuals` or :func:`season_actuals`.
        projections: ``sleeper_projections`` rows covering every
            ``(season, week)`` present in ``actuals`` — needs ``season``,
            ``week``, ``player_id``, ``stats``, ``team``. ``team`` is the
            source of the output's own ``team`` column, deliberately not
            ``players`` — see the inline comment above where it's joined.
        players: ``sleeper_players`` rows — needs ``player_id`` and,
            ideally, ``full_name``/``last_name``/``position``.
        scoring: The league's scoring rules, used to rescore each
            projection (see the module docstring for why).
        starters_only: When ``True`` (default), only a roster's actual
            starters are included — the players whose performance actually
            affected a manager's score that week. When ``False``, every
            rostered player (bench included) is considered.

    Returns:
        One row per player with both a real result and a real projection,
        sorted by ``delta`` (``actual_points - projected_points``)
        descending — biggest overachievers first, biggest underperformers
        last. Empty when no player has both.
    """
    if starters_only:
        actuals = actuals.filter(pl.col("is_starter"))
    if actuals.height == 0:
        return pl.DataFrame(schema=_PERFORMANCE_SCHEMA)

    if projections.height == 0:
        return pl.DataFrame(schema=_PERFORMANCE_SCHEMA)

    scored = projections.with_columns(
        pl.col("stats")
        .map_elements(
            lambda raw: score_projection(json.loads(raw or "{}"), scoring),
            return_dtype=pl.Float64,
        )
        .alias("projected_points"),
        # `sleeper_projections.team` is the real team Sleeper had on file
        # for this exact (season, week) -- not `sleeper_players.team`,
        # which is a single, current snapshot (today's roster). A player
        # traded mid-season, or since departed the league entirely as a
        # free agent, needs their *that-week* team here, not their team as
        # of whenever this table happened to be fetched. Real bug this
        # fixed: the season report rendering "FA" for Nick Chubb and Zach
        # Ertz for every 2025 week, including ones where they had a real
        # NFL team, because it was joining `sleeper_players`' current
        # (post-2025) snapshot instead of that week's own projection.
        pl.col("team").fill_null("FA").alias("team"),
    ).select("season", "week", "player_id", "projected_points", "team")

    names = players.select(
        "player_id",
        pl.coalesce(pl.col("full_name"), pl.col("last_name")).alias("player_name"),
        pl.col("position").fill_null("--"),
    )

    merged = (
        actuals.join(scored, on=["season", "week", "player_id"], how="inner")
        .join(names, on="player_id", how="left")
        .with_columns(
            pl.coalesce(pl.col("player_name"), pl.col("player_id")).alias(
                "player_name"
            ),
            (pl.col("actual_points") - pl.col("projected_points")).alias("delta"),
        )
        .sort("delta", descending=True)
    )
    return merged.select(list(_PERFORMANCE_SCHEMA))


def season_summary(performance: pl.DataFrame, *, min_games: int = 3) -> pl.DataFrame:
    """Collapse per-(player, week) performance rows into one row per player.

    Unlike the single-week report (see
    :func:`nuclearff.report.performance._underperformers`), a completed
    season has no "hasn't played yet" ambiguity to worry about — every week
    present already happened, so an exact-``0.0`` actual is a real result
    (a bye, a benching, a real dud game), not excluded here.

    Args:
        performance: e.g. :func:`weekly_performance`'s output run against a
            full season's worth of actuals (:func:`season_actuals`) and
            projections.
        min_games: A player needs at least this many weeks with both a
            real result and a real projection to appear at all — without
            this, a single huge or tiny delta from a player who barely
            played would dominate a season ranking meant to reflect
            sustained performance, not a small sample.

    Returns:
        One row per player: ``player_id``, ``player_name``, ``position``,
        ``team``, ``manager`` (as of that player's most recent counted
        week — handles an in-season trade), ``games`` (weeks counted),
        ``avg_projected_points``, ``avg_actual_points``, ``avg_delta``,
        sorted by ``avg_delta`` descending. Empty when no player reaches
        ``min_games``.
    """
    if performance.height == 0:
        return pl.DataFrame(schema=_SEASON_SCHEMA)

    summary = (
        performance.sort("week")
        .group_by("player_id")
        .agg(
            pl.col("player_name").last(),
            pl.col("position").last(),
            pl.col("team").last(),
            pl.col("manager").last(),
            pl.len().alias("games"),
            pl.col("projected_points").mean().alias("avg_projected_points"),
            pl.col("actual_points").mean().alias("avg_actual_points"),
            pl.col("delta").mean().alias("avg_delta"),
        )
        .filter(pl.col("games") >= min_games)
        .sort("avg_delta", descending=True)
    )
    return summary.select(list(_SEASON_SCHEMA))
