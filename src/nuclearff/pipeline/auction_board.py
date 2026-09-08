"""Build a league-specific auction draft board: points -> VORP -> dollars.

This is the wiring layer. Every step below already exists and is tested on
its own; nothing here reimplements scoring, projection, replacement level, or
the dollar conversion. What this module owns is the *order*, and the handful
of joins between them:

1. **League settings** from Sleeper — scoring rules, roster shape, team count
   (:mod:`nuclearff.config.league`), plus the auction budget from the draft
   object (:func:`nuclearff.valuation.auction.budget_from_draft`).
2. **Realized points per player-season**, scored under this league's own
   rules rather than a generic PPR formula
   (:class:`nuclearff.scoring.engine.ScoringEngine`).
3. **A value estimate**, as a recency-weighted average of those realized
   seasons (:func:`nuclearff.projection.blend.recency_weighted_rate`).
4. **Replacement level and VORP per position**
   (:mod:`nuclearff.valuation.vorp`).
5. **Auction dollars** (:func:`nuclearff.valuation.auction.auction_values`).
6. **Market comparison** — FantasyPros consensus, joined on ``gsis_id``
   (:mod:`nuclearff.nflverse.rankings`).

The value estimate is a modeling choice, not a fact
----------------------------------------------------
Step 3 is a **recency-weighted average of what players actually did**, not a
forward projection of what they will do. It carries no injury news, no
depth-chart change, no rookie who has never played (a player with no prior
season has no value estimate here and will not appear on the board at all).
Anyone reading the output needs that caveat stated plainly — see
:func:`nuclearff.report.build.write_report`, which puts it in the report's
methodology section.

Keeper adjustment is deliberately out of scope here
---------------------------------------------------
This produces the **no-keeper baseline** board. Keeper inflation is
implemented (:func:`nuclearff.valuation.auction.keeper_adjusted_values`) but
not wired in, because keeper *costs* have no source: Sleeper exposes no
keeper price, this league's draft description is empty, and there are no
historical auction prices to escalate from (2026 is the league's first
auction — every prior season was a snake draft). See
``brain/notes/sleeper-auction-keeper-fields.md``. Supply a
``{player_id: cost}`` mapping to that function directly when the league's
keeper rules are settled.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import polars as pl

from nuclearff.config.league import LeagueConfig, league_config_from_sleeper
from nuclearff.config.models import RecencyWeights
from nuclearff.nflverse.loader import load_ff_playerids
from nuclearff.nflverse.rankings import (
    attach_player_ids,
    consensus_adp,
    load_fantasypros_ecr,
)
from nuclearff.nflverse.stats import load_seasonal_skill_stats
from nuclearff.projection.blend import recency_weighted_rate
from nuclearff.scoring.engine import ScoringEngine
from nuclearff.sleeper.client import SleeperClient
from nuclearff.valuation.auction import auction_values, budget_from_draft
from nuclearff.valuation.vorp import replacement_points

logger = logging.getLogger(__name__)

BOARD_POSITIONS = ("QB", "RB", "WR", "TE")
"""Positions valued on the board, in report order."""

PROJ_COLUMN = "value_estimate"
"""Name of the recency-weighted points column the board ranks on."""


def score_seasons(stats: pl.DataFrame, scoring_engine: ScoringEngine) -> pl.DataFrame:
    """Score every player-season under this league's rules.

    Args:
        stats: Seasonal stat rows, e.g. from
            :func:`nuclearff.nflverse.stats.load_seasonal_skill_stats`.
        scoring_engine: Engine built from this league's scoring settings.

    Returns:
        ``stats`` with a ``fantasy_points`` column added.
    """
    scored = scoring_engine.score_frame(stats)
    unscored = scoring_engine.unscored_keys()
    if unscored:
        logger.info(
            "Scoring engine ignored %d league scoring key(s) with no nflverse "
            "stat mapping (kicker/defense/IDP keys are expected here): %s",
            len(unscored),
            sorted(unscored)[:10],
        )
    return scored


IDENTITY_COLUMNS = (
    "player_display_name",
    "position",
    "recent_team",
    "headshot_url",
    "games",
)
"""Per-player columns carried onto the board from the most recent season."""


def project_points(
    scored: pl.DataFrame,
    as_of_season: int,
    weights: RecencyWeights | None = None,
    *,
    points_column: str = "fantasy_points",
    proj_column: str = PROJ_COLUMN,
    identity_columns: Sequence[str] = IDENTITY_COLUMNS,
) -> pl.DataFrame:
    """Reduce multi-season scored points to one recency-weighted estimate per player.

    :func:`~nuclearff.projection.blend.recency_weighted_rate` deliberately
    returns only ``player_id`` and the blended value — it is a rate
    calculator, not a table builder. This re-attaches the identity columns a
    draft board needs (name, position, team, headshot, games) by taking each
    player's **most recent** season before ``as_of_season``, so a player who
    changed teams is shown on the team he finished on, and adds one
    ``fantasy_points_<season>`` column per input season for the CSV.

    Args:
        scored: Output of :func:`score_seasons` — one row per player-season.
        as_of_season: The season being drafted for. Only seasons *before*
            this one are used.
        weights: Season weights, newest first. Defaults to
            :class:`~nuclearff.config.models.RecencyWeights`' own default
            (0.5 / 0.3 / 0.2 over the three prior seasons).
        points_column: Column to weight.
        proj_column: Name of the value-estimate column to write.
        identity_columns: Per-player columns to carry through, when present.

    Returns:
        One row per player with ``proj_column``, the identity columns, a
        ``points_per_game`` column, and one ``fantasy_points_<season>``
        column per season in ``scored``.
    """
    weights = weights or RecencyWeights()
    blended = recency_weighted_rate(scored, points_column, as_of_season, weights)
    blended = blended.rename({f"{points_column}_blended": proj_column})

    prior = scored.filter(pl.col("season") < as_of_season)

    present = [c for c in identity_columns if c in prior.columns]
    identity = (
        prior.sort("season", descending=True)
        .unique(subset=["player_id"], keep="first")
        .select(["player_id", *present])
    )

    per_season = prior.select(["player_id", "season", points_column]).pivot(
        on="season", index="player_id", values=points_column
    )
    per_season = per_season.rename(
        {
            column: f"{points_column}_{column}"
            for column in per_season.columns
            if column != "player_id"
        }
    )

    result = blended.join(identity, on="player_id", how="left").join(
        per_season, on="player_id", how="left"
    )

    if "games" in result.columns:
        result = result.with_columns(
            pl.when(pl.col("games") > 0)
            .then(pl.col(proj_column) / pl.col("games"))
            .otherwise(None)
            .alias("points_per_game")
        )
    return result


def add_vorp_all_positions(
    projections: pl.DataFrame,
    cfg: LeagueConfig,
    *,
    positions: Sequence[str] = BOARD_POSITIONS,
    baseline: str = "vols",
    proj_column: str = PROJ_COLUMN,
) -> pl.DataFrame:
    """Add ``replacement_value_position`` and ``vorp`` for every board position.

    Computes a replacement level once per position (each one driven by this
    league's own roster shape via
    :meth:`~nuclearff.config.league.LeagueConfig.replacement_rank`) and
    subtracts it from each player's value estimate. Players at a position
    outside ``positions`` get nulls rather than being dropped.

    Args:
        projections: One row per player with ``position`` and ``proj_column``.
        cfg: The league configuration driving replacement ranks.
        positions: Positions to value.
        baseline: ``"vols"`` or ``"vorp"``.
        proj_column: The value-estimate column to measure against.

    Returns:
        ``projections`` with ``replacement_value_position`` and ``vorp``
        columns added.
    """
    replacement: dict[str, float] = {}
    for position in positions:
        pool = projections.filter(pl.col("position") == position)
        if pool.height == 0:
            logger.warning(
                "add_vorp_all_positions: no players at position %r; skipping.",
                position,
            )
            continue
        replacement[position] = replacement_points(
            projections,
            cfg,
            position=position,
            baseline=baseline,
            proj_column=proj_column,
        )
        logger.info(
            "Replacement level (%s, %s): rank %d -> %.1f points",
            position,
            baseline,
            cfg.replacement_rank(position, baseline),
            replacement[position],
        )

    return projections.with_columns(
        pl.col("position")
        .replace_strict(replacement, default=None, return_dtype=pl.Float64)
        .alias("replacement_value_position")
    ).with_columns(
        (pl.col(proj_column) - pl.col("replacement_value_position")).alias("vorp")
    )


def build_auction_board(
    league_id: str,
    *,
    seasons: Sequence[int],
    as_of_season: int,
    client: SleeperClient,
    weights: RecencyWeights | None = None,
    baseline: str = "vols",
    positions: Sequence[str] = BOARD_POSITIONS,
) -> tuple[pl.DataFrame, dict]:
    """Build the full no-keeper auction board for one Sleeper league.

    Args:
        league_id: Sleeper league identifier.
        seasons: Historical seasons to pull stats for, oldest first.
        as_of_season: The season being drafted for.
        client: A configured Sleeper client.
        weights: Recency weights for the value estimate.
        baseline: ``"vols"`` or ``"vorp"`` replacement baseline.
        positions: Positions to value.

    Returns:
        A ``(board, context)`` pair. ``board`` is one row per valued player,
        ranked by auction value; ``context`` carries the league facts a
        report needs (name, teams, budget, roster shape, scoring type,
        replacement ranks) so the report never hardcodes them.

    Raises:
        ConfigError: If the league's draft is not an auction or exposes no
            budget (see :func:`~nuclearff.valuation.auction.budget_from_draft`).
    """
    league_json = client.get_league(league_id)
    cfg = league_config_from_sleeper(league_json)
    draft = client.get_draft(str(cfg.draft_id))
    budget = budget_from_draft(draft)
    roster_spots = len(league_json.get("roster_positions") or [])

    logger.info(
        "Building auction board for %r (%d): %d teams, $%d each, %d roster spots",
        cfg.name,
        cfg.season,
        cfg.num_teams,
        budget,
        roster_spots,
    )

    stats = load_seasonal_skill_stats(list(seasons))
    scored = score_seasons(stats, ScoringEngine(cfg.scoring))
    projections = project_points(scored, as_of_season, weights)

    valued = add_vorp_all_positions(
        projections, cfg, positions=positions, baseline=baseline
    )
    valued = valued.filter(pl.col("vorp").is_not_null())

    priced = auction_values(
        valued,
        teams=cfg.num_teams,
        budget_per_team=budget,
        roster_spots=roster_spots,
    )

    market = consensus_adp(
        attach_player_ids(load_fantasypros_ecr(), load_ff_playerids()),
        positions=positions,
    )
    board = priced.join(
        market.select(
            ["gsis_id", "adp_overall", "adp_position_rank", "source", "as_of_date"]
        ).rename({"source": "adp_source", "as_of_date": "adp_as_of_date"}),
        left_on="player_id",
        right_on="gsis_id",
        how="left",
    )

    board = board.sort("auction_value", descending=True).with_columns(
        pl.col("auction_value")
        .rank("ordinal", descending=True)
        .cast(pl.Int64)
        .alias("rank_overall"),
        pl.col("auction_value")
        .rank("ordinal", descending=True)
        .over("position")
        .cast(pl.Int64)
        .alias("rank_position"),
    )

    context = {
        "league_id": cfg.league_id,
        "league_name": cfg.name,
        "season": cfg.season,
        "num_teams": cfg.num_teams,
        "budget_per_team": budget,
        "roster_spots": roster_spots,
        "roster_positions": league_json.get("roster_positions"),
        "scoring_type": (draft.get("metadata") or {}).get("scoring_type"),
        "max_keepers": (league_json.get("settings") or {}).get("max_keepers"),
        "baseline": baseline,
        "seasons_used": list(seasons),
        "as_of_season": as_of_season,
        "replacement_ranks": {
            position: cfg.replacement_rank(position, baseline) for position in positions
        },
        "players_valued": board.height,
    }
    return board, context
