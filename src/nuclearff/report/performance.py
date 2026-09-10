"""Render actual vs. projected fantasy points: a weekly report and a season report.

Both are two stacked reference tables (Overachievers / Underperformers)
composited into one PNG via PIL — the same "two independent figures, each
with a single Axes, composited afterwards" posture
:func:`nuclearff.report.user_leagues.render_user_leagues_table` uses, and
for the same reason: negotiating two ``plottable.Table``-bearing Axes
inside one shared figure under ``constrained_layout`` doesn't reliably give
either table the exact row height it was built for. The two report
functions share :func:`_render_stacked_tables` for that plumbing and differ
only in which columns they show.

``plottable``/``matplotlib``/``pandas``/``Pillow`` are imported lazily
inside :func:`_render_stacked_tables`, the same posture every other
PNG-table render function in this project uses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, NamedTuple

import polars as pl

logger = logging.getLogger(__name__)

_WEEKLY_DISPLAY_COLUMNS = (
    "player_name",
    "position",
    "team",
    "manager",
    "projected_points",
    "actual_points",
    "delta",
)

_SEASON_DISPLAY_COLUMNS = (
    "player_name",
    "position",
    "team",
    "manager",
    "games",
    "avg_projected_points",
    "avg_actual_points",
    "avg_delta",
)


class RenderingUnavailableError(ImportError):
    """The optional ``dev`` extra needed to render PNG tables is missing.

    Matches :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


class _Section(NamedTuple):
    """One stacked table: a title, an italic subtitle, and its rows."""

    title: str
    subtitle: str
    frame: pl.DataFrame


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
    played" as "busted." Not used by the season report — see
    :func:`nuclearff.sleeper.performance.season_summary`'s docstring for
    why a completed season has no such ambiguity.

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
        still in progress. See :func:`_underperformers` for what was found
        rendering this against real, real-time week 1 data.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``plottable``,
            ``matplotlib``, ``pandas``) is not installed.
    """
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

    column_definitions = _weekly_column_definitions()
    written = _render_stacked_tables(
        [
            _Section(f"Overachievers -- Week {week}", subtitle, overachievers),
            _Section(
                f"Underperformers -- Week {week}",
                underperformers_subtitle,
                underperformers,
            ),
        ],
        _WEEKLY_DISPLAY_COLUMNS,
        column_definitions,
        out_path,
    )
    logger.info(
        "Wrote %s (%d overachievers, %d underperformers)",
        written,
        len(overachievers),
        len(underperformers),
    )
    return written


def render_season_performance_table(
    summary: pl.DataFrame,
    out_path: str | Path,
    *,
    season: int,
    top_n: int = 10,
) -> Path:
    """Render top overachievers and underperformers for a season as one PNG.

    Args:
        summary: One row per player, e.g.
            :func:`nuclearff.sleeper.performance.season_summary`'s output —
            ``player_name``, ``position``, ``team``, ``manager``, ``games``,
            ``avg_projected_points``, ``avg_actual_points``, ``avg_delta``,
            sorted by ``avg_delta`` descending.
        out_path: Destination PNG path.
        season: Season year, used in the section titles only.
        top_n: How many players to show in each section.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``plottable``,
            ``matplotlib``, ``pandas``) is not installed.
    """
    subtitle = (
        "Average actual vs. Sleeper's pre-game projection per game, both "
        "scored under this league's own rules -- starters only unless "
        "requested otherwise."
    )
    overachievers = summary.sort("avg_delta", descending=True).head(top_n)
    underperformers = summary.sort("avg_delta").head(top_n)

    column_definitions = _season_column_definitions()
    written = _render_stacked_tables(
        [
            _Section(f"Season Overachievers -- {season}", subtitle, overachievers),
            _Section(f"Season Underperformers -- {season}", subtitle, underperformers),
        ],
        _SEASON_DISPLAY_COLUMNS,
        column_definitions,
        out_path,
    )
    logger.info(
        "Wrote %s (%d overachievers, %d underperformers)",
        written,
        len(overachievers),
        len(underperformers),
    )
    return written


def _weekly_column_definitions() -> list[Any]:
    from plottable import ColumnDefinition

    return [
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


def _season_column_definitions() -> list[Any]:
    from plottable import ColumnDefinition

    return [
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
            name="games",
            title="GP",
            width=0.6,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="avg_projected_points",
            title="AVG PROJ",
            width=1.1,
            textprops={"ha": "center", "fontsize": 10},
            formatter="{:.1f}",
            border="left",
        ),
        ColumnDefinition(
            name="avg_actual_points",
            title="AVG ACTUAL",
            width=1.1,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
            formatter="{:.1f}",
            border="left",
        ),
        ColumnDefinition(
            name="avg_delta",
            title="AVG DELTA",
            width=1.1,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
            formatter="{:+.1f}",
            border="left",
        ),
    ]


def _render_stacked_tables(
    sections: list[_Section],
    display_columns: tuple[str, ...],
    column_definitions: list[Any],
    out_path: str | Path,
) -> Path:
    """Render each section as its own ``plottable.Table`` and stack them via PIL.

    Args:
        sections: One entry per stacked table, top to bottom.
        display_columns: Column names to keep, in display order — must
            match ``column_definitions``.
        column_definitions: Shared column styling for every section.
        out_path: Destination PNG path.

    Returns:
        The path written.

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
        from plottable import Table
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RenderingUnavailableError(
            "Rendering this performance table needs the `dev` extra "
            "(plottable, matplotlib, pandas): `uv sync --extra dev`."
        ) from exc

    from PIL import Image

    images = []
    for title, subtitle, frame in sections:
        pdf = pd.DataFrame(frame.select(list(display_columns)).to_dicts())
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
        images.append(_figure_to_image(fig))
        plt.close(fig)

    width = max(image.width for image in images)
    total_height = sum(image.height for image in images)
    composite = Image.new("RGB", (width, total_height), "white")
    y = 0
    for image in images:
        composite.paste(image, (0, y))
        y += image.height

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path, format="PNG")
    return out_path


def _figure_to_image(fig: Any) -> Any:
    """Render a matplotlib figure to an in-memory ``PIL.Image``."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor="white", dpi=200, bbox_inches="tight")
    buf.seek(0)
    return Image.open(buf).convert("RGB")
