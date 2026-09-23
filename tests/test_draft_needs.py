"""Unit tests for per-roster starting-slot need.

Exercises issue 93's own acceptance criteria directly: zero picks reports
full need everywhere, a filled-but-flex-eligible position reports reduced
(not zero) need, and an unrecognized slot code doesn't crash.
"""

from __future__ import annotations

from nuclearff.config.league import (
    DEFAULT_FLEX_RATES,
    DEFAULT_SUPERFLEX_RATES,
    RosterSlots,
)
from nuclearff.draft.needs import roster_needs


def _pick(position: str) -> dict[str, str]:
    return {"position": position}


def test_roster_needs_reports_full_need_with_zero_picks():
    """Issue 93's acceptance criterion: a roster with 0 picks reports full
    need at every starting slot. No SUPER_FLEX slots here, so QB's need is
    just its locked slot -- see the dedicated superflex test below for that
    contribution."""
    slots = RosterSlots(counts={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 3, "BN": 6})

    needs = roster_needs([], slots)

    assert needs["QB"] == 1.0
    assert needs["RB"] == 2 + 3 * DEFAULT_FLEX_RATES["RB"]
    assert needs["WR"] == 2 + 3 * DEFAULT_FLEX_RATES["WR"]
    assert needs["TE"] == 1 + 3 * DEFAULT_FLEX_RATES["TE"]


def test_roster_needs_reports_reduced_not_zero_need_when_flex_eligible():
    """Issue 93's acceptance criterion: enough WRs to fill both WR slots
    and contribute to FLEX still reports reduced, nonzero further need."""
    slots = RosterSlots(counts={"WR": 2, "FLEX": 3, "BN": 6})
    picks = [_pick("WR"), _pick("WR")]  # exactly fills the 2 locked WR slots

    needs = roster_needs(picks, slots)

    expected_demand = 2 + 3 * DEFAULT_FLEX_RATES["WR"]
    assert 0.0 < needs["WR"] < expected_demand
    assert needs["WR"] == expected_demand - 2


def test_roster_needs_floors_at_zero_rather_than_going_negative():
    slots = RosterSlots(counts={"WR": 1, "FLEX": 0, "BN": 6})
    picks = [_pick("WR"), _pick("WR"), _pick("WR")]  # well past the 1 locked slot

    needs = roster_needs(picks, slots)

    assert needs["WR"] == 0.0


def test_roster_needs_handles_idp_and_superflex_slot_codes_without_crashing():
    """Issue 93's acceptance criterion: unrecognized/IDP/superflex slot
    codes in RosterSlots.counts don't crash."""
    slots = RosterSlots(
        counts={
            "QB": 1,
            "SUPER_FLEX": 1,
            "IDP_FLEX": 2,
            "DL": 2,
            "LB": 2,
            "DB": 2,
            "K": 1,
            "DEF": 1,
            "TAXI": 2,
        }
    )

    needs = roster_needs([], slots)

    assert needs["QB"] == 1 + 1 * DEFAULT_SUPERFLEX_RATES["QB"]
    assert set(needs) == {"QB", "RB", "WR", "TE"}  # unrecognized codes just absent


def test_roster_needs_ignores_a_pick_with_no_resolvable_position():
    slots = RosterSlots(counts={"WR": 2})
    picks = [{"position": None}, {}]

    needs = roster_needs(picks, slots)

    assert needs["WR"] == 2.0  # neither pick counted against WR need
