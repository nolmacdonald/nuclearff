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


def _truncate(text: str, limit: int) -> str:
    """``text`` clipped to ``limit`` characters with a trailing ``…``."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


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

    # `maintain_order=True`: a real trade explodes into 2+ rows sharing one
    # `year` (transaction_summary_rows' `group_id`) -- the default unstable
    # sort could otherwise separate them, breaking the same-transaction
    # grouping below.
    ordered = summary_rows.sort("year", descending=True, maintain_order=True)
    import pandas as pd

    frame = pd.DataFrame(ordered.to_dicts())
    frame.index = pd.RangeIndex(1, len(frame) + 1)
    frame.index.name = "#"

    # A real trade (transaction_summary_rows' `group_id`) explodes into one
    # row per party -- "who got what," not everyone's name on one combined
    # row. plottable has no true merged/spanning cell, so the grouping is
    # simulated: blank the repeated YEAR/TYPE on every row after a group's
    # first, and alternate each *group's* row background (not each row's --
    # `set_alternating_row_colors` only does that) so a 2+-row trade group
    # reads as one visual block instead of independent striped rows.
    group_ids = frame["group_id"].tolist()
    is_group_start = [
        i == 0 or group_ids[i] != group_ids[i - 1] for i in range(len(group_ids))
    ]
    group_index = [0] * len(group_ids)
    current = -1
    for i, start in enumerate(is_group_start):
        current += start
        group_index[i] = current

    frame["year"] = frame["year"].astype(str)
    for i, start in enumerate(is_group_start):
        if not start:
            frame.loc[frame.index[i], "year"] = ""
            frame.loc[frame.index[i], "type"] = ""

    # `transaction_summary_rows` joins a multi-part SUMMARY ("added ..." /
    # "dropped ..." / "received ..." / "gave up ...") with real newlines
    # instead of "; " -- one line per part, so nothing needs truncating.
    # plottable gives every row the *same* height regardless of content
    # (confirmed live: a wrapped cell doesn't push its own row taller, it
    # just overlaps the row below), so that height has to be sized for the
    # tallest cell in the whole table, not per-row -- a single-part day
    # (only waiver adds, no drops) stays compact; a day with an add-and-
    # drop or a multi-asset trade uniformly gets taller rows.
    max_summary_lines = max(
        (str(v).count("\n") + 1 for v in frame["summary"]), default=1
    )
    row_height_unit = 0.6 + 0.35 * (max_summary_lines - 1)

    # A fixed width let a real "free_agent" type (10 chars, vs. "waiver"/
    # "trade") wrap to a second line without reserving height for it,
    # visually overlapping the row below -- the same "don't hardcode a
    # width real data can exceed" lesson `report/user_leagues.py` already
    # learned for its own LEAGUE column. Sized to the longest string
    # actually present, not a guessed default. `parties` is truncated
    # (a single manager name, always short in practice) as a safety net;
    # `summary` is not -- its real width need is driven by the longest
    # *line* (see above), not truncated text.
    frame["parties"] = frame["parties"].map(lambda s: _truncate(str(s), 40))

    max_type_len = max((len(str(v)) for v in frame["type"]), default=6)
    max_parties_len = max((len(str(v)) for v in frame["parties"]), default=10)
    max_summary_len = max(
        (len(line) for v in frame["summary"] for line in str(v).split("\n")),
        default=10,
    )
    type_width = max(1.2, 0.16 * max_type_len)
    parties_width = max(2.2, 0.16 * max_parties_len)
    summary_width = max(4.5, 0.16 * max_summary_len)

    # Also scale the *figure* width to the real total column weight (default
    # columns sum to 8.7 units across a 14" figure, ~1.61"/unit) rather than
    # holding it fixed at 14" -- otherwise a real table with wider-than-
    # default columns keeps the same physical width and every column's
    # absolute inches shrinks even after truncation above bounds the
    # relative weights.
    total_width = 0.8 + type_width + parties_width + summary_width
    fig_width = max(14.0, total_width * (14.0 / 8.7))

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

    fig, ax = plt.subplots(figsize=(fig_width, row_height_unit * len(frame) + 2.2))
    table = Table(
        frame[[c.name for c in column_definitions]],
        column_definitions=column_definitions,
        textprops={"fontsize": 10, "ha": "center"},
        row_dividers=True,
        row_divider_kw={"linewidth": 0.5, "color": "#E3E3E3"},
        col_label_divider_kw={"linewidth": 1.5, "color": "black"},
        column_border_kw={"linewidth": 1.5, "color": "black"},
        ax=ax,
    )
    for row_idx, group_idx in enumerate(group_index):
        if group_idx % 2 == 1:
            table.rows[row_idx].set_facecolor("#F2F2F2")

    ax.set_title(title, loc="left", fontsize=17, weight="bold", pad=30)

    fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info("Wrote %s (%d matches)", out_path, len(frame))
    return out_path
