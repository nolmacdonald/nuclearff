"""Render "on this day" transaction callbacks to a PNG table (issue #118).

Follows :mod:`nuclearff.report.trades`' ``render_trade_leaderboard`` PNG-table
conventions (``plottable``, ``matplotlib``, a left-aligned bold title, no
headless browser anywhere in the path) rather than inventing a new rendering
style for one report.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl

from nuclearff.report.tables import RenderingUnavailableError

logger = logging.getLogger(__name__)


def render_on_this_day_table(
    summary_rows: pl.DataFrame,
    today: date,
    out_path: str | Path,
    *,
    league_name: str | None = None,
) -> Path:
    """Render a PNG table of transactions that happened on ``today``'s date.

    Args:
        summary_rows: Rows as returned by
            :func:`nuclearff.archive.on_this_day.transaction_summary_rows`.
            May be empty — a real, expected case for a league with a short
            or sparse history, rendered as an explicit "nothing happened"
            message rather than a blank table or a crash.
        today: The calendar date being looked up, used in the title.
        out_path: Destination PNG path.
        league_name: Included in the title if given.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``plottable``,
            ``matplotlib``) is not installed.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RenderingUnavailableError(
            "Rendering the on-this-day table needs the `dev` extra "
            "(plottable, matplotlib): `uv sync --extra dev`."
        ) from exc

    title = f"On This Day — {today:%B %-d}"
    if league_name:
        title = f"{league_name}: {title}"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if summary_rows.height == 0:
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.axis("off")
        ax.set_title(title, loc="left", fontsize=17, weight="bold", pad=30)
        ax.text(
            0.5,
            0.4,
            "No recorded transactions on this day in league history.",
            transform=ax.transAxes,
            fontsize=12,
            style="italic",
            color="#666666",
            ha="center",
            va="center",
        )
        fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
        plt.close(fig)
        logger.info("Wrote %s (no matches)", out_path)
        return out_path

    from plottable import ColumnDefinition, Table

    ordered = summary_rows.sort("year", descending=True)
    import pandas as pd

    frame = pd.DataFrame(ordered.to_dicts())
    frame.index = pd.RangeIndex(1, len(frame) + 1)
    frame.index.name = "#"

    # A fixed width let a real "added X; dropped Y" summary (this league's
    # real longest: 51 characters, "added Jacory Croskey-Merritt; dropped
    # Brandin Cooks") or a real "free_agent" type (10 chars, vs. "waiver"/
    # "trade") wrap to a second line that plottable's row layout doesn't
    # reserve height for, visually overlapping the row below -- the same
    # "don't hardcode a width real data can exceed" lesson
    # `report/user_leagues.py` already learned for its own LEAGUE column.
    # Sized to the longest string actually present, not a guessed default.
    max_type_len = max((len(str(v)) for v in frame["type"]), default=6)
    max_parties_len = max((len(str(v)) for v in frame["parties"]), default=10)
    max_summary_len = max((len(str(v)) for v in frame["summary"]), default=10)
    type_width = max(1.2, 0.16 * max_type_len)
    parties_width = max(2.2, 0.16 * max_parties_len)
    summary_width = max(4.5, 0.16 * max_summary_len)

    column_definitions = [
        ColumnDefinition(
            name="year",
            title="YEAR",
            width=0.8,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
        ),
        ColumnDefinition(
            name="type",
            title="TYPE",
            width=type_width,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="parties",
            title="PARTIES",
            width=parties_width,
            textprops={"ha": "left", "fontsize": 10, "weight": "bold"},
        ),
        ColumnDefinition(
            name="summary",
            title="SUMMARY",
            width=summary_width,
            textprops={"ha": "left", "fontsize": 10, "color": "#444444"},
        ),
    ]

    fig, ax = plt.subplots(figsize=(14, 0.6 * len(frame) + 2.2))
    Table(
        frame[[c.name for c in column_definitions]],
        column_definitions=column_definitions,
        textprops={"fontsize": 10, "ha": "center"},
        row_dividers=True,
        row_divider_kw={"linewidth": 0.5, "color": "#E3E3E3"},
        col_label_divider_kw={"linewidth": 1.5, "color": "black"},
        column_border_kw={"linewidth": 1.5, "color": "black"},
        ax=ax,
    )

    ax.set_title(title, loc="left", fontsize=17, weight="bold", pad=30)

    fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info("Wrote %s (%d matches)", out_path, len(frame))
    return out_path
