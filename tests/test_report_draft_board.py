"""Unit tests for rendering a draft board grid.

The pick rows below are a real slice of this project's real, currently
in-progress 2026 startup draft (``draft_id=1367225133646778368``), captured
live -- round 1 (plain snake), and rounds 2/3 whose real ``draft_slot``
values confirm this draft's real ``settings.reversal_round: 3`` (round 3
continues round 2's column direction rather than reversing). Rendering
itself is treated as manual-review, matching ``tests/test_report_bracket.py``'s
posture: these tests cover the pure-Python position logic and a smoke test
that rendering produces a file without raising, not pixel output.
"""

from __future__ import annotations

import pytest

from nuclearff.report.draft_board import (
    POSITION_COLORS,
    RenderingUnavailableError,
    draft_board_positions,
    render_draft_board,
)

TEAMS = 10
ROUNDS = 15

# league_id=1367225133634191360, draft_id=1367225133646778368.
PICKS = [
    {
        "pick_no": 1,
        "round": 1,
        "draft_slot": 1,
        "position": "RB",
        "first_name": "Jahmyr",
        "last_name": "Gibbs",
        "team": "DET",
    },
    {
        "pick_no": 3,
        "round": 1,
        "draft_slot": 3,
        "position": "WR",
        "first_name": "Ja'Marr",
        "last_name": "Chase",
        "team": "CIN",
    },
    {
        "pick_no": 16,
        "round": 2,
        "draft_slot": 5,
        "position": "QB",
        "first_name": "Josh",
        "last_name": "Allen",
        "team": "BUF",
    },
    {
        "pick_no": 20,
        "round": 2,
        "draft_slot": 1,
        "position": "RB",
        "first_name": "Bucky",
        "last_name": "Irving",
        "team": "TB",
    },
    {
        "pick_no": 21,
        "round": 3,
        "draft_slot": 10,
        "position": "TE",
        "first_name": "Brock",
        "last_name": "Bowers",
        "team": "LV",
    },
]

NAMES = {1: "nolmacdonald", 3: "casitzmann", 5: "aperry151", 10: "thatbolb"}


# --- draft_board_positions ---------------------------------------------


def test_draft_board_positions_uses_the_real_draft_slot_directly():
    positions = draft_board_positions(PICKS)

    assert positions[1] == (1, 1)
    assert positions[3] == (3, 1)


def test_draft_board_positions_reflects_the_real_reversal_round():
    """Round 3 continues round 2's column direction, not round 1's."""
    positions = draft_board_positions(PICKS)

    assert positions[20] == (1, 2)  # round 2 ends at slot 1
    assert positions[21] == (10, 3)  # round 3 starts again at slot 10


def test_draft_board_positions_skips_rows_missing_a_grid_field():
    positions = draft_board_positions([{"pick_no": 1, "round": 1}])

    assert positions == {}


# --- render_draft_board (smoke tests) -----------------------------------


def test_render_draft_board_writes_a_file_for_the_real_picks(tmp_path):
    out_path = render_draft_board(
        PICKS, NAMES, tmp_path / "board.png", teams=TEAMS, rounds=ROUNDS
    )

    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_draft_board_raises_without_any_resolvable_picks(tmp_path):
    with pytest.raises(ValueError, match="no picks"):
        render_draft_board(
            [], NAMES, tmp_path / "empty.png", teams=TEAMS, rounds=ROUNDS
        )


def test_render_draft_board_handles_an_unconfirmed_position_color(tmp_path):
    """A position outside POSITION_COLORS (e.g. K/DEF) still renders, no crash."""
    pick = {
        "pick_no": 1,
        "round": 1,
        "draft_slot": 1,
        "position": "K",
        "first_name": "Test",
        "last_name": "Kicker",
        "team": "SF",
    }
    assert "K" not in POSITION_COLORS

    out_path = render_draft_board(
        [pick], NAMES, tmp_path / "k.png", teams=TEAMS, rounds=ROUNDS
    )

    assert out_path.is_file()


def test_render_draft_board_falls_back_to_slot_label_for_an_unnamed_column(tmp_path):
    """A draft_slot missing from `names` still renders (falls back to "Slot N")."""
    out_path = render_draft_board(
        PICKS, {}, tmp_path / "unnamed.png", teams=TEAMS, rounds=ROUNDS
    )

    assert out_path.is_file()


def test_rendering_unavailable_error_is_an_import_error():
    assert issubclass(RenderingUnavailableError, ImportError)
