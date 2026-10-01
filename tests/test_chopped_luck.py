"""Unit tests for nuclearff.chopped.luck (issue #228)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.chopped.luck import LUCK_COLUMNS, guard_excluded_weeks, survival_luck


def _week(week, alive, rows, *, season=2025, league_id="L"):
    """Survival rows for one week: (owner, margin, margin_pct, z, pct, chopped)."""
    return [
        {
            "league_id": league_id,
            "season": season,
            "week": week,
            "owner_id": owner,
            "manager": owner.upper(),
            "alive_count": alive,
            "margin": margin,
            "margin_pct": margin_pct,
            "z_chop": z,
            "percentile": pct,
            "chopped": chopped,
        }
        for owner, margin, margin_pct, z, pct, chopped in rows
    ]


# Three weeks of a small league. "a" survives comfortably, "b" survives
# narrowly every week, "c" is chopped in week 1, "d" in week 3.
SURVIVAL = pl.DataFrame(
    _week(
        1,
        6,
        [
            ("a", 40.0, 0.50, 2.0, 1.0, False),
            ("b", 2.0, 0.025, 0.1, 0.5, False),
            ("d", 10.0, 0.10, 0.6, 0.66, False),
            ("c", 0.0, 0.0, 0.0, 0.16, True),
        ],
    )
    + _week(
        2,
        5,
        [
            ("a", 30.0, 0.40, 1.5, 0.8, False),
            ("b", 3.0, 0.04, 0.2, 0.6, False),
            ("d", 1.0, 0.01, 0.05, 0.4, False),
        ],
    )
    + _week(
        3,
        3,  # below the default late-week guard of 5
        [
            ("a", 20.0, 0.30, 2.0, 1.0, False),
            ("b", 5.0, 0.20, 0.25, 0.66, False),
            ("d", 0.0, 0.0, 0.0, 0.33, True),
        ],
    )
)


def _luck(**kwargs) -> dict[str, dict]:
    frame = survival_luck(SURVIVAL, **kwargs)
    return {row["owner_id"]: row for row in frame.iter_rows(named=True)}


def test_columns_and_every_manager_gets_a_row():
    frame = survival_luck(SURVIVAL)
    assert frame.columns == ["league_id", "season", *LUCK_COLUMNS]
    assert sorted(frame["owner_id"]) == ["a", "b", "c", "d"]


def test_weeks_survived_and_chopped_week():
    luck = _luck()
    assert (luck["a"]["weeks_survived"], luck["a"]["chopped_week"]) == (3, None)
    assert (luck["d"]["weeks_survived"], luck["d"]["chopped_week"]) == (2, 3)
    # Chopped in week 1: a row with nothing survived, not a missing manager.
    assert (luck["c"]["weeks_survived"], luck["c"]["chopped_week"]) == (0, 1)
    assert luck["c"]["avg_margin"] is None
    assert luck["c"]["nail_biter_ratio"] is None


def test_cumulative_and_average_margin_skip_the_chop_week():
    luck = _luck()
    assert luck["a"]["cumulative_margin"] == pytest.approx(90.0)
    assert luck["a"]["avg_margin"] == pytest.approx(30.0)
    assert luck["d"]["cumulative_margin"] == pytest.approx(11.0)


def test_field_relative_margin_can_go_negative():
    # Week medians among survivors: week 1 {40, 2, 10} -> 10; week 2 -> 3;
    # week 3 {20, 5} -> 12.5.
    luck = _luck()
    assert luck["b"]["field_relative_margin"] == pytest.approx(
        (2 - 10) + (3 - 3) + (5 - 12.5)
    )
    assert luck["a"]["field_relative_margin"] > 0


def test_nail_biter_ratio_uses_every_week_survived():
    luck = _luck()
    # b: 2.5% and 4% are within 5%; 20% is not.
    assert luck["b"]["nail_biter_weeks"] == 2
    assert luck["b"]["nail_biter_ratio"] == pytest.approx(2 / 3)
    assert luck["a"]["nail_biter_weeks"] == 0


def test_razor_thin_z_skips_guarded_weeks():
    luck = _luck()
    # b's z of 0.25 in week 3 is razor-thin but week 3 has only 3 alive.
    assert luck["b"]["guarded_weeks"] == 2
    assert luck["b"]["razor_thin_weeks"] == 2
    assert luck["b"]["razor_thin_ratio"] == pytest.approx(1.0)
    assert luck["a"]["mean_z"] == pytest.approx(1.75)  # weeks 1-2 only


def test_percentile_cv_needs_enough_guarded_weeks():
    assert _luck()["a"]["percentile_cv"] is None  # 2 guarded weeks < 4
    luck = _luck(min_weeks=2)
    pcts = [1.0, 0.8]
    mean = sum(pcts) / 2
    sd = (sum((p - mean) ** 2 for p in pcts) / 1) ** 0.5  # sample sd
    assert luck["a"]["percentile_cv"] == pytest.approx(sd / mean)


def test_thresholds_are_configurable():
    luck = _luck(nail_biter_pct=0.25, min_alive=3)
    assert luck["b"]["nail_biter_weeks"] == 3
    assert luck["b"]["guarded_weeks"] == 3


def test_career_combines_seasons_and_uses_the_latest_chop_week():
    later = pl.DataFrame(
        _week(1, 6, [("d", 8.0, 0.08, 0.5, 0.7, False)], season=2026, league_id="M")
    )
    frame = survival_luck(pl.concat([SURVIVAL, later]), career=True)
    assert frame.columns == list(LUCK_COLUMNS)
    luck = {row["owner_id"]: row for row in frame.iter_rows(named=True)}
    assert luck["d"]["weeks_survived"] == 3
    # Chopped in 2025 but alive in 2026: no chop week shown.
    assert luck["d"]["chopped_week"] is None


def test_sorted_by_average_margin():
    # a: 30.0, d: (10 + 1) / 2 = 5.5, b: (2 + 3 + 5) / 3 = 3.3, c: none.
    assert survival_luck(SURVIVAL)["owner_id"].to_list() == ["a", "d", "b", "c"]


def test_guard_excluded_weeks_lists_small_fields():
    excluded = guard_excluded_weeks(SURVIVAL)
    assert excluded.select("week", "alive_count").rows() == [(3, 3)]
