"""Unit tests for nuclearff.draft.byeweeks: bye-week collision warnings."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.draft.byeweeks import bye_collisions, bye_weeks


def _games(rows: list[tuple[int, int, str, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "season": pl.Int64,
            "week": pl.Int64,
            "game_type": pl.String,
            "home_team": pl.String,
            "away_team": pl.String,
        },
        orient="row",
    )


def _schedule() -> pl.DataFrame:
    # Byes: A and C in week 3, B and D in week 2.
    return _games(
        [
            (2025, 1, "REG", "A", "B"),
            (2025, 1, "REG", "C", "D"),
            (2025, 2, "REG", "A", "C"),
            (2025, 3, "REG", "B", "D"),
        ]
    )


def _pick(position: str, team: str | None) -> dict[str, object]:
    return {"position": position, "team": team}


def test_bye_weeks_finds_each_teams_missing_week():
    assert bye_weeks(_schedule()) == {"A": 3, "B": 2, "C": 3, "D": 2}


def test_bye_weeks_ignores_non_regular_season_games():
    schedule = pl.concat([_schedule(), _games([(2025, 5, "POST", "A", "B")])])

    assert bye_weeks(schedule) == {"A": 3, "B": 2, "C": 3, "D": 2}


def test_bye_weeks_defaults_to_latest_season_and_honors_season():
    # In 2026 A and B play both weeks, so neither has a bye.
    later = _games([(2026, 1, "REG", "A", "B"), (2026, 2, "REG", "A", "B")])
    schedule = pl.concat([_schedule(), later])

    assert bye_weeks(schedule) == {}
    assert bye_weeks(schedule, 2025) == {"A": 3, "B": 2, "C": 3, "D": 2}


def test_bye_weeks_leaves_out_teams_without_exactly_one_bye():
    # E and F play only week 1, so they miss weeks 2 and 3.
    schedule = pl.concat([_schedule(), _games([(2025, 1, "REG", "E", "F")])])

    assert bye_weeks(schedule) == {"A": 3, "B": 2, "C": 3, "D": 2}


def test_bye_weeks_requires_schedule_columns():
    with pytest.raises(ValueError, match="bye_weeks"):
        bye_weeks(_schedule().drop("game_type"))


def test_flags_a_collision_with_two_same_position_players():
    roster = [_pick("WR", "A"), _pick("WR", "C"), _pick("RB", "A")]

    warnings = bye_collisions(roster, _pick("WR", "A"), _schedule())

    assert warnings == ["3 WR on bye in week 3 (including the candidate)"]


def test_flags_a_pair_including_only_the_candidate():
    warnings = bye_collisions([_pick("RB", "A")], _pick("RB", "C"), _schedule())

    assert warnings == ["2 RB on bye in week 3 (including the candidate)"]


def test_no_warning_when_byes_do_not_collide():
    roster = [_pick("WR", "B"), _pick("WR", "D")]

    assert bye_collisions(roster, _pick("WR", "A"), _schedule()) == []


def test_no_warning_for_an_empty_roster():
    assert bye_collisions([], _pick("WR", "A"), _schedule()) == []


def test_other_positions_do_not_count():
    roster = [_pick("RB", "A"), _pick("TE", "C")]

    assert bye_collisions(roster, _pick("WR", "A"), _schedule()) == []


@pytest.mark.parametrize(
    "candidate",
    [
        _pick("WR", None),
        _pick("WR", "ZZZ"),
        {"team": "A"},
        {"position": "WR"},
    ],
)
def test_unknown_candidate_team_or_position_gives_no_warning(candidate):
    roster = [_pick("WR", "A")]

    assert bye_collisions(roster, candidate, _schedule()) == []


def test_roster_player_with_unknown_team_is_not_counted():
    roster = [_pick("WR", None), _pick("WR", "ZZZ")]

    assert bye_collisions(roster, _pick("WR", "A"), _schedule()) == []
