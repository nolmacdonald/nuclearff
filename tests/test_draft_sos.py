"""Unit tests for nuclearff.draft.sos: strength of schedule."""

from __future__ import annotations

import polars as pl

from nuclearff.config.league import LeagueConfig, RosterSlots, ScoringSettings
from nuclearff.draft.sos import playoff_strength_of_schedule, strength_of_schedule


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


# --- playoff_strength_of_schedule (issue #106) ------------------------


def test_playoff_strength_of_schedule_matches_strength_of_schedule_over_same_weeks():
    """Reuses strength_of_schedule entirely -- no new DvP computation, per
    issue #106's own instruction."""
    playoff_result = playoff_strength_of_schedule(
        "PHI", "WR", _dvp(), _schedules(), season=2025, playoff_weeks=[2, 3]
    )
    remaining_result = strength_of_schedule(
        "PHI", "WR", _dvp(), _schedules(), season=2025, remaining_weeks=[2, 3]
    )

    assert playoff_result == remaining_result == (30.0 + 5.0) / 2


def test_playoff_strength_of_schedule_is_none_without_a_resolvable_playoff_range():
    """Issue #106: don't guess a placeholder range when playoff_weeks is
    unresolvable (e.g. LeagueConfig.playoff_weeks() returned None)."""
    result = playoff_strength_of_schedule(
        "PHI", "WR", _dvp(), _schedules(), season=2025, playoff_weeks=None
    )

    assert result is None


def test_playoff_strength_of_schedule_is_none_for_an_empty_playoff_range():
    result = playoff_strength_of_schedule(
        "PHI", "WR", _dvp(), _schedules(), season=2025, playoff_weeks=[]
    )

    assert result is None


def test_playoff_strength_of_schedule_end_to_end_with_a_non_default_playoff_start():
    """Issue #106's own acceptance criterion: verified against a fixture
    league with a non-default playoff start week (2, not the common 15) --
    the full pipeline from LeagueConfig.playoff_weeks() through to a real
    strength-of-schedule number."""
    cfg = LeagueConfig(
        league_id="1",
        name="Test League",
        season=2025,
        num_teams=4,
        scoring=ScoringSettings.from_sleeper({"rec": 1.0}),
        roster=RosterSlots.from_sleeper(["WR", "BN"]),
        best_ball=False,
        league_type=0,
        draft_id=None,
        previous_league_id=None,
        playoff_week_start=2,  # non-default: matches the fixture schedule's weeks
        playoff_teams=4,  # ceil(log2(4)) = 2 rounds -> weeks [2, 3]
    )
    assert cfg.playoff_weeks() == [2, 3]

    result = playoff_strength_of_schedule(
        "PHI",
        "WR",
        _dvp(),
        _schedules(),
        season=2025,
        playoff_weeks=cfg.playoff_weeks(),
    )

    assert result == (30.0 + 5.0) / 2
