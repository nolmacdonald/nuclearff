"""Unit tests for nuclearff.metrics.passing.

No network: every function here is pure computation over a small synthetic
DataFrame shaped like the real ``load_player_stats``/``load_pbp`` schema
(columns and dtypes confirmed live this session, nflreadpy 0.1.5, 2025
season — see the module docstring in ``passing.py``), same convention as
``tests/test_nflverse_schedules.py``.
"""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.config.league import ScoringSettings
from nuclearff.metrics.passing import (
    _PASSING_SCORING_KEYS,
    _RUSHING_SCORING_KEYS,
    _TURNOVER_SCORING_KEYS,
    defense_epa_per_dropback,
    fantasy_point_breakdown,
    ftn_charting_rates,
    neutral_pass_rate,
    pace,
    volume_efficiency_split,
)
from nuclearff.scoring.engine import _HANDLED_KEYS


def _settings(**values: float) -> ScoringSettings:
    return ScoringSettings(values=dict(values))


# --- fantasy_point_breakdown ---------------------------------------------------


def test_breakdown_buckets_exhaustively_partition_every_handled_scoring_key():
    """The real correctness guarantee behind "sums to the total": these three
    buckets must together cover *every* key ScoringEngine knows how to
    score, with no overlap -- otherwise a real league using an omitted key
    would silently break the sum-to-total invariant. This test fails loudly
    if scoring/engine.py's own handled-key set ever changes without the
    buckets here being updated to match.
    """
    passing = set(_PASSING_SCORING_KEYS)
    rushing = set(_RUSHING_SCORING_KEYS)
    turnovers = set(_TURNOVER_SCORING_KEYS)

    assert passing & rushing == set()
    assert passing & turnovers == set()
    assert rushing & turnovers == set()
    assert passing | rushing | turnovers == _HANDLED_KEYS


def _qb_stat_line() -> pl.DataFrame:
    """One QB's real-shaped weekly stat line: a good passing game, a
    goal-line rushing score, one lost fumble, and a real (if rare) QB
    receiving stat from a broken-play throwback.
    """
    return pl.DataFrame(
        {
            "player_id": ["00-0036212"],
            "player_name": ["Baker Mayfield"],
            "season": [2026],
            "week": [2],
            "position": ["QB"],
            "passing_yards": [310],
            "passing_tds": [3],
            "passing_interceptions": [1],
            "passing_2pt_conversions": [0],
            "completions": [26],
            "attempts": [35],
            "rushing_yards": [15],
            "rushing_tds": [1],
            "rushing_fumbles_lost": [0],
            "sack_fumbles_lost": [1],
            "receiving_yards": [8],
            "receiving_tds": [0],
            "receiving_fumbles_lost": [0],
            "carries": [3],
        }
    )


def test_breakdown_subtotals_sum_to_exactly_the_real_total():
    scoring = _settings(
        pass_yd=0.04,
        pass_td=4.0,
        pass_int=-2.0,
        rush_yd=0.1,
        rush_td=6.0,
        rec_yd=0.1,
        fum_lost=-2.0,
        bonus_pass_yd_300=1.0,
    )
    df = _qb_stat_line()

    scored = fantasy_point_breakdown(df, scoring)

    total = (
        scored["passing_points"] + scored["rushing_points"] + scored["turnover_points"]
    ).to_list()
    assert total == pytest.approx(scored["fantasy_points"].to_list())


def test_breakdown_passing_points_reflects_yards_tds_and_the_interception():
    scoring = _settings(pass_yd=0.04, pass_td=4.0, pass_int=-2.0)
    df = _qb_stat_line()

    scored = fantasy_point_breakdown(df, scoring)

    expected = 310 * 0.04 + 3 * 4.0 + 1 * -2.0
    assert scored["passing_points"].item() == pytest.approx(expected)


def test_breakdown_rushing_points_includes_the_real_qb_receiving_stat():
    scoring = _settings(rush_yd=0.1, rush_td=6.0, rec_yd=0.1)
    df = _qb_stat_line()

    scored = fantasy_point_breakdown(df, scoring)

    expected = 15 * 0.1 + 1 * 6.0 + 8 * 0.1
    assert scored["rushing_points"].item() == pytest.approx(expected)


def test_breakdown_turnover_points_counts_the_sack_fumble_lost():
    scoring = _settings(fum_lost=-2.0)
    df = _qb_stat_line()

    scored = fantasy_point_breakdown(df, scoring)

    assert scored["turnover_points"].item() == pytest.approx(-2.0)


def test_breakdown_preserves_input_columns():
    scoring = _settings(pass_yd=0.04)
    df = _qb_stat_line()

    scored = fantasy_point_breakdown(df, scoring)

    assert "player_name" in scored.columns
    assert scored["player_name"].item() == "Baker Mayfield"


# --- volume_efficiency_split ----------------------------------------------------


def _pbp_for_volume_efficiency() -> pl.DataFrame:
    """Four plays for one passer: two real dropbacks (one a completion with
    cpoe, one a sack with null cpoe -- confirmed live that cpoe is only
    defined on a real pass attempt), one scramble (also a dropback, also
    null cpoe), and one non-dropback play that must be excluded entirely.
    """
    return pl.DataFrame(
        {
            "season": [2026, 2026, 2026, 2026],
            "passer_player_id": ["00-01", "00-01", "00-01", "00-01"],
            "passer_player_name": ["B.Mayfield"] * 4,
            "qb_dropback": [1, 1, 1, 0],
            "epa": [1.2, -1.8, 0.3, 0.1],
            "success": [1, 0, 1, 1],
            "cpoe": [8.5, None, None, None],
            "pass_oe": [5.0, 5.0, 5.0, None],
        }
    )


def test_volume_efficiency_split_counts_only_real_dropbacks():
    result = volume_efficiency_split(_pbp_for_volume_efficiency())

    assert result.height == 1
    assert result["dropbacks"].item() == 3


def test_volume_efficiency_split_epa_and_success_average_over_dropbacks_only():
    result = volume_efficiency_split(_pbp_for_volume_efficiency())

    assert result["epa_per_dropback"].item() == pytest.approx((1.2 - 1.8 + 0.3) / 3)
    assert result["success_rate"].item() == pytest.approx(2 / 3)


def test_volume_efficiency_split_cpoe_ignores_dropbacks_with_no_real_pass_attempt():
    """cpoe is null on the sack and the scramble; averaging must skip those
    nulls rather than treating them as zero (which would understate a real
    passer's accuracy)."""
    result = volume_efficiency_split(_pbp_for_volume_efficiency())

    assert result["cpoe"].item() == pytest.approx(8.5)


def test_volume_efficiency_split_raises_on_missing_column():
    broken = _pbp_for_volume_efficiency().drop("epa")

    with pytest.raises(ValueError, match="epa"):
        volume_efficiency_split(broken)


# --- neutral_pass_rate -----------------------------------------------------------


def _pbp_for_neutral_pass_rate() -> pl.DataFrame:
    """Six plays for one team: two real neutral-situation plays (one pass,
    one run), one blowout play (score_differential too large), one 4th
    quarter play (excluded by quarter alone), and two non-offensive play
    types (punt/kickoff) that must never count as a "run" or "pass".
    """
    return pl.DataFrame(
        {
            "season": [2026] * 6,
            "posteam": ["TB"] * 6,
            "qtr": [1, 2, 3, 4, 2, 2],
            "score_differential": [3, -3, 20, 0, 3, 3],
            "play_type": ["pass", "run", "pass", "pass", "punt", "kickoff"],
        }
    )


def test_neutral_pass_rate_excludes_blowouts_and_late_plays():
    result = neutral_pass_rate(_pbp_for_neutral_pass_rate())

    # Only the qtr=1/pass and qtr=2/run rows are neutral; the qtr=3 blowout
    # (diff=20) and qtr=4 play are excluded, and punt/kickoff never count as
    # offensive plays regardless of situation.
    assert result["neutral_plays"].item() == 2


def test_neutral_pass_rate_computes_the_pass_fraction_of_neutral_plays():
    result = neutral_pass_rate(_pbp_for_neutral_pass_rate())

    assert result["neutral_pass_rate"].item() == pytest.approx(0.5)


def test_neutral_pass_rate_groups_by_team_not_by_passer():
    result = neutral_pass_rate(_pbp_for_neutral_pass_rate())

    assert result.columns == ["season", "posteam", "neutral_plays", "neutral_pass_rate"]


# --- defense_epa_per_dropback -----------------------------------------------------


def _pbp_for_defense_epa() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2026, 2026, 2026, 2026],
            "defteam": ["GB", "GB", "ATL", "ATL"],
            "qb_dropback": [1, 1, 1, 0],
            "epa": [0.5, -0.1, 1.5, 9.9],
        }
    )


def test_defense_epa_per_dropback_counts_only_real_dropbacks():
    result = defense_epa_per_dropback(_pbp_for_defense_epa())

    by_team = {row["defteam"]: row for row in result.to_dicts()}
    assert by_team["GB"]["dropbacks_faced"] == 2
    assert by_team["ATL"]["dropbacks_faced"] == 1


def test_defense_epa_per_dropback_averages_epa_allowed():
    result = defense_epa_per_dropback(_pbp_for_defense_epa())

    by_team = {row["defteam"]: row for row in result.to_dicts()}
    assert by_team["GB"]["epa_allowed_per_dropback"] == pytest.approx((0.5 - 0.1) / 2)
    assert by_team["ATL"]["epa_allowed_per_dropback"] == pytest.approx(1.5)


def test_defense_epa_per_dropback_worst_defense_sorts_first_within_season():
    result = defense_epa_per_dropback(_pbp_for_defense_epa())

    assert result["defteam"].to_list()[0] == "ATL"


# --- pace --------------------------------------------------------------------------


def _pbp_for_pace() -> pl.DataFrame:
    """One team, one game, three drives:

    - Drive 1 (neutral): three plays. The first gap (3600 -> 3562, 38s) is a
      plausible snap-to-snap interval; the second (3562 -> 3520, 42s)
      exceeds the plausible-gap threshold (a clock stoppage, not tempo) and
      must be excluded.
    - Drive 2 (neutral): two plays, one plausible 30s gap.
    - Drive 3: a blowout (score_differential=20) run followed by a punt --
      excluded twice over (not neutral, and a punt isn't an offensive
      run/pass play), so it must contribute nothing regardless of its own
      internal gap.
    """
    return pl.DataFrame(
        {
            "season": [2026] * 7,
            "posteam": ["TB"] * 7,
            "game_id": ["g1"] * 7,
            "drive": [1, 1, 1, 2, 2, 3, 3],
            "play_id": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            "game_seconds_remaining": [
                3600.0,
                3562.0,
                3520.0,
                3000.0,
                2970.0,
                1800.0,
                1750.0,
            ],
            "qtr": [1, 1, 1, 2, 2, 1, 1],
            "score_differential": [0, 0, 0, 3, 3, 20, 20],
            "play_type": ["run", "pass", "run", "pass", "pass", "run", "punt"],
        }
    )


def test_pace_counts_only_plausible_same_drive_gaps():
    result = pace(_pbp_for_pace())

    assert result["snap_intervals"].item() == 2


def test_pace_excludes_a_gap_longer_than_the_plausible_snap_clock():
    """The 42s gap (a real clock stoppage) must not pull the average down
    toward a faster-looking pace than the offense actually played at."""
    result = pace(_pbp_for_pace())

    assert result["pace_seconds_per_play"].item() == pytest.approx((38.0 + 30.0) / 2)


def test_pace_excludes_blowout_and_non_offensive_plays():
    """Drive 3 (blowout + a punt) must contribute nothing -- removing it
    entirely from the input must not change the result."""
    without_drive_3 = _pbp_for_pace().filter(pl.col("drive") != 3)

    assert pace(_pbp_for_pace()).to_dicts() == pace(without_drive_3).to_dicts()


def test_pace_raises_on_missing_column():
    broken = _pbp_for_pace().drop("drive")

    with pytest.raises(ValueError, match="drive"):
        pace(broken)


# --- ftn_charting_rates -----------------------------------------------------------


def _pbp_for_ftn() -> pl.DataFrame:
    """Three plays for one passer: two real dropbacks with a charted match
    (play_id as Float64, matching real load_pbp) and one dropback FTN never
    charted (no matching nflverse_play_id below) -- an inner join, so it
    must simply not count rather than appearing as an all-null row. A
    fourth, non-dropback play must never reach the join at all.
    """
    return pl.DataFrame(
        {
            "season": [2026, 2026, 2026, 2026],
            "game_id": ["g1", "g1", "g1", "g1"],
            "play_id": [10.0, 20.0, 30.0, 40.0],
            "qb_dropback": [1, 1, 1, 0],
            "passer_player_id": ["00-01", "00-01", "00-01", "00-01"],
            "passer_player_name": ["B.Mayfield"] * 4,
        }
    )


def _ftn_charting() -> pl.DataFrame:
    """Charts plays 10 and 20 (as Int32 -- real load_ftn_charting dtype) but
    not 30, and not for the wrong game."""
    return pl.DataFrame(
        {
            "nflverse_game_id": ["g1", "g1"],
            "nflverse_play_id": pl.Series([10, 20], dtype=pl.Int32),
            "is_play_action": [True, False],
            "is_rpo": [False, False],
            "is_screen_pass": [False, False],
            "is_motion": [True, True],
            "n_blitzers": [1, 0],
            "n_pass_rushers": [4, 5],
            "is_qb_out_of_pocket": [False, True],
            "is_drop": [False, False],
            "is_catchable_ball": [True, True],
            "is_interception_worthy": [False, False],
        }
    )


def test_ftn_charting_rates_joins_despite_the_dtype_mismatch():
    """pbp.play_id is Float64, ftn.nflverse_play_id is Int32 -- the real,
    confirmed-live mismatch this function must cast around."""
    result = ftn_charting_rates(_pbp_for_ftn(), _ftn_charting())

    assert result["charted_dropbacks"].item() == 2


def test_ftn_charting_rates_uncharted_dropback_is_excluded_not_null():
    """Play 30 is a real dropback with no FTN match -- the inner join must
    drop it, not produce a null-padded row."""
    result = ftn_charting_rates(_pbp_for_ftn(), _ftn_charting())

    assert result.height == 1
    assert result["charted_dropbacks"].item() == 2  # not 3


def test_ftn_charting_rates_averages_each_rate_correctly():
    result = ftn_charting_rates(_pbp_for_ftn(), _ftn_charting())
    row = result.row(0, named=True)

    assert row["play_action_rate"] == pytest.approx(0.5)
    assert row["motion_rate"] == pytest.approx(1.0)
    assert row["avg_blitzers"] == pytest.approx(0.5)
    assert row["avg_pass_rushers"] == pytest.approx(4.5)
    assert row["out_of_pocket_rate"] == pytest.approx(0.5)


def test_ftn_charting_rates_raises_on_missing_pbp_column():
    broken = _pbp_for_ftn().drop("qb_dropback")

    with pytest.raises(ValueError, match="qb_dropback"):
        ftn_charting_rates(broken, _ftn_charting())


def test_ftn_charting_rates_raises_on_missing_ftn_column():
    broken = _ftn_charting().drop("is_rpo")

    with pytest.raises(ValueError, match="is_rpo"):
        ftn_charting_rates(_pbp_for_ftn(), broken)
