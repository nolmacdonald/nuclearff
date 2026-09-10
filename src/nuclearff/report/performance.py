"""Render a weekly over/underperformer table: actual vs. projected fantasy points.

Two stacked reference tables composited into one PNG via PIL — the same
"two independent figures, each with a single Axes, composited afterwards"
posture :func:`nuclearff.report.user_leagues.render_user_leagues_table`
uses, and for the same reason: negotiating two ``plottable.Table``-bearing
Axes inside one shared figure under ``constrained_layout`` doesn't reliably
give either table the exact row height it was built for.

``plottable``/``matplotlib``/``pandas``/``Pillow`` are imported lazily
inside :func:`render_weekly_performance_table`, the same posture every
other PNG-table render function in this project uses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

_DISPLAY_COLUMNS = (
    "player_name",
    "position",
    "team",
    "manager",
    "projected_points",
    "actual_points",
    "delta",
)


class RenderingUnavailableError(ImportError):
    """The optional ``dev`` extra needed to render PNG tables is missing.

    Matches :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


def _underperformers(performance: pl.DataFrame, top_n: int) -> pl.DataFrame:
    """The worst ``top_n`` deltas, excluding players stuck at exactly 0 actual points.

    A player Sleeper hasn't posted a real score for yet reads as ``0.0``,
    identically to a real player who genuinely scored nothing — there is no
    signal in this data to tell the two apart. Left in, an in-progress
    week's Underperformers list is overwhelmingly "games that haven't
    kicked off," not real busts: confirmed rendering this against this
    league's real, real-time week 1 data, where 8 of the real top 10 by
    delta were exactly this, not a real underperformance. Excluding exact
    zeros is an imperfect, documented trade-off — a player who truly played
    and scored 0 is excluded too — rather than silently presenting "hasn't
    played" as "busted."

    Also requires ``delta < 0``: without it, a week with fewer than
    ``top_n`` real underperformers backfills the rest of the table with
    whoever's next by ``delta`` regardless of sign — a real bug caught
    rendering this against this league's real, real-time week 1 data, where
    only 4 players qualified as real underperformers and the 5th row
    silently became that week's single biggest *overachiever* instead.

    Args:
        performance: e.g.
            :func:`nuclearff.sleeper.performance.weekly_performance`'s
            output.
        top_n: How many rows to return.

    Returns:
        Up to ``top_n`` rows with a real, negative delta, sorted ascending
        (worst first). Fewer than ``top_n`` when that's all that qualify —
        never padded with a positive-delta row.
    """
    return (
        performance.filter((pl.col("actual_points") > 0) & (pl.col("delta") < 0))
        .sort("delta")
        .head(top_n)
    )


def render_weekly_performance_table(
    performance: pl.DataFrame,
    out_path: str | Path,
    *,
    week: int,
    top_n: int = 10,
) -> Path:
    """Render top overachievers and underperformers for one week as one PNG.

    Args:
        performance: One row per player, e.g.
            :func:`nuclearff.sleeper.performance.weekly_performance`'s
            output — ``player_name``, ``position``, ``team``, ``manager``,
            ``projected_points``, ``actual_points``, ``delta``, sorted by
            ``delta`` descending.
        out_path: Destination PNG path.
        week: Week number, used in the section titles only.
        top_n: How many players to show in each section.

    Returns:
        The path written.

    Note:
        The Underperformers section excludes players with exactly ``0.0``
        actual points -- Sleeper's data has no way to distinguish "this
        player's game hasn't started yet" from "this player genuinely
        scored nothing," and the former dominates the list for any week
        still in progress. See the inline comment above where this is
        filtered for what was found rendering this against real, real-time
        week 1 data.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``plottable``,
            ``matplotlib``, ``pandas``) is not installed.
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
            "Rendering the weekly performance table needs the `dev` extra "
            "(plottable, matplotlib, pandas): `uv sync --extra dev`."
        ) from exc

    from PIL import Image

    column_definitions = [
        ColumnDefinition(
            name="player_name",
            title="PLAYER",
            width=2.2,
            textprops={"ha": "left", "weight": "bold", "fontsize": 11},
        ),
        ColumnDefinition(
            name="position",
            title="POS",
            width=0.7,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="team",
            title="TEAM",
            width=0.7,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="manager",
            title="MANAGER",
            width=1.8,
            textprops={"ha": "left", "fontsize": 10, "color": "#666666"},
            border="left",
        ),
        ColumnDefinition(
            name="projected_points",
            title="PROJ",
            width=1.0,
            textprops={"ha": "center", "fontsize": 10},
            formatter="{:.1f}",
            border="left",
        ),
        ColumnDefinition(
            name="actual_points",
            title="ACTUAL",
            width=1.0,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
            formatter="{:.1f}",
            border="left",
        ),
        ColumnDefinition(
            name="delta",
            title="DELTA",
            width=1.0,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
            formatter="{:+.1f}",
            border="left",
        ),
    ]

    def _section(frame: pl.DataFrame, title: str, subtitle: str) -> Any:
        pdf = pd.DataFrame(frame.select(_DISPLAY_COLUMNS).to_dicts())
        pdf.index = pd.RangeIndex(1, len(pdf) + 1)
        pdf.index.name = "#"

        fig, ax = plt.subplots(figsize=(13, 0.5 * max(len(pdf), 1) + 2.2))
        Table(
            pdf,
            column_definitions=column_definitions,
            textprops={"fontsize": 10, "ha": "center"},
            row_dividers=True,
            row_divider_kw={"linewidth": 0.5, "color": "#E3E3E3"},
            col_label_divider_kw={"linewidth": 1.5, "color": "black"},
            column_border_kw={"linewidth": 1.5, "color": "black"},
            ax=ax,
        )
        ax.set_title(title, loc="left", fontsize=14, weight="bold", pad=30)
        ax.text(
            0,
            1.006,
            subtitle,
            transform=ax.transAxes,
            fontsize=9,
            style="italic",
            color="#666666",
            ha="left",
            va="bottom",
        )
        image = _figure_to_image(fig)
        plt.close(fig)
        return image

    subtitle = (
        "Actual vs. Sleeper's pre-game projection, both scored under this "
        "league's own rules -- not a generic PPR/standard format."
    )
    underperformers_subtitle = subtitle + (
        " Players with exactly 0 actual points are excluded here -- Sleeper's "
        'data doesn\'t distinguish "hasn\'t played yet" from "played and '
        'scored zero," and mid-week the former would dominate this list.'
    )
    overachievers = performance.sort("delta", descending=True).head(top_n)
    underperformers = _underperformers(performance, top_n)

    over_image = _section(overachievers, f"Overachievers -- Week {week}", subtitle)
    under_image = _section(
        underperformers, f"Underperformers -- Week {week}", underperformers_subtitle
    )

    width = max(over_image.width, under_image.width)
    composite = Image.new(
        "RGB", (width, over_image.height + under_image.height), "white"
    )
    composite.paste(over_image, (0, 0))
    composite.paste(under_image, (0, over_image.height))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path, format="PNG")
    logger.info(
        "Wrote %s (%d overachievers, %d underperformers)",
        out_path,
        len(overachievers),
        len(underperformers),
    )
    return out_path


def _figure_to_image(fig: Any) -> Any:
    """Render a matplotlib figure to an in-memory ``PIL.Image``."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor="white", dpi=200, bbox_inches="tight")
    buf.seek(0)
    return Image.open(buf).convert("RGB")
