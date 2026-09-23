"""Known curated dataset schemas.

One :class:`~nuclearff.data.schema.DatasetSchema` per dataset the cloud data
platform (GitHub Issue 182) publishes, defined once here rather than
re-declared at every write site. :data:`PLAYER_WEEK_SCHEMA` is guide §10's
own worked example, reproduced exactly (natural key ``season, week,
player_id``; ``season``/``week``/``player_id``/``player_name``/``team`` all
required; weeks 1-22). Nothing writes real ``player_week`` data through this
schema yet -- that lands with the publisher (GitHub Issue 186), which will
add schemas for the other curated datasets (``matchups``, ``transactions``,
``draft_history``, ``rankings``) as each is actually wired up, following this
same pattern.
"""

from __future__ import annotations

import polars as pl

from nuclearff.data.schema import DatasetSchema

PLAYER_WEEK_SCHEMA = DatasetSchema(
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
