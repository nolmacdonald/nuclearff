"""Unit tests for live draft state polling and pick-order math.

Exercises issue 92's own acceptance criteria directly: next_pick_gap
matches simple snake math with no trades, reflects a traded pick correctly,
and drafted_player_ids matches get_draft_picks exactly, including the
empty-draft case.
"""

from __future__ import annotations

import pytest
import responses

from nuclearff.draft.state import (
    DraftState,
    draft_slot_for_pick,
    next_pick_gap,
    poll_draft_state,
    roster_for_pick,
)
from nuclearff.sleeper import SleeperClient
from tests.conftest import TEST_BASE_URL
from tests.test_sleeper_draft import DRAFT, DRAFT_ID, PICKS


@pytest.fixture
def client(tmp_path):
    with SleeperClient(
        cache_dir=tmp_path / "cache",
        base_url=TEST_BASE_URL,
        min_interval=0.0,
        backoff_factor=0.0,
    ) as sleeper:
        yield sleeper


def _draft(
    *, teams: int, rounds: int, reversal_round: int | None = None, type_: str = "snake"
):
    settings = {"teams": teams, "rounds": rounds}
    if reversal_round is not None:
        settings["reversal_round"] = reversal_round
    return {
        "draft_id": "test-draft",
        "type": type_,
        "settings": settings,
        "slot_to_roster_id": {str(slot): slot for slot in range(1, teams + 1)},
    }


# --- draft_slot_for_pick -------------------------------------------------


def test_draft_slot_for_pick_plain_snake_round_one_ascends():
    draft = _draft(teams=4, rounds=3)

    assert [draft_slot_for_pick(draft, pick) for pick in (1, 2, 3, 4)] == [1, 2, 3, 4]


def test_draft_slot_for_pick_plain_snake_round_two_descends():
    draft = _draft(teams=4, rounds=3)

    assert [draft_slot_for_pick(draft, pick) for pick in (5, 6, 7, 8)] == [4, 3, 2, 1]


def test_draft_slot_for_pick_plain_snake_round_three_reverses_again():
    """No reversal_round: every round alternates, so round 3 goes back to
    ascending, same direction as round 1."""
    draft = _draft(teams=4, rounds=3)

    assert [draft_slot_for_pick(draft, pick) for pick in (9, 10, 11, 12)] == [
        1,
        2,
        3,
        4,
    ]


def test_draft_slot_for_pick_matches_the_real_confirmed_reversal_round_draft():
    """Grounded in this project's real, captured reversal_round=3 draft
    (tests/test_sleeper_draft.py): picks 1, 20, and 21 are real Sleeper
    data, not synthetic."""
    real_draft = {**DRAFT, "slot_to_roster_id": {str(s): s for s in range(1, 11)}}

    assert draft_slot_for_pick(real_draft, 1) == 1  # real pick 1: round 1, slot 1
    assert draft_slot_for_pick(real_draft, 20) == 1  # real pick 20: round 2, slot 1
    assert draft_slot_for_pick(real_draft, 21) == 10  # real pick 21: round 3, slot 10


def test_draft_slot_for_pick_linear_never_reverses():
    draft = _draft(teams=4, rounds=2, type_="linear")

    assert [draft_slot_for_pick(draft, pick) for pick in (1, 2, 3, 4, 5, 6, 7, 8)] == [
        1,
        2,
        3,
        4,
        1,
        2,
        3,
        4,
    ]


def test_draft_slot_for_pick_rejects_an_unsupported_type():
    draft = _draft(teams=4, rounds=1, type_="auction")

    with pytest.raises(ValueError, match="Unsupported draft type"):
        draft_slot_for_pick(draft, 1)


def test_draft_slot_for_pick_requires_teams():
    with pytest.raises(ValueError, match="settings.teams"):
        draft_slot_for_pick({"settings": {}}, 1)


# --- roster_for_pick -----------------------------------------------------


def test_roster_for_pick_with_no_trades_uses_the_original_slot_owner():
    draft = _draft(teams=4, rounds=1)

    assert roster_for_pick(draft, [], 1) == 1
    assert roster_for_pick(draft, [], 4) == 4


def test_roster_for_pick_applies_a_traded_pick():
    draft = _draft(teams=4, rounds=1)
    # Round 1, originally roster 1's slot, traded to roster 3.
    traded_picks = [{"round": 1, "roster_id": 1, "owner_id": 3}]

    assert roster_for_pick(draft, traded_picks, 1) == 3


def test_roster_for_pick_returns_none_without_a_slot_to_roster_mapping():
    draft = _draft(teams=4, rounds=1)
    del draft["slot_to_roster_id"]

    assert roster_for_pick(draft, [], 1) is None


# --- next_pick_gap -------------------------------------------------------


def test_next_pick_gap_matches_simple_snake_order_math_with_no_trades():
    """Issue 92's acceptance criterion: no trades -> matches simple
    snake-order math. 4 teams, round 1 ascends 1..4 (picks 1-4), so with
    nothing picked yet, each roster's own gap is exactly its round-1
    position."""
    draft = _draft(teams=4, rounds=2)

    assert [next_pick_gap(draft, [], [], roster_id=r) for r in (1, 2, 3, 4)] == [
        1,
        2,
        3,
        4,
    ]


def test_next_pick_gap_accounts_for_snake_reversal_in_round_two():
    """4 teams, round 2 descends 4..1 (picks 5-8): with round 1 already
    complete, roster 4 picks again immediately (pick 5, gap 1) while
    roster 1 waits until the round's last pick (pick 8, gap 4)."""
    draft = _draft(teams=4, rounds=2)
    round_one_done = [{"pick_no": n} for n in (1, 2, 3, 4)]

    assert next_pick_gap(draft, round_one_done, [], roster_id=4) == 1
    assert next_pick_gap(draft, round_one_done, [], roster_id=1) == 4


def test_next_pick_gap_reflects_a_traded_pick_not_the_original_slot():
    """Issue 92's acceptance criterion: a traded pick changes the answer,
    not just for the team that lost it but for the team that gained it."""
    draft = _draft(teams=4, rounds=1)
    # Pick 4 (round 1, originally roster 4's slot) traded to roster 1.
    traded_picks = [{"round": 1, "roster_id": 4, "owner_id": 1}]

    # Without the trade: roster 1's own pick is pick 1 (gap 1); roster 4's
    # own pick is pick 4 (gap 4).
    assert next_pick_gap(draft, [], [], roster_id=1) == 1
    assert next_pick_gap(draft, [], [], roster_id=4) == 4

    # With the trade: roster 4 has nothing left in this one-round draft;
    # roster 1's nearer pick (pick 1) is unchanged, since it was already
    # earlier than the acquired pick 4.
    assert next_pick_gap(draft, [], traded_picks, roster_id=4) is None
    assert next_pick_gap(draft, [], traded_picks, roster_id=1) == 1

    # Skip past pick 1 to prove roster 1 now *also* owns pick 4 (the
    # traded-in pick), not just its own original pick 1.
    pick_one_done = [{"pick_no": 1}]
    assert next_pick_gap(draft, pick_one_done, traded_picks, roster_id=1) == 3


def test_next_pick_gap_accounts_for_already_made_picks():
    draft = _draft(teams=4, rounds=1)
    already_picked = [{"pick_no": 1}, {"pick_no": 2}]  # picks 1-2 already made

    assert next_pick_gap(draft, already_picked, [], roster_id=3) == 1
    assert next_pick_gap(draft, already_picked, [], roster_id=4) == 2


def test_next_pick_gap_is_none_when_the_draft_is_complete():
    draft = _draft(teams=2, rounds=1)
    all_picks = [{"pick_no": 1}, {"pick_no": 2}]

    assert next_pick_gap(draft, all_picks, [], roster_id=1) is None


# --- poll_draft_state ----------------------------------------------------


@responses.activate
def test_poll_draft_state_groups_picks_by_roster_and_collects_drafted_ids(client):
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=PICKS)

    state = poll_draft_state(client, DRAFT_ID)

    assert isinstance(state, DraftState)
    assert state.drafted_player_ids == {"9221", "4988", "11604"}
    assert [pick["player_id"] for pick in state.picks_by_roster[6]] == ["9221", "4988"]
    assert [pick["player_id"] for pick in state.picks_by_roster[10]] == ["11604"]


@responses.activate
def test_poll_draft_state_handles_a_draft_with_no_picks_yet(client):
    """Issue 92's acceptance criterion: drafted_player_ids matches exactly,
    including the empty-draft case."""
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])

    state = poll_draft_state(client, DRAFT_ID)

    assert state.drafted_player_ids == set()
    assert state.picks_by_roster == {}
