"""Render a manager's historical draft-order table (GitHub Issue 85).

Follows :mod:`nuclearff.report.tables`' PNG-table conventions (styled
columns, ``plottable``, a left-aligned bold title) the same way
:func:`nuclearff.report.trades.render_trade_leaderboard` does -- a plain
reference table, one row per manager, no images.

``plottable``/``matplotlib``/``pandas`` are imported lazily inside
:func:`render_draft_order_table`, the same posture every other PNG-table
render function in this project uses.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


class RenderingUnavailableError(ImportError):
    """The optional ``dev`` extra needed to render PNG tables is missing.

    Matches :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


def render_draft_order_table(
    stats: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Draft Order History",
) -> Path:
    """Render a single reference table of every manager's draft-order history.

    Args:
        stats: One row per manager, with ``manager``, ``seasons_drafted``,
            ``avg_draft_position``, ``times_first_pick``, and
            ``times_last_pick`` columns -- e.g.
            :func:`nuclearff.sleeper.draft.draft_order_stats`'s output.
        out_path: Destination PNG path.
        title: Figure title.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``plottable``,
            ``matplotlib``) is not installed.
    """
    try:
        import matplotlib
        import pandas as pd

        # Force the non-interactive Agg backend before pyplot is imported --
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        from plottable import ColumnDefinition, Table
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RenderingUnavailableError(
            "Rendering the draft order table needs the `dev` extra "
            "(plottable, matplotlib): `uv sync --extra dev`."
        ) from exc

    ordered = stats.sort("avg_draft_position")
    # Built from dicts, not `Polars.to_pandas()` -- avoids a pyarrow
    # dependency this project doesn't otherwise need, same as every other
    # PNG-table render function here.
    frame = pd.DataFrame(ordered.to_dicts())
    frame.index = pd.RangeIndex(1, len(frame) + 1)
    frame.index.name = "#"

    # Widths sized to fit each column's own header text, not just its
    # data -- plottable doesn't wrap or shrink a header wider than its
    # column, it just overflows into its neighbor. "SEASONS DRAFTED" and
    # "AVG DRAFT POSITION" are both wider than a first pass (1.8/2.0)
    # allowed for; the real overlap only showed up rendering against this
    # league's real 15-manager roster, the same "don't hardcode a width
    # real data can exceed" lesson issue #46's leaderboard table already
    # learned for its own column headers.
    column_definitions = [
        ColumnDefinition(
            name="manager",
            title="MANAGER",
            width=2.2,
            textprops={"ha": "left", "weight": "bold", "fontsize": 11},
        ),
        ColumnDefinition(
            name="seasons_drafted",
            title="SEASONS DRAFTED",
            width=2.3,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="avg_draft_position",
            title="AVG DRAFT POSITION",
            width=2.4,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
            formatter="{:.1f}",
            border="left",
        ),
        ColumnDefinition(
            name="times_first_pick",
            title="TIMES 1ST PICK",
            width=2.0,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="times_last_pick",
            title="TIMES LAST PICK",
            width=2.0,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
    ]

    fig, ax = plt.subplots(figsize=(13, 0.5 * len(frame) + 2.2))
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
    ax.text(
        0,
        1.006,
        "Lower avg draft position = picked earlier. "
        '"Last pick" is season-relative -- team count can vary by season.',
        transform=ax.transAxes,
        fontsize=9.5,
        style="italic",
        color="#666666",
        ha="left",
        va="bottom",
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info("Wrote %s (%d managers)", out_path, len(frame))
    return out_path
