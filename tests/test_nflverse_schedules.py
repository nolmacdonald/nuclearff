"""Unit tests for the nflreadpy schedules wrapper.

Never touches the network: monkeypatches ``nflreadpy.load_schedules``
against a small synthetic DataFrame shaped like the real schema observed
during live investigation for issue #105 (see the module docstring in
``schedules.py``), same convention as ``tests/test_nflverse_stats.py``.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

import nuclearff.nflverse.schedules as schedules


def _schedule_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2025, 2025],
            "week": [1, 1],
            "game_type": ["REG", "REG"],
            "gameday": ["2025-09-07", "2025-09-07"],
            "home_team": ["PHI", "LAC"],
            "away_team": ["DAL", "KC"],
        }
    )


def test_load_schedules_delegates_with_seasons(monkeypatch):
    calls: list[dict] = []
    expected = _schedule_df()
    monkeypatch.setattr(
        schedules.nflreadpy,
        "load_schedules",
        lambda **kwargs: calls.append(kwargs) or expected,
    )

    result = schedules.load_schedules([2025])

    assert calls == [{"seasons": [2025]}]
    assert result.equals(expected)


def test_load_schedules_raises_on_missing_column(monkeypatch):
    broken = _schedule_df().drop("gameday")
    monkeypatch.setattr(schedules.nflreadpy, "load_schedules", lambda **kwargs: broken)

    with pytest.raises(ValueError, match="gameday"):
        schedules.load_schedules([2025])


def test_team_opponents_flattens_home_and_away():
    result = schedules.team_opponents(_schedule_df())

    rows = {(r["team"], r["opponent"]) for r in result.to_dicts()}
    assert rows == {
        ("PHI", "DAL"),
        ("DAL", "PHI"),
        ("LAC", "KC"),
        ("KC", "LAC"),
    }
    assert result.height == 4


def test_games_on_date_filters_to_the_matching_gameday():
    result = schedules.games_on_date(_schedule_df(), date(2025, 9, 7))

    assert result.height == 2
    assert set(result["home_team"].to_list()) == {"PHI", "LAC"}


def test_games_on_date_returns_empty_frame_for_a_day_with_no_games():
    result = schedules.games_on_date(_schedule_df(), date(2025, 9, 8))

    assert result.height == 0
    assert result.columns == _schedule_df().columns
