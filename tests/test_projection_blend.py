"""Unit tests for projection blending: recency weighting, age curve, context
deltas, games projection, and the end-to-end orchestration.

Every fixture here is a small, hand-built synthetic Polars DataFrame with
realistic column names/values (matching the shapes documented in
`nuclearff.nflverse.stats`), never live network - `tests/conftest.py`'s
autouse `_no_network` fixture would fail any test that tried.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

from nuclearff.config.models import (
    AgeCurveConfig,
    ModelConfig,
    RecencyWeights,
    SampleThresholds,
)
from nuclearff.projection.blend import (
    apply_age_curve,
    apply_context_deltas,
    blend_projection,
    project_games_played,
    recency_weighted_rate,
)

AS_OF_SEASON = 2026
"""Every test blends into this season, so the lookback window is 2023-2025."""


def _seasons_frame() -> pl.DataFrame:
    """Multi-season synthetic receiving data for four players.

    - p1: all 3 lookback seasons present, every season a normal sample size.
    - p2: only 1 of 3 lookback seasons present (2025) - renormalization case.
    - p3: only data outside the lookback window (2020) - dropped entirely.
    - p4: all 3 lookback seasons present, but 2025 is a 2-game small sample
      (for the min-games guard).
    """
    return pl.DataFrame(
        {
            "player_id": [
                "p1",
                "p1",
                "p1",
                "p2",
                "p3",
                "p4",
                "p4",
                "p4",
            ],
            "season": [
                2025,
                2024,
                2023,
                2025,
                2020,
                2025,
                2024,
                2023,
            ],
            "games": [
                17,
                16,
                15,
                17,
                10,
                2,
                16,
                15,
            ],
            "targets": [
                120.0,
                100.0,
                80.0,
                90.0,
                999.0,
                50.0,
                80.0,
                70.0,
            ],
        }
    )


class TestRecencyWeightedRate:
    def test_full_history_matches_hand_computation(self):
        """p1 has all 3 seasons: blended = 120*.5 + 100*.3 + 80*.2 = 106."""
        result = recency_weighted_rate(
            _seasons_frame(), "targets", AS_OF_SEASON, RecencyWeights()
        )
        row = result.filter(pl.col("player_id") == "p1").row(0, named=True)
        assert row["targets_blended"] == pytest.approx(106.0)

    def test_partial_history_is_renormalized_not_zero_filled(self):
        """p2 has only 1 of 3 seasons (2025, weight 0.5).

        Renormalized: (90 * 0.5) / 0.5 == 90 - the single available season
        carries its own value at full strength. A naive implementation that
        treated the two missing seasons as zero-value rows (dividing by the
        full weight sum of 1.0 instead of the renormalized 0.5) would wrongly
        produce 45 instead - assert the *correct*, renormalized value.
        """
        result = recency_weighted_rate(
            _seasons_frame(), "targets", AS_OF_SEASON, RecencyWeights()
        )
        row = result.filter(pl.col("player_id") == "p2").row(0, named=True)
        assert row["targets_blended"] == pytest.approx(90.0)

    def test_zero_seasons_in_window_drops_player(self):
        """p3's only data (2020) is outside the 2023-2025 lookback window."""
        result = recency_weighted_rate(
            _seasons_frame(), "targets", AS_OF_SEASON, RecencyWeights()
        )
        assert "p3" not in result["player_id"].to_list()

    def test_no_threshold_uses_every_season_at_full_weight(self):
        """p4 without a sample guard: 50*.5 + 80*.3 + 70*.2 = 63."""
        result = recency_weighted_rate(
            _seasons_frame(), "targets", AS_OF_SEASON, RecencyWeights()
        )
        row = result.filter(pl.col("player_id") == "p4").row(0, named=True)
        assert row["targets_blended"] == pytest.approx(63.0)

    def test_min_games_threshold_excludes_low_game_season(self):
        """p4's 2025 season (2 games) is excluded by min_games=8.

        Renormalized over the remaining two seasons:
        (80*.3 + 70*.2) / (.3 + .2) == 76.
        """
        result = recency_weighted_rate(
            _seasons_frame(), "targets", AS_OF_SEASON, RecencyWeights(), min_games=8
        )
        row = result.filter(pl.col("player_id") == "p4").row(0, named=True)
        assert row["targets_blended"] == pytest.approx(76.0)

    def test_sample_threshold_config_has_same_effect_as_min_games(self):
        """Passing a SampleThresholds gives the same result as min_games."""
        result = recency_weighted_rate(
            _seasons_frame(),
            "targets",
            AS_OF_SEASON,
            RecencyWeights(),
            sample_threshold=SampleThresholds(min_games=8),
        )
        row = result.filter(pl.col("player_id") == "p4").row(0, named=True)
        assert row["targets_blended"] == pytest.approx(76.0)

    def test_missing_required_column_raises(self):
        broken = _seasons_frame().drop("targets")
        with pytest.raises(ValueError, match="recency_weighted_rate.*targets"):
            recency_weighted_rate(broken, "targets", AS_OF_SEASON, RecencyWeights())


def _players_frame() -> pl.DataFrame:
    """Players at the plateau boundary, well past it, and far past it, plus
    one missing entirely and one with a null birth_date.

    Ages computed as of September 1, 2026:
    - a1: born 1998-01-01 -> age 28 (exactly AgeCurveConfig.plateau_end).
    - a2: born 1994-01-01 -> age 32 (4 years past plateau_end).
    - a3: born 1986-01-01 -> age 40 (12 years past plateau_end, floor hit).
    - a5: present, but birth_date is null.
    (a4 is intentionally absent from this table entirely.)
    """
    return pl.DataFrame(
        {
            "gsis_id": ["a1", "a2", "a3", "a5"],
            "birth_date": ["1998-01-01", "1994-01-01", "1986-01-01", None],
        }
    )


def _projection_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["a1", "a2", "a3", "a4", "a5"],
            "targets_blended": [100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )


class TestApplyAgeCurve:
    def test_player_at_plateau_end_gets_factor_one(self):
        result = apply_age_curve(
            _projection_frame(),
            _players_frame(),
            AS_OF_SEASON,
            AgeCurveConfig(),
            "targets_blended",
        )
        row = result.filter(pl.col("player_id") == "a1").row(0, named=True)
        assert row["age_factor"] == pytest.approx(1.0)
        assert row["targets_blended"] == pytest.approx(100.0)

    def test_player_past_plateau_gets_linear_decline(self):
        """4 years past plateau_end: 1.0 - 0.025*4 == 0.9 (above the floor)."""
        result = apply_age_curve(
            _projection_frame(),
            _players_frame(),
            AS_OF_SEASON,
            AgeCurveConfig(),
            "targets_blended",
        )
        row = result.filter(pl.col("player_id") == "a2").row(0, named=True)
        assert row["age_factor"] == pytest.approx(0.9)
        assert row["targets_blended"] == pytest.approx(90.0)

    def test_player_far_past_plateau_is_floored_at_min_factor(self):
        """12 years past plateau_end: 1.0 - 0.025*12 == 0.7, floored to 0.85."""
        result = apply_age_curve(
            _projection_frame(),
            _players_frame(),
            AS_OF_SEASON,
            AgeCurveConfig(),
            "targets_blended",
        )
        row = result.filter(pl.col("player_id") == "a3").row(0, named=True)
        assert row["age_factor"] == pytest.approx(0.85)
        assert row["targets_blended"] == pytest.approx(85.0)

    def test_player_missing_from_players_table_gets_factor_one(self):
        result = apply_age_curve(
            _projection_frame(),
            _players_frame(),
            AS_OF_SEASON,
            AgeCurveConfig(),
            "targets_blended",
        )
        row = result.filter(pl.col("player_id") == "a4").row(0, named=True)
        assert row["age_factor"] == pytest.approx(1.0)

    def test_player_with_null_birth_date_gets_factor_one(self):
        result = apply_age_curve(
            _projection_frame(),
            _players_frame(),
            AS_OF_SEASON,
            AgeCurveConfig(),
            "targets_blended",
        )
        row = result.filter(pl.col("player_id") == "a5").row(0, named=True)
        assert row["age_factor"] == pytest.approx(1.0)

    def test_logs_warning_naming_how_many_players_were_affected(self, caplog):
        """a4 (missing from players) and a5 (null birth_date) == 2 affected."""
        with caplog.at_level(logging.WARNING, logger="nuclearff.projection.blend"):
            apply_age_curve(
                _projection_frame(),
                _players_frame(),
                AS_OF_SEASON,
                AgeCurveConfig(),
                "targets_blended",
            )
        assert any("2 of 5" in r.message for r in caplog.records)


class TestApplyContextDeltas:
    def _df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "player_id": ["p1", "p2", "p3"],
                "targets_blended": [100.0, 50.0, 20.0],
            }
        )

    def test_multiplicative_mode(self):
        result = apply_context_deltas(
            self._df(), {"p1": 1.15}, "targets_blended", mode="multiplicative"
        )
        values = dict(zip(result["player_id"], result["targets_blended"], strict=True))
        assert values["p1"] == pytest.approx(115.0)
        assert values["p2"] == pytest.approx(50.0)  # no delta -> unchanged
        assert values["p3"] == pytest.approx(20.0)

    def test_additive_mode(self):
        result = apply_context_deltas(
            self._df(), {"p1": 10.0}, "targets_blended", mode="additive"
        )
        values = dict(zip(result["player_id"], result["targets_blended"], strict=True))
        assert values["p1"] == pytest.approx(110.0)
        assert values["p2"] == pytest.approx(50.0)
        assert values["p3"] == pytest.approx(20.0)

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="mode"):
            apply_context_deltas(
                self._df(), {"p1": 1.1}, "targets_blended", mode="bogus"
            )

    def test_unknown_player_id_in_deltas_is_logged(self, caplog):
        """A typo'd player_id in the deltas dict is reported, not silently dropped."""
        with caplog.at_level(logging.WARNING, logger="nuclearff.projection.blend"):
            apply_context_deltas(
                self._df(), {"p1": 1.1, "typo_id": 1.5}, "targets_blended"
            )
        assert any("typo_id" in r.message for r in caplog.records)

    def test_empty_deltas_leaves_df_unchanged(self):
        result = apply_context_deltas(self._df(), {}, "targets_blended")
        assert result["targets_blended"].to_list() == [100.0, 50.0, 20.0]


class TestProjectGamesPlayed:
    def test_matches_hand_computation(self):
        """p1: 17*.5 + 16*.3 + 15*.2 == 16.3, well under the 17-game cap."""
        result = project_games_played(_seasons_frame(), AS_OF_SEASON, RecencyWeights())
        row = result.filter(pl.col("player_id") == "p1").row(0, named=True)
        assert row["projected_games"] == pytest.approx(16.3)

    def test_capped_at_max_games(self):
        """A weighted average above max_games is clipped down to it."""
        seasons = pl.DataFrame(
            {
                "player_id": ["over", "over", "over"],
                "season": [2025, 2024, 2023],
                "games": [20, 20, 20],
            }
        )
        result = project_games_played(
            seasons, AS_OF_SEASON, RecencyWeights(), max_games=17
        )
        row = result.filter(pl.col("player_id") == "over").row(0, named=True)
        assert row["projected_games"] == pytest.approx(17.0)


class TestBlendProjection:
    def _seasons(self) -> pl.DataFrame:
        """bp1: age-neutral (factor 1.0), gets a +10% context delta.
        bp2: 4 years past the age-curve plateau (factor 0.9), no context delta.
        Both have constant per-season games/targets/receiving_yards, chosen
        so hand-computing the expected blended values stays simple.
        """
        return pl.DataFrame(
            {
                "player_id": [
                    "bp1",
                    "bp1",
                    "bp1",
                    "bp2",
                    "bp2",
                    "bp2",
                ],
                "season": [2025, 2024, 2023, 2025, 2024, 2023],
                "games": [17, 16, 15, 16, 16, 16],
                "targets": [120.0, 100.0, 80.0, 100.0, 100.0, 100.0],
                "receiving_yards": [1200.0, 1000.0, 800.0, 900.0, 900.0, 900.0],
            }
        )

    def _players(self) -> pl.DataFrame:
        """bp1 born 1998-01-01 -> age 28 in 2026 (plateau, factor 1.0).
        bp2 born 1994-01-01 -> age 32 in 2026 (4 years past, factor 0.9).
        """
        return pl.DataFrame(
            {
                "gsis_id": ["bp1", "bp2"],
                "birth_date": ["1998-01-01", "1994-01-01"],
            }
        )

    def test_end_to_end_hand_computed(self):
        result = blend_projection(
            self._seasons(),
            self._players(),
            AS_OF_SEASON,
            ["targets", "receiving_yards"],
            ModelConfig(),
            context_deltas={"bp1": 1.10},
        )

        bp1 = result.filter(pl.col("player_id") == "bp1").row(0, named=True)
        # targets: recency-weighted 106.0, age factor 1.0, then *1.10 -> 116.6
        assert bp1["targets_blended"] == pytest.approx(116.6)
        # receiving_yards: recency-weighted 1060.0, age factor 1.0, *1.10 -> 1166.0
        assert bp1["receiving_yards_blended"] == pytest.approx(1166.0)
        assert bp1["age_factor"] == pytest.approx(1.0)
        assert bp1["projected_games"] == pytest.approx(16.3)

        bp2 = result.filter(pl.col("player_id") == "bp2").row(0, named=True)
        # targets: constant 100 across all seasons regardless of weights,
        # age factor 0.9, no context delta -> 90.0
        assert bp2["targets_blended"] == pytest.approx(90.0)
        assert bp2["receiving_yards_blended"] == pytest.approx(810.0)
        assert bp2["age_factor"] == pytest.approx(0.9)
        assert bp2["projected_games"] == pytest.approx(16.0)

    def test_empty_rate_columns_raises(self):
        with pytest.raises(ValueError, match="rate_columns"):
            blend_projection(
                self._seasons(), self._players(), AS_OF_SEASON, [], ModelConfig()
            )
