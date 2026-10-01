"""Roster construction preview via Monte Carlo simulation (issue #97).

:func:`preview_roster` aggregates the existing single-player machinery
(:func:`nuclearff.simulation.montecarlo.simulate_player_season` and
:func:`~nuclearff.simulation.montecarlo.summarize_distribution`) into a projected
floor, median and ceiling for a roster's season points. It adds no new
simulation logic.

Independence assumption
-----------------------
Each player's season is simulated independently and the seasons are summed
draw by draw. Real teammates are positively correlated (shared game script,
offense quality, a QB's health), so the summed distribution is **narrower** than
reality: floor too high, ceiling too low. The median and mean are unaffected by
the assumption. Every result carries the assumption in its ``assumption``
column rather than leaving it implicit.

Each player draws from a different seed (``seed + index``). One shared seed
would give identically parameterized players identical draws, i.e. perfect
correlation, the opposite of the stated assumption.

The preview totals every projected player on the roster. It does not model
start/sit decisions or bench value, so it is total roster talent, not weekly
optimal lineup output.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from nuclearff.config.models import SimulationConfig
from nuclearff.simulation.montecarlo import (
    simulate_from_config,
    summarize_distribution,
)

CORRELATION_ASSUMPTION = "independent: players' seasons simulated independently"
"""Value of the ``assumption`` column; see the module docstring."""

_SCHEMA = {
    "players": pl.UInt32,
    "players_unprojected": pl.UInt32,
    "floor": pl.Float64,
    "median": pl.Float64,
    "ceiling": pl.Float64,
    "mean": pl.Float64,
    "assumption": pl.String,
}


def preview_roster(
    roster_picks: list[dict[str, Any]],
    cfg: SimulationConfig,
    *,
    seed: int | None = None,
) -> pl.DataFrame:
    """Project a roster's floor, median and ceiling of season points.

    Args:
        roster_picks: This roster's picks so far, typically
            ``DraftState.picks_by_roster[roster_id]`` with each player's
            projection joined on: ``mean_ppg`` and ``sd_ppg`` (required to be
            simulated) and ``skew`` (optional, default ``0.0``), as taken by
            :func:`~nuclearff.simulation.montecarlo.simulate_player_season`. A
            pick lacking a numeric ``mean_ppg`` or ``sd_ppg`` is counted in
            ``players_unprojected`` and left out of the total.
        cfg: Supplies ``games``, ``n_simulations`` and the floor and ceiling
            percentiles.
        seed: Base seed. Player ``i`` uses ``seed + i``; ``None`` draws fresh
            entropy for every player.

    Returns:
        One row: ``players`` (simulated), ``players_unprojected``, ``floor``,
        ``median``, ``ceiling``, ``mean`` of the summed season totals, and
        ``assumption`` (:data:`CORRELATION_ASSUMPTION`). With no projected
        player the frame is empty.

    Raises:
        ValueError: If a projected player's ``sd_ppg`` is not positive.
    """
    total: np.ndarray | None = None
    projected = 0
    for pick in roster_picks:
        mean_ppg = pick.get("mean_ppg")
        sd_ppg = pick.get("sd_ppg")
        if not isinstance(mean_ppg, (int, float)) or not isinstance(
            sd_ppg, (int, float)
        ):
            continue
        samples = simulate_from_config(
            float(mean_ppg),
            float(sd_ppg),
            float(pick.get("skew") or 0.0),
            cfg,
            seed=None if seed is None else seed + projected,
        )
        total = samples if total is None else total + samples
        projected += 1

    if total is None:
        return pl.DataFrame(schema=_SCHEMA)

    summary = summarize_distribution(total, cfg)
    return pl.DataFrame(
        {
            "players": [projected],
            "players_unprojected": [len(roster_picks) - projected],
            "floor": [summary["floor"]],
            "median": [summary["median"]],
            "ceiling": [summary["ceiling"]],
            "mean": [summary["mean"]],
            "assumption": [CORRELATION_ASSUMPTION],
        },
        schema=_SCHEMA,
    )
