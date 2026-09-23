"""Per-roster starting-slot need, from picks so far and the league's roster shape.

:class:`~nuclearff.config.league.RosterSlots` already gives a typed count of
starting/bench slots per position from the league's real settings, but
nothing compares that against what a roster has actually drafted so far to
say "this team still needs a starting RB." :func:`roster_needs` does that
for one roster, given its picks so far (typically
:class:`nuclearff.draft.state.DraftState`'s ``picks_by_roster[roster_id]``,
GitHub Issue 92).

Reuses :data:`nuclearff.config.league.DEFAULT_FLEX_RATES`/
:data:`~nuclearff.config.league.DEFAULT_SUPERFLEX_RATES` -- the same
assumed per-position share of a FLEX/SUPER_FLEX slot
:meth:`~nuclearff.config.league.LeagueConfig.starter_demand` already uses
for league-wide replacement-rank math (:mod:`nuclearff.valuation.vorp`) --
rather than deriving a second flex-allocation heuristic for the single-
roster case. The formula here is that same per-team term
(``starter_demand``'s ``num_teams *`` multiplication dropped, since this is
one roster, not the whole league).
"""

from __future__ import annotations

from typing import Any

from nuclearff.config.league import (
    DEFAULT_FLEX_RATES,
    DEFAULT_SUPERFLEX_RATES,
    RosterSlots,
)

_NEED_POSITIONS = ("QB", "RB", "WR", "TE")
"""Positions this reports need for -- the same key set
:data:`~nuclearff.config.league.DEFAULT_FLEX_RATES`/
:data:`~nuclearff.config.league.DEFAULT_SUPERFLEX_RATES` cover. Any other
slot code present on ``slots`` (``K``, ``DEF``, an IDP slot, ``BN``, ``IR``,
...) has no assumed flex share and is simply not reported on -- not a
crash, matching :meth:`RosterSlots.count`'s own posture for an
unrecognized code."""


def roster_needs(
    roster_picks: list[dict[str, Any]], slots: RosterSlots
) -> dict[str, float]:
    """Compute one roster's remaining starting-slot need by position.

    Args:
        roster_picks: This roster's picks so far, each shaped like
            :func:`nuclearff.sleeper.draft.draft_pick_rows`'s output (needs
            only a ``"position"`` key) -- typically
            ``DraftState.picks_by_roster[roster_id]``.
        slots: The league's roster shape.

    Returns:
        Each of :data:`_NEED_POSITIONS` mapped to its remaining need: the
        position's assumed starter demand for one team (locked slots plus
        an assumed fractional share of FLEX/SUPER_FLEX slots) minus how
        many the roster has already drafted at that position, floored at
        ``0.0`` -- never negative. A position already filled at its locked
        slots but with FLEX room left still reports a reduced, nonzero
        need rather than ``0.0``, since a FLEX slot can still go to that
        position.
    """
    drafted_counts: dict[str, int] = {}
    for pick in roster_picks:
        position = pick.get("position")
        if isinstance(position, str):
            drafted_counts[position] = drafted_counts.get(position, 0) + 1

    needs: dict[str, float] = {}
    for position in _NEED_POSITIONS:
        flex_rate = DEFAULT_FLEX_RATES.get(position, 0.0)
        superflex_rate = DEFAULT_SUPERFLEX_RATES.get(position, 0.0)
        demand = (
            slots.count(position)
            + slots.flex * flex_rate
            + slots.superflex * superflex_rate
        )
        needs[position] = max(0.0, demand - drafted_counts.get(position, 0))

    return needs
