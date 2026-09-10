"""Unit tests for nuclearff.sleeper.performance."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nuclearff.config.league import ScoringSettings
from nuclearff.sleeper.performance import (
    season_actuals,
    season_summary,
    weekly_actuals,
    weekly_performance,
)


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


def test_weekly_performance_fills_a_null_team_rather_than_rendering_nan():
    """Real bug caught rendering the season report against this league's
    real 2025 data: a real, legitimate null team (a current free agent, per
    Sleeper's player map, which reflects today's rosters not the reported
    season's) crossed the pandas/plottable boundary as the literal string
    "nan" instead of a blank or placeholder."""
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
    players = _players(
        [
            {
                "player_id": "100",
                "full_name": "Nick Chubb",
                "last_name": "Chubb",
                "position": "RB",
                "team": None,
            }
        ]
    )
    scoring = ScoringSettings(values={})

    performance = weekly_performance(actuals, projections, players, scoring)

    assert performance.row(0, named=True)["team"] == "FA"


# --- season_actuals -------------------------------------------------------


def test_season_actuals_spans_every_week_present():
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
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "matchup_id": 5,
                "points": 15.0,
                "starters": json.dumps(["100"]),
                "players_points": json.dumps({"100": 15.0}),
            },
        ]
    )
    standings = _standings(
        [{"league_id": "L1", "roster_id": 1, "display_name": "nolmacdonald"}]
    )

    actuals = season_actuals(matchups, standings, "L1").sort("week")

    assert actuals["week"].to_list() == [1, 2]
    assert actuals["actual_points"].to_list() == [20.5, 15.0]


def test_season_actuals_filters_to_the_requested_league():
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
                "season": 2024,
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
            {"league_id": "L2", "roster_id": 1, "display_name": "nolmacdonald"},
        ]
    )

    actuals = season_actuals(matchups, standings, "L1")

    assert actuals["player_id"].to_list() == ["100"]


# --- season_summary --------------------------------------------------------


def _performance_rows(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "week": pl.Int64,
        "roster_id": pl.Int64,
        "manager": pl.String,
        "player_id": pl.String,
        "is_starter": pl.Boolean,
        "actual_points": pl.Float64,
        "player_name": pl.String,
        "position": pl.String,
        "team": pl.String,
        "projected_points": pl.Float64,
        "delta": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def _perf_row(**overrides) -> dict:
    row = {
        "league_id": "L1",
        "season": 2025,
        "week": 1,
        "roster_id": 1,
        "manager": "nolmacdonald",
        "player_id": "100",
        "is_starter": True,
        "actual_points": 10.0,
        "player_name": "Drake Maye",
        "position": "QB",
        "team": "NE",
        "projected_points": 15.0,
        "delta": -5.0,
    }
    row.update(overrides)
    return row


def test_season_summary_averages_across_weeks():
    performance = _performance_rows(
        [
            _perf_row(week=1, actual_points=10.0, projected_points=15.0, delta=-5.0),
            _perf_row(week=2, actual_points=20.0, projected_points=15.0, delta=5.0),
            _perf_row(week=3, actual_points=15.0, projected_points=15.0, delta=0.0),
        ]
    )

    summary = season_summary(performance, min_games=1)

    row = summary.row(0, named=True)
    assert row["games"] == 3
    assert row["avg_actual_points"] == pytest.approx(15.0)
    assert row["avg_projected_points"] == pytest.approx(15.0)
    assert row["avg_delta"] == pytest.approx(0.0)


def test_season_summary_excludes_players_below_min_games():
    performance = _performance_rows(
        [
            _perf_row(player_id="100", week=1),
            _perf_row(player_id="200", week=1, delta=-50.0),
            _perf_row(player_id="200", week=2, delta=-50.0),
            _perf_row(player_id="200", week=3, delta=-50.0),
        ]
    )

    summary = season_summary(performance, min_games=3)

    assert summary["player_id"].to_list() == ["200"]


def test_season_summary_uses_the_most_recent_weeks_manager_and_team():
    """A real in-season trade: the same player rostered by two different
    managers/teams across the season should report the most recent one, not
    the first."""
    performance = _performance_rows(
        [
            _perf_row(week=1, manager="old_manager", team="OLD"),
            _perf_row(week=2, manager="new_manager", team="NEW"),
        ]
    )

    summary = season_summary(performance, min_games=1)

    row = summary.row(0, named=True)
    assert row["manager"] == "new_manager"
    assert row["team"] == "NEW"


def test_season_summary_sorts_by_avg_delta_descending():
    performance = _performance_rows(
        [
            _perf_row(player_id="100", week=1, delta=-10.0),
            _perf_row(player_id="200", week=1, delta=10.0),
        ]
    )

    summary = season_summary(performance, min_games=1)

    assert summary["player_id"].to_list() == ["200", "100"]


def test_season_summary_empty_input_returns_empty_frame():
    summary = season_summary(_performance_rows([]), min_games=1)

    assert summary.height == 0
