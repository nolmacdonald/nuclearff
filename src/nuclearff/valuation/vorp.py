"""Value-based drafting: VORP, VOLS, and VONA over a projected-points table.

Per the technical plan's "A.5 Value-based drafting (VBD): VORP, VOLS, VONA",
a projected point total is not, by itself, a draft-actionable number — it has
to be measured against some replacement level before it says anything about
*where* to draft a player:

- **VOLS** (Value Over Last Starter): points above the worst weekly starter
  at the position across the league. The right default for a set-lineup
  redraft league like this project's own (see
  :meth:`nuclearff.config.league.LeagueConfig.replacement_rank`).
- **VORP** (Value Over Replacement Player): points above a freely-available
  waiver-level player. Goes deeper than VOLS, emphasizing bench/depth value.
- **VONA** (Value Over Next Available): points above the best player likely
  to survive to the drafter's *next* pick — a live-draft, pick-flow metric
  rather than a season-long positional baseline.

This module does not reimplement the league-specific replacement-*rank* math
(how many WRs a 10-team, 2-WR/3-FLEX league actually starts) — that already
lives on :class:`~nuclearff.config.league.LeagueConfig` and is verified
there. This module only consumes that rank and applies it to a real
projected-points table, converting "WR33 by rank" into "N points, and every
other WR's value relative to that."
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import polars as pl

from nuclearff.config.league import LeagueConfig

logger = logging.getLogger(__name__)


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing a column ``fn_name`` needs.

    Args:
        df: The DataFrame passed to ``fn_name``.
        required: Column names ``fn_name`` depends on.
        fn_name: Name of the calling function, included in the error message.

    Raises:
        ValueError: If any column in ``required`` is absent from ``df``.
    """
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{fn_name}: input DataFrame is missing expected column(s) "
            f"{missing!r} (got {df.columns!r})."
        )


def replacement_points(
    projections: pl.DataFrame,
    cfg: LeagueConfig,
    *,
    position: str = "WR",
    baseline: str = "vols",
    proj_column: str = "proj_points",
    flex_rate: float | None = None,
    superflex_rate: float | None = None,
    bench_fraction: float | None = None,
) -> float:
    """Return the projected points of the replacement-level player at ``position``.

    Filters ``projections`` to ``position``, sorts descending by
    ``proj_column``, looks up the replacement rank from
    :meth:`~nuclearff.config.league.LeagueConfig.replacement_rank`, and
    returns the ``proj_column`` value of the player at that rank (1-indexed
    — rank ``N`` means the Nth-best projected player at ``position``).

    A real player pool can be thinner than the replacement rank calls for
    (a synthetic test fixture, or a shallow late-season waiver wire). Rather
    than raising or indexing out of bounds, this returns the value of the
    worst-ranked player actually available and logs a warning — the caller's
    pool is too shallow for a fully meaningful replacement level, but a
    degraded answer is more useful than a crash.

    Args:
        projections: Projected-points table with at least ``player_id``,
            ``position``, and ``proj_column``.
        cfg: The league configuration driving the replacement-rank math.
        position: The position to compute a replacement level for, e.g.
            ``"QB"``, ``"RB"``, ``"WR"``, or ``"TE"``. Passed through to
            :meth:`LeagueConfig.replacement_rank`.
        baseline: ``"vols"`` or ``"vorp"``, passed through to
            :meth:`LeagueConfig.replacement_rank`.
        proj_column: Name of the projected-points column to rank and read.
        flex_rate: Assumed fraction of FLEX slots filled by ``position``,
            passed through to :meth:`LeagueConfig.replacement_rank`. Defaults
            to that method's own per-position default when omitted.
        superflex_rate: Assumed fraction of SUPER_FLEX slots filled by
            ``position``, passed through to
            :meth:`LeagueConfig.replacement_rank`.
        bench_fraction: Assumed fraction of total league bench slots stashed
            at ``position``, used only for ``baseline="vorp"``, passed
            through to :meth:`LeagueConfig.replacement_rank`.

    Returns:
        The ``proj_column`` value of the replacement-level player.

    Raises:
        ValueError: If ``projections`` is missing a required column, has no
            rows at ``position``, or the selected replacement-rank player's
            ``proj_column`` value is null. Also raised by
            :meth:`LeagueConfig.replacement_rank` itself for an unsupported
            ``baseline``.
    """
    _require_columns(
        projections, ("player_id", "position", proj_column), "replacement_points"
    )

    pool = projections.filter(pl.col("position") == position).sort(
        proj_column, descending=True, nulls_last=True
    )
    n_available = pool.height
    if n_available == 0:
        raise ValueError(
            f"replacement_points: no players found in `projections` at "
            f"position {position!r}; cannot compute a replacement level from "
            f"an empty pool."
        )

    rank = cfg.replacement_rank(
        position, baseline, flex_rate, superflex_rate, bench_fraction
    )
    rank = max(rank, 1)

    if rank > n_available:
        logger.warning(
            "replacement_points: replacement rank for %r (baseline=%r) is %d, "
            "but only %d player(s) at that position are present in "
            "`projections` - using the worst-ranked player available instead. "
            "This player pool is too shallow for a fully meaningful "
            "replacement level.",
            position,
            baseline,
            rank,
            n_available,
        )
        rank = n_available

    value = pool[proj_column][rank - 1]
    if value is None:
        raise ValueError(
            f"replacement_points: the {proj_column!r} value for the "
            f"replacement-rank player (rank {rank} at position {position!r}) "
            f"is null; cannot compute a replacement level."
        )
    return float(value)


def vorp(
    projections: pl.DataFrame,
    cfg: LeagueConfig,
    *,
    position: str = "WR",
    baseline: str = "vols",
    proj_column: str = "proj_points",
    flex_rate: float | None = None,
    superflex_rate: float | None = None,
    bench_fraction: float | None = None,
) -> pl.DataFrame:
    """Add a ``vorp`` column: ``proj_column`` minus the replacement level.

    Computes :func:`replacement_points` once for ``position`` and subtracts
    it from every player at that position. Players at other positions pass
    through unchanged with a null ``vorp`` — this does not filter
    ``projections`` down to ``position``, so a caller can pass a
    multi-position frame and layer one position's valuation onto it without
    losing the other rows (e.g. call this once per position and coalesce the
    resulting ``vorp`` columns to value a whole roster).

    Args:
        projections: Projected-points table with at least ``player_id``,
            ``position``, and ``proj_column``. May contain players at
            positions other than ``position``.
        cfg: The league configuration driving the replacement-rank math.
        position: The position to compute VORP/VOLS for, e.g. ``"QB"``,
            ``"RB"``, ``"WR"``, or ``"TE"``.
        baseline: ``"vols"`` or ``"vorp"``, passed through to
            :func:`replacement_points`.
        proj_column: Name of the projected-points column.
        flex_rate: Assumed fraction of FLEX slots filled by ``position``,
            passed through to :func:`replacement_points`.
        superflex_rate: Assumed fraction of SUPER_FLEX slots filled by
            ``position``, passed through to :func:`replacement_points`.
        bench_fraction: Assumed fraction of total league bench slots stashed
            at ``position``, passed through to :func:`replacement_points`.

    Returns:
        ``projections`` with a ``vorp`` column added: ``proj_column`` minus
        the replacement level for rows at ``position``, null for every other
        row.

    Raises:
        ValueError: See :func:`replacement_points`.
    """
    _require_columns(projections, ("player_id", "position", proj_column), "vorp")

    replacement = replacement_points(
        projections,
        cfg,
        position=position,
        baseline=baseline,
        proj_column=proj_column,
        flex_rate=flex_rate,
        superflex_rate=superflex_rate,
        bench_fraction=bench_fraction,
    )

    return projections.with_columns(
        pl.when(pl.col("position") == position)
        .then(pl.col(proj_column) - replacement)
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("vorp")
    )


def vona(
    projections: pl.DataFrame,
    drafted_player_ids: set[str],
    next_pick_gap: int,
    *,
    position: str = "WR",
    proj_column: str = "proj_points",
) -> pl.DataFrame:
    """Add a ``vona`` column: Value Over Next Available.

    A live-draft metric: among **undrafted** players at ``position``
    (``projections`` filtered to exclude ``drafted_player_ids``), sorted by
    ``proj_column`` descending, the player ``next_pick_gap`` picks from now
    is approximated as the ``next_pick_gap``-th-ranked undrafted player at
    that position (rank 1 = the best player still on the board). ``vona`` is
    then ``proj_column`` minus that player's ``proj_column`` value, for
    every undrafted player at ``position`` — a positive value means a player
    is worth more than what is projected to still be available at the
    drafter's next turn.

    This is a straight rank-based approximation, not a survival-probability
    model (e.g. accounting for how likely other drafters are to take a given
    player first) — that is out of scope here per the technical plan.

    Drafted players, and players at other positions, pass through unchanged
    with a null ``vona`` — this does not filter ``projections`` down, so a
    caller can layer this onto a multi-position frame.

    If fewer undrafted players remain at ``position`` than
    ``next_pick_gap``, this uses the worst-ranked remaining undrafted player
    instead and logs a warning, the same graceful-degradation posture as
    :func:`replacement_points`.

    Args:
        projections: Projected-points table with at least ``player_id``,
            ``position``, and ``proj_column``.
        drafted_player_ids: ``player_id`` values already off the board.
        next_pick_gap: Number of picks from now the drafter's next selection
            falls, e.g. ``1`` for the very next pick. Values below ``1`` are
            treated as ``1``.
        position: The position to compute VONA for.
        proj_column: Name of the projected-points column.

    Returns:
        ``projections`` with a ``vona`` column added: ``proj_column`` minus
        the projected next-available player's value, for undrafted rows at
        ``position``; null for drafted rows and rows at other positions.

    Raises:
        ValueError: If ``projections`` is missing a required column, or no
            undrafted players remain at ``position``.
    """
    _require_columns(projections, ("player_id", "position", proj_column), "vona")

    is_position = pl.col("position") == position
    is_undrafted = ~pl.col("player_id").is_in(list(drafted_player_ids))

    pool = projections.filter(is_position & is_undrafted).sort(
        proj_column, descending=True, nulls_last=True
    )
    n_remaining = pool.height
    if n_remaining == 0:
        raise ValueError(
            f"vona: no undrafted players found in `projections` at position "
            f"{position!r}; cannot compute Value Over Next Available."
        )

    rank = max(next_pick_gap, 1)
    if rank > n_remaining:
        logger.warning(
            "vona: next_pick_gap is %d, but only %d undrafted player(s) "
            "remain at position %r - using the worst-ranked remaining "
            "undrafted player instead.",
            next_pick_gap,
            n_remaining,
            position,
        )
        rank = n_remaining

    next_available_value = pool[proj_column][rank - 1]
    if next_available_value is None:
        raise ValueError(
            f"vona: the {proj_column!r} value for the next-available player "
            f"(rank {rank} among undrafted players at position {position!r}) "
            f"is null; cannot compute Value Over Next Available."
        )
    next_available_value = float(next_available_value)

    return projections.with_columns(
        pl.when(is_position & is_undrafted)
        .then(pl.col(proj_column) - next_available_value)
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("vona")
    )
