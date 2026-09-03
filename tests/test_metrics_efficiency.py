"""Unit tests for WR efficiency metrics: the routes-run join, YPRR, TPRR.

Every fixture here is a small, hand-built synthetic Polars DataFrame with
realistic column names/dtypes (matching the shapes documented in
`nuclearff.nflverse.stats`), never live network - `tests/conftest.py`'s
autouse `_no_network` fixture would fail any test that tried.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

from nuclearff.metrics.efficiency import (
    ambiguous_player_id_pairs,
    join_routes,
    tprr,
    yprr,
)


def _players_with_name_collision_and_ambiguity() -> pl.DataFrame:
    """A players (load_players()-shaped) frame covering every join edge case.

    Two different real players share the display name "Justin Jefferson"
    with different (gsis_id, pfr_id) pairs - mirroring the real collision
    confirmed live against nflreadpy's load_players() this session (a
    Vikings WR and an unrelated linebacker). "Ambiguous Guy A"/"B" share one
    pfr_id pointing at two different gsis_ids, the case join_routes must
    reject rather than guess.
    """
    return pl.DataFrame(
        {
            "gsis_id": ["00-01", "00-02", "00-03", "00-04", "00-06", "00-07"],
            "pfr_id": ["AAAA00", "BBBB00", "CCCC00", "EEEE00", "FFFF00", "FFFF00"],
            "display_name": [
                "Justin Jefferson",
                "Justin Jefferson",
                "Traded Guy",
                "No Routes Guy",
                "Ambiguous Guy A",
                "Ambiguous Guy B",
            ],
            "position": ["WR", "WR", "WR", "WR", "WR", "WR"],
        }
    )


def _routes() -> pl.DataFrame:
    """Season-grain routes data: two matched players, a traded player split
    across two teams in the same season, and a pfr_id that is ambiguous in
    the players table above.
    """
    return pl.DataFrame(
        {
            "season": [2024, 2024, 2024, 2024, 2024],
            "pfr_player_id": ["AAAA00", "BBBB00", "CCCC00", "CCCC00", "FFFF00"],
            "player": [
                "Justin Jefferson",
                "Justin Jefferson",
                "Traded Guy",
                "Traded Guy",
                "Ambiguous Guy",
            ],
            "position": ["WR", "WR", "WR", "WR", "WR"],
            "team": ["MIN", "XXX", "OLD", "NEW", "ZZZ"],
            "routes_run": [520.0, 480.0, 150.0, 100.0, 999.0],
            "source": ["offense_snaps_proxy"] * 5,
        }
    )


def _seasonal_receiving() -> pl.DataFrame:
    """Season-grain receiving data (gsis_id-keyed player_id, no 'week' column)."""
    return pl.DataFrame(
        {
            "season": [2024, 2024, 2024, 2024, 2024, 2024],
            "player_id": ["00-01", "00-02", "00-03", "00-04", "00-06", "00-07"],
            "receiving_yards": [1400.0, 900.0, 700.0, 500.0, 300.0, 300.0],
            "targets": [180, 140, 110, 90, 60, 60],
        }
    )


class TestAmbiguousPlayerIdPairs:
    def test_finds_the_shared_pfr_id(self):
        result = ambiguous_player_id_pairs(_players_with_name_collision_and_ambiguity())

        assert set(result["pfr_id"].to_list()) == {"FFFF00"}
        assert set(result["gsis_id"].to_list()) == {"00-06", "00-07"}

    def test_name_collision_alone_is_not_ambiguous(self):
        """Two players sharing a display_name but each with their own unique
        (pfr_id, gsis_id) pair - Justin Jefferson x2 - is NOT reported: only
        an actual ID collision counts.
        """
        result = ambiguous_player_id_pairs(_players_with_name_collision_and_ambiguity())

        assert "AAAA00" not in result["pfr_id"].to_list()
        assert "BBBB00" not in result["pfr_id"].to_list()


class TestJoinRoutes:
    def test_name_collision_does_not_cause_a_wrong_match(self):
        """Both 'Justin Jefferson's get their OWN player's routes_run, keyed
        by ID - never by the shared display_name.
        """
        result = join_routes(
            _seasonal_receiving(),
            _routes(),
            _players_with_name_collision_and_ambiguity(),
        )
        by_id = dict(zip(result["player_id"], result["routes_run"], strict=True))

        assert by_id["00-01"] == pytest.approx(520.0)
        assert by_id["00-02"] == pytest.approx(480.0)
        assert by_id["00-01"] != by_id["00-02"]

    def test_traded_player_routes_summed_across_teams(self):
        """CCCC00 has two routes rows in the same season (a mid-season trade);
        join_routes sums them into one season total rather than picking one.
        """
        result = join_routes(
            _seasonal_receiving(),
            _routes(),
            _players_with_name_collision_and_ambiguity(),
        )
        row = result.filter(pl.col("player_id") == "00-03").row(0, named=True)

        assert row["routes_run"] == pytest.approx(150.0 + 100.0)

    def test_player_with_no_routes_row_gets_null_not_dropped(self):
        """A receiving player load_routes has no row for stays in the output
        with null routes_run/routes_source, rather than disappearing.
        """
        result = join_routes(
            _seasonal_receiving(),
            _routes(),
            _players_with_name_collision_and_ambiguity(),
        )

        assert result.height == _seasonal_receiving().height
        row = result.filter(pl.col("player_id") == "00-04").row(0, named=True)
        assert row["routes_run"] is None
        assert row["routes_source"] is None

    def test_ambiguous_pfr_id_excluded_and_logged(self, caplog):
        """Both players sharing the ambiguous pfr_id get null routes_run,
        even though a routes row exists for that pfr_id - and a warning
        names what was excluded.
        """
        with caplog.at_level(logging.WARNING, logger="nuclearff.metrics.efficiency"):
            result = join_routes(
                _seasonal_receiving(),
                _routes(),
                _players_with_name_collision_and_ambiguity(),
            )

        for player_id in ("00-06", "00-07"):
            row = result.filter(pl.col("player_id") == player_id).row(0, named=True)
            assert row["routes_run"] is None

        assert any("ambiguous" in r.message.lower() for r in caplog.records)
        assert any("FFFF00" in r.message for r in caplog.records)

    def test_raises_on_weekly_grain_receiving_input(self):
        """A 'week' column in receiving means season-grain routes_run would
        be silently repeated onto every weekly row - reject it instead.
        """
        weekly = _seasonal_receiving().with_columns(week=pl.lit(1))
        with pytest.raises(ValueError, match="seasonal"):
            join_routes(weekly, _routes(), _players_with_name_collision_and_ambiguity())

    def test_missing_required_column_raises(self):
        broken_routes = _routes().drop("routes_run")
        with pytest.raises(ValueError, match="join_routes.*routes_run"):
            join_routes(
                _seasonal_receiving(),
                broken_routes,
                _players_with_name_collision_and_ambiguity(),
            )


def _routes_run_frame() -> pl.DataFrame:
    """Receiving data already carrying routes_run/routes_source, as
    join_routes() would produce - one row above min_routes, one below, and
    one with a null routes_run (no match found upstream).
    """
    return pl.DataFrame(
        {
            "player_id": ["a", "b", "c"],
            "receiving_yards": [600.0, 100.0, 50.0],
            "targets": [90, 20, 10],
            "routes_run": [300.0, 150.0, None],
            "routes_source": ["offense_snaps_proxy", "offense_snaps_proxy", None],
        }
    )


class TestYprr:
    def test_matches_hand_computation_above_min_routes(self):
        result = yprr(_routes_run_frame())
        row = result.filter(pl.col("player_id") == "a").row(0, named=True)
        assert row["yprr"] == pytest.approx(600.0 / 300.0)

    def test_below_min_routes_is_null(self):
        result = yprr(_routes_run_frame())
        row = result.filter(pl.col("player_id") == "b").row(0, named=True)
        assert row["yprr"] is None

    def test_null_routes_run_is_null(self):
        result = yprr(_routes_run_frame())
        row = result.filter(pl.col("player_id") == "c").row(0, named=True)
        assert row["yprr"] is None

    def test_custom_min_routes_threshold(self):
        result = yprr(_routes_run_frame(), min_routes=100)
        row = result.filter(pl.col("player_id") == "b").row(0, named=True)
        assert row["yprr"] == pytest.approx(100.0 / 150.0)

    def test_missing_routes_run_column_raises(self):
        broken = _routes_run_frame().drop("routes_run")
        with pytest.raises(ValueError, match="yprr.*join_routes"):
            yprr(broken)

    def test_carries_routes_source_through(self):
        result = yprr(_routes_run_frame())
        assert result["routes_source"].to_list() == [
            "offense_snaps_proxy",
            "offense_snaps_proxy",
            None,
        ]


class TestTprr:
    def test_matches_hand_computation_above_min_routes(self):
        result = tprr(_routes_run_frame())
        row = result.filter(pl.col("player_id") == "a").row(0, named=True)
        assert row["tprr"] == pytest.approx(90.0 / 300.0)

    def test_below_min_routes_is_null(self):
        result = tprr(_routes_run_frame())
        row = result.filter(pl.col("player_id") == "b").row(0, named=True)
        assert row["tprr"] is None

    def test_missing_routes_run_column_raises(self):
        broken = _routes_run_frame().drop("routes_run")
        with pytest.raises(ValueError, match="tprr.*join_routes"):
            tprr(broken)

    def test_carries_routes_source_through(self):
        result = tprr(_routes_run_frame())
        assert result["routes_source"].to_list() == [
            "offense_snaps_proxy",
            "offense_snaps_proxy",
            None,
        ]
