"""Unit tests for nuclearff.nflverse.injuries (issue #103).

No network: ``nflreadpy.load_injuries`` is monkeypatched with a small frame
shaped like the real schema observed live (see the module docstring).
"""

from __future__ import annotations

import polars as pl
import pytest

import nuclearff.nflverse.injuries as injuries


def _injuries_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2024, 2024, 2024, 2024],
            "game_type": ["REG", "REG", "WC", "SB"],
            "week": [1, 2, 19, 22],
            "gsis_id": ["00-01", "00-01", "00-01", "00-02"],
            "report_status": ["Questionable", "Out", "Out", "Out"],
        }
    )


def test_load_injury_history_delegates_with_the_requested_seasons(monkeypatch):
    calls: list[dict] = []
    expected = _injuries_df()
    monkeypatch.setattr(
        injuries.nflreadpy,
        "load_injuries",
        lambda **kwargs: calls.append(kwargs) or expected,
    )

    injuries.load_injury_history([2023, 2024])

    assert calls == [{"seasons": [2023, 2024]}]


def test_load_injury_history_keeps_only_regular_season_rows(monkeypatch):
    monkeypatch.setattr(
        injuries.nflreadpy, "load_injuries", lambda **kwargs: _injuries_df()
    )

    result = injuries.load_injury_history([2024])

    assert result["game_type"].unique().to_list() == ["REG"]
    assert result["week"].to_list() == [1, 2]


def test_load_injury_history_raises_on_a_missing_column(monkeypatch):
    broken = _injuries_df().drop("gsis_id")
    monkeypatch.setattr(injuries.nflreadpy, "load_injuries", lambda **kwargs: broken)

    with pytest.raises(ValueError, match="load_injury_history.*gsis_id"):
        injuries.load_injury_history([2024])
