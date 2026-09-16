"""Unit tests for nuclearff.archive.awards: season awards (issue #113)."""

from __future__ import annotations

import polars as pl

from nuclearff.archive.awards import (
    KIND_BEST_REGULAR_SEASON,
    KIND_BIGGEST_IMPROVEMENT,
    KIND_CINDERELLA_RUN,
    season_awards,
)


def _standings(rows: list[dict[str, object]]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "roster_id": pl.Int64,
        "display_name": pl.String,
        "fpts": pl.Float64,
        "regular_season_rank": pl.Int64,
        "final_rank": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def _row(
    *,
    league_id: str = "L1",
    season: int,
    roster_id: int,
    display_name: str | None,
    fpts: float,
    regular_season_rank: int | None,
    final_rank: int | None = None,
) -> dict[str, object]:
    return {
        "league_id": league_id,
        "season": season,
        "roster_id": roster_id,
        "display_name": display_name,
        "fpts": fpts,
        "regular_season_rank": regular_season_rank,
        "final_rank": final_rank,
    }


def _awards_by_kind(awards: pl.DataFrame) -> dict[str, dict[str, object]]:
    return {row["kind"]: row for row in awards.iter_rows(named=True)}


def test_best_regular_season_picks_the_highest_fpts_number_one_seed():
    standings = _standings(
        [
            _row(
                season=2024,
                roster_id=1,
                display_name="Alice",
                fpts=1500.0,
                regular_season_rank=1,
            ),
            _row(
                season=2025,
                roster_id=1,
                display_name="Bob",
                fpts=2000.0,
                regular_season_rank=1,
            ),
            _row(
                season=2025,
                roster_id=2,
                display_name="Carol",
                fpts=1800.0,
                regular_season_rank=2,
            ),
        ]
    )

    awards = _awards_by_kind(season_awards(standings))

    row = awards[KIND_BEST_REGULAR_SEASON]
    assert row["season"] == 2025
    assert row["manager"] == "Bob"
    assert row["value"] == 2000.0


def test_cinderella_run_picks_the_champion_with_the_worst_regular_season_seed():
    standings = _standings(
        [
            _row(
                season=2025,
                roster_id=1,
                display_name="Alice",
                fpts=1400.0,
                regular_season_rank=6,
                final_rank=1,
            ),
            _row(
                season=2025,
                roster_id=2,
                display_name="Bob",
                fpts=1900.0,
                regular_season_rank=1,
                final_rank=2,
            ),
            _row(
                season=2024,
                roster_id=1,
                display_name="Carol",
                fpts=1600.0,
                regular_season_rank=2,
                final_rank=1,
            ),
        ]
    )

    awards = _awards_by_kind(season_awards(standings))

    row = awards[KIND_CINDERELLA_RUN]
    assert row["season"] == 2025
    assert row["manager"] == "Alice"
    assert row["value"] == 5.0  # regular_season_rank 6, minus 1


def test_biggest_improvement_requires_the_same_manager_in_consecutive_seasons():
    standings = _standings(
        [
            _row(
                season=2023,
                roster_id=1,
                display_name="Alice",
                fpts=1000.0,
                regular_season_rank=5,
            ),
            _row(
                season=2024,
                roster_id=1,
                display_name="Alice",
                fpts=1500.0,
                regular_season_rank=1,
            ),
            # Dave only ever appears once -- no prior season to compare against.
            _row(
                season=2024,
                roster_id=2,
                display_name="Dave",
                fpts=2000.0,
                regular_season_rank=2,
            ),
        ]
    )

    awards = _awards_by_kind(season_awards(standings))

    row = awards[KIND_BIGGEST_IMPROVEMENT]
    assert row["season"] == 2024
    assert row["manager"] == "Alice"
    assert row["value"] == 500.0


def test_biggest_improvement_ignores_a_gap_season():
    """A manager who skipped a season (not truly consecutive) doesn't qualify."""
    standings = _standings(
        [
            _row(
                season=2022,
                roster_id=1,
                display_name="Alice",
                fpts=1000.0,
                regular_season_rank=5,
            ),
            # Alice is absent in 2023.
            _row(
                season=2024,
                roster_id=1,
                display_name="Alice",
                fpts=5000.0,
                regular_season_rank=1,
            ),
        ]
    )

    awards = season_awards(standings)

    assert KIND_BIGGEST_IMPROVEMENT not in _awards_by_kind(awards)


def test_missing_display_name_is_excluded_from_every_award():
    standings = _standings(
        [
            _row(
                season=2025,
                roster_id=1,
                display_name=None,
                fpts=9999.0,
                regular_season_rank=1,
                final_rank=1,
            ),
        ]
    )

    awards = season_awards(standings)

    assert awards.height == 0


def test_award_absent_entirely_when_no_season_qualifies():
    """No completed season (no resolvable final_rank) yet -- no cinderella_run row."""
    standings = _standings(
        [
            _row(
                season=2026,
                roster_id=1,
                display_name="Alice",
                fpts=100.0,
                regular_season_rank=1,
                final_rank=None,
            ),
        ]
    )

    awards = _awards_by_kind(season_awards(standings))

    assert KIND_CINDERELLA_RUN not in awards
    assert KIND_BEST_REGULAR_SEASON in awards


def test_empty_input_returns_empty_frame_with_expected_columns():
    awards = season_awards(_standings([]))

    assert awards.height == 0
    assert awards.columns == ["kind", "season", "manager", "value"]
