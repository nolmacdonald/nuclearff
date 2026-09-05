"""Assemble the auction draft report: CSV, position tables, and markdown.

Every league fact in the output is read from the board context that
:func:`nuclearff.pipeline.auction_board.build_auction_board` returns — team
count, budget, roster shape, scoring type, replacement ranks — so nothing
here hardcodes one league's settings.

The methodology and caveats sections are not decoration. This board is built
on a **recency-weighted average of realized past scoring**, which is a
modeling choice with real limits (no rookies, no injury or depth-chart news,
no age curve at this step), and its market column is **expert consensus
ranking, not observed ADP**. Both are stated in the report itself rather than
left for the reader to infer from a number that looks authoritative.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nuclearff.report.tables import RenderingUnavailableError, render_position_table

logger = logging.getLogger(__name__)

CSV_COLUMNS = (
    "rank_overall",
    "rank_position",
    "player_id",
    "player_display_name",
    "position",
    "recent_team",
    "games",
    "value_estimate",
    "points_per_game",
    "replacement_value_position",
    "vorp",
    "auction_value",
    "in_draft_pool",
    "adp_overall",
    "adp_position_rank",
    "adp_source",
    "adp_as_of_date",
    "headshot_url",
)
"""Board columns written to the deliverable CSV, in order. Season point
columns (``fantasy_points_<season>``) are appended after these."""


def write_board_csv(board: pl.DataFrame, out_path: str | Path) -> Path:
    """Write the board to CSV with a stable, documented column order.

    Args:
        board: Output of
            :func:`~nuclearff.pipeline.auction_board.build_auction_board`.
        out_path: Destination CSV path.

    Returns:
        The path written.
    """
    ordered = [c for c in CSV_COLUMNS if c in board.columns]
    season_columns = sorted(c for c in board.columns if c.startswith("fantasy_points_"))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    board.select([*ordered, *season_columns]).write_csv(out_path)
    logger.info("Wrote %s (%d rows)", out_path, board.height)
    return out_path


def _markdown_table(frame: pl.DataFrame, columns: Sequence[str]) -> str:
    """Render a small Polars frame as a GitHub-flavored markdown table.

    Args:
        frame: Rows to render.
        columns: Columns to include, in order.

    Returns:
        The markdown table as a string.
    """
    present = [c for c in columns if c in frame.columns]
    header = "| " + " | ".join(present) + " |"
    divider = "|" + "|".join("---" for _ in present) + "|"
    rows = []
    for row in frame.select(present).iter_rows(named=True):
        cells = []
        for column in present:
            value = row[column]
            if value is None:
                cells.append("—")
            elif isinstance(value, float):
                cells.append(f"{value:,.1f}")
            else:
                cells.append(str(value))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, divider, *rows])


def write_report(
    board: pl.DataFrame,
    context: dict[str, Any],
    out_dir: str | Path,
    *,
    positions: Sequence[str] = ("QB", "RB", "WR", "TE"),
    top_n: int = 12,
    render_tables: bool = True,
) -> Path:
    """Write the full report: CSV, per-position PNG tables, and ``report.md``.

    Args:
        board: Output of
            :func:`~nuclearff.pipeline.auction_board.build_auction_board`.
        context: The board context from the same call.
        out_dir: Directory to write into. ``tables/`` and ``report.md`` are
            created beneath it.
        positions: Positions to build tables for, in report order.
        top_n: Players per position table.
        render_tables: When ``False``, skip PNG rendering (and its
            ``plottable``/matplotlib dependency) and link the CSV only —
            useful in environments without the ``dev`` extra installed.

    Returns:
        The path to the written ``report.md``.
    """
    out_dir = Path(out_dir)
    tables_dir = out_dir / "tables"
    csv_path = write_board_csv(board, out_dir / "auction_board.csv")

    table_paths: dict[str, Path] = {}
    if render_tables:
        for position in positions:
            try:
                table_paths[position] = render_position_table(
                    board,
                    position,
                    tables_dir / f"top_{top_n}_{position.lower()}.png",
                    context=context,
                    n=top_n,
                )
            except RenderingUnavailableError:
                # Narrow on purpose: a blanket `except ImportError` here also
                # caught unrelated ModuleNotFoundErrors raised *during*
                # rendering and misreported them as a missing plottable.
                logger.warning(
                    "plottable/matplotlib unavailable - writing the report "
                    "without PNG tables. Install the `dev` extra to render them."
                )
                break
            except ValueError as exc:
                logger.warning("Skipping %s table: %s", position, exc)

    roster = context.get("roster_positions") or []
    roster_summary = ", ".join(
        f"{roster.count(slot)}x {slot}" for slot in dict.fromkeys(roster)
    )
    replacement_ranks = context.get("replacement_ranks", {})
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    league_pool = (context.get("num_teams") or 0) * (
        context.get("budget_per_team") or 0
    )
    scoring_label = str(context.get("scoring_type") or "unknown").replace("_", " ")
    season_label = context.get("season", "this season")

    lines = [
        f"# {context.get('league_name', 'League')} — "
        f"{context.get('season', '')} Auction Draft Board",
        "",
        f"*Generated {generated}*",
        "",
        "## League",
        "",
        f"- **Teams:** {context.get('num_teams')}",
        f"- **Auction budget:** ${context.get('budget_per_team')} per team "
        f"(${league_pool:,} league-wide)",
        f"- **Roster ({context.get('roster_spots')} spots):** {roster_summary}",
        f"- **Scoring:** {scoring_label}",
        f"- **Keepers allowed:** {context.get('max_keepers', 'unknown')}",
        f"- **Players valued:** {context.get('players_valued', board.height):,}",
        "",
        "## How these numbers were built",
        "",
        "1. **Scoring.** Every player-season from "
        f"{', '.join(str(s) for s in context.get('seasons_used', []))} was rescored "
        "under this league's own scoring settings, pulled live from Sleeper — not a "
        "generic PPR formula. Kicker, defense, and IDP scoring keys are not modeled.",
        "2. **Value estimate (`PTS`).** A recency-weighted average of those realized "
        "seasons, most recent weighted heaviest. Players with fewer prior seasons have "
        "their weights renormalized over the seasons they actually have.",
        "3. **Replacement level.** Per position, from this league's real roster shape "
        "(locked starters plus an assumed share of FLEX slots), then **VORP** = value "
        "estimate − replacement level. Replacement ranks used: "
        + ", ".join(f"{pos} {rank}" for pos, rank in replacement_ranks.items())
        + f" (`{context.get('baseline')}` baseline).",
        "4. **Auction dollars.** $1 is reserved for every rosterable slot; the rest of "
        "the league budget is distributed across the draftable pool in proportion to "
        "each player's share of total positive VORP. Dollars sum to exactly the league "
        "budget across the pool.",
        "",
        "## Caveats — read these before bidding",
        "",
        "- **`PTS` is backward-looking, not a projection.** It measures what players "
        "*did*, not what they will do. It carries no injury news, no depth-chart or "
        "scheme change, and no age curve. A 32-year-old coming off a huge season will "
        "be priced high here and the market will disagree — compare the `ECR` column "
        "and trust your own read where they diverge.",
        "- **Rookies and players with no prior season are absent entirely.** They have "
        "no realized seasons to weight, so they do not appear on this board at all.",
        "- **`ECR` is expert consensus ranking, not ADP.** It is what analysts say "
        "*should* happen, not observed draft behavior. FantasyPros publishes separate "
        "PPR/half-PPR/standard boards and the feed does not say which one these rows "
        "came from, so it is not matched to this league's exact scoring.",
        "- **No keeper adjustment.** These are no-keeper baseline values. Sleeper "
        "exposes no keeper price, this league's draft description is empty, and "
        "there are no historical auction prices to escalate from — every prior "
        f"season of this league was a snake draft, making {season_label} its "
        "first auction. Real keeper inflation will push non-kept prices "
        "**above** these numbers.",
        "- **The FLEX split is an assumption.** Replacement level assumes a fixed "
        "share of FLEX slots goes to each position; that split is a documented "
        "heuristic, not calibrated against this league's actual lineups.",
        "",
        "## Board",
        "",
        f"Full board: [`{csv_path.name}`]({csv_path.name}) ({board.height:,} players).",
        "",
    ]

    for position in positions:
        pool = board.filter(pl.col("position") == position).head(top_n)
        if pool.height == 0:
            continue
        lines.append(f"### Top {min(top_n, pool.height)} {position}")
        lines.append("")
        if position in table_paths:
            relative = table_paths[position].relative_to(out_dir)
            lines.append(f"![Top {top_n} {position}]({relative})")
            lines.append("")
        lines.append(
            _markdown_table(
                pool,
                (
                    "rank_position",
                    "player_display_name",
                    "recent_team",
                    "adp_overall",
                    "value_estimate",
                    "vorp",
                    "auction_value",
                ),
            )
        )
        lines.append("")

    report_path = out_dir / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote %s", report_path)
    return report_path
