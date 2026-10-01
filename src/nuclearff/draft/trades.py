"""Draft-pick trade fairness grade (issue #104).

:func:`grade_pick_trade` values each traded pick as the VORP of the player
projected to be on the board at that overall pick number, sums each side, and
labels the difference. It reports pure expected-value differential only: team
needs are not considered, so a trade that is poor on value can still suit a
roster, and this is not a trade recommendation.

Valuation reuses :func:`nuclearff.valuation.vorp.vorp` and the rank-based
approximation :func:`nuclearff.valuation.vorp.vona` uses: players are assumed to
come off the board in descending-VORP order, so overall pick ``n`` yields the
``n``-th ranked player by VORP across QB/RB/WR/TE. That is a chalk-draft
assumption, not a survival model: it ignores positional runs and drafters who
reach.

A pick is never valued below ``0.0``. A player below replacement level has
negative VORP, but a pick can always be spent on a replacement-level player.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

import polars as pl

from nuclearff.config.league import LeagueConfig
from nuclearff.valuation.vorp import _require_columns, vorp

logger = logging.getLogger(__name__)

FAIR_TOLERANCE = 0.10
"""Default fair band: a net difference within 10% of the larger side is fair."""

_VALUE_POSITIONS = ("QB", "RB", "WR", "TE")

Label = Literal["favorable", "fair", "unfavorable"]


@dataclass(frozen=True, slots=True)
class PickTradeGrade:
    """Grade of a pick-for-pick trade, from the giving side's point of view.

    Attributes:
        given: Estimated value of each pick given up, keyed by overall pick
            number.
        received: Estimated value of each pick received, keyed the same way.
        value_given: Sum of ``given``.
        value_received: Sum of ``received``.
        differential: ``value_received - value_given``; positive favors the
            side giving up ``picks_given``.
        label: ``"favorable"``, ``"fair"`` or ``"unfavorable"`` for that side.
        tolerance: The fair band used, as a fraction of the larger side's value.
    """

    given: dict[int, float]
    received: dict[int, float]
    value_given: float
    value_received: float
    differential: float
    label: Label
    tolerance: float


def _board_values(
    projections: pl.DataFrame,
    cfg: LeagueConfig,
    drafted_player_ids: Collection[str],
    baseline: str,
    proj_column: str,
) -> list[float]:
    """VORP of every undrafted player, best first, pooled across positions."""
    _require_columns(
        projections, ("player_id", "position", proj_column), "grade_pick_trade"
    )
    present = projections["position"].unique().to_list()
    frames = [
        vorp(
            projections,
            cfg,
            position=position,
            baseline=baseline,
            proj_column=proj_column,
        ).filter(pl.col("position") == position)
        for position in _VALUE_POSITIONS
        if position in present
    ]
    if not frames:
        raise ValueError(
            f"grade_pick_trade: `projections` has no players at any of "
            f"{_VALUE_POSITIONS}; cannot value a pick."
        )
    board = (
        pl.concat(frames)
        .filter(
            pl.col("vorp").is_not_null()
            & ~pl.col("player_id").is_in(list(drafted_player_ids))
        )
        .sort(["vorp", "player_id"], descending=[True, False])
    )
    return board["vorp"].to_list()


def _pick_values(
    picks: Sequence[int], board: list[float], offset: int
) -> dict[int, float]:
    values: dict[int, float] = {}
    for pick_no in picks:
        rank = pick_no - offset
        if rank < 1:
            raise ValueError(
                f"grade_pick_trade: pick {pick_no} has already been made "
                f"({offset} player(s) are off the board)."
            )
        if rank > len(board):
            logger.warning(
                "grade_pick_trade: pick %d needs board rank %d, but only %d "
                "valued player(s) remain - using the worst-ranked instead.",
                pick_no,
                rank,
                len(board),
            )
            rank = len(board)
        values[pick_no] = max(float(board[rank - 1]), 0.0)
    return values


def grade_pick_trade(
    picks_given: Sequence[int],
    picks_received: Sequence[int],
    projections: pl.DataFrame,
    cfg: LeagueConfig,
    *,
    drafted_player_ids: Collection[str] = (),
    baseline: str = "vols",
    proj_column: str = "proj_points",
    tolerance: float = FAIR_TOLERANCE,
) -> PickTradeGrade:
    """Grade a pick-for-pick trade by expected-value differential.

    Args:
        picks_given: Overall pick numbers the grading side gives up.
        picks_received: Overall pick numbers the grading side receives.
        projections: Projected-points table with at least ``player_id``,
            ``position`` and ``proj_column``.
        cfg: The league configuration driving the replacement level, passed to
            :func:`nuclearff.valuation.vorp.vorp`.
        drafted_player_ids: Players already off the board. Replacement level is
            still computed from the full ``projections``; drafted players only
            leave the board, so pick ``n`` is then the
            ``n - len(drafted_player_ids)``-th best remaining player.
        baseline: ``"vols"`` or ``"vorp"``, passed to ``vorp``.
        proj_column: Name of the projected-points column.
        tolerance: Fair band as a fraction of the larger side's total value.

    Returns:
        A :class:`PickTradeGrade`. A net difference within
        ``tolerance * max(value_given, value_received)`` is ``"fair"`` (two
        zero-value sides are fair); beyond it the label is ``"favorable"`` or
        ``"unfavorable"`` for the side giving up ``picks_given``.

    Raises:
        ValueError: If either side is empty, a pick number is below 1, a pick
            is on both sides, a pick has already been made, ``tolerance`` is
            negative, or ``projections`` is unusable (see
            :func:`nuclearff.valuation.vorp.vorp`).
    """
    if not picks_given or not picks_received:
        raise ValueError("grade_pick_trade: both sides of a trade need a pick.")
    if min((*picks_given, *picks_received)) < 1:
        raise ValueError("grade_pick_trade: pick numbers must be 1 or greater.")
    if set(picks_given) & set(picks_received):
        raise ValueError("grade_pick_trade: a pick cannot be on both sides.")
    if tolerance < 0:
        raise ValueError("grade_pick_trade: tolerance cannot be negative.")

    board = _board_values(projections, cfg, drafted_player_ids, baseline, proj_column)
    if not board:
        raise ValueError("grade_pick_trade: no undrafted players left to value.")
    offset = len(set(drafted_player_ids))

    given = _pick_values(picks_given, board, offset)
    received = _pick_values(picks_received, board, offset)
    value_given = sum(given.values())
    value_received = sum(received.values())
    differential = value_received - value_given

    band = tolerance * max(value_given, value_received)
    label: Label
    if abs(differential) <= band:
        label = "fair"
    elif differential > 0:
        label = "favorable"
    else:
        label = "unfavorable"
    return PickTradeGrade(
        given=given,
        received=received,
        value_given=value_given,
        value_received=value_received,
        differential=differential,
        label=label,
        tolerance=tolerance,
    )
