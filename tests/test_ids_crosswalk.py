"""Unit tests for resolving Sleeper player IDs against the ff_playerids crosswalk."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.exceptions import StorageError
from nuclearff.ids.crosswalk import (
    SLEEPER_PLAYERS_TABLE,
    TABLE_NAME,
    ambiguous_sleeper_ids,
    read_sleeper_players,
    resolve_missing_gsis_ids,
    write_player_id_map,
)
from nuclearff.sleeper.players import write_players_table


@pytest.fixture
def players() -> pl.DataFrame:
    """Four Sleeper players covering every resolution outcome."""
    return pl.DataFrame(
        {
            "player_id": ["4046", "100", "200", "300"],
            "full_name": [
                "Patrick Mahomes",
                "Has Sleeper GSIS",
                "Needs Crosswalk",
                "Unmatched",
            ],
            "position": ["QB", "WR", "WR", "WR"],
            "team": ["KC", "AAA", "BBB", "CCC"],
            "gsis_id": ["00-0033873", "00-EXISTING", None, None],
        }
    )


@pytest.fixture
def ff_ids() -> pl.DataFrame:
    """A crosswalk sample with a clean match, a gap-filler, and an ambiguity."""
    return pl.DataFrame(
        {
            "sleeper_id": [4046, 200, 999, 999],
            "gsis_id": ["00-0033873", "00-FROM-CROSSWALK", "00-AMBIG-A", "00-AMBIG-B"],
            "name": [
                "Patrick Mahomes",
                "Needs Crosswalk",
                "Ambiguous A",
                "Ambiguous B",
            ],
        },
        schema={"sleeper_id": pl.Int64, "gsis_id": pl.Utf8, "name": pl.Utf8},
    )


def test_ambiguous_sleeper_ids_finds_duplicates(ff_ids):
    """Only the repeated sleeper_id is reported, not the unique ones."""
    result = ambiguous_sleeper_ids(ff_ids)

    assert result["sleeper_id"].to_list() == [999, 999]
    assert set(result["name"].to_list()) == {"Ambiguous A", "Ambiguous B"}


def test_ambiguous_sleeper_ids_ignores_nulls(ff_ids):
    """Two crosswalk rows both missing sleeper_id are not 'duplicates' of each other."""
    with_nulls = pl.concat(
        [
            ff_ids,
            pl.DataFrame(
                {
                    "sleeper_id": [None, None],
                    "gsis_id": [None, None],
                    "name": [None, None],
                },
                schema=ff_ids.schema,
            ),
        ]
    )

    result = ambiguous_sleeper_ids(with_nulls)

    assert result["sleeper_id"].to_list() == [999, 999]


def test_resolve_missing_gsis_ids_prefers_existing_sleeper_value(players, ff_ids):
    """A player who already has a gsis_id from Sleeper keeps it untouched."""
    resolved = resolve_missing_gsis_ids(players, ff_ids)
    row = resolved.filter(pl.col("player_id") == "4046").row(0, named=True)

    assert row["gsis_id"] == "00-0033873"
    assert row["gsis_id_source"] == "sleeper"


def test_resolve_missing_gsis_ids_fills_gap_from_crosswalk(players, ff_ids):
    """A player with no Sleeper gsis_id gets filled from the crosswalk."""
    resolved = resolve_missing_gsis_ids(players, ff_ids)
    row = resolved.filter(pl.col("player_id") == "200").row(0, named=True)

    assert row["gsis_id"] == "00-FROM-CROSSWALK"
    assert row["gsis_id_source"] == "ff_playerids"


def test_resolve_missing_gsis_ids_skips_ambiguous_crosswalk_rows(players, ff_ids):
    """A crosswalk sleeper_id with two candidates never fills a gap."""
    players_with_ambiguous = pl.concat(
        [
            players,
            pl.DataFrame(
                {
                    "player_id": ["999"],
                    "full_name": ["Ambiguous Target"],
                    "position": ["WR"],
                    "team": ["DDD"],
                    "gsis_id": [None],
                },
                schema=players.schema,
            ),
        ]
    )

    resolved = resolve_missing_gsis_ids(players_with_ambiguous, ff_ids)
    row = resolved.filter(pl.col("player_id") == "999").row(0, named=True)

    assert row["gsis_id"] is None
    assert row["gsis_id_source"] is None


def test_resolve_missing_gsis_ids_leaves_unmatched_players_unresolved(players, ff_ids):
    """A player absent from the crosswalk entirely stays unresolved."""
    resolved = resolve_missing_gsis_ids(players, ff_ids)
    row = resolved.filter(pl.col("player_id") == "300").row(0, named=True)

    assert row["gsis_id"] is None
    assert row["gsis_id_source"] is None


def test_read_sleeper_players_missing_table_hints_at_fetch_command(tmp_path):
    """A missing sleeper_players table points the user at the command to run."""
    with pytest.raises(StorageError, match="nuclearff sleeper fetch-players"):
        read_sleeper_players(tmp_path / "empty.duckdb")


def test_read_sleeper_players_reads_what_fetch_players_wrote(tmp_path):
    """read_sleeper_players round-trips through the real write path."""
    db_path = tmp_path / "test.duckdb"
    write_players_table(
        {"4046": {"full_name": "Patrick Mahomes", "position": "QB"}}, db_path
    )

    frame = read_sleeper_players(db_path)

    assert frame["full_name"].to_list() == ["Patrick Mahomes"]


def test_write_player_id_map_round_trips(players, ff_ids, tmp_path):
    """The resolved frame lands in DuckDB with the expected columns."""
    db_path = tmp_path / "test.duckdb"
    resolved = resolve_missing_gsis_ids(players, ff_ids)

    count = write_player_id_map(resolved, db_path)

    assert count == 4
    from nuclearff.duckdb_io import read_table

    stored = read_table(db_path, TABLE_NAME)
    sources_by_id = dict(
        zip(stored["player_id"], stored["gsis_id_source"], strict=True)
    )
    assert sources_by_id == {
        "4046": "sleeper",
        "100": "sleeper",
        "200": "ff_playerids",
        "300": None,
    }


def test_sleeper_players_table_name_constant_matches_the_writer():
    """The crosswalk reads the same table name the Sleeper writer uses."""
    from nuclearff.sleeper.players import TABLE_NAME as WRITER_TABLE_NAME

    assert SLEEPER_PLAYERS_TABLE == WRITER_TABLE_NAME
