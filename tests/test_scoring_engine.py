"""Unit tests for `nuclearff.scoring.engine.ScoringEngine`.

No network: `ScoringEngine` is pure computation over `ScoringSettings` and
either a plain dict or a small synthetic Polars DataFrame, so every test here
builds its own fixtures in-line, plus one that reuses the real captured
league scoring settings already committed under
`tests/fixtures/sleeper/league.json` (see `conftest.py::league_payload`).
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

from nuclearff.config.league import ScoringSettings
from nuclearff.scoring.engine import ScoringEngine


def _settings(**values: float) -> ScoringSettings:
    """Build ScoringSettings directly from keyword coefficients."""
    return ScoringSettings(values=dict(values))


def test_full_ppr_reception_line_scores_exactly() -> None:
    """The plan's own acceptance benchmark: 8 rec / 100 rec_yd / 1 rec_td
    under full-PPR rules (rec=1.0, rec_yd=0.1, rec_td=6.0, else 0.0) must
    equal exactly 8*1.0 + 100*0.1 + 1*6.0 = 24.0.
    """
    scoring = _settings(rec=1.0, rec_yd=0.1, rec_td=6.0)
    engine = ScoringEngine(scoring)

    line = {"receptions": 8, "receiving_yards": 100, "receiving_tds": 1}

    assert engine.score_stat_line(line) == pytest.approx(24.0)


def test_real_league_zero_bonuses_contribute_nothing(league_payload) -> None:
    """Scored under this project's real captured league.json, a stat line
    crossing every bonus threshold (100+ rec yards, 100+ rush yards, 300+
    pass yards, 25+ completions, 20+ carries, 100+ combined rush+rec yards)
    scores the same as the plain linear terms alone, because every
    bonus_* coefficient in the real league is 0.0 — bonus logic must
    contribute exactly zero when its coefficient is zero, whether or not
    the logic exists in the engine at all.
    """
    scoring = ScoringSettings.from_sleeper(league_payload["scoring_settings"])
    assert (
        scoring.get("bonus_rec_yd_100") == 0.0
    )  # sanity: fixture really is all-zero bonuses
    engine = ScoringEngine(scoring)

    line = {
        "receptions": 10,
        "receiving_yards": 150,
        "receiving_tds": 2,
        "rushing_yards": 120,
        "carries": 22,
        "passing_yards": 350,
        "completions": 28,
        "position": "WR",
    }

    expected_linear = (
        line["receptions"] * scoring.get("rec")
        + line["receiving_yards"] * scoring.get("rec_yd")
        + line["receiving_tds"] * scoring.get("rec_td")
        + line["rushing_yards"] * scoring.get("rush_yd")
        + line["passing_yards"] * scoring.get("pass_yd")
    )

    assert engine.score_stat_line(line) == pytest.approx(expected_linear)


def test_threshold_bonus_fires_at_exactly_100_not_99() -> None:
    """A 100+ receiving-yard game gets the flat bonus; a 99-yard game does not."""
    scoring = _settings(rec_yd=0.1, bonus_rec_yd_100=3.0)
    engine = ScoringEngine(scoring)

    qualifying = engine.score_stat_line({"receiving_yards": 100})
    short = engine.score_stat_line({"receiving_yards": 99})

    assert qualifying == pytest.approx(100 * 0.1 + 3.0)
    assert short == pytest.approx(99 * 0.1)


def test_position_conditional_bonus_applies_only_to_matching_position() -> None:
    """bonus_rec_te applies to a TE's receptions but not a WR's, even with
    an identical reception count.
    """
    scoring = _settings(rec=1.0, bonus_rec_te=0.5)
    engine = ScoringEngine(scoring)

    te_line = {"receptions": 6, "position": "TE"}
    wr_line = {"receptions": 6, "position": "WR"}

    assert engine.score_stat_line(te_line) == pytest.approx(6 * 1.0 + 6 * 0.5)
    assert engine.score_stat_line(wr_line) == pytest.approx(6 * 1.0)


def test_position_bonus_skipped_without_a_position_key() -> None:
    """No `position` key at all means position-conditional bonuses are
    silently skipped rather than guessed at.
    """
    scoring = _settings(rec=1.0, bonus_rec_te=0.5, bonus_rec_wr=0.5, bonus_rec_rb=0.5)
    engine = ScoringEngine(scoring)

    assert engine.score_stat_line({"receptions": 6}) == pytest.approx(6.0)


def test_fum_lost_composite_sums_three_separate_columns() -> None:
    """fum_lost = receiving_fumbles_lost + rushing_fumbles_lost +
    sack_fumbles_lost, each contributing independently.
    """
    scoring = _settings(fum_lost=-2.0)
    engine = ScoringEngine(scoring)

    line = {
        "receiving_fumbles_lost": 1,
        "rushing_fumbles_lost": 1,
        "sack_fumbles_lost": 1,
    }

    assert engine.score_stat_line(line) == pytest.approx(3 * -2.0)


def test_unscored_keys_lists_unmapped_nonzero_key() -> None:
    """A nonzero key with no mapping (def_td: no defense stat source in
    this project) is reported by unscored_keys(), while a mapped key
    (rec) and a zero-valued unmapped key (fgm) are not.
    """
    scoring = _settings(rec=1.0, def_td=6.0, fgm=0.0)
    engine = ScoringEngine(scoring)

    unscored = engine.unscored_keys()

    assert "def_td" in unscored
    assert "rec" not in unscored
    assert "fgm" not in unscored


def test_unscored_keys_empty_when_only_mapped_keys_are_nonzero() -> None:
    """A league that only uses mapped keys has nothing to warn about."""
    scoring = _settings(rec=1.0, rec_yd=0.1, rec_td=6.0, fum_lost=-2.0)
    engine = ScoringEngine(scoring)

    assert engine.unscored_keys() == []


def test_constructor_logs_warning_naming_unscored_keys(caplog) -> None:
    """Constructing the engine over a league with an unmapped nonzero key
    logs a warning naming that key and its coefficient, rather than
    failing or staying silent.
    """
    scoring = _settings(rec=1.0, def_td=6.0)

    with caplog.at_level(logging.WARNING, logger="nuclearff.scoring.engine"):
        ScoringEngine(scoring)

    assert any("def_td" in record.message for record in caplog.records)


def test_missing_stat_column_scores_as_zero_not_an_error() -> None:
    """A stat line with no rushing keys at all (a pure pass-catcher's game)
    scores as if rushing stats were zero, not an error.
    """
    scoring = _settings(rec=1.0, rec_yd=0.1, rush_yd=0.1, rush_td=6.0)
    engine = ScoringEngine(scoring)

    line = {"receptions": 5, "receiving_yards": 60}

    assert engine.score_stat_line(line) == pytest.approx(5 * 1.0 + 60 * 0.1)


def _synthetic_weekly_frame() -> pl.DataFrame:
    """A small stand-in for `load_weekly_receiving`, four rows, mixed
    positions, including a TE (for the position bonus), a zero-fumble row,
    and one row that crosses the 100-yard rushing+receiving bonus only in
    combination (60 rush + 60 rec, neither alone reaching 100).
    """
    return pl.DataFrame(
        {
            "player_id": ["00-01", "00-02", "00-03", "00-04"],
            "position": ["WR", "TE", "RB", "WR"],
            "receptions": [8, 6, 2, 3],
            "receiving_yards": [100, 70, 15, 40],
            "receiving_tds": [1, 1, 0, 0],
            "rushing_yards": [0, 0, 60, 0],
            "carries": [0, 0, 22, 0],
            "receiving_fumbles_lost": [0, 0, 0, 1],
            "rushing_fumbles_lost": [0, 0, 1, 0],
            "sack_fumbles_lost": [0, 0, 0, 0],
        }
    )


def _rich_scoring() -> ScoringSettings:
    """Scoring settings that exercise every mapping kind at once: linear,
    composite, single-threshold, combined-threshold, and position bonus.
    """
    return _settings(
        rec=1.0,
        rec_yd=0.1,
        rec_td=6.0,
        rush_yd=0.1,
        fum_lost=-2.0,
        bonus_rec_yd_100=3.0,
        bonus_rush_att_20=1.0,
        bonus_rush_rec_yd_100=2.0,
        bonus_rec_te=0.5,
    )


def test_score_frame_matches_score_stat_line_row_by_row() -> None:
    """A strong equivalence test: score_frame's vectorized result matches
    score_stat_line computed independently, row by row, on the same data,
    across every mapping kind at once.
    """
    scoring = _rich_scoring()
    engine = ScoringEngine(scoring)
    df = _synthetic_weekly_frame()

    scored = engine.score_frame(df)

    assert "fantasy_points" in scored.columns
    assert scored.height == df.height

    expected = [engine.score_stat_line(row) for row in df.to_dicts()]
    actual = scored["fantasy_points"].to_list()

    for got, want in zip(actual, expected, strict=True):
        assert got == pytest.approx(want)

    # And pin one row's absolute value so a change in either path is caught:
    # row 0 is 8 rec / 100 rec_yd / 1 rec_td / WR (no TE bonus, no rushing),
    # crossing bonus_rec_yd_100 (100 rec yards alone) *and*
    # bonus_rush_rec_yd_100 (0 rush + 100 rec is still >= 100 combined).
    row0 = scored.row(0, named=True)
    assert row0["fantasy_points"] == pytest.approx(
        8 * 1.0 + 100 * 0.1 + 1 * 6.0 + 3.0 + 2.0
    )


def test_score_frame_treats_absent_column_as_zero_contribution() -> None:
    """A frame with no rushing columns at all still scores WR/TE rows
    correctly, contributing zero for every rushing-keyed term.
    """
    scoring = _settings(rec=1.0, rec_yd=0.1, rush_yd=0.1, rush_td=6.0)
    engine = ScoringEngine(scoring)

    df = pl.DataFrame(
        {
            "receptions": [5, 3],
            "receiving_yards": [60, 40],
        }
    )

    scored = engine.score_frame(df)

    assert scored["fantasy_points"].to_list() == pytest.approx(
        [5 * 1.0 + 60 * 0.1, 3 * 1.0 + 40 * 0.1]
    )


def test_score_frame_without_position_column_skips_position_bonuses() -> None:
    """A frame with no `position` column contributes zero for every
    position-conditional bonus, matching score_stat_line's behavior when
    no `position` key is present.
    """
    scoring = _settings(rec=1.0, bonus_rec_te=5.0)
    engine = ScoringEngine(scoring)

    df = pl.DataFrame({"receptions": [4]})

    scored = engine.score_frame(df)

    assert scored["fantasy_points"].to_list() == pytest.approx([4.0])
