"""Unit tests for rendering a playoff bracket tree.

The winners/losers bracket rows below are the real 2025-season bracket for
the project's real league (7 winners-bracket matches, 4 losers-bracket
matches — the exact numbers this issue's acceptance criteria names),
captured live earlier this session, converted into ``sleeper_playoff_matches``
row shape (matching what :func:`nuclearff.sleeper.standings.bracket_match_rows`
produces, ``t1_from``/``t2_from`` as JSON text — exercising the JSON-string
parsing path, not just plain dicts). Rendering itself is treated as
manual-review, matching ``dev/tables/``'s posture — these tests cover the
pure-Python parsing/layout and a smoke test that rendering produces a file
without raising, not pixel output.
"""

from __future__ import annotations

import json

import pytest

from nuclearff.report.bracket import (
    RenderingUnavailableError,
    _nudge_colliding_positions,
    bracket_matches_by_number,
    compute_bracket_positions,
    render_bracket_tree,
    render_playoff_brackets,
)

LEAGUE_ID = "1240509989819273216"


def _row(
    match: int,
    round_: int,
    t1: int | None,
    t2: int | None,
    winner: int | None,
    loser: int | None,
    placement: int | None = None,
    t1_from: dict | None = None,
    t2_from: dict | None = None,
    bracket: str = "winners",
) -> dict:
    return {
        "league_id": LEAGUE_ID,
        "season": 2025,
        "bracket": bracket,
        "match": match,
        "round": round_,
        "t1": t1,
        "t2": t2,
        "winner": winner,
        "loser": loser,
        "placement": placement,
        "t1_from": json.dumps(t1_from) if t1_from else None,
        "t2_from": json.dumps(t2_from) if t2_from else None,
    }


WINNERS_BRACKET = [
    _row(1, 1, 10, 9, 9, 10),
    _row(2, 1, 4, 8, 4, 8),
    _row(3, 2, 1, 4, 4, 1),
    _row(4, 2, 3, 9, 3, 9),
    _row(5, 2, 8, 10, 10, 8, placement=5),
    _row(6, 3, 4, 3, 3, 4, placement=1, t1_from={"w": 3}, t2_from={"w": 4}),
    _row(7, 3, 1, 9, 9, 1, placement=3, t1_from={"l": 3}, t2_from={"l": 4}),
]

LOSERS_BRACKET = [
    _row(1, 1, 7, 6, 6, 7, bracket="losers"),
    _row(2, 1, 2, 5, 2, 5, bracket="losers"),
    _row(
        3,
        2,
        6,
        2,
        2,
        6,
        placement=1,
        t1_from={"w": 1},
        t2_from={"w": 2},
        bracket="losers",
    ),
    _row(
        4,
        2,
        7,
        5,
        7,
        5,
        placement=3,
        t1_from={"l": 1},
        t2_from={"l": 2},
        bracket="losers",
    ),
]

NAMES = {
    1: "casitzmann",
    2: "hyoga10",
    3: "nolmacdonald",
    4: "ksavabi",
    5: "aperry151",
    6: "Donkeysride",
    7: "ruhbberduhcky",
    8: "jwhitney0220",
    9: "nawfeastdallas",
    10: "thatbolb",
}


# --- bracket_matches_by_number ---------------------------------------------


def test_bracket_matches_by_number_indexes_by_match_number():
    indexed = bracket_matches_by_number(WINNERS_BRACKET)

    assert set(indexed) == {1, 2, 3, 4, 5, 6, 7}
    assert indexed[6]["placement"] == 1


def test_bracket_matches_by_number_drops_rows_with_no_match_number():
    indexed = bracket_matches_by_number([{"match": None}, *WINNERS_BRACKET])

    assert set(indexed) == {1, 2, 3, 4, 5, 6, 7}


# --- compute_bracket_positions ----------------------------------------------


def test_compute_bracket_positions_assigns_sequential_leaf_slots_to_round_one():
    matches = bracket_matches_by_number(WINNERS_BRACKET)
    positions = compute_bracket_positions(matches)

    assert positions[1] == (0.0, 1.0)
    assert positions[2] == (2.0, 3.0)


def test_compute_bracket_positions_gives_a_bye_its_own_fresh_leaf_slot():
    """Match 3's t1=1 and match 4's t1=3 are byes straight into round 2."""
    matches = bracket_matches_by_number(WINNERS_BRACKET)
    positions = compute_bracket_positions(matches)

    # Byes must not collide with round 1's leaves (0.0-3.0).
    y1_match3, _ = positions[3]
    y1_match4, _ = positions[4]
    assert {y1_match3, y1_match4}.isdisjoint({0.0, 1.0, 2.0, 3.0})
    assert y1_match3 != y1_match4


def test_compute_bracket_positions_championship_and_third_place_share_inputs():
    """Match 6 (championship) and match 7 (3rd place) both split off matches 3 & 4.

    Also exercises t1_from/t2_from as JSON text (this fixture's real shape),
    not a plain dict.
    """
    matches = bracket_matches_by_number(WINNERS_BRACKET)
    positions = compute_bracket_positions(matches)

    assert positions[6] == positions[7]


def test_compute_bracket_positions_handles_a_malformed_reference():
    """An unparseable t1_from falls back to a fresh leaf rather than raising."""
    matches = bracket_matches_by_number([_row(1, 1, 5, None, None, None, t1_from=None)])
    matches[1]["t2_from"] = "not valid json"

    positions = compute_bracket_positions(matches)

    assert positions[1] == (0.0, 1.0)


# --- _nudge_colliding_positions ---------------------------------------------
#
# Imported directly (unlike this repo's usual private-helper convention):
# this fixes a real visual bug found by rendering the real winners bracket
# and looking at it -- the championship and 3rd-place matches' labels drew
# exactly on top of each other, illegible, because compute_bracket_positions
# correctly gives them the same logical position. A rendering smoke test
# alone (file exists, nonzero size) can't catch overlapping text; only
# either eyeballing the PNG or asserting on the actual positions can. This
# regression test is the latter, cheaper to keep running in CI than a human
# re-checking the image every time.


def test_nudge_colliding_positions_separates_matches_that_share_a_position():
    matches = bracket_matches_by_number(WINNERS_BRACKET)
    positions = compute_bracket_positions(matches)
    assert positions[6] == positions[7]  # the collision this fixes

    display = _nudge_colliding_positions(matches, positions)

    assert display[6] != display[7]
    # The actual bug: the two boxes' y-ranges must no longer overlap at all,
    # not just differ by some fixed amount -- a nudge smaller than the box
    # height still leaves them visually crossing (see the module docstring).
    lo6, hi6 = sorted(display[6])
    lo7, hi7 = sorted(display[7])
    assert hi6 <= lo7 or hi7 <= lo6


def test_nudge_colliding_positions_leaves_non_colliding_matches_untouched():
    matches = bracket_matches_by_number(WINNERS_BRACKET)
    positions = compute_bracket_positions(matches)

    display = _nudge_colliding_positions(matches, positions)

    assert display[1] == positions[1]
    assert display[3] == positions[3]


# --- render_bracket_tree (smoke tests) --------------------------------------


def test_render_bracket_tree_writes_a_file_for_the_real_winners_bracket(tmp_path):
    out_path = render_bracket_tree(
        WINNERS_BRACKET, NAMES, tmp_path / "winners.png", title="Test League"
    )

    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_bracket_tree_writes_a_file_for_the_real_losers_bracket(tmp_path):
    out_path = render_bracket_tree(LOSERS_BRACKET, NAMES, tmp_path / "losers.png")

    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_bracket_tree_raises_without_any_matches(tmp_path):
    with pytest.raises(ValueError, match="no matches"):
        render_bracket_tree([], NAMES, tmp_path / "empty.png")


def test_render_bracket_tree_handles_an_unresolved_participant(tmp_path):
    """A TBD participant (no direct roster id, no resolvable reference)."""
    pending = [_row(1, 1, None, 5, None, None)]

    out_path = render_bracket_tree(pending, NAMES, tmp_path / "pending.png")

    assert out_path.is_file()


# --- render_playoff_brackets --------------------------------------------------


def test_render_playoff_brackets_writes_both_files(tmp_path):
    written = render_playoff_brackets(
        WINNERS_BRACKET + LOSERS_BRACKET, NAMES, tmp_path, league_name="Test League"
    )

    assert set(written) == {"winners", "losers"}
    assert written["winners"].is_file()
    assert written["losers"].is_file()


def test_render_playoff_brackets_omits_losers_when_absent(tmp_path):
    written = render_playoff_brackets(WINNERS_BRACKET, NAMES, tmp_path)

    assert set(written) == {"winners"}
    assert not (tmp_path / "losers_bracket.png").exists()


def test_rendering_unavailable_error_is_an_import_error():
    assert issubclass(RenderingUnavailableError, ImportError)
