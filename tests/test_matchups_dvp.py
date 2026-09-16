"""Unit tests for nuclearff.matchups.dvp: defense-vs-position points allowed.

Hand-computable synthetic stat lines, scored under a simple settings object
(1 pt/reception, 0.1 pt/receiving yard) so expected fantasy_points totals
are easy to verify by hand, matching this project's existing test
conventions (see ``tests/test_valuation_vorp.py``).
"""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.config.league import ScoringSettings
from nuclearff.matchups.dvp import points_allowed_by_position
from nuclearff.scoring.engine import ScoringEngine


def _engine() -> ScoringEngine:
    return ScoringEngine(ScoringSettings(values={"rec": 1.0, "rec_yd": 0.1}))


def _weekly_stats() -> pl.DataFrame:
    # Week 1: two WRs both faced DAL -> DAL allows 6 + 9 = 15 WR points.
    # Week 2: one WR faced DAL again -> 4 WR points, season avg (15+4)/2=9.5.
    return pl.DataFrame(
        {
            "season": [2024, 2024, 2024],
            "week": [1, 1, 2],
            "position": ["WR", "WR", "WR"],
            "opponent_team": ["DAL", "DAL", "DAL"],
            "receptions": [5, 8, 4],
            "receiving_yards": [10, 10, 0],
        }
    )


def test_points_allowed_by_position_sums_within_week():
    result = points_allowed_by_position(_weekly_stats(), _engine())

    week1 = result.filter(pl.col("week") == 1)
    assert week1.height == 1
    assert week1["points_allowed"].item() == pytest.approx(6.0 + 9.0)


def test_points_allowed_by_position_season_avg_spans_all_weeks():
    result = points_allowed_by_position(_weekly_stats(), _engine())

    avgs = result["season_avg_points_allowed"].unique().to_list()
    assert avgs == pytest.approx([9.5])


def test_points_allowed_by_position_raises_on_missing_column():
    broken = _weekly_stats().drop("opponent_team")

    with pytest.raises(ValueError, match="opponent_team"):
        points_allowed_by_position(broken, _engine())
