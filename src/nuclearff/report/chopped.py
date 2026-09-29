"""Render the Chopped league analytics (epic #223) as PNGs.

One module for every Chopped chart and table, each built from a
:mod:`nuclearff.chopped` frame. ``matplotlib``/``plottable``/``pandas`` are
imported lazily inside each render function, the same posture
:mod:`nuclearff.report.tables` uses; tables need the ``dev`` extra
(``plottable``), charts only ``matplotlib``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)


class RenderingUnavailableError(ImportError):
    """``matplotlib`` or ``plottable`` isn't installed.

    Matches :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


def _pyplot() -> Any:
    """``matplotlib.pyplot`` on the non-interactive Agg backend."""
    try:
        import matplotlib

        # Force Agg before pyplot is imported -- see nuclearff.report.tables.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering Chopped charts needs matplotlib, a core dependency: `uv sync`."
        ) from exc
    return plt


def _title(ax: Any, title: str, subtitle: str | None, *, size: float = 14) -> None:
    """Bold left-aligned title with an optional italic caption under it."""
    ax.set_title(
        title, loc="left", fontsize=size, weight="bold", pad=30 if subtitle else 12
    )
    if subtitle:
        ax.text(
            0,
            1.006,
            subtitle,
            transform=ax.transAxes,
            fontsize=9.5,
            style="italic",
            color="#666666",
            ha="left",
            va="bottom",
        )


def _save(fig: Any, out_path: str | Path, what: str, count: int) -> Path:
    """Write ``fig`` to ``out_path`` as PNG and close it."""
    plt = _pyplot()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info("Wrote %s (%d %s)", out_path, count, what)
    return out_path


def _render_table(
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str, float, dict[str, Any]]],
    out_path: str | Path,
    *,
    title: str,
    subtitle: str | None,
    what: str,
) -> Path:
    """Render ``rows`` as a plottable table.

    Args:
        rows: One dict per table row, already formatted for display.
        columns: ``(key, header, width, textprops)`` per column. Widths must
            fit the header text: plottable doesn't wrap a header, it overflows
            into its neighbor.
        out_path: Destination PNG path.
        title: Table title.
        subtitle: Italic caption under the title.
        what: What a row is, for the log line.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``plottable``) is
            not installed.
    """
    plt = _pyplot()
    try:
        import pandas as pd
        from plottable import ColumnDefinition, Table
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RenderingUnavailableError(
            "Rendering a Chopped table needs the `dev` extra (plottable): "
            "`uv sync --extra dev`."
        ) from exc

    keys = [key for key, *_ in columns]
    # Built from dicts, not Polars.to_pandas() -- avoids a pyarrow dependency.
    frame = pd.DataFrame(
        [{key: row[key] for key in keys} for row in rows], columns=keys
    )
    frame.index = pd.RangeIndex(1, len(frame) + 1, name="#")
    definitions = [
        ColumnDefinition(
            name=key,
            title=header,
            width=width,
            textprops={"ha": "center", "fontsize": 10, **props},
            **({"border": "left"} if i else {}),
        )
        for i, (key, header, width, props) in enumerate(columns)
    ]
    width = sum(width for _, _, width, _ in columns) + 1.2
    fig, ax = plt.subplots(figsize=(width, 0.5 * len(frame) + 2.2))
    Table(
        frame,
        column_definitions=definitions,
        textprops={"fontsize": 10, "ha": "center"},
        row_dividers=True,
        row_divider_kw={"linewidth": 0.5, "color": "#E3E3E3"},
        col_label_divider_kw={"linewidth": 1.5, "color": "black"},
        column_border_kw={"linewidth": 1.5, "color": "black"},
        ax=ax,
    )
    _title(ax, title, subtitle, size=17)
    return _save(fig, out_path, what, len(frame))


def _fmt(value: Any, spec: str, missing: str = "—") -> str:
    """Format a number, or ``missing`` for null."""
    return missing if value is None else format(value, spec)


# -------------------------------------------------------------------------------------
# SURVIVAL LUCK (#228)
# -------------------------------------------------------------------------------------


def render_luck_table(
    luck: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Chopped Survival Luck",
    subtitle: str | None = None,
) -> Path:
    """Render :func:`nuclearff.chopped.luck.survival_luck` as a table.

    Args:
        luck: One season's (or career) :func:`~nuclearff.chopped.luck.
            survival_luck` output.
        out_path: Destination PNG path.
        title: Table title.
        subtitle: Caption; by default it says which way means lucky.

    Returns:
        The path written.
    """
    rows = [
        {
            "manager": row["manager"],
            "weeks": str(row["weeks_survived"]),
            "chopped": _fmt(row["chopped_week"], "d", "survived"),
            "avg_margin": _fmt(row["avg_margin"], ".1f"),
            "relative": _fmt(row["field_relative_margin"], "+.1f"),
            "nail_biter": _fmt(row["nail_biter_ratio"], ".0%"),
            "razor": _fmt(row["razor_thin_ratio"], ".0%"),
            "mean_z": _fmt(row["mean_z"], ".2f"),
            "cv": _fmt(row["percentile_cv"], ".2f"),
        }
        for row in luck.iter_rows(named=True)
    ]
    columns = [
        ("manager", "MANAGER", 2.4, {"ha": "left", "weight": "bold"}),
        ("weeks", "WEEKS SURVIVED", 1.9, {}),
        ("chopped", "CHOPPED WEEK", 1.8, {}),
        ("avg_margin", "AVG MARGIN ↓", 1.9, {"weight": "bold"}),
        ("relative", "VS. FIELD ↓", 1.7, {}),
        ("nail_biter", "NAIL-BITERS ↑", 1.9, {}),
        ("razor", "RAZOR-THIN Z ↑", 2.0, {}),
        ("mean_z", "MEAN Z ↓", 1.4, {}),
        ("cv", "RANK CV ↑", 1.4, {}),
    ]
    return _render_table(
        rows,
        columns,
        out_path,
        title=title,
        subtitle=subtitle
        or "↑ higher = luckier, ↓ lower = luckier. Z and rank CV skip weeks with "
        "fewer than 5 teams alive.",
        what="managers",
    )


def render_luck_scatter(
    luck: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Survival Margin vs. Nail-Biters",
    subtitle: str | None = None,
) -> Path:
    """Scatter of average margin (x) against nail-biter ratio (y), per manager.

    Lucky survivors sit top left: small cushions, many close calls. Points
    are colored by weeks survived.

    Args:
        luck: :func:`~nuclearff.chopped.luck.survival_luck` output.
        out_path: Destination PNG path.
        title: Chart title.
        subtitle: Caption.

    Returns:
        The path written.
    """
    plt = _pyplot()
    from matplotlib.transforms import blended_transform_factory

    points = luck.filter(
        pl.col("avg_margin").is_not_null() & pl.col("nail_biter_ratio").is_not_null()
    ).sort(["nail_biter_ratio", "avg_margin"])  # ties left to right: no crossings
    fig, ax = plt.subplots(figsize=(9, 7), layout="constrained")
    scatter = ax.scatter(
        points["avg_margin"],
        points["nail_biter_ratio"],
        c=points["weeks_survived"],
        cmap="viridis",
        s=70,
        edgecolors="white",
        linewidths=0.8,
        zorder=2,
    )
    fig.colorbar(scatter, ax=ax, label="Weeks survived")
    ax.set_xlabel("Average margin above the chop line (points)")
    ax.set_ylabel("Nail-biter ratio (weeks within 5% of the line)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(True, alpha=0.3, zorder=1)
    x0, x1 = ax.get_xlim()
    ax.set_xlim(x0, x1 + (x1 - x0) * 0.2)

    # Label each point at its x, spread in y (axes fraction) so labels never
    # overlap -- the declutter pass from render_trade_partner_diversity.
    y0, y1 = ax.get_ylim()
    min_gap = min(0.05, 0.9 / max(points.height - 1, 1))
    placed = -1.0
    for row in points.iter_rows(named=True):
        frac = (row["nail_biter_ratio"] - y0) / (y1 - y0) if y1 > y0 else 0.0
        frac = max(frac, placed + min_gap)
        placed = frac
        ax.annotate(
            row["manager"],
            xy=(row["avg_margin"], row["nail_biter_ratio"]),
            xytext=(row["avg_margin"] + (x1 - x0) * 0.03, frac),
            textcoords=blended_transform_factory(ax.transData, ax.transAxes),
            va="center",
            fontsize=8,
            color="#222222",
            arrowprops={"arrowstyle": "-", "color": "#999999", "lw": 0.6},
        )
    _title(ax, title, subtitle or "Top left: small cushions and many close calls")
    return _save(fig, out_path, "managers", points.height)


# -------------------------------------------------------------------------------------
# TOP-3 / BOTTOM-3 FINISHES (#229)
# -------------------------------------------------------------------------------------


def render_weekly_finishes(
    finishes: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Top-3 and Bottom-3 Weeks",
    subtitle: str | None = None,
) -> Path:
    """Diverging bars: top-3 weeks to the right, bottom-3 weeks to the left.

    Close calls (bottom-3 weeks survived) are the lighter part of each
    bottom bar; the rest is the week the manager was chopped. Rows are
    sorted by top-3 minus bottom-3 weeks, best at the top.

    Args:
        finishes: :func:`nuclearff.chopped.finishes.weekly_finishes` output.
        out_path: Destination PNG path.
        title: Chart title.
        subtitle: Caption.

    Returns:
        The path written.
    """
    plt = _pyplot()
    ordered = finishes.reverse()  # barh draws bottom-up; best row on top
    managers = ordered["manager"].to_list()
    top = ordered["top3_weeks"].to_list()
    close = ordered["close_calls"].to_list()
    chopped = [b - c for b, c in zip(ordered["bottom3_weeks"], close, strict=True)]
    positions = range(len(managers))

    fig, ax = plt.subplots(
        figsize=(9, 0.38 * len(managers) + 1.8), layout="constrained"
    )
    ax.barh(positions, top, color="#1b7837", label="Top 3 (good luck)")
    ax.barh(
        positions,
        [-c for c in close],
        color="#e08a8a",
        label="Bottom 3, survived (close call)",
    )
    ax.barh(
        positions,
        [-c for c in chopped],
        left=[-c for c in close],
        color="#c44e52",
        label="Bottom 3, chopped",
    )
    ax.axvline(0, color="#444444", linewidth=0.8)
    ax.set_yticks(list(positions), managers)
    limit = max([*top, *(b for b in ordered["bottom3_weeks"])], default=1) + 1
    ax.set_xlim(-limit, limit)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{abs(v):g}"))
    ax.set_xlabel("Weeks")
    ax.grid(True, axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    _title(
        ax,
        title,
        subtitle
        or "Weeks with 7+ teams alive · top-3 finishes also reflect skill, "
        "not only luck",
    )
    return _save(fig, out_path, "managers", len(managers))
