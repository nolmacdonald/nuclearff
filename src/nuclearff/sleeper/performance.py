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
    names = dict(
        standings.filter(pl.col("league_id") == league_id)
        .select("roster_id", "display_name")
        .iter_rows()
    )

    filtered = matchups.filter(
        (pl.col("league_id") == league_id) & (pl.col("week") == week)
    )

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
                    "week": week,
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


def weekly_performance(
    actuals: pl.DataFrame,
    projections: pl.DataFrame,
    players: pl.DataFrame,
    scoring: ScoringSettings,
    *,
    starters_only: bool = True,
) -> pl.DataFrame:
    """Combine actual and projected points into one ranked over/underperformer table.

    Args:
        actuals: Output of :func:`weekly_actuals`.
        projections: ``sleeper_projections`` rows for the same
            ``(season, week)`` — needs ``season``, ``week``, ``player_id``,
            ``stats``.
        players: ``sleeper_players`` rows — needs ``player_id`` and,
            ideally, ``full_name``/``last_name``/``position``/``team``.
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
        .alias("projected_points")
    ).select("season", "week", "player_id", "projected_points")

    names = players.select(
        "player_id",
        pl.coalesce(pl.col("full_name"), pl.col("last_name")).alias("player_name"),
        "position",
        "team",
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
