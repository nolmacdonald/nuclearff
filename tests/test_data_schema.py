"""Unit tests for dataset contracts and their pre-publication checks."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.data.schema import DatasetSchema, validate_frame
from nuclearff.exceptions import DataQualityError

SCHEMA = DatasetSchema(
    name="player_week",
    columns={
        "season": pl.Int64,
        "week": pl.Int64,
        "player_id": pl.String,
        "player_name": pl.String,
        "team": pl.String,
    },
    natural_key=("season", "week", "player_id"),
    value_ranges={"week": (1, 22)},
)


def _valid_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2026, 2026],
            "week": [1, 1],
            "player_id": ["1001", "1002"],
            "player_name": ["Player One", "Player Two"],
            "team": ["SEA", "SF"],
        }
    )


# --- DatasetSchema construction ---------------------------------------------


def test_dataset_schema_rejects_a_natural_key_column_not_in_columns():
    with pytest.raises(ValueError, match="natural_key"):
        DatasetSchema(
            name="bad",
            columns={"a": pl.Int64},
            natural_key=("a", "b"),
        )


def test_dataset_schema_rejects_a_nullable_column_not_in_columns():
    with pytest.raises(ValueError, match="nullable"):
        DatasetSchema(
            name="bad",
            columns={"a": pl.Int64},
            natural_key=("a",),
            nullable=frozenset({"b"}),
        )


# --- validate_frame ----------------------------------------------------


def test_validate_frame_accepts_a_valid_frame():
    validate_frame(SCHEMA, _valid_frame())


def test_validate_frame_rejects_a_missing_column():
    frame = _valid_frame().drop("team")

    with pytest.raises(DataQualityError, match="missing required column"):
        validate_frame(SCHEMA, frame)


def test_validate_frame_rejects_a_mistyped_column():
    frame = _valid_frame().with_columns(pl.col("season").cast(pl.String))

    with pytest.raises(DataQualityError, match="dtype"):
        validate_frame(SCHEMA, frame)


def test_validate_frame_rejects_too_few_rows():
    schema = DatasetSchema(
        name="min3", columns={"a": pl.Int64}, natural_key=("a",), min_rows=3
    )

    with pytest.raises(DataQualityError, match="below the minimum"):
        validate_frame(schema, pl.DataFrame({"a": [1, 2]}))


def test_validate_frame_rejects_too_many_rows():
    schema = DatasetSchema(
        name="max1", columns={"a": pl.Int64}, natural_key=("a",), max_rows=1
    )

    with pytest.raises(DataQualityError, match="exceeds the maximum"):
        validate_frame(schema, pl.DataFrame({"a": [1, 2]}))


def test_validate_frame_rejects_an_empty_frame_by_default():
    with pytest.raises(DataQualityError, match="below the minimum"):
        validate_frame(SCHEMA, _valid_frame().clear())


def test_validate_frame_rejects_a_null_in_a_non_nullable_column():
    frame = _valid_frame().with_columns(pl.Series("player_name", ["Player One", None]))

    with pytest.raises(DataQualityError, match="player_name.*null"):
        validate_frame(SCHEMA, frame)


def test_validate_frame_allows_a_null_in_a_declared_nullable_column():
    schema = DatasetSchema(
        name="nullable-team",
        columns={"player_id": pl.String, "team": pl.String},
        natural_key=("player_id",),
        nullable=frozenset({"team"}),
    )
    frame = pl.DataFrame({"player_id": ["1", "2"], "team": ["SEA", None]})

    validate_frame(schema, frame)


def test_validate_frame_rejects_a_value_outside_its_declared_range():
    frame = _valid_frame().with_columns(pl.Series("week", [1, 25]))

    with pytest.raises(DataQualityError, match=r"week.*\[1, 22\]"):
        validate_frame(SCHEMA, frame)


def test_validate_frame_rejects_a_duplicate_natural_key():
    frame = pl.concat([_valid_frame(), _valid_frame()])

    with pytest.raises(DataQualityError, match="duplicate natural key"):
        validate_frame(SCHEMA, frame)
