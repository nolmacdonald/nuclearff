"""Render a draft as a snake-order grid, from ``sleeper_draft_picks`` rows.

Sleeper's own draft-room UI is a grid: one column per draft slot (team), one
row per round, each drafted cell a position-colored "bento box" card. A
draft's pick order isn't always a simple alternating snake -- a league
setting (``settings.reversal_round``) can make a later round continue the
same column direction as the round before it instead of reversing. Rather
than reproduce that logic here, each pick's own ``draft_slot`` (already
resolved by Sleeper, and already captured by
:func:`nuclearff.sleeper.draft.draft_pick_rows`) *is* the grid column --
confirmed live against this project's real, currently in-progress draft,
whose ``reversal_round: 3`` makes round 3 continue round 2's ``draft_slot``
direction rather than reversing back to round 1's.

``matplotlib`` is imported lazily inside :func:`render_draft_board`, the
same posture :mod:`nuclearff.report.tables` uses, so importing
``nuclearff.report`` doesn't require it at module load time. Everything
above the rendering step (:func:`draft_board_positions`) is pure Python and
works without it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POSITION_COLORS = {
    "RB": "#b6e3b6",
    "WR": "#a9d3f5",
    "QB": "#f5a9b8",
    "TE": "#f7c98e",
}
"""Cell background color by position, confirmed against Sleeper's own
draft-room UI (a real screenshot showing all four). Any other position
(K/DEF/DL/LB/DB, ...) falls back to :data:`_UNCONFIRMED_COLOR` -- not seen in
a real league yet, so not asserted as Sleeper's actual color for it."""

_UNCONFIRMED_COLOR = "#d9d9d9"
"""Fallback cell color for a position not in :data:`POSITION_COLORS`."""


class RenderingUnavailableError(ImportError):
    """``matplotlib`` isn't installed.

    A dedicated type so callers can distinguish "matplotlib isn't installed"
    from any other :class:`ImportError` raised while rendering, matching
    :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


def draft_board_positions(
    picks: list[dict[str, Any]],
) -> dict[int, tuple[int, int]]:
    """Map each pick to its grid ``(column, row)`` position.

    Args:
        picks: :data:`nuclearff.sleeper.draft.TABLE_NAME`-shaped rows (or
            anything with the same ``pick_no``/``draft_slot``/``round``
            keys). A row missing ``draft_slot`` or ``round`` is skipped
            rather than guessed.

    Returns:
        ``pick_no`` mapped to ``(column, row)``, both 1-indexed:
        ``column == draft_slot``, ``row == round``. No direction/alternation
        is computed -- see the module docstring for why that would be wrong
        for a league with a ``reversal_round``.
    """
    positions: dict[int, tuple[int, int]] = {}
    for pick in picks:
        pick_no = pick.get("pick_no")
        draft_slot = pick.get("draft_slot")
        round_ = pick.get("round")
        if (
            not isinstance(pick_no, int)
            or not isinstance(draft_slot, int)
            or not isinstance(round_, int)
        ):
            continue
        positions[pick_no] = (draft_slot, round_)
    return positions


def render_draft_board(
    picks: list[dict[str, Any]],
    names: dict[int, str],
    out_path: str | Path,
    *,
    teams: int,
    rounds: int,
    title: str = "Draft Board",
) -> Path:
    """Render a draft's picks so far as a snake-order grid PNG.

    Args:
        picks: :data:`nuclearff.sleeper.draft.TABLE_NAME`-shaped rows for one
            draft. A draft still in progress (fewer picks than
            ``teams * rounds``) renders exactly the cells that have been
            picked -- that is the normal state for any draft that hasn't
            finished, not an exceptional one.
        names: ``draft_slot`` mapped to a display name for that column's
            header (e.g. the team/owner's display name). A slot with no
            entry falls back to ``"Slot <n>"``.
        out_path: Destination PNG path.
        teams: Number of draft slots (grid columns).
        rounds: Number of rounds (grid rows).
        title: Figure title.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If ``matplotlib`` is not installed.
        ValueError: If ``picks`` has no row with a resolvable grid position.
    """
    try:
        import matplotlib

        # Force the non-interactive Agg backend before pyplot is imported --
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering a draft board needs matplotlib, a core dependency: `uv sync`."
        ) from exc

    positions = draft_board_positions(picks)
    if not positions:
        raise ValueError(
            "render_draft_board: no picks with a resolvable grid position to render."
        )
    picks_by_no = {pick["pick_no"]: pick for pick in picks if "pick_no" in pick}

    fig, ax = plt.subplots(figsize=(1.9 * teams + 1, 1.1 * rounds + 1.2))

    for pick_no, (column, row) in positions.items():
        pick = picks_by_no[pick_no]
        x0, y0 = column - 1, row - 1
        color = POSITION_COLORS.get(pick.get("position"), _UNCONFIRMED_COLOR)

        ax.add_patch(
            plt.Rectangle(
                (x0, y0), 1, 1, facecolor=color, edgecolor="#444444", linewidth=1.0
            )
        )
        first_name = pick.get("first_name") or ""
        last_name = pick.get("last_name") or ""
        team = pick.get("team") or ""
        header = f"{pick.get('position') or ''} - {team}".strip(" -")
        header = f"{header}  {row}.{column}" if header else f"{row}.{column}"
        ax.text(
            x0 + 0.05,
            y0 + 0.20,
            header,
            fontsize=7.5,
            va="bottom",
            color="#444444",
        )
        # First/last name on separate lines, not one combined string: a
        # combined "First Last" for a long real name (e.g. "Rhamondre
        # Stevenson") overflows this cell's width into the next column --
        # confirmed by rendering the real draft. Each name part alone fits;
        # this also matches the reference screenshot's own two-line layout.
        if first_name or last_name:
            ax.text(
                x0 + 0.05, y0 + 0.44, first_name, fontsize=9, va="bottom", clip_on=True
            )
            ax.text(
                x0 + 0.05,
                y0 + 0.68,
                last_name,
                fontsize=9,
                va="bottom",
                weight="bold",
                clip_on=True,
            )
        else:
            ax.text(x0 + 0.05, y0 + 0.5, "TBD", fontsize=9, va="bottom")

    for column in range(1, teams + 1):
        ax.text(
            column - 0.5,
            -0.15,
            names.get(column, f"Slot {column}"),
            fontsize=9,
            ha="center",
            va="bottom",
            weight="bold",
        )

    ax.set_xlim(0, teams)
    ax.set_ylim(rounds, -0.5)
    ax.axis("off")
    ax.set_title(title, loc="left", fontsize=15, weight="bold", pad=14)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info("Wrote %s (%d picks)", out_path, len(positions))
    return out_path
