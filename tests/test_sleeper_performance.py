"""Unit tests for nuclearff.sleeper.performance."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nuclearff.config.league import ScoringSettings
from nuclearff.sleeper.performance import weekly_actuals, weekly_performance


def _matchups(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "week": pl.Int64,
        "roster_id": pl.Int64,
        "matchup_id": pl.Int64,
        "points": pl.Float64,
        "starters": pl.String,
        "players_points": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def _standings(rows: list[dict]) -> pl.DataFrame:
    schema = {"league_id": pl.String, "roster_id": pl.Int64, "display_name": pl.String}
    return pl.DataFrame(rows, schema=schema)


def _players(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "player_id": pl.String,
        "full_name": pl.String,
        "last_name": pl.String,
        "position": pl.String,
        "team": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def _projections(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "season": pl.Int64,
        "week": pl.Int64,
        "player_id": pl.String,
        "stats": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


# --- weekly_actuals -----------------------------------------------------------


def test_weekly_actuals_explodes_players_points():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 20.5,
                "starters": json.dumps(["100"]),
                "players_points": json.dumps({"100": 20.5, "200": 3.0}),
            }
        ]
    )
    standings = _standings(
        [{"league_id": "L1", "roster_id": 1, "display_name": "nolmacdonald"}]
    )

    actuals = weekly_actuals(matchups, standings, "L1", 1).sort("player_id")

    assert actuals["player_id"].to_list() == ["100", "200"]
    assert actuals["is_starter"].to_list() == [True, False]
    assert actuals["manager"].to_list() == ["nolmacdonald", "nolmacdonald"]


def test_weekly_actuals_skips_a_roster_with_no_resolvable_manager():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 20.5,
                "starters": json.dumps(["100"]),
                "players_points": json.dumps({"100": 20.5}),
            }
        ]
    )
    standings = _standings([])

    actuals = weekly_actuals(matchups, standings, "L1", 1)

    assert actuals.height == 0


def test_weekly_actuals_filters_to_the_requested_league_and_week():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 20.5,
                "starters": json.dumps(["100"]),
                "players_points": json.dumps({"100": 20.5}),
            },
            {
                "league_id": "L2",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 5.0,
                "starters": json.dumps(["999"]),
                "players_points": json.dumps({"999": 5.0}),
            },
        ]
    )
    standings = _standings(
        [
            {"league_id": "L1", "roster_id": 1, "display_name": "nolmacdonald"},
            {"league_id": "L2", "roster_id": 1, "display_name": "someoneelse"},
        ]
    )

    actuals = weekly_actuals(matchups, standings, "L1", 1)

    assert actuals["player_id"].to_list() == ["100"]


# --- weekly_performance --------------------------------------------------------


def _actuals(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "week": pl.Int64,
        "roster_id": pl.Int64,
        "manager": pl.String,
        "player_id": pl.String,
        "is_starter": pl.Boolean,
        "actual_points": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def test_weekly_performance_computes_delta_under_league_scoring():
    actuals = _actuals(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "manager": "nolmacdonald",
                "player_id": "100",
                "is_starter": True,
                "actual_points": 5.0,
            }
        ]
    )
    projections = _projections(
        [
            {
                "season": 2025,
                "week": 1,
                "player_id": "100",
                "stats": json.dumps({"rec_yd": 100.0}),
            }
        ]
    )
    players = _players(
        [
            {
                "player_id": "100",
                "full_name": "Drake Maye",
                "last_name": "Maye",
                "position": "QB",
                "team": "NE",
            }
        ]
    )
    scoring = ScoringSettings(values={"rec_yd": 0.1})

    performance = weekly_performance(actuals, projections, players, scoring)

    row = performance.row(0, named=True)
    assert row["projected_points"] == pytest.approx(10.0)
    assert row["actual_points"] == 5.0
    assert row["delta"] == pytest.approx(-5.0)
    assert row["player_name"] == "Drake Maye"


def test_weekly_performance_excludes_a_player_with_no_projection():
    actuals = _actuals(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "manager": "nolmacdonald",
                "player_id": "100",
                "is_starter": True,
                "actual_points": 5.0,
            }
        ]
    )
    projections = _projections([])
    players = _players([])
    scoring = ScoringSettings(values={"rec_yd": 0.1})

    performance = weekly_performance(actuals, projections, players, scoring)

    assert performance.height == 0


def test_weekly_performance_starters_only_filters_bench_players():
    actuals = _actuals(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "manager": "nolmacdonald",
                "player_id": "100",
                "is_starter": False,
                "actual_points": 5.0,
            }
        ]
    )
    projections = _projections(
        [
            {
                "season": 2025,
                "week": 1,
                "player_id": "100",
                "stats": json.dumps({"rec_yd": 100.0}),
            }
        ]
    )
    players = _players([])
    scoring = ScoringSettings(values={"rec_yd": 0.1})

    default = weekly_performance(actuals, projections, players, scoring)
    with_bench = weekly_performance(
        actuals, projections, players, scoring, starters_only=False
    )

    assert default.height == 0
    assert with_bench.height == 1


def test_weekly_performance_falls_back_to_player_id_with_no_name():
    actuals = _actuals(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "manager": "nolmacdonald",
                "player_id": "100",
                "is_starter": True,
                "actual_points": 5.0,
            }
        ]
    )
    projections = _projections(
        [{"season": 2025, "week": 1, "player_id": "100", "stats": json.dumps({})}]
    )
    players = _players([])
    scoring = ScoringSettings(values={})

    performance = weekly_performance(actuals, projections, players, scoring)

    assert performance.row(0, named=True)["player_name"] == "100"
