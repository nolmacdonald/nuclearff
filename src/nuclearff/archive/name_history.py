"""League history archive: team name change history (issue #130, epic #116).

Turns ``sleeper_roster_names`` (:mod:`nuclearff.sleeper.roster_names`,
issue #130's persistence half) into each manager's chronological team-name
history and a real change count.

**Keyed by ``owner_id``, not ``display_name``** — a deliberate deviation
from this project's usual manager-identity convention, per issue #130's own
note: ``owner_id`` is the real stable key across a manager's roster
history, while ``display_name`` (the Sleeper account username) is a
separate field that happens to double as identity elsewhere in this
project but isn't the subject here. A ``manager`` display name is still
resolved (from ``sleeper_standings``, most recent known value) for
readability, not as the join key.

**Season-grain only.** A single per-season roster snapshot can't see a
name changed more than once within one season — only the value present at
fetch time. Not solved here, per issue #130's own non-goals.
"""

from __future__ import annotations

import polars as pl

_SCHEMA = {
    "owner_id": pl.String,
    "manager": pl.String,
    "name_sequence": pl.List(pl.String),
    "change_count": pl.UInt32,
}


def _count_real_changes(names: list[str | None]) -> int:
    """Season-over-season transitions between two *non-null* names only.

    A null entry ("no custom name set that season") never itself counts as
    a name change, and never breaks the comparison across it — going
    unnamed for a season doesn't erase whether the name before and after
    differ. Counting transitions (not distinct-name cardinality) also means
    a name reused later doesn't inflate the count: A, B, A is 2 real
    changes, not "2 distinct names used."
    """
    real = [name for name in names if name is not None]
    return sum(
        1 for earlier, later in zip(real, real[1:], strict=False) if earlier != later
    )


def team_name_changes(
    roster_names: pl.DataFrame, standings: pl.DataFrame
) -> pl.DataFrame:
    """Each manager's chronological team-name history and real change count.

    Args:
        roster_names: ``sleeper_roster_names`` rows — needs ``owner_id``,
            ``season``, ``team_name``.
        standings: ``sleeper_standings`` rows, for resolving a readable
            ``manager`` display name per ``owner_id`` (most recent known
            value) — not used as the join key itself.

    Returns:
        One row per ``owner_id`` with at least one roster row: ``manager``,
        ``name_sequence`` (one entry per season, chronological, ``None``
        for a season with no custom name set — a real state, not omitted),
        ``change_count`` (real season-over-season changes between
        non-null names only), sorted by ``change_count`` descending — the
        "most name changes" leaderboard is this same table.
    """
    if roster_names.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    with_owner = roster_names.filter(pl.col("owner_id").is_not_null()).sort(
        ["owner_id", "season"]
    )
    if with_owner.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    sequences = with_owner.group_by("owner_id", maintain_order=True).agg(
        pl.col("team_name").alias("name_sequence")
    )
    sequences = sequences.with_columns(
        pl.col("name_sequence")
        .map_elements(_count_real_changes, return_dtype=pl.UInt32)
        .alias("change_count")
    )

    managers = (
        standings.filter(
            pl.col("owner_id").is_not_null() & pl.col("display_name").is_not_null()
        )
        .sort(["owner_id", "season"])
        .group_by("owner_id", maintain_order=True)
        .agg(pl.col("display_name").last().alias("manager"))
    )

    result = sequences.join(managers, on="owner_id", how="left")
    return result.select(list(_SCHEMA.keys())).sort("change_count", descending=True)
