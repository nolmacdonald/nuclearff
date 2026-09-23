"""Render a discovered user network: a PNG from ``nuclearff.sleeper.network`` output.

``matplotlib``/``networkx`` are imported lazily, the same posture
:func:`nuclearff.report.trades.render_trade_network` uses -- both are ``dev``
extras, not core dependencies, so importing this module doesn't require
either at load time.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import polars as pl

from nuclearff.sleeper.network import UserNetwork

logger = logging.getLogger(__name__)


class RenderingUnavailableError(ImportError):
    """``matplotlib`` or ``networkx`` isn't installed.

    A dedicated type so callers can distinguish this from any other
    :class:`ImportError` raised while rendering, matching
    :exc:`nuclearff.report.trades.RenderingUnavailableError`.
    """


def render_user_network(
    network: UserNetwork,
    out_path: str | Path,
    *,
    title: str = "User Network",
) -> Path:
    """Render a node-link graph of a discovered Sleeper user network.

    Each discovered user is a node (sized by their own league count that
    season); each pair of users who share at least one league is an edge
    (widened by the number of shared leagues). The seed user is drawn in a
    distinct color as the graph's natural center. Follows
    :func:`nuclearff.report.trades.render_trade_network`'s pattern exactly.

    Args:
        network: Output of
            :func:`nuclearff.sleeper.network.crawl_user_network`.
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
            "Rendering the user network needs the `dev` extra (networkx, "
            "matplotlib): `uv sync --extra dev`."
        ) from exc

    user_ids = network.users["user_id"].to_list()
    league_counts = dict(
        zip(user_ids, network.users["league_count"].to_list(), strict=True)
    )
    display_names = network.users["display_name"].to_list()
    labels = {
        uid: (name if name else uid)
        for uid, name in zip(user_ids, display_names, strict=True)
    }

    graph = nx.Graph()
    graph.add_nodes_from(user_ids)

    edges = (
        network.memberships.join(network.memberships, on="league_id", suffix="_b")
        .filter(pl.col("user_id") < pl.col("user_id_b"))
        .group_by(["user_id", "user_id_b"])
        .agg(pl.len().alias("weight"))
    )
    for user_a, user_b, weight in edges.iter_rows():
        graph.add_edge(user_a, user_b, weight=weight)

    # A fixed seed, not networkx's default random one -- same determinism
    # habit as render_trade_network, and for the same reason: the same
    # crawl should always render to the same layout.
    n = max(len(user_ids), 1)
    pos = nx.spring_layout(graph, seed=42, k=3 / math.sqrt(n))

    node_sizes = [400 + 60 * league_counts.get(u, 0) for u in graph.nodes]
    node_colors = [
        "#c44e52" if u == network.seed_user_id else "#4c72b0" for u in graph.nodes
    ]
    edge_widths = [1 + 1.5 * w for *_, w in graph.edges(data="weight")]

    fig, ax = plt.subplots(figsize=(10, 10))
    nx.draw_networkx_edges(
        graph, pos, width=edge_widths, edge_color="#4c72b0", alpha=0.4, ax=ax
    )
    nx.draw_networkx_nodes(
        graph, pos, node_size=node_sizes, node_color=node_colors, alpha=0.85, ax=ax
    )
    nx.draw_networkx_labels(graph, pos, labels=labels, font_size=8, ax=ax)

    # `pad=30` on the title, matching render_trade_network's own note: the
    # default title padding is too tight for a second line of text
    # immediately above the axes.
    ax.set_title(title, loc="left", fontsize=14, weight="bold", pad=30)
    ax.text(
        0,
        1.006,
        "Red is the seed user; node size is each user's own league count.",
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
        "Wrote %s (%d users, %d edges)",
        out_path,
        len(user_ids),
        graph.number_of_edges(),
    )
    return out_path
