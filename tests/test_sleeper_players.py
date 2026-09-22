"""Unit tests for normalizing and storing the Sleeper player map."""

from __future__ import annotations

import duckdb
import pytest

from nuclearff.sleeper.players import TABLE_NAME, player_rows, write_players_table


@pytest.fixture
def players_payload() -> dict[str, dict]:
    """A tiny player map covering a normal player and a team-defense entry."""
    return {
        "4046": {
            "player_id": "4046",
            "sport": "nfl",
            "first_name": "Patrick",
            "last_name": "Mahomes",
            "full_name": "Patrick Mahomes",
            "search_full_name": "patrickmahomes",
            "position": "QB",
            "fantasy_positions": ["QB"],
            "team": "KC",
            "status": "Active",
            "active": True,
            "injury_status": None,
            "age": 30,
            "years_exp": 8,
            "college": "Texas Tech",
            "gsis_id": "00-0033873",
            "espn_id": 3139477,
            "yahoo_id": 30123,
            "sportradar_id": "some-uuid",
            "rotowire_id": 12345,
            "fantasy_data_id": 18890,
            "stats_id": None,
            "swish_id": None,
            "pandascore_id": None,
            "high_school": "Whitehouse",  # not a kept column
        },
        # Team-defense entries carry a null player_id in the payload itself;
        # the dict key is what must be trusted.
        "SF": {
            "player_id": None,
            "sport": "nfl",
            "first_name": None,
            "last_name": None,
            "full_name": "San Francisco 49ers",
            "position": "DEF",
            "fantasy_positions": ["DEF"],
            "team": "SF",
        },
    }


def test_player_rows_uses_the_dict_key_as_player_id(players_payload):
    """The dict key wins over a null or mismatched player_id field."""
    rows = {row["player_id"]: row for row in player_rows(players_payload)}

    assert set(rows) == {"4046", "SF"}
    assert rows["4046"]["full_name"] == "Patrick Mahomes"
    assert rows["4046"]["gsis_id"] == "00-0033873"
    assert rows["SF"]["full_name"] == "San Francisco 49ers"


def test_player_rows_drops_columns_not_in_the_kept_set(players_payload):
    """Fields outside the curated column set are not carried through."""
    rows = player_rows(players_payload)

    assert "high_school" not in rows[0]


def test_player_rows_skips_non_object_entries(players_payload):
    """A malformed entry is skipped rather than raising."""
    players_payload["bogus"] = "not-a-player"

    rows = player_rows(players_payload)

    assert {row["player_id"] for row in rows} == {"4046", "SF"}


def test_write_players_table_round_trips_through_duckdb(players_payload, tmp_path):
    """Rows written to DuckDB match the flattened player rows."""
    db_path = tmp_path / "nuclearff.duckdb"

    count = write_players_table(players_payload, db_path)

    assert count == 2
    assert db_path.is_file()

    with duckdb.connect(str(db_path)) as conn:
        result = conn.execute(
            f"SELECT player_id, full_name, gsis_id FROM {TABLE_NAME} ORDER BY player_id"
        ).fetchall()

    assert result == [
        ("4046", "Patrick Mahomes", "00-0033873"),
        ("SF", "San Francisco 49ers", None),
    ]


def test_write_players_table_replaces_rather_than_appends(players_payload, tmp_path):
    """A second write reflects only the latest snapshot, not both."""
    db_path = tmp_path / "nuclearff.duckdb"

    write_players_table(players_payload, db_path)
    smaller_payload = {"4046": players_payload["4046"]}
    count = write_players_table(smaller_payload, db_path)

    assert count == 1
    with duckdb.connect(str(db_path)) as conn:
        (total,) = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()

    assert total == 1


def test_write_players_table_skips_rewrite_when_unchanged(players_payload, tmp_path):
    """An identical second fetch never triggers the DROP + reinsert.

    Proven directly, not just by return value: a row manually inserted
    between the two writes (something only a real rewrite would ever
    remove) must still be there afterward if the rewrite was truly
    skipped.
    """
    db_path = tmp_path / "nuclearff.duckdb"
    write_players_table(players_payload, db_path)

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            f"INSERT INTO {TABLE_NAME} (player_id, full_name) VALUES "
            "('sentinel', 'Should Survive')"
        )

    count = write_players_table(dict(players_payload), db_path)

    assert count == 3  # 2 real players + the sentinel row, unless rewritten
    with duckdb.connect(str(db_path)) as conn:
        survived = conn.execute(
            f"SELECT 1 FROM {TABLE_NAME} WHERE player_id = 'sentinel'"
        ).fetchone()
    assert survived is not None


def test_write_players_table_rejects_a_bad_table_name(players_payload, tmp_path):
    """A table name that is not a plain identifier is refused, not interpolated."""
    with pytest.raises(ValueError, match="plain identifier"):
        write_players_table(
            players_payload,
            tmp_path / "nuclearff.duckdb",
            table_name="players; DROP TABLE sleeper_players;",
        )
