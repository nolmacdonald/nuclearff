"""Unit tests for WR volume metrics: target share, air-yards share, WOPR, RACR, aDOT.

Every fixture here is a small, hand-built synthetic Polars DataFrame with
realistic column names/dtypes (matching the shapes documented in
`nuclearff.nflverse.stats`), never live network - `tests/conftest.py`'s
autouse `_no_network` fixture would fail any test that tried.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

from nuclearff.metrics.volume import adot, air_yards_share, racr, target_share, wopr


def _weekly_frame() -> pl.DataFrame:
    """Two teams, five players, one week - shares are hand-computable.

    Team AAA targets sum to 20 and air yards sum to 100 (chosen so p1 lands
    on the plan's own worked WOPR example: target share 0.20, air-yards
    share 0.15). Team BBB is a second team in the same week, to confirm
    grouping is scoped per team and not pooled across the whole league.
    """
    return pl.DataFrame(
        {
            "season": [2024, 2024, 2024, 2024, 2024],
            "week": [1, 1, 1, 1, 1],
            "team": ["AAA", "AAA", "AAA", "BBB", "BBB"],
            "player_id": ["p1", "p2", "p3", "q1", "q2"],
            "targets": [4, 10, 6, 5, 5],
            "receiving_yards": [40, 90, 55, 45, 20],
            "receiving_air_yards": [15, 50, 35, 40, 10],
        }
    )


def _seasonal_frame() -> pl.DataFrame:
    """The same players' data collapsed to season grain - no 'week' column."""
    return pl.DataFrame(
        {
            "season": [2024, 2024, 2024],
            "team": ["AAA", "AAA", "BBB"],
            "player_id": ["p1", "p2", "q1"],
            "targets": [4, 10, 5],
            "receiving_air_yards": [15, 50, 40],
        }
    )


class TestTargetShare:
    def test_matches_hand_computation(self):
        """Each player's share is targets / team-week total targets."""
        result = target_share(_weekly_frame())
        shares = dict(zip(result["player_id"], result["target_share"], strict=True))

        assert shares["p1"] == pytest.approx(4 / 20)
        assert shares["p2"] == pytest.approx(10 / 20)
        assert shares["p3"] == pytest.approx(6 / 20)
        assert shares["q1"] == pytest.approx(5 / 10)
        assert shares["q2"] == pytest.approx(5 / 10)

    def test_raises_on_seasonal_grain_input(self):
        """No 'week' column raises ValueError rather than mis-computing."""
        with pytest.raises(ValueError, match="weekly-grain"):
            target_share(_seasonal_frame())

    def test_zero_team_total_yields_null(self):
        """A team-week with zero total targets yields null shares, not NaN/inf."""
        df = pl.DataFrame(
            {
                "season": [2024, 2024],
                "week": [1, 1],
                "team": ["ZZZ", "ZZZ"],
                "player_id": ["z1", "z2"],
                "targets": [0, 0],
                "receiving_air_yards": [0, 0],
            }
        )
        result = target_share(df)
        assert result["target_share"].to_list() == [None, None]

    def test_missing_required_column_raises(self):
        """A frame missing 'targets' fails loudly, not with a bare polars error."""
        broken = _weekly_frame().drop("targets")
        with pytest.raises(ValueError, match="target_share.*targets"):
            target_share(broken)

    def test_cross_check_does_not_warn_when_values_agree(self, caplog):
        """A pre-existing target_share column that matches the recompute is silent."""
        df = _weekly_frame().with_columns(
            pl.Series("target_share", [0.20, 0.50, 0.30, 0.50, 0.50])
        )
        with caplog.at_level(logging.WARNING, logger="nuclearff.metrics.volume"):
            target_share(df)

        assert caplog.records == []

    def test_cross_check_warns_on_disagreement(self, caplog):
        """A pre-existing target_share column that disagrees logs a warning."""
        df = _weekly_frame().with_columns(
            pl.Series("target_share", [0.99, 0.99, 0.99, 0.99, 0.99])
        )
        with caplog.at_level(logging.WARNING, logger="nuclearff.metrics.volume"):
            target_share(df)

        assert any("target_share" in r.message for r in caplog.records)


class TestAirYardsShare:
    def test_matches_hand_computation(self):
        """Each player's share is receiving_air_yards / team-week total."""
        result = air_yards_share(_weekly_frame())
        shares = dict(zip(result["player_id"], result["air_yards_share"], strict=True))

        assert shares["p1"] == pytest.approx(15 / 100)
        assert shares["p2"] == pytest.approx(50 / 100)
        assert shares["p3"] == pytest.approx(35 / 100)
        assert shares["q1"] == pytest.approx(40 / 50)
        assert shares["q2"] == pytest.approx(10 / 50)

    def test_raises_on_seasonal_grain_input(self):
        """No 'week' column raises ValueError rather than mis-computing."""
        with pytest.raises(ValueError, match="weekly-grain"):
            air_yards_share(_seasonal_frame())

    def test_zero_total_air_yards_yields_null(self):
        """A team-week with zero total air yards yields null, not a divide error."""
        df = pl.DataFrame(
            {
                "season": [2024, 2024],
                "week": [1, 1],
                "team": ["CCC", "CCC"],
                "player_id": ["r1", "r2"],
                "targets": [2, 3],
                "receiving_air_yards": [-5, 5],
            }
        )
        result = air_yards_share(df)
        assert result["air_yards_share"].to_list() == [None, None]

    def test_negative_total_air_yards_yields_null(self):
        """A team-week with a negative total air yards also yields null."""
        df = pl.DataFrame(
            {
                "season": [2024, 2024],
                "week": [1, 1],
                "team": ["DDD", "DDD"],
                "player_id": ["s1", "s2"],
                "targets": [2, 3],
                "receiving_air_yards": [-10, -5],
            }
        )
        result = air_yards_share(df)
        assert result["air_yards_share"].to_list() == [None, None]


class TestWopr:
    def test_matches_plan_worked_example(self):
        """p1's target share (0.20) and air-yards share (0.15) give WOPR 0.405,
        the plan's own worked example: 1.5*0.20 + 0.7*0.15 = 0.405.
        """
        result = wopr(_weekly_frame())
        row = result.filter(pl.col("player_id") == "p1").row(0, named=True)

        assert row["target_share"] == pytest.approx(0.20)
        assert row["air_yards_share"] == pytest.approx(0.15)
        assert row["wopr"] == pytest.approx(0.405)

    def test_composes_shares_when_not_already_present(self):
        """wopr() adds target_share/air_yards_share itself when absent."""
        df = _weekly_frame()
        assert "target_share" not in df.columns
        assert "air_yards_share" not in df.columns

        result = wopr(df)

        assert "target_share" in result.columns
        assert "air_yards_share" in result.columns

    def test_uses_existing_share_columns_without_recomputing(self):
        """wopr() trusts already-present share columns rather than overwriting them."""
        df = _weekly_frame().with_columns(
            target_share=pl.Series([0.9, 0.0, 0.0, 0.0, 0.0]),
            air_yards_share=pl.Series([0.1, 0.0, 0.0, 0.0, 0.0]),
        )
        result = wopr(df)
        row = result.filter(pl.col("player_id") == "p1").row(0, named=True)

        assert row["wopr"] == pytest.approx(1.5 * 0.9 + 0.7 * 0.1)

    def test_raises_on_seasonal_grain_input_even_with_share_columns_present(self):
        """The week-column guard fires even if target_share/air_yards_share
        already exist - those could be nflreadpy's own wrong-for-traded-
        players seasonal columns, and this function must not trust them.
        """
        seasonal_with_shares = _seasonal_frame().with_columns(
            target_share=pl.Series([0.2, 0.5, 0.5]),
            air_yards_share=pl.Series([0.15, 0.5, 0.8]),
        )
        with pytest.raises(ValueError, match="weekly-grain"):
            wopr(seasonal_with_shares)


def _racr_adot_frame() -> pl.DataFrame:
    """Three rows covering a normal case, zero air yards, and negative air yards."""
    return pl.DataFrame(
        {
            "receiving_yards": [80.0, 0.0, 50.0],
            "receiving_air_yards": [100.0, 0.0, -10.0],
            "targets": [8, 0, 5],
        }
    )


class TestRacr:
    def test_matches_hand_computation(self):
        """RACR = receiving_yards / receiving_air_yards, per row."""
        result = racr(_racr_adot_frame())
        assert result["racr"][0] == pytest.approx(80.0 / 100.0)

    def test_zero_air_yards_yields_null(self):
        result = racr(_racr_adot_frame())
        assert result["racr"][1] is None

    def test_negative_air_yards_yields_null(self):
        result = racr(_racr_adot_frame())
        assert result["racr"][2] is None

    def test_missing_required_column_raises(self):
        broken = _racr_adot_frame().drop("receiving_air_yards")
        with pytest.raises(ValueError, match="racr.*receiving_air_yards"):
            racr(broken)


class TestAdot:
    def test_matches_hand_computation(self):
        """aDOT = receiving_air_yards / targets, per row."""
        result = adot(_racr_adot_frame())
        assert result["adot"][0] == pytest.approx(100.0 / 8.0)

    def test_zero_targets_yields_null(self):
        result = adot(_racr_adot_frame())
        assert result["adot"][1] is None

    def test_negative_air_yards_with_nonzero_targets_still_computes(self):
        """Only targets == 0 is guarded; a negative air-yards row (real,
        observed data) still produces a (negative) ratio rather than null.
        """
        result = adot(_racr_adot_frame())
        assert result["adot"][2] == pytest.approx(-10.0 / 5.0)

    def test_missing_required_column_raises(self):
        broken = _racr_adot_frame().drop("targets")
        with pytest.raises(ValueError, match="adot.*targets"):
            adot(broken)
