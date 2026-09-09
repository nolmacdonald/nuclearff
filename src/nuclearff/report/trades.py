"""Render the manager trade network: PNGs from ``nuclearff.sleeper.trades`` output.

``matplotlib`` is imported lazily inside each render function, the same
posture :mod:`nuclearff.report.tables` uses, so importing ``nuclearff.report``
doesn't require it at module load time -- though it never actually needs the
``dev`` extra here, since ``matplotlib`` is a core dependency (unlike
``plottable``).
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


class RenderingUnavailableError(ImportError):
    """``matplotlib`` isn't installed.

    A dedicated type so callers can distinguish "matplotlib isn't installed"
    from any other :class:`ImportError` raised while rendering, matching
    :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


def render_trades_by_manager(
    counts: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Trades by Manager",
) -> Path:
    """Render a horizontal bar chart of total trades per manager.

    Args:
        counts: One row per manager, with ``manager`` and ``trades`` columns
            -- e.g. :func:`nuclearff.sleeper.trades.manager_trade_counts`'s
            output. That function alone only includes managers who traded
            at least once; a manager with zero trades will only appear here
            if the caller has already densified ``counts`` against the full
            manager roster (:mod:`nuclearff.sleeper.trades`' own module
            docstring explains why that isn't this module's job -- it needs
            ``sleeper_standings``, not ``sleeper_transactions``).
        out_path: Destination PNG path.
        title: Figure title.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If ``matplotlib`` is not installed.
    """
    try:
        import matplotlib

        # Force the non-interactive Agg backend before pyplot is imported --
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering trades-by-manager needs matplotlib, a core dependency: "
            "`uv sync`."
        ) from exc

    # Ascending, so the highest trade count ends up at the top of the chart:
    # matplotlib's barh draws categories bottom-to-top in list order. Ties
    # broken alphabetically by manager for a deterministic chart.
    ordered = counts.sort(["trades", "manager"], descending=[False, False])
    managers = ordered["manager"].to_list()
    trades = ordered["trades"].to_list()

    fig, ax = plt.subplots(figsize=(8, 0.4 * len(managers) + 1.5))
    ax.barh(managers, trades, color="#4c72b0")
    for i, value in enumerate(trades):
        ax.text(
            value + max(trades, default=0) * 0.01,
            i,
            str(value),
            va="center",
            fontsize=9,
        )

    ax.set_xlabel("Trades")
    ax.set_title(title, loc="left", fontsize=14, weight="bold")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info("Wrote %s (%d managers)", out_path, len(managers))
    return out_path


def render_trades_heatmap(
    matrix: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Trades Between Managers",
) -> Path:
    """Render a heatmap of trade counts between every manager pair.

    Args:
        matrix: A manager x manager trade-count matrix, one ``manager``
            column plus one same-named column per manager -- e.g.
            :func:`nuclearff.sleeper.trades.pairwise_trade_matrix`'s output.
            That function alone is only dense over managers who appear in
            trade data; a manager with zero trades will only appear here if
            the caller has already densified the matrix against the full
            manager roster, same caveat as :func:`render_trades_by_manager`.
        out_path: Destination PNG path.
        title: Figure title.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If ``matplotlib`` is not installed.
    """
    try:
        import matplotlib

        # Force the non-interactive Agg backend before pyplot is imported --
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering the trades heatmap needs matplotlib, a core "
            "dependency: `uv sync`."
        ) from exc

    managers = matrix["manager"].to_list()
    n = len(managers)
    values = matrix.select(managers).to_numpy() if n else matrix.select([]).to_numpy()
    max_value = int(values.max()) if values.size else 0

    # "constrained" layout, not tight_layout(): imshow's equal-aspect box
    # combined with a colorbar (which eats into available width but not
    # height) makes tight_layout() center the grid in its allotted space,
    # leaving a large dead gap between the title and the grid that even an
    # explicit ax.set_anchor("N") can't fix -- colorbar()/tight_layout() both
    # reposition the axes and silently reset the anchor. constrained layout
    # solves the same problem without that fight.
    # A floor, not just 0.6 * n + 2: below ~6 inches, constrained layout has
    # too little room for the title, tick labels, and colorbar together and
    # silently falls back to an uncorrected, overlapping layout (a real
    # failure mode hit by a 1-2 manager matrix -- a small but real league
    # size, not just a synthetic edge case).
    side = max(0.6 * n + 2, 6)
    fig, ax = plt.subplots(figsize=(side, side), layout="constrained")
    im = ax.imshow(values, cmap="Blues", vmin=0)
    ax.set_xticks(range(n))
    ax.set_xticklabels(managers, rotation=45, ha="right")
    ax.set_yticks(range(n))
    ax.set_yticklabels(managers)

    for i in range(n):
        for j in range(n):
            value = int(values[i, j])
            color = "white" if max_value and value > max_value / 2 else "black"
            ax.text(j, i, str(value), ha="center", va="center", color=color, fontsize=9)

    ax.set_title(title, loc="left", fontsize=14, weight="bold")
    fig.colorbar(im, ax=ax, label="Trades")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info("Wrote %s (%d managers)", out_path, n)
    return out_path
