"""Unit tests for nuclearff.draft.sos: strength of schedule."""

from __future__ import annotations

import polars as pl

from nuclearff.draft.sos import strength_of_schedule


def _schedules() -> pl.DataFrame:
    # PHI's remaining schedule: week 2 @ DAL, week 3 @ NYG.
    return pl.DataFrame(
        {
            "season": [2025, 2025],
            "week": [2, 3],
            "home_team": ["PHI", "NYG"],
            "away_team": ["DAL", "PHI"],
        }
    )


def _dvp() -> pl.DataFrame:
    # DAL is a soft matchup for WRs (allows a lot); NYG is tough (allows little).
    return pl.DataFrame(
        {
            "season": [2025, 2025, 2025, 2025],
            "week": [1, 1, 1, 1],
            "opponent_team": ["DAL", "DAL", "NYG", "NYG"],
            "position": ["WR", "RB", "WR", "RB"],
            "points_allowed": [30.0, 10.0, 5.0, 10.0],
            "season_avg_points_allowed": [30.0, 10.0, 5.0, 10.0],
        }
    )


def test_strength_of_schedule_averages_remaining_opponents():
    result = strength_of_schedule(
        "PHI", "WR", _dvp(), _schedules(), season=2025, remaining_weeks=[2, 3]
    )

    assert result == (30.0 + 5.0) / 2


def test_strength_of_schedule_returns_none_when_no_scheduled_opponent():
    result = strength_of_schedule(
        "PHI", "WR", _dvp(), _schedules(), season=2025, remaining_weeks=[99]
    )

    assert result is None


def test_strength_of_schedule_returns_none_when_no_dvp_history():
    thin_dvp = _dvp().filter(pl.col("season") != 2025)

    result = strength_of_schedule(
        "PHI", "WR", thin_dvp, _schedules(), season=2025, remaining_weeks=[2, 3]
    )

    assert result is None
