"""Render cumulative wins over time, one line per manager, ending in a headshot.

Extends :func:`nuclearff.report.trades.render_cumulative_trades` (issue
#50)'s exact end-of-line label-declutter technique -- sorted labels pushed
apart by a minimum gap in axes-fraction y-space, connected back to the real
data point by a thin leader line -- but swaps the text label for that
manager's real Sleeper headshot (``OffsetImage`` + ``AnnotationBbox``,
matplotlib's standard mechanism for placing a small image at a data point;
not used anywhere else in this repo before GitHub Issue 79). A manager's
headshot resolves the same way issue #77's league-avatar table already
does: :meth:`nuclearff.sleeper.client.SleeperClient.avatar_url` plus
:mod:`nuclearff.report.tables`'s existing disk-cache-with-placeholder
pattern.

``matplotlib``/``Pillow``/``requests`` are imported lazily inside
:func:`render_cumulative_wins`, the same posture
:mod:`nuclearff.report.tables` uses.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


class RenderingUnavailableError(ImportError):
    """``matplotlib`` isn't installed.

    Matches :exc:`nuclearff.report.trades.RenderingUnavailableError`.
    """


def render_cumulative_wins(
    cumulative: pl.DataFrame,
    avatar_ids: dict[str, str | None],
    out_path: str | Path,
    *,
    title: str = "Cumulative Wins Over Time",
    subtitle: str = (
        "Raw win count in chronological order -- not adjusted for "
        "strength of schedule or playoff seeding."
    ),
    cache_dir: str | Path = "data/cache/avatars",
) -> Path:
    """Render a step chart of each manager's running win total.

    A step (not straight-line) chart, matching
    :func:`~nuclearff.report.trades.render_cumulative_trades`: a manager's
    count is flat between games and jumps only at a real win, so the chart
    doesn't imply gradual accrual between games that didn't happen. The
    x-axis is each manager's *own* game number (1, 2, 3, ...), not a
    league-wide week index -- a manager who joined the league partway
    through still starts their line at game 1, the same real mid-history
    turnover :func:`render_cumulative_trades` already handles.

    Args:
        cumulative: One row per (manager, game), e.g.
            :func:`nuclearff.sleeper.wins.cumulative_wins`'s output --
            ``manager``, ``game_number``, ``cumulative_wins``.
        avatar_ids: Manager display name -> raw Sleeper avatar id (or
            ``None``). A manager missing from this mapping, or mapped to
            ``None`` or a failed download, renders with a neutral
            placeholder image instead of a broken one.
        out_path: Destination PNG path.
        title: Figure title.
        subtitle: Caption under the title. Real, already-known data for
            this project's league shows why the default caption matters:
            a 2025 regular-season leader (20-8) finished 4th place, while
            the eventual champion was 16-12 -- cumulative wins and final
            placement are not the same thing here.
        cache_dir: Directory for cached avatar PNGs.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If the ``dev`` extra (``matplotlib``,
            ``Pillow``, ``requests``) is not installed.
    """
    try:
        import matplotlib

        # Force the non-interactive Agg backend before pyplot is imported --
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        from matplotlib.offsetbox import AnnotationBbox, OffsetImage
        from matplotlib.transforms import blended_transform_factory
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering cumulative wins needs matplotlib, a core dependency: `uv sync`."
        ) from exc

    from nuclearff.report.tables import _fetch_headshot, _placeholder_headshot
    from nuclearff.sleeper.client import SleeperClient

    cache_dir = Path(cache_dir)
    managers = sorted(cumulative["manager"].unique())
    fig, ax = plt.subplots(figsize=(11, 7), layout="constrained")
    cmap = plt.get_cmap("tab20")

    ends = []
    for i, manager in enumerate(managers):
        series = cumulative.filter(pl.col("manager") == manager).sort("game_number")
        color = cmap(i / max(len(managers) - 1, 1))
        ax.step(
            series["game_number"],
            series["cumulative_wins"],
            where="post",
            color=color,
            linewidth=1.5,
        )
        avatar_id = avatar_ids.get(manager)
        url = SleeperClient.avatar_url(avatar_id) if avatar_id else None
        avatar_path = _fetch_headshot(
            url, cache_dir / f"{manager}.png"
        ) or _placeholder_headshot(cache_dir)
        ends.append(
            (
                manager,
                series["game_number"][-1],
                series["cumulative_wins"][-1],
                color,
                avatar_path,
            )
        )

    # Extra room to the right of the last real game: every image is
    # left-aligned to its line's endpoint (`box_alignment=(0, 0.5)`) and
    # extends rightward from there, so a manager whose line reaches the
    # real max game number has their image extend past the axes -- and,
    # without this margin, past the figure's own canvas edge entirely,
    # clipped by `savefig`. Real for this league: several managers'
    # longest tenure reaches the actual last game plotted.
    x0, x1 = ax.get_xlim()
    ax.set_xlim(x0, x1 + (x1 - x0) * 0.12)

    ax.set_xlabel("Game Number")
    ax.set_ylabel("Cumulative Wins")
    # `pad=30` reserves real vertical room between the axes and the title
    # for the subtitle line below it -- without it, the title's own glyph
    # height (at fontsize=14, bold) extends down far enough to run directly
    # through a subtitle placed just above the axes edge, a real overlap
    # caught rendering against this league's real 15 managers.
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

    # Declutter end-of-line images along y (in axes-fraction space, so the
    # minimum gap holds regardless of the data's actual range) -- the same
    # technique `render_cumulative_trades` uses for text labels, but a
    # rendered headshot is visually much taller than a line of text, so it
    # needs a wider minimum gap or adjacent images overlap each other
    # outright rather than merely crowding.
    #
    # A *fixed* minimum gap (tried first) broke for this league's real 15
    # managers: 15 * a gap wide enough for an image comfortably exceeds the
    # axes' full [0, 1] fraction range, so the top few images cascaded
    # above the axes entirely, through the title, and off the top of the
    # figure. Fixed two ways: size the gap to the real number of managers
    # (never wider than needed), then rescale the whole placed set back
    # inside a safe [0, 0.97] band if it still overflows -- guaranteeing
    # every image renders inside the figure regardless of how many
    # managers there are or how tightly their real win totals cluster.
    y0, y1 = ax.get_ylim()
    sorted_ends = sorted(ends, key=lambda e: e[2])
    min_gap = min(0.11, 0.9 / max(len(sorted_ends) - 1, 1))
    fracs = []
    placed = -min_gap
    for _manager, _x_end, y_end, _color, _avatar_path in sorted_ends:
        frac = (y_end - y0) / (y1 - y0) if y1 > y0 else 0.0
        frac = max(frac, placed + min_gap)
        placed = frac
        fracs.append(frac)
    max_frac = max(fracs, default=0.0)
    if max_frac > 0.97:
        fracs = [f * 0.97 / max_frac for f in fracs]

    name_offset = min(0.035, min_gap * 0.4)
    for (manager, x_end, y_end, color, avatar_path), frac in zip(
        sorted_ends, fracs, strict=True
    ):
        image = OffsetImage(plt.imread(avatar_path), zoom=0.08)
        ax.add_artist(
            AnnotationBbox(
                image,
                (x_end, y_end),
                xycoords="data",
                xybox=(x_end, frac),
                boxcoords=blended_transform_factory(ax.transData, ax.transAxes),
                frameon=False,
                box_alignment=(0, 0.5),
                pad=0,
                arrowprops={"arrowstyle": "-", "color": color, "lw": 0.6, "alpha": 0.6},
                annotation_clip=False,
            )
        )
        # The manager's name as a small caption *below* the image, not on
        # top of it -- unlike a QB, a fantasy manager's face alone doesn't
        # identify them to most viewers, so the image alone (this module's
        # only difference from render_cumulative_trades' plain text label)
        # isn't enough on its own.
        ax.annotate(
            manager,
            xy=(x_end, y_end),
            xycoords="data",
            xytext=(x_end, frac - name_offset),
            textcoords=blended_transform_factory(ax.transData, ax.transAxes),
            va="center",
            ha="left",
            fontsize=7.5,
            color="#222222",
            annotation_clip=False,
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200)
    plt.close(fig)
    logger.info("Wrote %s (%d managers)", out_path, len(managers))
    return out_path
