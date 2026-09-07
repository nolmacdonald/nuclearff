"""Render a playoff bracket as a horizontal tree, from ``sleeper_playoff_matches`` rows.

A Sleeper bracket isn't a similarity-clustering dendrogram, it's a
fixed-shape single-elimination tree keyed by ``t1_from``/``t2_from`` match
references (see :mod:`nuclearff.sleeper.standings`) — this hand-builds the
tree with matplotlib line segments rather than coercing it into
``scipy.cluster.hierarchy``'s linkage-matrix format.

``matplotlib`` is imported lazily inside :func:`render_bracket_tree`, the
same posture :mod:`nuclearff.report.tables` uses, so importing
``nuclearff.report`` doesn't require it at module load time. Everything
above the rendering step (:func:`bracket_matches_by_number`, the layout
computation) is pure Python and works without it.
"""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}


class RenderingUnavailableError(ImportError):
    """``matplotlib`` isn't installed.

    A dedicated type so callers can distinguish "matplotlib isn't installed"
    from any other :class:`ImportError` raised while rendering, matching
    :exc:`nuclearff.report.tables.RenderingUnavailableError`.
    """


def _ordinal(n: int) -> str:
    """Format an integer as an English ordinal, e.g. ``3`` -> ``"3rd"``.

    Args:
        n: A positive rank.

    Returns:
        The ordinal string.
    """
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = _ORDINAL_SUFFIXES.get(n % 10, "th")
    return f"{n}{suffix}"


def _load_from_ref(value: Any) -> tuple[str, int] | None:
    """Parse a ``t1_from``/``t2_from`` value into a ``(side, match_number)`` pair.

    Args:
        value: The raw field — a dict like ``{"w": 3}``, the same dict
            JSON-encoded as a string (as stored in ``sleeper_playoff_matches``),
            or ``None``.

    Returns:
        ``("w"|"l", match_number)``, or ``None`` if there's no reference
        (a bare entrant, direct from ``t1``/``t2``, or a bye).
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, dict) or not value:
        return None
    side, match_number = next(iter(value.items()))
    if side not in ("w", "l"):
        return None
    try:
        return side, int(match_number)
    except (TypeError, ValueError):
        return None


def bracket_matches_by_number(
    matches: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Index bracket match rows by their Sleeper match number.

    Args:
        matches: Rows for a single bracket (winners or losers) of a single
            season, e.g. from ``sleeper_playoff_matches`` filtered to one
            ``bracket``/``season``/``league_id``. Each row needs ``match``,
            ``round``, ``t1``, ``t2``, ``winner``, ``loser``, ``placement``,
            ``t1_from``, ``t2_from``.

    Returns:
        ``match`` number mapped to its row, dropping any row with no
        ``match`` number.
    """
    return {row["match"]: row for row in matches if isinstance(row.get("match"), int)}


def compute_bracket_positions(
    matches_by_number: dict[int, dict[str, Any]],
) -> dict[int, tuple[float, float]]:
    """Assign each match a vertical position for its two input lines.

    A match whose participant is a direct entrant (no ``t1_from``/``t2_from``
    — round 1, or a bye straight into a later round) gets the next unused
    integer slot. A match whose participant comes from an earlier match's
    winner or loser is positioned at that earlier match's own output
    midpoint — which is exactly how a real bracket's consolation matches
    (e.g. a 3rd-place game) end up sharing a physical position with the
    championship match they split from.

    Without real seed data, leaf ordering is simply match-number traversal
    order — it need not match Sleeper's own visual seeding, only produce a
    consistent, non-crossing tree for the matches actually present.

    Args:
        matches_by_number: Output of :func:`bracket_matches_by_number`.

    Returns:
        ``match`` number mapped to ``(y1, y2)``, the vertical positions of
        its two inputs.
    """
    leaf_counter = itertools.count()
    output_y: dict[int, float] = {}
    input_y: dict[int, tuple[float, float]] = {}

    def get_output_y(match_number: int) -> float:
        if match_number not in output_y:
            get_input_y(match_number)
        return output_y[match_number]

    def get_input_y(match_number: int) -> tuple[float, float]:
        if match_number in input_y:
            return input_y[match_number]
        match = matches_by_number[match_number]
        t1_ref = _load_from_ref(match.get("t1_from"))
        t2_ref = _load_from_ref(match.get("t2_from"))
        y1 = get_output_y(t1_ref[1]) if t1_ref else float(next(leaf_counter))
        y2 = get_output_y(t2_ref[1]) if t2_ref else float(next(leaf_counter))
        input_y[match_number] = (y1, y2)
        output_y[match_number] = (y1 + y2) / 2
        return input_y[match_number]

    for match_number in matches_by_number:
        get_input_y(match_number)

    return input_y


def _nudge_colliding_positions(
    matches_by_number: dict[int, dict[str, Any]],
    input_positions: dict[int, tuple[float, float]],
) -> dict[int, tuple[float, float]]:
    """Separate matches that land on the exact same round and y-positions.

    A championship and its 3rd-place game legitimately share the same
    :func:`compute_bracket_positions` output — both split from the same pair
    of earlier matches, which is correct for the *logical* tree. Drawn
    as-is, their lines and labels would sit exactly on top of each other.
    This only adjusts what gets drawn; it does not change
    ``compute_bracket_positions``'s own (tested) output.

    Args:
        matches_by_number: Output of :func:`bracket_matches_by_number`.
        input_positions: Output of :func:`compute_bracket_positions`.

    Returns:
        ``match`` number mapped to a display ``(y1, y2)``, nudged apart
        whenever two or more matches in the same round would otherwise
        overlap exactly. The nudge is sized to the colliding matches' own
        box height (``y2 - y1``) plus a margin, not a fixed constant — a
        fixed nudge smaller than the box height still leaves the boxes
        visually overlapping, which is exactly the bug this fixes.
    """
    groups: dict[tuple[int, float, float], list[int]] = {}
    for match_number, (y1, y2) in input_positions.items():
        round_ = matches_by_number[match_number].get("round")
        if round_ is None:
            continue
        groups.setdefault((round_, y1, y2), []).append(match_number)

    adjusted = dict(input_positions)
    for (_, y1, y2), numbers in groups.items():
        if len(numbers) < 2:
            continue
        nudge = (y2 - y1) * 1.15 or 0.4
        for i, match_number in enumerate(sorted(numbers)):
            offset = (i - (len(numbers) - 1) / 2) * nudge
            adjusted[match_number] = (y1 + offset, y2 + offset)
    return adjusted


def render_bracket_tree(
    matches: list[dict[str, Any]],
    names: dict[int, str],
    out_path: str | Path,
    *,
    title: str = "Playoff Bracket",
) -> Path:
    """Render one bracket (winners or losers) as a horizontal tree PNG.

    Args:
        matches: Rows for a single bracket, as described in
            :func:`bracket_matches_by_number`.
        names: ``roster_id`` mapped to display name (from
            ``sleeper_standings``). A roster with no entry falls back to
            ``"#<roster_id>"``.
        out_path: Destination PNG path.
        title: Figure title.

    Returns:
        The path written.

    Raises:
        RenderingUnavailableError: If ``matplotlib`` is not installed.
        ValueError: If ``matches`` has no rows with a ``match`` number.
    """
    try:
        import matplotlib

        # Force the non-interactive Agg backend before pyplot is imported —
        # see nuclearff.report.tables for why.
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dependency
        raise RenderingUnavailableError(
            "Rendering a bracket needs matplotlib, a core dependency: `uv sync`."
        ) from exc

    matches_by_number = bracket_matches_by_number(matches)
    if not matches_by_number:
        raise ValueError(
            "render_bracket_tree: no matches with a match number to render."
        )

    input_positions = compute_bracket_positions(matches_by_number)
    display_positions = _nudge_colliding_positions(matches_by_number, input_positions)
    rounds = [
        m["round"] for m in matches_by_number.values() if m.get("round") is not None
    ]
    max_round = max(rounds) if rounds else 1
    max_y = max(max(y1, y2) for y1, y2 in display_positions.values())
    min_y = min(min(y1, y2) for y1, y2 in display_positions.values())

    fig, ax = plt.subplots(
        figsize=(2.6 * max_round + 2, 0.6 * (max_y - min_y + 1) + 1.2)
    )

    for match_number, match in matches_by_number.items():
        round_ = match.get("round")
        if round_ is None:
            continue
        y1, y2 = display_positions[match_number]
        x0, x1 = round_ - 1, round_

        ax.plot([x0, x1], [y1, y1], color="#444444", linewidth=1.2)
        ax.plot([x0, x1], [y2, y2], color="#444444", linewidth=1.2)
        ax.plot([x1, x1], [y1, y2], color="#444444", linewidth=1.2)

        winner = match.get("winner")
        placement = match.get("placement")
        for roster_id, y in ((match.get("t1"), y1), (match.get("t2"), y2)):
            label = (
                names.get(roster_id, f"#{roster_id}")
                if roster_id is not None
                else "TBD"
            )
            if isinstance(placement, int):
                rank = placement if roster_id == winner else placement + 1
                label = f"{label} ({_ordinal(rank)})"
            ax.text(
                x0 + 0.03,
                y - 0.08,
                label,
                fontsize=9,
                va="bottom",
                weight="bold" if roster_id == winner else "normal",
            )

    for round_number in range(1, max_round + 1):
        ax.text(
            round_number - 0.5,
            min_y - 0.6,
            f"Round {round_number}",
            fontsize=9,
            ha="center",
            style="italic",
            color="#888888",
        )

    ax.set_xlim(-0.05, max_round + 1.6)
    ax.set_ylim(max_y + 0.6, min_y - 1.0)
    ax.axis("off")
    ax.set_title(title, loc="left", fontsize=15, weight="bold", pad=14)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info("Wrote %s (%d matches)", out_path, len(matches_by_number))
    return out_path


def render_playoff_brackets(
    matches: list[dict[str, Any]],
    names: dict[int, str],
    out_dir: str | Path,
    *,
    league_name: str = "",
) -> dict[str, Path]:
    """Render both the winners and losers bracket for one season.

    Args:
        matches: All ``sleeper_playoff_matches`` rows for one league/season
            (both brackets together — this splits them by the ``bracket``
            column).
        names: ``roster_id`` mapped to display name.
        out_dir: Destination directory; writes ``winners_bracket.png`` and,
            if there are any losers-bracket rows, ``losers_bracket.png``.
        league_name: Used in each figure's title.

    Returns:
        The bracket name (``"winners"``/``"losers"``) mapped to the PNG
        path written. A season with no losers bracket omits that key rather
        than raising — not every league runs one.
    """
    out_dir = Path(out_dir)
    by_bracket: dict[str, list[dict[str, Any]]] = {"winners": [], "losers": []}
    for row in matches:
        bracket = row.get("bracket")
        if bracket in by_bracket:
            by_bracket[bracket].append(row)

    written: dict[str, Path] = {}
    for bracket, rows in by_bracket.items():
        if not rows:
            continue
        title = f"{league_name} — {bracket.title()} Bracket".strip(" —")
        written[bracket] = render_bracket_tree(
            rows, names, out_dir / f"{bracket}_bracket.png", title=title
        )
    return written
