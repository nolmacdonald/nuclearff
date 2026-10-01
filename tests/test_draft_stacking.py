"""Unit tests for nuclearff.draft.stacking (issue #98)."""

from __future__ import annotations

from typing import Any

from nuclearff.draft.stacking import handcuff_candidates, stack_candidates


def _player(
    player_id: str,
    position: str,
    team: str | None,
    rank: float | None = None,
) -> dict[str, Any]:
    player: dict[str, Any] = {
        "player_id": player_id,
        "position": position,
        "team": team,
    }
    if rank is not None:
        player["rank"] = rank
    return player


def _ids(pairings: list[dict[str, Any]]) -> list[str]:
    return [p["candidate"]["player_id"] for p in pairings]


# --- stack_candidates -------------------------------------------------------


def test_stack_surfaces_same_team_wr_and_te_for_a_rostered_qb():
    roster = [_player("qb1", "QB", "BUF")]
    pool = [
        _player("wr1", "WR", "BUF", 10),
        _player("te1", "TE", "BUF", 30),
        _player("wr2", "WR", "MIA", 5),
        _player("rb1", "RB", "BUF", 2),
    ]

    result = stack_candidates(roster, pool)

    assert _ids(result) == ["wr1", "te1"]
    assert {p["rostered_player_id"] for p in result} == {"qb1"}
    assert {p["team"] for p in result} == {"BUF"}


def test_stack_excludes_a_player_already_on_the_roster():
    roster = [_player("qb1", "QB", "BUF"), _player("wr1", "WR", "BUF")]
    pool = [_player("wr1", "WR", "BUF", 10), _player("wr3", "WR", "BUF", 20)]

    assert _ids(stack_candidates(roster, pool)) == ["wr3"]


def test_stack_orders_best_rank_first_with_unranked_last():
    roster = [_player("qb1", "QB", "BUF")]
    pool = [
        _player("unranked", "WR", "BUF"),
        _player("late", "WR", "BUF", 40),
        _player("early", "TE", "BUF", 12),
    ]

    assert _ids(stack_candidates(roster, pool)) == ["early", "late", "unranked"]


def test_stack_with_no_qb_returns_nothing():
    roster = [_player("rb1", "RB", "BUF")]

    assert stack_candidates(roster, [_player("wr1", "WR", "BUF")]) == []


def test_stack_covers_each_rostered_qb_separately():
    roster = [_player("qb1", "QB", "BUF"), _player("qb2", "QB", "MIA")]
    pool = [_player("wr1", "WR", "BUF"), _player("wr2", "WR", "MIA")]

    result = stack_candidates(roster, pool)

    assert [(p["rostered_player_id"], p["candidate"]["player_id"]) for p in result] == [
        ("qb1", "wr1"),
        ("qb2", "wr2"),
    ]


def test_stack_ignores_players_without_a_team_and_matches_case_insensitively():
    roster = [_player("qb1", "QB", "buf"), _player("qb2", "QB", None)]
    pool = [_player("wr1", "WR", "BUF"), _player("wr2", "WR", None)]

    assert _ids(stack_candidates(roster, pool)) == ["wr1"]


# --- handcuff_candidates ----------------------------------------------------


def test_handcuff_surfaces_same_team_rbs_only():
    roster = [_player("rb1", "RB", "SF")]
    pool = [
        _player("rb2", "RB", "SF", 80),
        _player("rb3", "RB", "DAL", 70),
        _player("wr1", "WR", "SF", 60),
    ]

    assert _ids(handcuff_candidates(roster, pool)) == ["rb2"]


def test_handcuff_with_no_rbs_on_the_roster_returns_nothing():
    roster = [_player("qb1", "QB", "SF"), _player("wr1", "WR", "SF")]

    assert handcuff_candidates(roster, [_player("rb2", "RB", "SF", 80)]) == []


def test_handcuff_with_an_empty_roster_does_not_error():
    assert handcuff_candidates([], [_player("rb2", "RB", "SF")]) == []


def test_handcuff_excludes_an_available_rb_ranked_above_the_rostered_one():
    roster = [_player("rb1", "RB", "SF", rank=50)]
    pool = [_player("better", "RB", "SF", 20), _player("backup", "RB", "SF", 90)]

    assert _ids(handcuff_candidates(roster, pool)) == ["backup"]


def test_handcuff_without_ranks_keeps_every_same_team_rb():
    roster = [_player("rb1", "RB", "SF")]
    pool = [_player("rb2", "RB", "SF"), _player("rb3", "RB", "SF", 90)]

    assert sorted(_ids(handcuff_candidates(roster, pool))) == ["rb2", "rb3"]


def test_handcuff_excludes_a_player_already_on_the_roster():
    roster = [_player("rb1", "RB", "SF"), _player("rb2", "RB", "SF")]
    pool = [_player("rb2", "RB", "SF", 80), _player("rb3", "RB", "SF", 90)]

    result = handcuff_candidates(roster, pool)

    # rb2 is rostered so never a candidate; rb3 backs up each rostered RB.
    assert [(p["rostered_player_id"], p["candidate"]["player_id"]) for p in result] == [
        ("rb1", "rb3"),
        ("rb2", "rb3"),
    ]
