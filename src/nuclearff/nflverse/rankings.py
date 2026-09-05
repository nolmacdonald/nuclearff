"""FantasyPros expert-consensus rankings (ECR) via ``nflreadpy``/DynastyProcess.

This is the market-value signal the auction/keeper valuation work needs: what
the consensus thinks a player is worth, to sit alongside this project's own
projection-driven VORP. ``nflreadpy.load_ff_rankings`` pulls FantasyPros'
published expert-consensus rankings through the DynastyProcess pipeline, so no
scraping and no FantasyPros account are involved.

ECR is not ADP
--------------
The technical plan asked for "ADP". **What this module returns is ECR, not
ADP, and they are different numbers.** ADP (average draft position) is
observed behavior — where players actually went in real drafts. ECR (expert
consensus ranking) is stated opinion — where a panel of analysts says players
*should* go. They correlate strongly at the top and diverge in the middle
rounds, where ADP carries real draft-room behavior (name recognition, positional
runs) that a ranking panel deliberately strips out.

The normalized output of :func:`consensus_adp` uses the plan's requested
``adp_*`` column names so the rest of the pipeline has one stable schema, but
every row is labeled ``source="fantasypros_ecr"``. Do not present these numbers
as ADP in a report without that label — the same honesty posture
:func:`nuclearff.nflverse.stats.load_routes` uses for its snap-count proxy.

Live findings (2026-09-02, nflreadpy 0.1.5)
-------------------------------------------
- ``load_ff_rankings(type="draft")`` returns 5,552 rows / 25 columns across 31
  ``page_type`` values (redraft, dynasty, and best-ball variants, each split
  overall and per position). ``page_type="redraft-overall"`` (517 rows) is the
  slice matching a single-QB redraft league; ``"redraft-op"`` is the
  superflex/2-QB board, **not** a synonym.
- ``type="draft"`` carries exactly one ``scrape_date`` — the latest snapshot.
- **Plan-contradicting finding:** ``type="all"`` *does* carry history —
  1,824,172 rows across 362 distinct ``scrape_date`` values spanning
  2019-12-27 to 2026-08-28. The plan assumed historical ADP might be
  unavailable ("If it only exposes 'latest', that's a real limitation"); it is
  available, at roughly weekly granularity. It is also ~1.8M rows, so
  :func:`load_fantasypros_ecr` only fetches it when explicitly asked.
- The rankings' ``id`` column is FantasyPros' own player ID and joins directly
  to ``fantasypros_id`` in the DynastyProcess crosswalk
  (:func:`nuclearff.nflverse.load_ff_playerids`) — both ``Int64``, no name
  matching required. Measured coverage on ``redraft-overall``: 471/517 rows
  (91%) resolve to a ``gsis_id``, rising to **446/450 (99.1%)** when restricted
  to QB/RB/WR/TE. The unresolved remainder is almost entirely team defenses and
  kickers, which have no ``gsis_id`` by nature.

Scoring format is not identifiable from this data: FantasyPros publishes
separate PPR/half-PPR/standard boards, but the DynastyProcess feed carries no
column saying which one a row came from. Treat the ECR as approximately-PPR
consensus and say so rather than implying it was matched to this league's exact
scoring rules.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import nflreadpy
import polars as pl

logger = logging.getLogger(__name__)

REDRAFT_OVERALL = "redraft-overall"
"""``page_type`` for the single-QB redraft consensus board."""

REDRAFT_SUPERFLEX = "redraft-op"
"""``page_type`` for the superflex/2-QB redraft board ("offensive player")."""

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
"""Positions :func:`consensus_adp` keeps by default."""

RANKINGS_SOURCE = "fantasypros_ecr"
"""Value written to the normalized ``source`` column. See the module docstring:
this is expert-consensus ranking, deliberately not labeled ``adp``."""

_RANKINGS_REQUIRED_COLUMNS = (
    "player",
    "id",
    "pos",
    "team",
    "ecr",
    "page_type",
    "scrape_date",
)

_CROSSWALK_REQUIRED_COLUMNS = ("fantasypros_id", "gsis_id", "sleeper_id")


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing an expected column.

    Args:
        df: The DataFrame an nflreadpy loader returned.
        required: Column names the caller of ``fn_name`` depends on.
        fn_name: Name of the wrapper function that needed these columns,
            included in the error message.

    Raises:
        ValueError: If any column in ``required`` is absent from ``df``.
    """
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{fn_name}: nflreadpy returned a DataFrame missing expected "
            f"column(s) {missing!r} (got {df.columns!r}). nflreadpy is an "
            f"experimental, actively-evolving library — its schema may have "
            f"changed; re-verify against the current version before "
            f"widening this check."
        )


def load_fantasypros_ecr(
    page_type: str = REDRAFT_OVERALL,
    *,
    historical: bool = False,
) -> pl.DataFrame:
    """Load FantasyPros expert-consensus rankings for one board.

    Delegates to ``nflreadpy.load_ff_rankings`` and filters to ``page_type``.

    Args:
        page_type: Which FantasyPros board to keep, e.g.
            :data:`REDRAFT_OVERALL` (single-QB redraft) or
            :data:`REDRAFT_SUPERFLEX`. An unrecognized value is not rejected
            here — it simply yields no rows, and this logs a warning naming
            the values that were actually available.
        historical: When ``False`` (default), fetch only the latest snapshot
            (``type="draft"``). When ``True``, fetch every archived snapshot
            (``type="all"``) — roughly 1.8M rows spanning 2019 to date, so
            only ask for it when back-testing against past seasons.

    Returns:
        The rankings rows for ``page_type``, with nflreadpy's own columns
        unchanged (``player``, ``id``, ``pos``, ``team``, ``ecr``, ``sd``,
        ``best``, ``worst``, ``scrape_date``, ...).

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    rankings_type = "all" if historical else "draft"
    logger.info(
        "Fetching FantasyPros ECR (nflreadpy load_ff_rankings type=%r, page_type=%r)",
        rankings_type,
        page_type,
    )
    df = nflreadpy.load_ff_rankings(type=rankings_type)
    _require_columns(df, _RANKINGS_REQUIRED_COLUMNS, "load_fantasypros_ecr")

    filtered = df.filter(pl.col("page_type") == page_type)
    if filtered.height == 0:
        logger.warning(
            "load_fantasypros_ecr: no rows for page_type=%r; available "
            "page_type values are %r",
            page_type,
            sorted(df["page_type"].unique().to_list()),
        )
    return filtered


def ambiguous_fantasypros_ids(ff_ids: pl.DataFrame) -> pl.DataFrame:
    """Return crosswalk rows whose ``fantasypros_id`` maps to more than one player.

    The same guard :func:`nuclearff.ids.crosswalk.ambiguous_sleeper_ids`
    applies to ``sleeper_id``: a duplicated ID is never used to resolve a
    player, because picking one of several candidates would be a fuzzy match
    wearing an exact-match costume.

    Args:
        ff_ids: The raw ff_playerids crosswalk
            (:func:`nuclearff.nflverse.load_ff_playerids`).

    Returns:
        Rows of ``ff_ids`` whose ``fantasypros_id`` repeats, sorted by that
        ID. Empty when every ``fantasypros_id`` is unique.
    """
    with_id = ff_ids.filter(pl.col("fantasypros_id").is_not_null())
    dupes = (
        with_id.group_by("fantasypros_id")
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
        .select("fantasypros_id")
    )
    return with_id.join(dupes, on="fantasypros_id", how="inner").sort("fantasypros_id")


def attach_player_ids(rankings: pl.DataFrame, ff_ids: pl.DataFrame) -> pl.DataFrame:
    """Join ``gsis_id``/``sleeper_id`` onto FantasyPros rankings by exact ID.

    Joins the rankings' FantasyPros ``id`` to the crosswalk's
    ``fantasypros_id``. Ambiguous crosswalk IDs are dropped first (see
    :func:`ambiguous_fantasypros_ids`), and no name-based fallback is
    attempted: an unresolved player keeps a null ``gsis_id`` and is counted in
    the coverage log line rather than being guessed at.

    Args:
        rankings: Output of :func:`load_fantasypros_ecr`.
        ff_ids: The raw ff_playerids crosswalk.

    Returns:
        ``rankings`` with ``gsis_id`` and ``sleeper_id`` columns added, one
        row per input row (the join cannot fan out — ambiguous IDs are
        excluded).

    Raises:
        ValueError: If either input is missing a required column.
    """
    _require_columns(rankings, ("id",), "attach_player_ids")
    _require_columns(ff_ids, _CROSSWALK_REQUIRED_COLUMNS, "attach_player_ids")

    ambiguous = ambiguous_fantasypros_ids(ff_ids).select("fantasypros_id").unique()
    candidates = (
        ff_ids.filter(pl.col("fantasypros_id").is_not_null())
        .join(ambiguous, on="fantasypros_id", how="anti")
        .select(
            pl.col("fantasypros_id").cast(pl.Int64).alias("_fp_id"),
            pl.col("gsis_id"),
            pl.col("sleeper_id").cast(pl.Utf8),
        )
    )

    resolved = rankings.with_columns(pl.col("id").cast(pl.Int64).alias("_fp_id")).join(
        candidates, on="_fp_id", how="left"
    )

    matched = resolved.filter(pl.col("gsis_id").is_not_null()).height
    logger.info(
        "attach_player_ids: resolved gsis_id for %d of %d ranking rows (%.1f%%)",
        matched,
        resolved.height,
        100.0 * matched / resolved.height if resolved.height else 0.0,
    )
    return resolved.drop("_fp_id")


def consensus_adp(
    rankings: pl.DataFrame,
    *,
    positions: Sequence[str] = SKILL_POSITIONS,
) -> pl.DataFrame:
    """Normalize FantasyPros rankings to the pipeline's market-value schema.

    Produces the plan's requested normalized columns — ``player_name``,
    ``position``, ``source``, ``adp_overall``, ``adp_position_rank``,
    ``as_of_date`` — carrying ``gsis_id``/``sleeper_id`` through when
    :func:`attach_player_ids` has already run, plus the consensus spread
    (``ecr_sd``, ``ecr_best``, ``ecr_worst``) which is genuinely useful for
    auction work: a wide expert spread is exactly where a keeper-league
    bargain or a bidding war tends to live.

    ``adp_overall`` is the raw ``ecr`` value and ``adp_position_rank`` is the
    dense rank within ``position`` ordered by it. Both are ECR-derived, hence
    the ``source`` column — see the module docstring.

    Args:
        rankings: Output of :func:`load_fantasypros_ecr`, ideally after
            :func:`attach_player_ids`.
        positions: Positions to keep. Defaults to :data:`SKILL_POSITIONS`;
            pass a wider tuple to retain K/DST.

    Returns:
        One row per player, sorted by ``adp_overall`` ascending (best
        consensus rank first). Rows with a null ``ecr`` are dropped — a
        player with no consensus rank has no market value to report.

    Raises:
        ValueError: If ``rankings`` is missing a required column.
    """
    _require_columns(
        rankings, ("player", "pos", "team", "ecr", "scrape_date"), "consensus_adp"
    )

    filtered = rankings.filter(
        pl.col("pos").is_in(list(positions)) & pl.col("ecr").is_not_null()
    )

    normalized = filtered.select(
        pl.col("player").alias("player_name"),
        pl.col("pos").alias("position"),
        pl.col("team").alias("nfl_team"),
        pl.lit(RANKINGS_SOURCE).alias("source"),
        pl.col("ecr").alias("adp_overall"),
        pl.col("scrape_date").alias("as_of_date"),
        *(
            [pl.col("gsis_id")]
            if "gsis_id" in rankings.columns
            else [pl.lit(None, dtype=pl.Utf8).alias("gsis_id")]
        ),
        *(
            [pl.col("sleeper_id")]
            if "sleeper_id" in rankings.columns
            else [pl.lit(None, dtype=pl.Utf8).alias("sleeper_id")]
        ),
        *(
            [pl.col("sd").alias("ecr_sd")]
            if "sd" in rankings.columns
            else [pl.lit(None, dtype=pl.Float64).alias("ecr_sd")]
        ),
        *(
            [pl.col("best").alias("ecr_best")]
            if "best" in rankings.columns
            else [pl.lit(None, dtype=pl.Int64).alias("ecr_best")]
        ),
        *(
            [pl.col("worst").alias("ecr_worst")]
            if "worst" in rankings.columns
            else [pl.lit(None, dtype=pl.Int64).alias("ecr_worst")]
        ),
    )

    return normalized.with_columns(
        pl.col("adp_overall")
        .rank("dense")
        .over("position")
        .cast(pl.Int64)
        .alias("adp_position_rank")
    ).sort("adp_overall")
