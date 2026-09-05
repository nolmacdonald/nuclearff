"""Unit tests for nuclearff.pipeline.auction_board.

Network is never touched: `build_auction_board` itself is exercised only
through its pure sub-steps (`score_seasons`, `project_points`,
`add_vorp_all_positions`), which take frames directly. The real committed
Sleeper fixture supplies the LeagueConfig, matching the convention in
`tests/test_valuation_vorp.py`.
"""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.config.league import league_config_from_sleeper
from nuclearff.config.models import RecencyWeights
from nuclearff.pipeline.auction_board import (
    add_vorp_all_positions,
    project_points,
    score_seasons,
)
from nuclearff.scoring.engine import ScoringEngine


def _seasonal_frame() -> pl.DataFrame:
    """Two players across three seasons, with the identity columns a board needs."""
    return pl.DataFrame(
        {
            "player_id": ["a", "a", "a", "b", "b", "b"],
            "player_display_name": ["A Back"] * 3 + ["B Wideout"] * 3,
            "position": ["RB"] * 3 + ["WR"] * 3,
            "recent_team": ["DET", "DET", "DET", "CIN", "CIN", "SEA"],
            "headshot_url": ["http://x/a.png"] * 3 + ["http://x/b.png"] * 3,
            "season": [2023, 2024, 2025, 2023, 2024, 2025],
            "games": [17, 17, 16, 17, 17, 15],
            "receptions": [50, 60, 70, 80, 90, 100],
            "receiving_yards": [400.0, 500.0, 600.0, 1000.0, 1100.0, 1200.0],
            "receiving_tds": [2, 3, 4, 8, 9, 10],
            "rushing_yards": [1000.0, 1100.0, 1200.0, 0.0, 0.0, 0.0],
            "rushing_tds": [10, 11, 12, 0, 0, 0],
        }
    )


def test_score_seasons_adds_league_scored_points(league_payload):
    cfg = league_config_from_sleeper(league_payload)
    scored = score_seasons(_seasonal_frame(), ScoringEngine(cfg.scoring))

    assert "fantasy_points" in scored.columns
    assert scored["fantasy_points"].null_count() == 0


def test_project_points_recency_weights_and_keeps_identity(league_payload):
    """The blend keeps name/position/team/headshot, which the rate fn drops."""
    cfg = league_config_from_sleeper(league_payload)
    scored = score_seasons(_seasonal_frame(), ScoringEngine(cfg.scoring))

    projected = project_points(scored, 2026, RecencyWeights())

    assert projected.height == 2
    for column in (
        "value_estimate",
        "player_display_name",
        "position",
        "recent_team",
        "headshot_url",
        "games",
        "points_per_game",
    ):
        assert column in projected.columns
    assert projected["position"].null_count() == 0


def test_project_points_uses_the_most_recent_team(league_payload):
    """A player who changed teams is shown on the team he finished on."""
    cfg = league_config_from_sleeper(league_payload)
    scored = score_seasons(_seasonal_frame(), ScoringEngine(cfg.scoring))

    projected = project_points(scored, 2026, RecencyWeights())
    by_id = dict(zip(projected["player_id"], projected["recent_team"], strict=True))

    assert by_id["b"] == "SEA"  # 2025 team, not the 2023/2024 CIN


def test_project_points_adds_one_column_per_season(league_payload):
    cfg = league_config_from_sleeper(league_payload)
    scored = score_seasons(_seasonal_frame(), ScoringEngine(cfg.scoring))

    projected = project_points(scored, 2026, RecencyWeights())

    for season in (2023, 2024, 2025):
        assert f"fantasy_points_{season}" in projected.columns


def test_project_points_hand_computed_weighting(league_payload):
    """0.5/0.3/0.2 newest-first over three known season totals."""
    cfg = league_config_from_sleeper(league_payload)
    scored = pl.DataFrame(
        {
            "player_id": ["a", "a", "a"],
            "position": ["RB"] * 3,
            "season": [2023, 2024, 2025],
            "games": [17, 17, 17],
            "fantasy_points": [100.0, 200.0, 300.0],
        }
    )

    projected = project_points(scored, 2026, RecencyWeights())

    # 300*0.5 + 200*0.3 + 100*0.2 = 230
    assert projected["value_estimate"][0] == pytest.approx(230.0)
    assert cfg is not None


def test_add_vorp_all_positions_values_every_position(league_payload):
    """Each position gets its own replacement level, not one shared number."""
    cfg = league_config_from_sleeper(league_payload)
    frame = pl.DataFrame(
        {
            "player_id": [f"p{i}" for i in range(8)],
            "position": ["QB", "QB", "RB", "RB", "WR", "WR", "TE", "TE"],
            "value_estimate": [400.0, 300.0, 250.0, 150.0, 240.0, 140.0, 200.0, 100.0],
        }
    )

    result = add_vorp_all_positions(frame, cfg)

    assert "replacement_value_position" in result.columns
    assert result["vorp"].null_count() == 0
    # Each position's replacement is drawn from its own (shallow) pool, so the
    # worst player at each position sits exactly at replacement level.
    by_id = dict(zip(result["player_id"], result["vorp"], strict=True))
    assert by_id["p1"] == pytest.approx(0.0)  # worst QB
    assert by_id["p3"] == pytest.approx(0.0)  # worst RB


def test_add_vorp_all_positions_leaves_other_positions_null(league_payload):
    """A kicker in the frame is not silently valued against a skill baseline."""
    cfg = league_config_from_sleeper(league_payload)
    frame = pl.DataFrame(
        {
            "player_id": ["a", "b", "k"],
            "position": ["WR", "WR", "K"],
            "value_estimate": [200.0, 100.0, 150.0],
        }
    )

    result = add_vorp_all_positions(frame, cfg, positions=("WR",))
    by_id = dict(zip(result["player_id"], result["vorp"], strict=True))

    assert by_id["k"] is None


def test_add_vorp_all_positions_skips_missing_positions(league_payload, caplog):
    """A position with no players warns instead of raising."""
    cfg = league_config_from_sleeper(league_payload)
    frame = pl.DataFrame(
        {
            "player_id": ["a"],
            "position": ["WR"],
            "value_estimate": [200.0],
        }
    )

    with caplog.at_level("WARNING", logger="nuclearff.pipeline.auction_board"):
        result = add_vorp_all_positions(frame, cfg, positions=("WR", "QB"))

    assert result.height == 1
    assert any("no players at position" in r.message for r in caplog.records)
