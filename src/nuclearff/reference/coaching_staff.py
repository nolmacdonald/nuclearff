"""Coordinator/play-caller reference data — a curated table nflverse doesn't track.

Built for GitHub Issue 215 (epic #213): "what changed in the offense" needs to
know who was calling plays, and no nflverse loader tracks coordinators or
play-callers at all (confirmed by column scan of ``load_schedules``, which
carries only ``home_coach``/``away_coach`` — the head coach, already wrapped
by :func:`nuclearff.nflverse.schedules.load_schedules`). This module owns a
small, hand-curated, cited CSV instead (:data:`_CSV_PATH`) — not scraped, not
guessed.

Why ``src/nuclearff/reference/`` and not the ``data/reference/`` path the
issue sketched: this project's ``.gitignore`` matches a bare ``data/``
anywhere in the tree (generated caches, raw snapshots), with a single,
narrow carve-out for the ``src/nuclearff/data/`` *package* — a repo-root
``data/reference/coaching_staff.csv`` would be silently swallowed by that
pattern (confirmed by reading ``.gitignore`` directly, the same "don't
assume, check" posture as everything else in this project). Shipping it
under ``src/nuclearff/`` instead sidesteps that entirely and has a real
second benefit the issue's own path doesn't: it ships as real package data
inside the built wheel, so ``load_coaching_staff`` also works for anyone who
installs ``nuclearff`` as a dependency (e.g. ``nuclearff_dashboard``), not
only inside a git checkout.

Every row must cite a real ``source_url`` — :func:`load_coaching_staff`
raises rather than silently accepting a placeholder or missing citation. The
five rows shipped today (Tampa Bay OC, 2022-2026) were verified live via web
search this session, not transcribed from the epic's own motivating example
text: that text names the 2025 coordinator only as "Grizzard", which this
module's own research confirms and completes as Josh Grizzard, promoted from
pass-game coordinator in February 2025 and fired after the season (January 8
2026, not mid-season, despite Tampa Bay's offense fading after the bye that
year) — a detail worth having gotten right independently rather than
assumed from the issue body.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

_CSV_PATH = Path(__file__).parent / "coaching_staff.csv"
"""Location of the curated coaching-staff CSV, shipped as package data."""

_COLUMNS = (
    "season",
    "team",
    "role",
    "name",
    "is_play_caller",
    "start_week",
    "end_week",
    "source_url",
    "notes",
)

_SCHEMA_OVERRIDES = {
    "season": pl.Int32,
    "team": pl.String,
    "role": pl.String,
    "name": pl.String,
    "is_play_caller": pl.Boolean,
    "start_week": pl.Int32,
    "end_week": pl.Int32,
    "source_url": pl.String,
    "notes": pl.String,
}
"""Passed to ``pl.read_csv`` so ``is_play_caller`` parses as a real boolean
and a blank ``end_week`` cell (a still-current or full-season role) reads as
null rather than an empty string."""

_REQUIRED_NOT_NULL = (
    "season",
    "team",
    "role",
    "name",
    "is_play_caller",
    "start_week",
    "source_url",
)
"""Every column but ``end_week`` (null means "through the end of the
season," not missing data) and ``notes`` (genuinely optional)."""


def _validate(df: pl.DataFrame) -> None:
    """Fail loudly on a malformed coaching-staff table, per issue #215.

    Args:
        df: The raw table, as read from :data:`_CSV_PATH`.

    Raises:
        ValueError: If a required column is missing, a required column has
            a null value, a row's ``source_url`` doesn't look like a real
            URL, a row's ``end_week`` is before its ``start_week``, or two
            rows for the same ``(season, team, role)`` claim overlapping
            week ranges.
    """
    missing_columns = [c for c in _COLUMNS if c not in df.columns]
    if missing_columns:
        raise ValueError(
            f"load_coaching_staff: coaching_staff.csv is missing expected "
            f"column(s) {missing_columns!r} (got {df.columns!r})."
        )

    for column in _REQUIRED_NOT_NULL:
        n_null = df[column].null_count()
        if n_null:
            raise ValueError(
                f"load_coaching_staff: {n_null} row(s) have a null {column!r} "
                f"— every row needs a real value there."
            )

    not_a_url = df.filter(~pl.col("source_url").str.starts_with("http"))
    if not_a_url.height:
        examples = not_a_url.select("season", "team", "name").rows()
        raise ValueError(
            f"load_coaching_staff: {not_a_url.height} row(s) have a "
            f"source_url that doesn't start with 'http' — a placeholder or "
            f"missing citation, e.g. {examples}. Every row must cite where "
            f"it was verified."
        )

    backwards = df.filter(
        pl.col("end_week").is_not_null() & (pl.col("end_week") < pl.col("start_week"))
    )
    if backwards.height:
        raise ValueError(
            f"load_coaching_staff: {backwards.height} row(s) have end_week "
            f"before start_week: {backwards.select('season', 'team', 'name').rows()}."
        )

    _check_no_overlapping_ranges(df)


def _check_no_overlapping_ranges(df: pl.DataFrame) -> None:
    """Raise if two rows for the same (season, team, role) overlap in weeks.

    Deliberately plain Python over ``df.to_dicts()`` rather than a
    vectorized Polars self-join: this table is, and will stay, at most a
    few hundred rows (one row per coordinator change, ever), so there is no
    real performance reason to reach for a harder-to-read vectorized
    approach — see the module docstring's broader "don't guess, verify"
    posture applied here to engineering effort, not just data accuracy.

    Args:
        df: The raw table, as read from :data:`_CSV_PATH`.

    Raises:
        ValueError: On the first overlapping pair found.
    """
    groups: dict[tuple[int, str, str], list[dict]] = {}
    for row in df.to_dicts():
        key = (row["season"], row["team"], row["role"])
        groups.setdefault(key, []).append(row)

    for key, rows in groups.items():
        rows.sort(key=lambda r: r["start_week"])
        for previous, current in zip(rows, rows[1:], strict=False):
            previous_end = (
                previous["end_week"]
                if previous["end_week"] is not None
                else float("inf")
            )
            if current["start_week"] <= previous_end:
                raise ValueError(
                    f"load_coaching_staff: overlapping week ranges for "
                    f"{key!r} — {previous['name']!r} "
                    f"(weeks {previous['start_week']}-{previous['end_week']}) and "
                    f"{current['name']!r} "
                    f"(weeks {current['start_week']}-{current['end_week']})."
                )


def load_coaching_staff(seasons: Sequence[int] | None = None) -> pl.DataFrame:
    """Load the curated coaching-staff table, validated.

    Args:
        seasons: Restrict to these seasons. ``None`` (the default) returns
            every season currently curated.

    Returns:
        One row per coordinator/play-caller stint, keyed by :data:`_COLUMNS`,
        sorted by season, team, and start week. Empty (not an error) if
        ``seasons`` matches nothing curated yet — this table covers only
        Tampa Bay so far (see the module docstring); a request for another
        team's season isn't a bug, just data not yet backfilled.

    Raises:
        ValueError: If the table fails validation — see :func:`_validate`.
    """
    df = pl.read_csv(_CSV_PATH, schema_overrides=_SCHEMA_OVERRIDES)
    _validate(df)

    if seasons is not None:
        df = df.filter(pl.col("season").is_in(list(seasons)))

    return df.sort(["season", "team", "start_week"])


def join_coaching_staff(
    df: pl.DataFrame, coaching_staff: pl.DataFrame, *, team_column: str = "posteam"
) -> pl.DataFrame:
    """Attach each ``(season, team_column, week)`` row's coordinator/play-caller.

    ``coaching_staff`` should already be filtered to the one role wanted
    (e.g. ``load_coaching_staff().filter(pl.col("role") == "OC")``) before
    calling this — a ``df`` row matching more than one ``coaching_staff`` row
    (two different roles, or an overlapping week range
    :func:`load_coaching_staff` should already have rejected) raises rather
    than silently picking one, the same "never guess" posture as
    :func:`nuclearff.metrics.efficiency.join_routes`.

    Args:
        df: Rows to attach a coordinator to — needs ``season``, ``week``, and
            ``team_column`` (real play-by-play's own team column is
            ``posteam``, the default, not a generic ``"team"``).
        coaching_staff: :func:`load_coaching_staff` output (or a subset of
            it), pre-filtered to one role.
        team_column: The column in ``df`` naming the team to match against
            ``coaching_staff``'s own ``team`` column.

    Returns:
        ``df`` with ``coach_name`` and ``coach_is_play_caller`` added, null
        wherever no curated row covers that ``(season, team, week)`` —
        real and expected for any season/team this table hasn't been
        backfilled for yet, not an error.

    Raises:
        ValueError: If a required column is missing from either input, or a
            ``(season, team_column, week)`` combination in ``df`` matches
            more than one ``coaching_staff`` row.
    """
    if "season" not in df.columns or "week" not in df.columns:
        raise ValueError(
            "join_coaching_staff: df must have 'season' and 'week' columns "
            f"(got {df.columns!r})."
        )
    if team_column not in df.columns:
        raise ValueError(
            f"join_coaching_staff: df has no {team_column!r} column "
            f"(got {df.columns!r}); pass team_column= if your team column "
            f"is named something other than the play-by-play default "
            f"'posteam'."
        )
    missing = [c for c in _COLUMNS if c not in coaching_staff.columns]
    if missing:
        raise ValueError(
            f"join_coaching_staff: coaching_staff is missing expected "
            f"column(s) {missing!r} — pass load_coaching_staff() output."
        )

    weeks = df.select(
        pl.col("season"), pl.col(team_column).alias("team"), pl.col("week")
    ).unique()
    candidates = weeks.join(coaching_staff, on=["season", "team"], how="left")
    in_range = candidates.filter(
        pl.col("start_week").is_null()
        | (
            (pl.col("week") >= pl.col("start_week"))
            & (pl.col("end_week").is_null() | (pl.col("week") <= pl.col("end_week")))
        )
    )

    duplicated = (
        in_range.group_by(["season", "team", "week"])
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
    )
    if duplicated.height:
        raise ValueError(
            f"join_coaching_staff: {duplicated.height} (season, team, week) "
            f"combination(s) match more than one coaching_staff row — pass "
            f"coaching_staff already filtered to a single role, e.g. "
            f".filter(pl.col('role') == 'OC')."
        )

    lookup = in_range.select(
        pl.col("team").alias(team_column),
        "season",
        "week",
        pl.col("name").alias("coach_name"),
        pl.col("is_play_caller").alias("coach_is_play_caller"),
    )
    return df.join(lookup, on=["season", "week", team_column], how="left")
