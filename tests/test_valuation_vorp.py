"""Unit tests for nuclearff.valuation.vorp: VORP, VOLS, and VONA.

Per the brain's routing conventions this project's tests already follow
(``tests/test_config_league.py``, ``tests/test_metrics_volume.py``): small,
hand-computable synthetic Polars frames, and - for anything that needs a
:class:`~nuclearff.config.league.LeagueConfig` - the real committed Sleeper
fixture via ``league_config_from_sleeper(league_payload)`` rather than a
hand-built fake league object. ``tests/conftest.py``'s autouse ``_no_network``
fixture would fail any test that reached the network; none of these do.

The real fixture league is a 10-team, 2-WR/3-FLEX league whose WR replacement
ranks are already hand-verified in ``tests/test_config_league.py``:
``replacement_rank("WR", "vols") == 35`` and, since ``vorp`` baseline adds
30% of total bench slots (6 bench * 10 teams * 0.3 = 18),
``replacement_rank("WR", "vorp") == 53``. Both ranks are deeper than any
small synthetic roster this file builds, which is deliberate: it means these
tests exercise the graceful too-shallow-pool fallback (and its warning) by
construction, using real league math rather than an invented one.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

from nuclearff.config.league import league_config_from_sleeper
from nuclearff.valuation.vorp import replacement_points, vona, vorp


def _wr_and_rb_frame() -> pl.DataFrame:
    """40 WRs at proj_points 100..61 (descending, one point apart) plus 2 RBs.

    Values are deliberately spaced by exactly 1.0 so the value at any rank N
    is hand-computable as ``100 - (N - 1)`` without needing the frame to
    already be sorted - `player_id` order here is index order, matching
    proj_points order, but the functions under test must not assume that.
    """
    positions = ["WR"] * 40 + ["RB", "RB"]
    proj_points = [float(x) for x in range(100, 60, -1)] + [80.0, 70.0]
    player_ids = [f"wr{i}" for i in range(1, 41)] + ["rb1", "rb2"]
    return pl.DataFrame(
        {"player_id": player_ids, "position": positions, "proj_points": proj_points}
    )


class TestReplacementPoints:
    def test_matches_hand_computation_vols(self, league_payload):
        """VOLS rank 35 on a 40-player pool spaced 1.0 apart -> value 66.0.

        Rank 35 (1-indexed) of proj_points [100, 99, ..., 61] is
        100 - (35 - 1) = 66.0, and with 40 WRs present this rank is fully
        satisfied - no shallow-pool fallback fires.
        """
        cfg = league_config_from_sleeper(league_payload)
        assert cfg.replacement_rank("WR", "vols") == 35

        result = replacement_points(
            _wr_and_rb_frame(), cfg, position="WR", baseline="vols"
        )
        assert result == pytest.approx(66.0)

    def test_shallow_pool_falls_back_to_worst_available_and_warns(
        self, league_payload, caplog
    ):
        """VORP baseline needs rank 53; only 40 WRs exist -> falls back to WR40.

        The worst-ranked available player (wr40, proj_points 61.0) is
        returned instead of raising or indexing out of bounds, and a warning
        is logged naming both the requested rank and the shallower pool size.
        """
        cfg = league_config_from_sleeper(league_payload)
        assert cfg.replacement_rank("WR", "vorp") == 53

        with caplog.at_level(logging.WARNING, logger="nuclearff.valuation.vorp"):
            result = replacement_points(
                _wr_and_rb_frame(), cfg, position="WR", baseline="vorp"
            )

        assert result == pytest.approx(61.0)
        assert any(
            "53" in r.message and "40" in r.message and "too shallow" in r.message
            for r in caplog.records
        )

    def test_very_shallow_pool_still_does_not_crash(self, league_payload, caplog):
        """A tiny 5-WR pool against a real 35-rank league still returns a value."""
        cfg = league_config_from_sleeper(league_payload)
        df = pl.DataFrame(
            {
                "player_id": ["a", "b", "c", "d", "e"],
                "position": ["WR"] * 5,
                "proj_points": [50.0, 40.0, 30.0, 20.0, 10.0],
            }
        )

        with caplog.at_level(logging.WARNING, logger="nuclearff.valuation.vorp"):
            result = replacement_points(df, cfg, position="WR", baseline="vols")

        assert result == pytest.approx(10.0)  # worst-ranked of the 5 available
        assert len(caplog.records) == 1

    def test_missing_required_column_raises(self, league_payload):
        cfg = league_config_from_sleeper(league_payload)
        broken = _wr_and_rb_frame().drop("proj_points")
        with pytest.raises(ValueError, match="replacement_points.*proj_points"):
            replacement_points(broken, cfg, position="WR")

    def test_empty_pool_at_position_raises(self, league_payload):
        cfg = league_config_from_sleeper(league_payload)
        no_wrs = _wr_and_rb_frame().filter(pl.col("position") != "WR")
        with pytest.raises(ValueError, match="no players found"):
            replacement_points(no_wrs, cfg, position="WR")

    def test_custom_proj_column_name(self, league_payload):
        """A caller-supplied proj_column name is honored, not hardcoded."""
        cfg = league_config_from_sleeper(league_payload)
        df = _wr_and_rb_frame().rename({"proj_points": "custom_score"})

        result = replacement_points(
            df, cfg, position="WR", baseline="vols", proj_column="custom_score"
        )
        assert result == pytest.approx(66.0)


class TestReplacementLevelBeyondWr:
    def test_rb_position_no_longer_raises(self, league_payload):
        """replacement_rank's WR-only gate was removed (see decisions.md);
        RB now computes a real replacement level instead of raising.
        """
        cfg = league_config_from_sleeper(league_payload)
        result = replacement_points(
            _wr_and_rb_frame(), cfg, position="RB", baseline="vols"
        )
        assert result == pytest.approx(70.0)  # worst-ranked (rb2) of the 2 RBs


class TestVorp:
    def test_matches_hand_computation_and_non_wr_rows_get_null(self, league_payload):
        """vorp = proj_points - 66.0 for WRs; RB rows pass through with null vorp."""
        cfg = league_config_from_sleeper(league_payload)

        result = vorp(_wr_and_rb_frame(), cfg, position="WR", baseline="vols")
        by_id = dict(zip(result["player_id"], result["vorp"], strict=True))

        assert by_id["wr1"] == pytest.approx(34.0)  # 100 - 66
        assert by_id["wr35"] == pytest.approx(0.0)  # 66 - 66
        assert by_id["wr40"] == pytest.approx(-5.0)  # 61 - 66

        assert by_id["rb1"] is None
        assert by_id["rb2"] is None

    def test_output_has_same_row_count_as_input(self, league_payload):
        """Multi-position input is not filtered down - every row survives."""
        cfg = league_config_from_sleeper(league_payload)
        df = _wr_and_rb_frame()

        result = vorp(df, cfg, position="WR", baseline="vols")
        assert result.height == df.height

    def test_shallow_pool_warns_but_still_populates_vorp(self, league_payload, caplog):
        """The vorp baseline (rank 53) is shallow against 40 WRs, and still works."""
        cfg = league_config_from_sleeper(league_payload)

        with caplog.at_level(logging.WARNING, logger="nuclearff.valuation.vorp"):
            result = vorp(_wr_and_rb_frame(), cfg, position="WR", baseline="vorp")

        by_id = dict(zip(result["player_id"], result["vorp"], strict=True))
        assert by_id["wr1"] == pytest.approx(100.0 - 61.0)
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_missing_required_column_raises(self, league_payload):
        cfg = league_config_from_sleeper(league_payload)
        broken = _wr_and_rb_frame().drop("position")
        with pytest.raises(ValueError, match="vorp.*position"):
            vorp(broken, cfg, position="WR")


class TestVona:
    def _draft_frame(self) -> pl.DataFrame:
        """Six WRs 100..50 (10 apart) plus one RB, for hand-computable VONA."""
        return pl.DataFrame(
            {
                "player_id": ["p1", "p2", "p3", "p4", "p5", "p6", "rb1"],
                "position": ["WR", "WR", "WR", "WR", "WR", "WR", "RB"],
                "proj_points": [100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 999.0],
            }
        )

    def test_matches_hand_computation(self):
        """p1 and p3 drafted; among undrafted (p2,p4,p5,p6), next_pick_gap=2
        lands on p4 (rank 2 of the undrafted, value 70.0) - vona = proj - 70.
        """
        result = vona(self._draft_frame(), {"p1", "p3"}, 2, position="WR")
        by_id = dict(zip(result["player_id"], result["vona"], strict=True))

        assert by_id["p2"] == pytest.approx(20.0)  # 90 - 70
        assert by_id["p4"] == pytest.approx(0.0)  # 70 - 70
        assert by_id["p5"] == pytest.approx(-10.0)  # 60 - 70
        assert by_id["p6"] == pytest.approx(-20.0)  # 50 - 70

    def test_drafted_players_get_null_vona(self):
        result = vona(self._draft_frame(), {"p1", "p3"}, 2, position="WR")
        by_id = dict(zip(result["player_id"], result["vona"], strict=True))

        assert by_id["p1"] is None
        assert by_id["p3"] is None

    def test_other_position_rows_get_null_vona(self):
        result = vona(self._draft_frame(), {"p1", "p3"}, 2, position="WR")
        by_id = dict(zip(result["player_id"], result["vona"], strict=True))

        assert by_id["rb1"] is None

    def test_next_pick_gap_of_one_uses_the_best_available(self):
        """next_pick_gap=1 (the very next pick) values against the current
        best undrafted player - vona should be 0 for that player itself.
        """
        result = vona(self._draft_frame(), set(), 1, position="WR")
        by_id = dict(zip(result["player_id"], result["vona"], strict=True))

        assert by_id["p1"] == pytest.approx(0.0)
        assert by_id["p2"] == pytest.approx(-10.0)

    def test_too_few_undrafted_players_falls_back_and_warns(self, caplog):
        """Only p5, p6 remain undrafted; next_pick_gap=5 exceeds that count,
        so this falls back to the worst-ranked remaining player (p6, 50.0).
        """
        drafted = {"p1", "p2", "p3", "p4"}
        with caplog.at_level(logging.WARNING, logger="nuclearff.valuation.vorp"):
            result = vona(self._draft_frame(), drafted, 5, position="WR")

        by_id = dict(zip(result["player_id"], result["vona"], strict=True))
        assert by_id["p5"] == pytest.approx(10.0)  # 60 - 50
        assert by_id["p6"] == pytest.approx(0.0)  # 50 - 50
        assert len(caplog.records) == 1

    def test_all_drafted_at_position_raises(self):
        all_wr_ids = {"p1", "p2", "p3", "p4", "p5", "p6"}
        with pytest.raises(ValueError, match="no undrafted players"):
            vona(self._draft_frame(), all_wr_ids, 1, position="WR")

    def test_missing_required_column_raises(self):
        broken = self._draft_frame().drop("proj_points")
        with pytest.raises(ValueError, match="vona.*proj_points"):
            vona(broken, set(), 1, position="WR")
