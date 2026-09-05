"""Unit tests for the FantasyPros ECR wrapper (`nuclearff.nflverse.rankings`).

These never touch the network: `load_fantasypros_ecr` is a thin
delegate-filter-validate wrapper, so the tests monkeypatch
`nflreadpy.load_ff_rankings` with small synthetic Polars DataFrames shaped
like the real schema observed live (see the module docstring in
`rankings.py` for the live findings these fixtures mirror).
"""

from __future__ import annotations

import polars as pl
import pytest

import nuclearff.nflverse.rankings as rankings


def _rankings_df() -> pl.DataFrame:
    """A stand-in for `nflreadpy.load_ff_rankings(type="draft")`.

    Mirrors the real schema: FantasyPros `id`, `pos`/`team` (not
    `position`/`nfl_team`), `ecr` as a float, and multiple `page_type` boards
    in one frame — the redraft-overall/redraft-op split is real and is the
    single easiest thing to get wrong (`redraft-op` is superflex, not a
    synonym for the single-QB board).
    """
    return pl.DataFrame(
        {
            "player": ["A Back", "B Wideout", "C Passer", "D Tight", "E Kicker"],
            "id": [101, 102, 103, 104, 105],
            "pos": ["RB", "WR", "QB", "TE", "K"],
            "team": ["DET", "CIN", "BUF", "KC", "BAL"],
            "ecr": [2.5, 1.5, 20.0, 30.0, 180.0],
            "sd": [1.3, 0.9, 4.0, 6.0, 20.0],
            "best": [1, 1, 12, 20, 150],
            "worst": [7, 4, 30, 45, 210],
            "page_type": [
                "redraft-overall",
                "redraft-overall",
                "redraft-overall",
                "redraft-overall",
                "redraft-overall",
            ],
            "scrape_date": ["2026-08-28"] * 5,
        }
    )


def _superflex_row() -> pl.DataFrame:
    """One `redraft-op` (superflex) row, to prove page filtering is real."""
    return _rankings_df().head(1).with_columns(pl.lit("redraft-op").alias("page_type"))


def _crosswalk_df() -> pl.DataFrame:
    """A stand-in for the DynastyProcess `ff_playerids` crosswalk.

    `id` 104 is deliberately absent (an unresolvable player) and `id` 103
    appears twice (an ambiguous `fantasypros_id`, which must be dropped
    rather than guessed between).
    """
    return pl.DataFrame(
        {
            "fantasypros_id": [101, 102, 103, 103],
            "gsis_id": ["00-0001", "00-0002", "00-0003", "00-0004"],
            "sleeper_id": ["1001", "1002", "1003", "1004"],
        }
    )


def test_load_fantasypros_ecr_defaults_to_latest_draft_snapshot(monkeypatch):
    """Default call asks for type='draft' (latest), not the 1.8M-row archive."""
    calls: list[dict] = []
    monkeypatch.setattr(
        rankings.nflreadpy,
        "load_ff_rankings",
        lambda **kwargs: calls.append(kwargs) or _rankings_df(),
    )

    result = rankings.load_fantasypros_ecr()

    assert calls == [{"type": "draft"}]
    assert result.height == 5


def test_load_fantasypros_ecr_historical_requests_the_full_archive(monkeypatch):
    """historical=True switches to type='all', which carries every scrape_date."""
    calls: list[dict] = []
    monkeypatch.setattr(
        rankings.nflreadpy,
        "load_ff_rankings",
        lambda **kwargs: calls.append(kwargs) or _rankings_df(),
    )

    rankings.load_fantasypros_ecr(historical=True)

    assert calls == [{"type": "all"}]


def test_load_fantasypros_ecr_filters_to_the_requested_board(monkeypatch):
    """redraft-op (superflex) rows must not leak into a redraft-overall pull."""
    mixed = pl.concat([_rankings_df(), _superflex_row()], how="vertical")
    monkeypatch.setattr(rankings.nflreadpy, "load_ff_rankings", lambda **kwargs: mixed)

    result = rankings.load_fantasypros_ecr(rankings.REDRAFT_OVERALL)

    assert result.height == 5
    assert result["page_type"].unique().to_list() == ["redraft-overall"]


def test_load_fantasypros_ecr_unknown_board_warns_and_returns_empty(
    monkeypatch, caplog
):
    """An unrecognized page_type yields no rows and names what was available."""
    monkeypatch.setattr(
        rankings.nflreadpy, "load_ff_rankings", lambda **kwargs: _rankings_df()
    )

    with caplog.at_level("WARNING", logger="nuclearff.nflverse.rankings"):
        result = rankings.load_fantasypros_ecr("not-a-real-board")

    assert result.height == 0
    assert any("redraft-overall" in r.message for r in caplog.records)


def test_load_fantasypros_ecr_raises_on_missing_column(monkeypatch):
    """A schema drift missing a required column fails loudly, not silently."""
    broken = _rankings_df().drop("ecr")
    monkeypatch.setattr(rankings.nflreadpy, "load_ff_rankings", lambda **kwargs: broken)

    with pytest.raises(ValueError, match="load_fantasypros_ecr.*ecr"):
        rankings.load_fantasypros_ecr()


def test_ambiguous_fantasypros_ids_flags_only_repeated_ids():
    """Only the duplicated fantasypros_id (103) is reported as ambiguous."""
    result = rankings.ambiguous_fantasypros_ids(_crosswalk_df())

    assert sorted(result["fantasypros_id"].unique().to_list()) == [103]
    assert result.height == 2


def test_attach_player_ids_resolves_by_exact_id_and_skips_ambiguous():
    """Unambiguous IDs resolve; the ambiguous one stays null rather than guessing."""
    result = rankings.attach_player_ids(_rankings_df(), _crosswalk_df())
    by_player = dict(zip(result["player"], result["gsis_id"], strict=True))

    assert by_player["A Back"] == "00-0001"
    assert by_player["B Wideout"] == "00-0002"
    assert by_player["C Passer"] is None  # ambiguous fantasypros_id, not guessed
    assert by_player["D Tight"] is None  # absent from the crosswalk entirely


def test_attach_player_ids_never_fans_out_rows():
    """A duplicated crosswalk ID must not multiply ranking rows."""
    result = rankings.attach_player_ids(_rankings_df(), _crosswalk_df())

    assert result.height == _rankings_df().height


def test_attach_player_ids_raises_when_crosswalk_lacks_columns():
    broken = _crosswalk_df().drop("gsis_id")

    with pytest.raises(ValueError, match="attach_player_ids.*gsis_id"):
        rankings.attach_player_ids(_rankings_df(), broken)


def test_consensus_adp_normalizes_to_the_pipeline_schema():
    """Output carries the plan's column names, ECR-labeled, sorted best-first."""
    with_ids = rankings.attach_player_ids(_rankings_df(), _crosswalk_df())
    result = rankings.consensus_adp(with_ids)

    assert result["player_name"].to_list() == [
        "B Wideout",
        "A Back",
        "C Passer",
        "D Tight",
    ]  # K dropped by the default position filter; sorted by ecr ascending
    assert result["source"].unique().to_list() == ["fantasypros_ecr"]
    assert result["as_of_date"].unique().to_list() == ["2026-08-28"]
    assert result["adp_overall"].to_list() == [1.5, 2.5, 20.0, 30.0]


def test_consensus_adp_ranks_within_position():
    """adp_position_rank is per-position, not a slice of the overall order."""
    extra = pl.DataFrame(
        {
            "player": ["F Wideout"],
            "id": [106],
            "pos": ["WR"],
            "team": ["MIA"],
            "ecr": [12.0],
            "sd": [3.0],
            "best": [6],
            "worst": [20],
            "page_type": ["redraft-overall"],
            "scrape_date": ["2026-08-28"],
        }
    )
    frame = pl.concat([_rankings_df(), extra], how="vertical")

    result = rankings.consensus_adp(frame)
    by_player = dict(
        zip(result["player_name"], result["adp_position_rank"], strict=True)
    )

    assert by_player["B Wideout"] == 1  # ecr 1.5, best WR
    assert by_player["F Wideout"] == 2  # ecr 12.0, second WR
    assert by_player["A Back"] == 1  # best (only) RB


def test_consensus_adp_keeps_the_consensus_spread():
    """sd/best/worst survive as ecr_* — the auction-relevant disagreement signal."""
    result = rankings.consensus_adp(_rankings_df())
    row = result.filter(pl.col("player_name") == "B Wideout")

    assert row["ecr_sd"][0] == pytest.approx(0.9)
    assert row["ecr_best"][0] == 1
    assert row["ecr_worst"][0] == 4


def test_consensus_adp_drops_rows_with_no_consensus_rank():
    """A player with a null ecr has no market value to report."""
    frame = _rankings_df().with_columns(
        pl.when(pl.col("player") == "A Back")
        .then(None)
        .otherwise(pl.col("ecr"))
        .alias("ecr")
    )

    result = rankings.consensus_adp(frame)

    assert "A Back" not in result["player_name"].to_list()


def test_consensus_adp_works_without_the_id_join():
    """Calling before attach_player_ids still produces the schema, with null IDs."""
    result = rankings.consensus_adp(_rankings_df())

    assert result["gsis_id"].null_count() == result.height
    assert "adp_position_rank" in result.columns


def test_consensus_adp_position_filter_is_caller_controlled():
    """Passing a wider tuple retains K/DST rows the default drops."""
    result = rankings.consensus_adp(_rankings_df(), positions=("K",))

    assert result["player_name"].to_list() == ["E Kicker"]
