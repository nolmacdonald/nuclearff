"""Render a user's leagues for a season as a PNG table with avatars.

``SleeperClient.get_user_leagues`` already returns every league a user
belongs to for a season, and ``SleeperClient.avatar_url`` already resolves
a league's bare avatar id to a real CDN URL -- neither has ever been wired
into any rendered output before this (see GitHub Issue 77). This module
does that: one row per league (avatar, name, id, type, teams, status), plus
a per-type count summary.

``plottable``/``matplotlib``/``pandas``/``Pillow``/``requests`` are imported
lazily inside :func:`render_user_leagues_table`, the same posture
:mod:`nuclearff.report.tables` uses. :func:`summarize_league_types` is pure
Python/dict work and needs none of them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nuclearff.sleeper.leagues import league_type_name

logger = logging.getLogger(__name__)


class RenderingUnavailableError(ImportError):
    """The optional ``dev`` extra needed to render PNG tables is missing.

    Matches :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


def summarize_league_types(leagues: list[dict[str, Any]]) -> dict[str, int]:
    """Count leagues by type, plus a total.

    Args:
        leagues: Raw league payloads, e.g.
            :meth:`~nuclearff.sleeper.client.SleeperClient.get_user_leagues`'s
            output.

    Returns:
        One entry per distinct league type actually present (alphabetical,
        via :func:`nuclearff.sleeper.leagues.league_type_name` -- not a
        fixed redraft/keeper/dynasty/chopped template, since a real account
        could have any subset, or an ``"unknown"`` type Sleeper hasn't
        documented), plus a final ``"Total"`` entry equal to their sum.
    """
    counts: dict[str, int] = {}
    for league in leagues:
        type_name = league_type_name(league)
        counts[type_name] = counts.get(type_name, 0) + 1
    ordered = {name: counts[name] for name in sorted(counts)}
    ordered["Total"] = len(leagues)
    return ordered


def render_user_leagues_table(
    leagues: list[dict[str, Any]],
    out_path: str | Path,
    *,
    title: str = "User Leagues",
    subtitle: str | None = None,
    cache_dir: str | Path = "data/cache/avatars",
) -> Path:
    """Render one user's leagues for a season as a PNG table.

    A league with no avatar (real for several leagues on a real account --
    Sleeper doesn't require one) renders with a neutral placeholder image
    rather than a broken cell.

    Args:
        leagues: Raw league payloads, e.g.
            :meth:`~nuclearff.sleeper.client.SleeperClient.get_user_leagues`'s
            output. Every league appears as a row -- no filtering.
        out_path: Destination PNG path.
        title: Figure title.
        subtitle: Optional italic line under the title (e.g. the season and
            sport).
        cache_dir: Directory for cached avatar PNGs.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``plottable``,
            ``matplotlib``, ``Pillow``, ``requests``) is not installed.
    """
    try:
        import matplotlib
        import pandas as pd

        # Force the non-interactive Agg backend before pyplot is imported --
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        from PIL import Image
        from plottable import ColumnDefinition, Table
        from plottable.plots import circled_image
    except ImportError as exc:
        raise RenderingUnavailableError(
            "Rendering the user-leagues table needs the `dev` extra "
            "(plottable, matplotlib): `uv sync --extra dev`."
        ) from exc

    from nuclearff.report.tables import _fetch_headshot, _placeholder_headshot
    from nuclearff.sleeper.client import SleeperClient

    cache_dir = Path(cache_dir)
    rows = []
    for league in leagues:
        avatar_id = league.get("avatar")
        url = SleeperClient.avatar_url(avatar_id) if avatar_id else None
        avatar_path = _fetch_headshot(
            url, cache_dir / f"{league.get('league_id')}.png"
        ) or _placeholder_headshot(cache_dir)
        rows.append(
            {
                "avatar_path": avatar_path,
                # `plottable`'s row height is sized from the Axes' own pixel
                # height at draw time, and `circled_image` draws at a fixed
                # size regardless of that -- a real mismatch caught by
                # rendering a real account's 18 leagues, where several
                # avatars overlapped the row above/below. Never an issue for
                # `render_position_table`, which never shares a figure with
                # a second Axes the way an earlier version of this function
                # did for its summary block -- see below.
                "league_name": _matplotlib_safe_text(league.get("name") or ""),
                "league_id": str(league.get("league_id", "")),
                "league_type": league_type_name(league).capitalize(),
                "total_rosters": league.get("total_rosters"),
                "status": (league.get("status") or "").replace("_", " "),
            }
        )

    frame = pd.DataFrame(rows)
    frame.index = pd.RangeIndex(1, len(frame) + 1, name="#")

    # A fixed width overflowed into the LEAGUE ID column next to it for
    # every real league name longer than ~20 characters -- a real account's
    # real leagues include a 38-character one ("Dynasty Fish Bowl Tiger
    # Barb Division"), not just short ones like "NATO". Sized to the
    # longest name actually present, the same "don't hardcode a width real
    # data can exceed" lesson issue #46's leaderboard table already learned
    # for its own column headers.
    max_name_len = max((len(str(row["league_name"])) for row in rows), default=10)
    name_width = max(2.4, 0.16 * max_name_len)
    other_columns_width = 5.2  # avatar 0.5 + id 2.2 + type 0.8 + teams 0.7 + status 1.0

    # `border="left"` on every column after the first draws an actual
    # divider line at that column's boundary (matching
    # `render_position_table`'s convention, where only one column opts in) --
    # every column here gets one, not just one: with no divider at all,
    # a league id's rightmost centered digit and the type column's leftmost
    # letter had no visual separation, close enough to misread as one
    # overflowing into the other even where the underlying column widths
    # (verified against the longest real id/type strings) don't actually
    # overlap.
    column_definitions = [
        ColumnDefinition(
            name="avatar_path",
            title="",
            width=0.5,
            textprops={"ha": "center"},
            plot_fn=circled_image,
        ),
        ColumnDefinition(
            name="league_name",
            title="LEAGUE",
            width=name_width,
            textprops={"ha": "left", "weight": "bold", "fontsize": 11},
        ),
        ColumnDefinition(
            name="league_id",
            title="LEAGUE ID",
            width=2.2,
            textprops={"ha": "center", "fontsize": 8.5, "color": "#999999"},
            border="left",
        ),
        ColumnDefinition(
            name="league_type",
            title="TYPE",
            width=0.8,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="total_rosters",
            title="TEAMS",
            width=0.7,
            textprops={"ha": "center", "fontsize": 10},
            border="left",
        ),
        ColumnDefinition(
            name="status",
            title="STATUS",
            width=1.0,
            textprops={"ha": "center", "fontsize": 9.5, "color": "#666666"},
            border="left",
        ),
    ]

    # Two independent figures, each with a single Axes -- the same posture
    # `render_position_table` uses -- composited into one PNG via PIL
    # afterwards, rather than two Axes sharing one figure. A shared figure
    # (tried first) needs the two Axes' heights negotiated by
    # `constrained_layout`/`gridspec` `height_ratios`, which doesn't
    # actually guarantee the league table's Axes gets the exact per-row
    # pixel height `circled_image` was sized against -- the real overlap
    # bug above. Keeping the league table alone in its own figure removes
    # that negotiation entirely.
    fig_leagues, ax_leagues = plt.subplots(
        figsize=(
            other_columns_width + name_width + 1.5,
            0.62 * max(len(frame), 1) + 2.2,
        )
    )
    Table(
        frame,
        column_definitions=column_definitions,
        textprops={"fontsize": 10, "ha": "center"},
        row_dividers=True,
        row_divider_kw={"linewidth": 0.5, "color": "#E3E3E3"},
        col_label_divider_kw={"linewidth": 1.5, "color": "black"},
        column_border_kw={"linewidth": 1.5, "color": "black"},
        ax=ax_leagues,
    )
    ax_leagues.set_title(title, loc="left", fontsize=17, weight="bold", pad=30)
    if subtitle:
        ax_leagues.text(
            0,
            1.006,
            subtitle,
            transform=ax_leagues.transAxes,
            fontsize=9.5,
            style="italic",
            color="#666666",
            ha="left",
            va="bottom",
        )
    leagues_image = _figure_to_image(fig_leagues)
    plt.close(fig_leagues)

    summary = summarize_league_types(leagues)
    fig_summary, ax_summary = plt.subplots(
        figsize=(other_columns_width + name_width + 1.5, 0.5 * len(summary) + 0.6)
    )
    # A plain text block, not a second `plottable.Table`: the summary has a
    # different, narrower schema (label, count) than the league grid above
    # it, and is only ever a handful of rows -- simpler than fighting
    # plottable's column model for something this small.
    ax_summary.axis("off")
    line_height = 1 / (len(summary) + 1)
    for i, (label, count) in enumerate(summary.items()):
        y = 1 - (i + 0.5) * line_height
        is_total = label == "Total"
        if is_total:
            ax_summary.axhline(
                y + line_height / 2, color="#999999", linewidth=0.8, xmax=0.35
            )
        ax_summary.text(
            0,
            y,
            "Total leagues" if is_total else f"{label} leagues",
            fontsize=11,
            weight="bold" if is_total else "normal",
            ha="left",
            va="center",
            transform=ax_summary.transAxes,
        )
        ax_summary.text(
            0.3,
            y,
            str(count),
            fontsize=11,
            weight="bold" if is_total else "normal",
            ha="left",
            va="center",
            transform=ax_summary.transAxes,
        )
    summary_image = _figure_to_image(fig_summary)
    plt.close(fig_summary)

    width = max(leagues_image.width, summary_image.width)
    composite = Image.new(
        "RGB", (width, leagues_image.height + summary_image.height), "white"
    )
    composite.paste(leagues_image, (0, 0))
    composite.paste(summary_image, (0, leagues_image.height))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path, format="PNG")
    logger.info("Wrote %s (%d leagues)", out_path, len(frame))
    return out_path


def _figure_to_image(fig: Any) -> Any:
    """Render a matplotlib figure to an in-memory ``PIL.Image``."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor="white", dpi=200, bbox_inches="tight")
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _matplotlib_safe_text(text: str) -> str:
    """Strip characters outside the Basic Multilingual Plane.

    Matplotlib's bundled default font (DejaVu Sans) has no emoji glyphs --
    real for a real league name (``"TEXAS BOYS \U0001f920"``), which
    otherwise renders as a missing-glyph box. Installing a color-emoji font
    is a real new system dependency for one cosmetic case; dropping the
    unsupported character (only ever supplementary-plane emoji in practice,
    not any BMP script) keeps rendering self-contained instead.
    """
    return "".join(ch for ch in text if ord(ch) <= 0xFFFF).strip()
