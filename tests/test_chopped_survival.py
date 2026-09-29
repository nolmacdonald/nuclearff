"""Unit tests for nuclearff.chopped.survival and chopped.common (issue #226)."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nuclearff.chopped.common import chopped_leagues, manager_names
from nuclearff.chopped.survival import (
    SURVIVAL_COLUMNS,
    chop_line_problems,
    weekly_survival,
)
from nuclearff.exceptions import ChoppedLeagueError

LEAGUE_ID = "chop1"

# Four rosters. Roster 4 is chopped in week 1, roster 3 in week 2, roster 2
# in week 3; roster 1 wins. The league's last processed chop is week 3, so
# week 4's rows (every roster scoring again, as after a real final chop) are
# ignored.
POINTS = {
    1: {1: 110.0, 2: 120.0, 3: 130.0, 4: 90.0},
    2: {1: 100.0, 2: 105.0, 3: 90.0, 4: 150.0},
    3: {1: 95.0, 2: 80.0, 3: 0.0, 4: 160.0},  # 0.0 after its chop
    4: {1: 70.0, 2: 0.0, 3: 0.0, 4: 170.0},
}
ELIMINATED = {1: None, 2: 3, 3: 2, 4: 1}


def _matchups(points=POINTS) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "league_id": LEAGUE_ID,
                "season": 2025,
                "week": week,
                "roster_id": roster_id,
                "matchup_id": roster_id,
                "points": value,
            }
            for roster_id, weeks in points.items()
            for week, value in weeks.items()
        ]
    )


def _chopped_rosters(eliminated=ELIMINATED) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "league_id": LEAGUE_ID,
                "season": 2025,
                "roster_id": roster_id,
                "owner_id": f"u{roster_id}",
                "eliminated_leg": leg,
                "waiver_budget_used": 0,
            }
            for roster_id, leg in eliminated.items()
        ],
        schema_overrides={"eliminated_leg": pl.Int64},
    )


def _leagues(league_type: int = 3, last_chopped_leg: int = 3) -> pl.DataFrame:
    settings = {"type": league_type, "waiver_budget": 1000}
    if league_type == 3:
        settings["last_chopped_leg"] = last_chopped_leg
    return pl.DataFrame(
        [
            {
                "league_id": LEAGUE_ID,
                "season": 2025,
                "total_rosters": 4,
                "settings": json.dumps(settings),
            }
        ]
    )


def _standings() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "league_id": LEAGUE_ID,
                "season": 2025,
                "roster_id": roster_id,
                "owner_id": f"u{roster_id}",
                "display_name": f"manager{roster_id}",
            }
            for roster_id in POINTS
        ]
    )


def _survival(**kwargs) -> pl.DataFrame:
    return weekly_survival(
        kwargs.get("matchups", _matchups()),
        kwargs.get("chopped_rosters", _chopped_rosters()),
        kwargs.get("leagues", _leagues()),
        kwargs.get("standings", _standings()),
        strict=kwargs.get("strict", True),
    )


def _row(frame: pl.DataFrame, week: int, roster_id: int) -> dict:
    return frame.filter(
        (pl.col("week") == week) & (pl.col("roster_id") == roster_id)
    ).row(0, named=True)


# --- common ------------------------------------------------------------------


def test_chopped_leagues_reads_budget_and_last_chop_from_settings():
    leagues = chopped_leagues(_leagues())
    assert leagues.row(0, named=True) == {
        "league_id": LEAGUE_ID,
        "season": 2025,
        "total_rosters": 4,
        "waiver_budget": 1000,
        "last_chopped_leg": 3,
    }


def test_chopped_leagues_skips_a_normal_league():
    assert chopped_leagues(_leagues(league_type=0)).height == 0


def test_manager_names_uses_the_latest_season_name():
    """An owner who used another name for one season is one manager."""
    standings = pl.DataFrame(
        [
            {"season": 2022, "owner_id": "u1", "display_name": "old_name"},
            {"season": 2025, "owner_id": "u1", "display_name": "new_name"},
            {"season": 2025, "owner_id": None, "display_name": None},
        ]
    )
    assert manager_names(standings) == {"u1": "new_name"}


# --- weekly_survival -----------------------------------------------------------


def test_only_alive_rosters_and_processed_weeks():
    """Chopped rosters' 0.0 rows and week 4 (after the last chop) are dropped."""
    survival = _survival()
    counts = dict(survival.group_by("week").len().sort("week").iter_rows())
    assert counts == {1: 4, 2: 3, 3: 2}
    assert survival.columns == list(SURVIVAL_COLUMNS)
    assert survival.filter(pl.col("points") == 0).height == 0


def test_chop_line_is_the_lowest_alive_score_not_zero():
    survival = _survival()
    lines = dict(survival.select("week", "chop_line").unique().sort("week").iter_rows())
    assert lines == {1: 70.0, 2: 80.0, 3: 90.0}


def test_chopped_flag_margin_and_gap_to_safety():
    survival = _survival()
    chopped = _row(survival, 1, 4)
    assert chopped["chopped"] is True
    assert chopped["margin"] == 0.0
    assert chopped["gap_to_safety"] == pytest.approx(25.0)  # 95 - 70
    survivor = _row(survival, 1, 1)
    assert survivor["chopped"] is False
    assert survivor["margin"] == pytest.approx(40.0)
    assert survivor["margin_pct"] == pytest.approx(40.0 / 70.0)
    assert survivor["gap_to_safety"] is None


def test_rank_percentile_and_z_score():
    survival = _survival()
    week1 = survival.filter(pl.col("week") == 1)
    assert week1["rank"].to_list() == [1, 2, 3, 4]
    assert week1["percentile"].to_list() == pytest.approx([1.0, 0.75, 0.5, 0.25])
    values = [110.0, 100.0, 95.0, 70.0]
    mean = sum(values) / 4
    sd = (sum((v - mean) ** 2 for v in values) / 4) ** 0.5  # population
    top = _row(survival, 1, 1)
    assert top["week_sd"] == pytest.approx(sd)
    assert top["z_chop"] == pytest.approx(40.0 / sd)


def test_two_alive_rosters_give_a_survivor_z_of_exactly_two():
    """The late-week effect the luck index guards against (#228)."""
    assert _row(_survival(), 3, 1)["z_chop"] == pytest.approx(2.0)


def test_managers_come_from_owner_ids():
    survival = _survival()
    assert _row(survival, 1, 2)["manager"] == "manager2"
    assert _row(survival, 1, 2)["owner_id"] == "u2"


def test_in_progress_season_stops_at_the_last_processed_chop():
    survival = _survival(leagues=_leagues(last_chopped_leg=1))
    assert survival["week"].unique().to_list() == [1]


# --- errors ----------------------------------------------------------------------


def test_a_normal_league_raises():
    with pytest.raises(ChoppedLeagueError, match="No Chopped-format league"):
        _survival(leagues=_leagues(league_type=0))


def test_missing_chopped_roster_rows_raise():
    empty = _chopped_rosters().clear()
    with pytest.raises(ChoppedLeagueError, match="sleeper_chopped_rosters"):
        _survival(chopped_rosters=empty)


def test_chopped_roster_that_was_not_lowest_raises():
    """If Sleeper chopped someone else, the numbers can't be trusted."""
    wrong = {**ELIMINATED, 3: 1, 4: 2}  # roster 3 (95.0) "chopped" in week 1
    with pytest.raises(ChoppedLeagueError, match="wasn't the lowest scorer"):
        _survival(chopped_rosters=_chopped_rosters(wrong))


def test_non_strict_mode_logs_instead_of_raising(caplog):
    wrong = {**ELIMINATED, 3: 1, 4: 2}
    with caplog.at_level("WARNING", logger="nuclearff.chopped.survival"):
        survival = _survival(chopped_rosters=_chopped_rosters(wrong), strict=False)
    assert survival.height
    assert "wasn't the lowest scorer" in caplog.text


def test_chop_line_problems_flags_a_tie_for_lowest():
    points = {**POINTS, 3: {**POINTS[3], 1: 70.0}}  # roster 3 ties roster 4
    survival = _survival(matchups=_matchups(points), strict=False)
    problems = chop_line_problems(survival)
    assert problems["problem"].to_list() == ["alive rosters tied for the lowest score"]
    assert problems["week"].to_list() == [1]
