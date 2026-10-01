"""Unit tests for nuclearff.draft.injuries (issue #103)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.draft.injuries import InjuryRisk, injury_risk

_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "gsis_id": pl.String,
    "report_status": pl.String,
}


def _history(rows: list[tuple]) -> pl.DataFrame:
    # (season, week, gsis_id, report_status)
    return pl.DataFrame(rows, schema=_SCHEMA, orient="row")


def _out(player: str, season: int, weeks: range) -> list[tuple]:
    return [(season, week, player, "Out") for week in weeks]


def test_two_significant_seasons_raise_a_caution():
    history = _history(
        _out("P1", 2025, range(1, 6))
        + _out("P1", 2024, range(3, 7))
        + _out("P1", 2023, range(1, 2))
    )

    risk = injury_risk("P1", history)

    assert risk.out_weeks == {2025: 5, 2024: 4, 2023: 1}
    assert risk.caution == (
        "Listed Out on the injury report for 5 weeks in 2025, 4 weeks in 2024"
    )


def test_a_clean_history_returns_no_caution():
    history = _history([(2025, 1, "P2", "Out")] + _out("P1", 2025, range(1, 6)))

    risk = injury_risk("P2", history)

    assert risk.caution is None
    assert risk.out_weeks[2025] == 1


def test_one_bad_season_alone_is_not_a_caution():
    history = _history(_out("P1", 2025, range(1, 9)) + _out("P1", 2024, range(1, 2)))

    assert injury_risk("P1", history).caution is None


def test_only_out_status_counts():
    rows = [(2025, w, "P1", "Questionable") for w in range(1, 8)]
    rows += [(2024, w, "P1", "Doubtful") for w in range(1, 8)]
    rows += [(2023, w, "P1", None) for w in range(1, 8)]

    risk = injury_risk("P1", _history(rows))

    assert set(risk.out_weeks.values()) == {0}
    assert risk.caution is None


def test_repeated_rows_for_one_player_week_count_once():
    rows = _out("P1", 2025, range(1, 4)) * 3 + _out("P1", 2024, range(1, 4)) * 3

    risk = injury_risk("P1", _history(rows))

    assert risk.out_weeks == {2025: 3, 2024: 3}


def test_window_is_the_most_recent_seasons_in_the_history():
    history = _history(
        _out("P1", 2025, range(1, 4))
        + _out("P1", 2024, range(1, 4))
        + _out("P1", 2022, range(1, 9))
    )

    risk = injury_risk("P1", history, seasons=2)

    assert list(risk.out_weeks) == [2025, 2024]


def test_a_season_missing_for_the_player_counts_as_zero():
    history = _history(_out("P1", 2025, range(1, 5)) + [(2024, 1, "OTHER", "Out")])

    risk = injury_risk("P1", history)

    assert risk.out_weeks == {2025: 4, 2024: 0}
    assert risk.caution is None


def test_thresholds_are_adjustable():
    history = _history(_out("P1", 2025, range(1, 3)))

    risk = injury_risk("P1", history, significant_out_weeks=2, caution_seasons=1)

    assert risk.caution == "Listed Out on the injury report for 2 weeks in 2025"


def test_single_week_is_singular_in_the_caution():
    history = _history(_out("P1", 2025, range(1, 2)))

    risk = injury_risk("P1", history, significant_out_weeks=1, caution_seasons=1)

    assert risk.caution == "Listed Out on the injury report for 1 week in 2025"


def test_unknown_player_and_empty_history_are_clean():
    assert injury_risk("NOPE", _history(_out("P1", 2025, range(1, 6)))).caution is None

    empty = injury_risk("P1", pl.DataFrame(schema=_SCHEMA))

    assert empty == InjuryRisk(player_id="P1", out_weeks={}, caution=None)


@pytest.mark.parametrize(
    "kwargs",
    [{"seasons": 0}, {"significant_out_weeks": 0}, {"caution_seasons": 0}],
)
def test_nonpositive_thresholds_are_rejected(kwargs):
    with pytest.raises(ValueError, match="injury_risk"):
        injury_risk("P1", pl.DataFrame(schema=_SCHEMA), **kwargs)
