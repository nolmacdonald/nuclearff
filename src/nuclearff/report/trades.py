"""Render the manager trade network: PNGs from ``nuclearff.sleeper.trades`` output.

``matplotlib`` is imported lazily inside each render function, the same
posture :mod:`nuclearff.report.tables` uses, so importing ``nuclearff.report``
doesn't require it at module load time. Every render function here needs no
``dev`` extra at all, since ``matplotlib`` is a core dependency (unlike
``plottable``) -- ``render_trade_network`` needs ``networkx`` and
``render_trade_leaderboard`` needs ``plottable``, both genuine ``dev``
extras. ``render_chord_diagram`` is hand-drawn in matplotlib rather than
via ``plotly``/``kaleido`` for the same "no headless browser anywhere in
the path" reason -- see that function's own docstring.
"""

from __future__ import annotations

import logging
import math
from itertools import combinations
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


def render_trade_network(
    counts: pl.DataFrame,
    matrix: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Manager Trade Network",
) -> Path:
    """Render a node-link graph of the manager trade network.

    Each manager is a node (sized by their total trades); each manager pair
    with at least one trade between them is an edge (widened by the trade
    count between that pair).

    Args:
        counts: One row per manager, with ``manager`` and ``trades`` columns
            -- e.g. :func:`render_trades_by_manager`'s ``counts`` input,
            densified against the full manager roster so a zero-trade
            manager still appears, here as an isolated node rather than a
            missing one.
        matrix: A manager x manager trade-count matrix, densified the same
            way -- e.g. :func:`render_trades_heatmap`'s ``matrix`` input.
            Must have exactly the same managers as ``counts``.
        out_path: Destination PNG path.
        title: Figure title.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If ``matplotlib`` or ``networkx`` (a
            ``dev`` extra) is not installed.
    """
    try:
        import matplotlib

        # Force the non-interactive Agg backend before pyplot is imported --
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        import networkx as nx
    except ImportError as exc:
        raise RenderingUnavailableError(
            "Rendering the trade network needs the `dev` extra (networkx, "
            "matplotlib): `uv sync --extra dev`."
        ) from exc

    managers = counts["manager"].to_list()
    trades_by_manager = dict(zip(managers, counts["trades"].to_list(), strict=True))
    matrix_rows = {row["manager"]: row for row in matrix.to_dicts()}

    graph = nx.Graph()
    graph.add_nodes_from(managers)
    for manager_a, manager_b in combinations(managers, 2):
        weight = matrix_rows[manager_a][manager_b]
        if weight > 0:
            graph.add_edge(manager_a, manager_b, weight=weight)

    # A fixed seed, not networkx's default random one: the same trade
    # history should always render to the same layout, matching this
    # project's broader habit of deterministic output (e.g. alphabetical
    # tie-breaks elsewhere in this module). `k` (target inter-node distance)
    # is widened well past spring_layout's own default (`1/sqrt(n)`) --
    # real-name manager labels are much wider than the single-character
    # labels the default spacing assumes, and at the default spacing every
    # label in a real, non-trivial roster overlapped its neighbors.
    n = max(len(managers), 1)
    pos = nx.spring_layout(graph, seed=42, k=3 / math.sqrt(n))

    node_sizes = [400 + 200 * trades_by_manager[m] for m in graph.nodes]
    edge_widths = [1 + 1.5 * w for *_, w in graph.edges(data="weight")]

    fig, ax = plt.subplots(figsize=(10, 10))
    nx.draw_networkx_edges(
        graph, pos, width=edge_widths, edge_color="#4c72b0", alpha=0.5, ax=ax
    )
    nx.draw_networkx_nodes(
        graph, pos, node_size=node_sizes, node_color="#4c72b0", alpha=0.85, ax=ax
    )
    nx.draw_networkx_labels(graph, pos, font_size=9, ax=ax)

    # `pad=30` on the title, matching `report/tables.py`'s subtitle pattern:
    # without it, the default title padding is too tight for a second line
    # of text immediately above the axes, and the caveat below overlapped
    # the title outright.
    ax.set_title(title, loc="left", fontsize=14, weight="bold", pad=30)
    ax.text(
        0,
        1.006,
        "A sparse graph reflects a small, real trade sample -- not missing data.",
        transform=ax.transAxes,
        fontsize=9,
        style="italic",
        color="#888888",
        ha="left",
        va="bottom",
    )
    ax.axis("off")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info(
        "Wrote %s (%d managers, %d edges)",
        out_path,
        len(managers),
        graph.number_of_edges(),
    )
    return out_path


def render_trade_leaderboard(
    counts: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Trade Leaderboard",
) -> Path:
    """Render a single reference table of every manager's trade activity.

    Follows :mod:`nuclearff.report.tables`' PNG-table conventions (styled
    columns, ``plottable``, a left-aligned bold title) but without
    circle-cropped headshots: unlike a player, a Sleeper manager has no
    headshot URL anywhere in this project's data model, only an avatar id
    that nothing currently persists
    (:func:`nuclearff.sleeper.client.SleeperClient.avatar_url` exists but is
    unwired) -- out of scope here, not an oversight.

    Args:
        counts: One row per manager, with ``manager``, ``trades``,
            ``unique_partners``, ``most_frequent_partner``, and
            ``trades_with_partner`` columns -- e.g.
            :func:`nuclearff.sleeper.trades.manager_trade_counts`'s output,
            densified against the full manager roster so a zero-trade
            manager still gets a row. That function alone never produces a
            zero-trade row at all, so ``most_frequent_partner`` is expected
            to be null for one -- rendered as ``"—"``, not an error.
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
            "Rendering the trade leaderboard needs the `dev` extra "
            "(plottable, matplotlib): `uv sync --extra dev`."
        ) from exc

    ordered = counts.sort(["trades", "manager"], descending=[True, False])
    # Built from dicts, not `Polars.to_pandas()` -- see report/tables.py's
    # own comment on why (avoids a pyarrow dependency this project doesn't
    # otherwise need).
    frame = pd.DataFrame(ordered.to_dicts())
    frame["most_frequent_partner"] = frame["most_frequent_partner"].fillna("—")
    # A pre-formatted display string, not left as a mixed int/"—" column:
    # plottable's numeric `formatter` option would raise trying to format
    # the dash. A manager with no partner has no trades-with-partner count
    # to show either, regardless of what densification filled the raw
    # column with.
    frame["trades_with_partner_display"] = [
        "—" if partner == "—" else str(int(value))
        for partner, value in zip(
            frame["most_frequent_partner"], frame["trades_with_partner"], strict=True
        )
    ]

    frame.index = pd.RangeIndex(1, len(frame) + 1)
    frame.index.name = "#"

    # Widths sized to fit each column's header text, not just its data --
    # plottable doesn't wrap or shrink a title that's wider than the column,
    # it just overflows into its neighbor. "UNIQUE PARTNERS", "MOST FREQUENT
    # PARTNER", and "TRADES WITH PARTNER" are far wider than report/tables.py's
    # short abbreviations ("PTS", "VORP"), and the first pass rendered against
    # this league's real 15-manager roster showed exactly that overlap.
    column_definitions = [
        ColumnDefinition(
            name="manager",
            title="MANAGER",
            width=2.2,
            textprops={"ha": "left", "weight": "bold", "fontsize": 11},
        ),
        ColumnDefinition(
            name="trades",
            title="TRADES",
            width=1.3,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
            border="left",
        ),
        ColumnDefinition(
            name="unique_partners",
            title="UNIQUE PARTNERS",
            width=1.8,
            textprops={"ha": "center", "fontsize": 10},
        ),
        ColumnDefinition(
            name="most_frequent_partner",
            title="MOST FREQUENT PARTNER",
            width=2.6,
            textprops={"ha": "left", "fontsize": 10, "color": "#444444"},
        ),
        ColumnDefinition(
            name="trades_with_partner_display",
            title="TRADES WITH PARTNER",
            width=2.2,
            textprops={"ha": "center", "fontsize": 10},
        ),
    ]

    fig, ax = plt.subplots(figsize=(14, 0.5 * len(frame) + 2.2))
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
        '"—" marks a manager who has not made a trade yet.',
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


def render_manager_pair_leaderboard(
    pairs: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Manager-Pair Leaderboard",
) -> Path:
    """Render a horizontal bar chart of the league's most active trading pairs.

    Args:
        pairs: One row per manager pair, with ``manager_a``, ``manager_b``,
            and ``trades`` columns -- e.g.
            :func:`nuclearff.sleeper.trades.top_manager_pairs`'s output.
            Each pair must appear only once (that function's own docstring
            explains why its output already guarantees this).
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
            "Rendering the manager-pair leaderboard needs matplotlib, a "
            "core dependency: `uv sync`."
        ) from exc

    # Ascending, so the top pair ends up at the top of the chart -- same
    # barh-draws-bottom-to-top reasoning as render_trades_by_manager. Ties
    # broken alphabetically for a deterministic chart.
    ordered = pairs.sort(
        ["trades", "manager_a", "manager_b"], descending=[False, False, False]
    )
    labels = [
        f"{row['manager_a']} ↔ {row['manager_b']}"
        for row in ordered.iter_rows(named=True)
    ]
    trades = ordered["trades"].to_list()

    fig, ax = plt.subplots(figsize=(9, 0.4 * len(labels) + 1.5))
    ax.barh(labels, trades, color="#4c72b0")
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
    logger.info("Wrote %s (%d pairs)", out_path, len(labels))
    return out_path


def render_trades_over_time(
    by_season: pl.DataFrame,
    totals: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Trades Over Time",
) -> Path:
    """Render a line chart of trade activity by season, one line per manager.

    A manager's line is drawn only across the seasons they actually appear
    in ``by_season`` -- a manager who joined the league partway through
    simply starts later, rather than having earlier seasons connected to
    real ones by a misleading straight line. A season the manager rostered
    but didn't trade in should already be an explicit ``0`` row in
    ``by_season`` (not an absent one), so it renders as a real dip in the
    line rather than a gap.

    Args:
        by_season: One row per (manager, season), with ``manager``,
            ``season``, and ``trades`` columns -- e.g.
            :func:`nuclearff.sleeper.trades.trades_by_season`'s output,
            densified by the caller against the seasons each manager
            actually rostered (that function alone is only dense over
            seasons with at least one trade -- its own docstring explains
            why densifying isn't its job, needing ``sleeper_standings``,
            not ``sleeper_transactions``).
        totals: One row per season, with ``season`` and ``trades`` columns
            -- e.g. :func:`nuclearff.sleeper.trades.total_trades_by_season`'s
            output, the league-wide line.
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
            "Rendering trades-over-time needs matplotlib, a core dependency: `uv sync`."
        ) from exc

    fig, ax = plt.subplots(figsize=(10, 6))

    for manager in sorted(by_season["manager"].unique()):
        series = by_season.filter(pl.col("manager") == manager).sort("season")
        ax.plot(
            series["season"],
            series["trades"],
            color="#4c72b0",
            alpha=0.25,
            linewidth=1,
            marker="o",
            markersize=3,
        )

    totals = totals.sort("season")
    ax.plot(
        totals["season"],
        totals["trades"],
        color="#c44e52",
        linewidth=2.5,
        marker="o",
        markersize=5,
        label="League total",
    )

    all_seasons = sorted(set(by_season["season"]) | set(totals["season"]))
    if all_seasons:
        ax.set_xticks(all_seasons)

    ax.set_xlabel("Season")
    ax.set_ylabel("Trades")
    ax.set_title(title, loc="left", fontsize=14, weight="bold")
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info(
        "Wrote %s (%d managers, %d seasons)",
        out_path,
        by_season["manager"].n_unique(),
        len(all_seasons),
    )
    return out_path


def render_manager_season_heatmap(
    matrix: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Manager Activity by Season",
) -> Path:
    """Render a heatmap of trade counts per manager per season.

    Args:
        matrix: One ``manager`` column plus one column per season (as a
            string column name, e.g. ``"2021"``) -- e.g. the CLI's
            ``_densify_manager_season_matrix`` output, dense over every
            manager x season combination in the league's real history.
            :func:`nuclearff.sleeper.trades.trades_by_season` alone is only
            dense over combinations with at least one trade -- see its own
            docstring for why densifying isn't its job.
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
            "Rendering the manager-season heatmap needs matplotlib, a "
            "core dependency: `uv sync`."
        ) from exc

    managers = matrix["manager"].to_list()
    seasons = [c for c in matrix.columns if c != "manager"]
    n_managers = len(managers)
    n_seasons = len(seasons)
    values = (
        matrix.select(seasons).to_numpy() if seasons else matrix.select([]).to_numpy()
    )
    max_value = int(values.max()) if values.size else 0

    # Same lessons already learned rendering render_trades_heatmap (issue
    # #43): constrained layout, not tight_layout(), for a colorbar +
    # equal-aspect imshow; and a floor under both dimensions so a small
    # roster/short history doesn't leave too little room for labels and the
    # colorbar together.
    width = max(0.7 * n_seasons + 3, 6)
    height = max(0.5 * n_managers + 2, 6)
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
    im = ax.imshow(values, cmap="Blues", vmin=0)
    ax.set_xticks(range(n_seasons))
    ax.set_xticklabels(seasons)
    ax.set_yticks(range(n_managers))
    ax.set_yticklabels(managers)

    for i in range(n_managers):
        for j in range(n_seasons):
            value = int(values[i, j])
            color = "white" if max_value and value > max_value / 2 else "black"
            ax.text(j, i, str(value), ha="center", va="center", color=color, fontsize=9)

    ax.set_title(title, loc="left", fontsize=14, weight="bold")
    fig.colorbar(im, ax=ax, label="Trades")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info("Wrote %s (%d managers, %d seasons)", out_path, n_managers, n_seasons)
    return out_path


def render_cumulative_trades(
    cumulative: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Cumulative Trades Over Time",
) -> Path:
    """Render a step chart of each manager's running trade total over time.

    A step (not straight-line) chart: a manager's count is flat between
    trades and jumps at the real ``created_at`` of each one, so the chart
    doesn't imply gradual accrual between events that didn't happen. Lines
    are labeled directly at their right end rather than in a legend --
    with as many managers as a real league roster (15 for this league),
    a legend box would either run off the figure or need its own overlap
    fixes, the same class of problem already hit rendering
    ``render_trades_heatmap`` (issue #43) and ``render_trade_leaderboard``
    (issue #46). End-of-line labels are then decluttered in y -- several
    managers in this league's real history stop trading early and
    plateau at the same low count (1 or 2), which left their default
    labels overlapping into unreadable merged text before this fix.

    Args:
        cumulative: One row per (manager, trade), e.g.
            :func:`nuclearff.sleeper.trades.cumulative_trade_counts`'s
            output -- ``manager``, ``created_at``, ``cumulative_trades``.
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
        from matplotlib.transforms import blended_transform_factory
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering cumulative trades needs matplotlib, a core "
            "dependency: `uv sync`."
        ) from exc

    managers = sorted(cumulative["manager"].unique())
    fig, ax = plt.subplots(figsize=(10, 6), layout="constrained")
    cmap = plt.get_cmap("tab20")

    ends = []
    for i, manager in enumerate(managers):
        series = cumulative.filter(pl.col("manager") == manager).sort("created_at")
        color = cmap(i / max(len(managers) - 1, 1))
        ax.step(
            series["created_at"],
            series["cumulative_trades"],
            where="post",
            color=color,
            linewidth=1.5,
        )
        ends.append(
            (manager, series["created_at"][-1], series["cumulative_trades"][-1], color)
        )

    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative trades")
    ax.set_title(title, loc="left", fontsize=14, weight="bold")
    fig.autofmt_xdate()

    # Declutter end-of-line labels along y (in axes-fraction space, so the
    # minimum gap holds regardless of the data's actual range): several
    # managers plateau at the same low count and would otherwise collide.
    # A thin leader line (drawn by `annotate`'s own arrowprops, since xy and
    # xytext live in different coordinate systems here) still points each
    # label back at its real endpoint.
    y0, y1 = ax.get_ylim()
    min_gap = 0.045
    placed = -1.0
    for manager, x_end, y_end, color in sorted(ends, key=lambda e: e[2]):
        frac = (y_end - y0) / (y1 - y0) if y1 > y0 else 0.0
        frac = max(frac, placed + min_gap)
        placed = frac
        ax.annotate(
            manager,
            xy=(x_end, y_end),
            xycoords="data",
            xytext=(x_end, frac),
            textcoords=blended_transform_factory(ax.transData, ax.transAxes),
            va="center",
            fontsize=8,
            # Label text stays a fixed dark color regardless of the line's
            # own color -- tab20 includes pale entries (e.g. light yellow)
            # that are illegible on white. The leader line still carries
            # the series' real color, so identity isn't lost.
            color="#222222",
            arrowprops={"arrowstyle": "-", "color": color, "lw": 0.6, "alpha": 0.6},
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info("Wrote %s (%d managers)", out_path, len(managers))
    return out_path


def render_trade_partner_diversity(
    counts: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Trade Partner Diversity",
) -> Path:
    """Render a scatter of total trades vs. unique trade partners.

    Separates a manager who trades with everyone from one who repeatedly
    trades with the same 1-2 people -- a distinction the raw trade count
    alone can't make.

    Args:
        counts: One row per manager, with ``manager``, ``trades``, and
            ``unique_partners`` columns -- e.g. the CLI's densified
            :func:`nuclearff.sleeper.trades.manager_trade_counts` output
            (dense over the full manager roster, so a manager with zero
            trades appears at the origin rather than being dropped).
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
        from matplotlib.transforms import blended_transform_factory
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering trade-partner diversity needs matplotlib, a core "
            "dependency: `uv sync`."
        ) from exc

    # Every zero-trade manager lands on the exact same point (0, 0) -- a
    # real collision for this league (5 of 15 managers never traded), not
    # just a synthetic edge case. Group by the (trades, unique_partners)
    # coordinate first and draw one marker with a joined label per group,
    # rather than stacking fully-overlapping duplicate points and labels.
    groups = (
        counts.group_by(["trades", "unique_partners"], maintain_order=True)
        .agg(pl.col("manager").sort())
        .sort(["trades", "unique_partners"])
    )

    fig, ax = plt.subplots(figsize=(8, 8), layout="constrained")
    xs = groups["trades"].to_list()
    ys = groups["unique_partners"].to_list()
    ax.scatter(xs, ys, color="#4c72b0", s=60, zorder=2)

    ax.set_xlabel("Total trades")
    ax.set_ylabel("Unique trade partners")
    ax.set_title(title, loc="left", fontsize=14, weight="bold")
    ax.set_xlim(left=-0.5)
    ax.set_ylim(bottom=-0.5)
    ax.grid(True, alpha=0.3, zorder=1)

    # Declutter labels along y (axes-fraction space, so the minimum gap
    # holds regardless of the data's range) the same way
    # render_cumulative_trades (issue #50) does -- this scatter's discrete,
    # small integer axes produce exactly the same kind of coordinate
    # collisions a naive per-point label would overlap on.
    y0, y1 = ax.get_ylim()
    min_gap = 0.05
    placed = -1.0
    points = sorted(
        zip(xs, ys, groups["manager"].to_list(), strict=True),
        key=lambda point: point[1],
    )
    for x, y, managers in points:
        label = ", ".join(managers)
        frac = (y - y0) / (y1 - y0) if y1 > y0 else 0.0
        frac = max(frac, placed + min_gap)
        placed = frac
        ax.annotate(
            label,
            xy=(x, y),
            xycoords="data",
            xytext=(x, frac),
            textcoords=blended_transform_factory(ax.transData, ax.transAxes),
            va="center",
            fontsize=8,
            color="#222222",
            arrowprops={
                "arrowstyle": "-",
                "color": "#4c72b0",
                "lw": 0.6,
                "alpha": 0.6,
            },
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info("Wrote %s (%d managers)", out_path, counts.height)
    return out_path


def render_chord_diagram(
    counts: pl.DataFrame,
    matrix: pl.DataFrame,
    out_path: str | Path,
    *,
    title: str = "Manager Trade Network (Chord Diagram)",
) -> Path:
    """Render a circular chord diagram of trades between managers.

    A more presentation-oriented view of the same trade-partner
    relationships as :func:`render_trade_network`'s node-link graph:
    managers sit evenly spaced on a circle, and each pair with at least
    one trade is joined by a curved arc, widened by the trade count
    between that pair.

    Hand-drawn in matplotlib (a quadratic Bezier per arc, sampled and
    plotted as a line) rather than via ``plotly``/``kaleido`` -- GitHub
    Issue 45 originally decided on the latter, but ``kaleido``'s current
    major version needs a separately-installed headless Chrome to export
    a static image at all (its old self-contained-Chromium 0.x line is
    no longer compatible with current ``plotly``), which both broke
    outright in a real sandboxed environment with no browser present and
    directly contradicts this module's own "no headless browser anywhere
    in the path" posture (see :mod:`nuclearff.report.tables`). Reversed
    back to the epic's original (#40) fallback recommendation -- see
    ``decisions.md`` in the project brain for the full reasoning. Needs no
    ``dev`` extra at all: matplotlib is a core dependency.

    Args:
        counts: One row per manager, with ``manager`` and ``trades``
            columns, densified against the full manager roster so a
            zero-trade manager still appears, here as an isolated point
            on the circle -- e.g. :func:`render_trade_network`'s
            ``counts`` input.
        matrix: A manager x manager trade-count matrix, densified the
            same way and with exactly the same managers as ``counts`` --
            e.g. :func:`render_trades_heatmap`'s ``matrix`` input.
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
            "Rendering the chord diagram needs matplotlib, a core "
            "dependency: `uv sync`."
        ) from exc

    managers = counts["manager"].to_list()
    n = len(managers)
    trades_by_manager = dict(zip(managers, counts["trades"].to_list(), strict=True))
    matrix_rows = {row["manager"]: row for row in matrix.to_dicts()}

    angles = [2 * math.pi * i / n for i in range(n)] if n else []
    positions = dict(
        zip(managers, ((math.cos(a), math.sin(a)) for a in angles), strict=True)
    )

    fig, ax = plt.subplots(figsize=(9, 9), layout="constrained")

    weights = [
        matrix_rows[a][b] for a, b in combinations(managers, 2) if matrix_rows[a][b] > 0
    ]
    max_weight = max(weights, default=0)
    for manager_a, manager_b in combinations(managers, 2):
        weight = matrix_rows[manager_a][manager_b]
        if weight == 0:
            continue
        x0, y0 = positions[manager_a]
        x1, y1 = positions[manager_b]
        # Quadratic Bezier control point pulled toward the circle's
        # center -- the standard chord-diagram bulge, so arcs between
        # nearby managers don't run straight along the circle's rim and
        # clutter it.
        cx, cy = (x0 + x1) * 0.15, (y0 + y1) * 0.15
        t = [i / 40 for i in range(41)]
        curve_x = [(1 - s) ** 2 * x0 + 2 * (1 - s) * s * cx + s**2 * x1 for s in t]
        curve_y = [(1 - s) ** 2 * y0 + 2 * (1 - s) * s * cy + s**2 * y1 for s in t]
        ax.plot(
            curve_x,
            curve_y,
            color="#4c72b0",
            alpha=0.5,
            linewidth=0.5 + 3.5 * weight / max_weight if max_weight else 1,
            zorder=1,
        )

    max_trades = max(trades_by_manager.values(), default=0)
    node_x = [positions[m][0] for m in managers]
    node_y = [positions[m][1] for m in managers]
    node_size = [
        80 + 320 * trades_by_manager[m] / max_trades if max_trades else 80
        for m in managers
    ]
    ax.scatter(node_x, node_y, s=node_size, color="#c44e52", zorder=2)

    # Labels placed just outside the circle at each node's own angle,
    # with horizontal alignment flipped on the circle's left half so text
    # extends away from the circle rather than back over it.
    for manager, angle in zip(managers, angles, strict=True):
        lx, ly = 1.15 * math.cos(angle), 1.15 * math.sin(angle)
        ax.text(
            lx,
            ly,
            manager,
            ha="left" if math.cos(angle) >= 0 else "right",
            va="center",
            fontsize=9,
            color="#222222",
        )

    ax.set_title(title, loc="left", fontsize=14, weight="bold")
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.6, 1.6)
    ax.set_aspect("equal")
    ax.axis("off")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info("Wrote %s (%d managers)", out_path, n)
    return out_path
