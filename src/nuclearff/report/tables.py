"""Styled per-position auction tables, rendered to PNG with ``plottable``.

Conventions follow ``dev/tables/ex_table.py``, the project's existing
``plottable`` table (see ``decisions.md`` for why ``plottable`` over
``great_tables``): circle-cropped headshots via ``plot_fn=circled_image``,
disk-cached image downloads, a left-aligned bold title with an italic
subtitle above and a source note below, and ``fig.savefig()`` — no headless
browser anywhere in the path.

``plottable``, ``matplotlib``, ``pandas``, ``Pillow`` and ``requests`` are
imported lazily inside :func:`render_position_table` rather than at module
import. ``plottable`` is a **dev extra**, not a runtime dependency of the
installed package, so importing it at module scope would break
``import nuclearff.report`` on a plain install. Everything above the
rendering step (:func:`top_n_by_position`) is pure Polars and works without
any of them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

TABLE_COLUMNS = (
    "rank_position",
    "headshot_path",
    "player_display_name",
    "recent_team",
    "adp_overall",
    "value_estimate",
    "vorp",
    "auction_value",
)
"""Board columns rendered, in display order."""


class RenderingUnavailableError(ImportError):
    """The optional ``dev`` extra needed to render PNG tables is missing.

    A dedicated type so callers can distinguish "plottable isn't installed"
    from any other :class:`ImportError` raised while rendering. A blanket
    ``except ImportError`` here previously swallowed a ``pyarrow``
    ``ModuleNotFoundError`` (which subclasses ``ImportError``) and reported it
    as a missing ``plottable`` — misleading, and it hid a real bug.
    """


COLUMN_TITLES = {
    "rank_position": "#",
    "headshot_path": "",
    "player_display_name": "PLAYER",
    "recent_team": "TEAM",
    "adp_overall": "ECR",
    "value_estimate": "PTS",
    "vorp": "VORP",
    "auction_value": "$",
}
"""Header labels. ``ECR`` is deliberately not labeled ``ADP`` — see
:mod:`nuclearff.nflverse.rankings`."""


def top_n_by_position(board: pl.DataFrame, position: str, n: int = 12) -> pl.DataFrame:
    """Return the top ``n`` players at ``position``, ranked by auction value.

    Args:
        board: Output of
            :func:`nuclearff.pipeline.auction_board.build_auction_board`.
        position: Position to filter to.
        n: Number of players to keep.

    Returns:
        At most ``n`` rows, best first.

    Raises:
        ValueError: If ``board`` has no rows at ``position``.
    """
    pool = board.filter(pl.col("position") == position).sort(
        "auction_value", descending=True, nulls_last=True
    )
    if pool.height == 0:
        raise ValueError(
            f"top_n_by_position: no players at position {position!r} in the board."
        )
    return pool.head(n)


def _fetch_headshot(url: str | None, dest: Path, max_size: int = 300) -> str:
    """Download ``url`` to ``dest`` as a PNG, caching on disk.

    Mirrors ``dev/tables/ex_table.py``'s ``fetch_image``. A missing URL or a
    failed download returns an empty string rather than raising: one
    unavailable headshot should not sink a whole table.

    Args:
        url: Headshot URL, or ``None``.
        dest: Destination path for the cached PNG.
        max_size: Longest edge, in pixels.

    Returns:
        The cached file path as a string, or ``""`` when unavailable.
    """
    from io import BytesIO

    import requests
    from PIL import Image

    if not url:
        return ""
    if dest.exists():
        return str(dest)

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
        image.thumbnail((max_size, max_size))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        image.save(dest, format="PNG")
        return str(dest)
    except Exception as exc:  # noqa: BLE001 - a headshot is never load-bearing
        logger.warning("Could not fetch headshot %s: %s", url, exc)
        return ""


def _placeholder_headshot(cache_dir: Path, size: int = 300) -> str:
    """A neutral filled-circle image standing in for a missing headshot.

    ``circled_image`` (from ``plottable``) opens its ``headshot_path`` cell
    with ``plt.imread`` unconditionally — an empty path (what
    :func:`_fetch_headshot` returns for a missing URL or a failed download)
    raises ``FileNotFoundError`` deep inside matplotlib/PIL rather than
    rendering a blank cell. Generated once per ``cache_dir`` and reused, the
    same way a real headshot is cached.

    Args:
        cache_dir: Directory the placeholder is cached under.
        size: Edge length in pixels.

    Returns:
        The cached placeholder file path as a string.
    """
    from PIL import Image

    dest = cache_dir / "_placeholder.png"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (size, size), (200, 200, 200, 255)).save(dest, format="PNG")
    return str(dest)


def render_position_table(
    board: pl.DataFrame,
    position: str,
    out_path: str | Path,
    *,
    context: dict[str, Any] | None = None,
    n: int = 12,
    cache_dir: str | Path = "data/cache/headshots",
) -> Path:
    """Render one position's top-``n`` auction table to a PNG.

    Args:
        board: Output of
            :func:`nuclearff.pipeline.auction_board.build_auction_board`.
        position: Position to render.
        out_path: Destination PNG path.
        context: The board context, used for the subtitle (league name,
            budget, scoring type). Optional.
        n: Number of players in the table.
        cache_dir: Directory for cached headshot PNGs.

    Returns:
        The path written.

    Raises:
        ImportError: If the ``dev`` extra (``plottable``/``matplotlib``) is
            not installed.
        ValueError: If ``board`` has no rows at ``position``.
    """
    try:
        import matplotlib
        import pandas as pd

        # Force the non-interactive Agg backend before pyplot is imported.
        # matplotlib otherwise picks an interactive default (TkAgg), which
        # raises `ModuleNotFoundError: _tkinter` on a Python built without Tk
        # — as this project's 3.14 interpreter is. Nothing here ever opens a
        # window; it renders straight to a PNG file.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        from plottable import ColumnDefinition, Table
        from plottable.plots import circled_image
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RenderingUnavailableError(
            "Rendering tables needs the `dev` extra (plottable, matplotlib): "
            "`uv sync --extra dev`. The board CSV itself needs none of that."
        ) from exc

    top = top_n_by_position(board, position, n)
    cache_dir = Path(cache_dir)

    headshots = [
        _fetch_headshot(
            row["headshot_url"],
            cache_dir / f"{row['player_id']}.png",
        )
        or _placeholder_headshot(cache_dir)
        for row in top.iter_rows(named=True)
    ]
    top = top.with_columns(pl.Series("headshot_path", headshots))

    # Built from dicts rather than `Polars.to_pandas()` on purpose: that path
    # converts through Arrow and raises `ModuleNotFoundError: pyarrow`, which
    # this project deliberately does not depend on (see the DuckDB+Arrow entry
    # in the brain's gotchas, and `duckdb_io`'s plain-SQL write path for the
    # same reason). `pd.DataFrame(list_of_dicts)` needs no Arrow at all.
    selected = top.select([c for c in TABLE_COLUMNS if c in top.columns])
    frame = pd.DataFrame(selected.to_dicts(), columns=selected.columns)
    frame = frame.set_index("rank_position")
    # plottable labels the index column from the index's *name*, so renaming
    # the column before set_index would not help - this is the only hook.
    frame.index.name = COLUMN_TITLES["rank_position"]

    column_definitions = [
        ColumnDefinition(
            name="headshot_path",
            title="",
            width=0.55,
            textprops={"ha": "center"},
            plot_fn=circled_image,
        ),
        ColumnDefinition(
            name="player_display_name",
            title=COLUMN_TITLES["player_display_name"],
            width=2.1,
            textprops={"ha": "left", "weight": "bold", "fontsize": 11},
        ),
        ColumnDefinition(
            name="recent_team",
            title=COLUMN_TITLES["recent_team"],
            width=0.6,
            textprops={"ha": "center", "color": "#999999", "fontsize": 10},
        ),
        ColumnDefinition(
            name="adp_overall",
            title=COLUMN_TITLES["adp_overall"],
            width=0.6,
            textprops={"ha": "center", "fontsize": 9.5, "color": "#666666"},
            formatter="{:.1f}",
        ),
        ColumnDefinition(
            name="value_estimate",
            title=COLUMN_TITLES["value_estimate"],
            width=0.7,
            textprops={"ha": "center", "fontsize": 9.5},
            formatter="{:.0f}",
            border="left",
        ),
        ColumnDefinition(
            name="vorp",
            title=COLUMN_TITLES["vorp"],
            width=0.7,
            textprops={"ha": "center", "fontsize": 9.5},
            formatter="{:.1f}",
        ),
        ColumnDefinition(
            name="auction_value",
            title=COLUMN_TITLES["auction_value"],
            width=0.7,
            textprops={"ha": "center", "fontsize": 11, "weight": "bold"},
            formatter="${:.0f}",
        ),
    ]

    fig, ax = plt.subplots(figsize=(11, 0.62 * len(frame) + 2.2))
    # Constructed for its side effect: Table draws itself onto `ax`. The
    # instance is not needed afterwards (the figure is saved via `fig`).
    Table(
        frame,
        column_definitions=column_definitions,
        textprops={"fontsize": 10, "ha": "center"},
        row_dividers=True,
        row_divider_kw={"linewidth": 0.5, "color": "#E3E3E3"},
        col_label_divider_kw={"linewidth": 1.5, "color": "black"},
        column_border_kw={"linewidth": 1.5, "color": "black"},
        ax=ax,
    )

    context = context or {}
    ax.set_title(
        f"Top {len(frame)} {position} — Auction Values",
        loc="left",
        fontsize=17,
        weight="bold",
        pad=30,
    )
    subtitle_bits = [
        str(context.get("league_name", "")),
        f"{context.get('num_teams', '?')} teams",
        f"${context.get('budget_per_team', '?')} budget",
        str(context.get("scoring_type", "") or "").replace("_", " "),
    ]
    ax.text(
        0,
        1.006,
        "  |  ".join(bit for bit in subtitle_bits if bit),
        transform=ax.transAxes,
        fontsize=9.5,
        style="italic",
        color="#666666",
        ha="left",
        va="bottom",
    )
    ax.text(
        0,
        -0.012,
        "PTS = recency-weighted realized points (not a forward projection)  |  "
        "ECR = FantasyPros expert consensus, not observed ADP  |  "
        "No keeper adjustment",
        transform=ax.transAxes,
        fontsize=8.5,
        style="italic",
        color="#888888",
        ha="left",
        va="top",
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # `fig`, not `table.figure`: they are the same figure, but plottable types
    # `.figure` as `Figure | SubFigure` and SubFigure has no `savefig`.
    fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info("Wrote %s (%d %s rows)", out_path, len(frame), position)
    return out_path
