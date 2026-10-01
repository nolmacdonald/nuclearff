"""Injury-risk caution for a draft candidate (issue #103).

:func:`injury_risk` summarizes how often a player was listed ``Out`` on the
weekly injury report over recent seasons and, when that happened often enough,
returns a short caution to show beside a recommendation.

Informational only. Nothing here adjusts VORP, VONA, or tier math: turning
"weeks Out" into a point deduction is a separate modeling decision.

The count is a *floor* on games missed. Players on injured reserve drop off the
weekly report, so a long-term absence shows up as only a few ``Out`` weeks (see
:mod:`nuclearff.nflverse.injuries`). A clean result therefore means "not
repeatedly listed Out", not "durable".

``player_id`` is a ``gsis_id``, the key ``load_injuries`` uses. A Sleeper player
ID is converted with ``sleeper_player_id_map`` (:mod:`nuclearff.ids.crosswalk`).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

HISTORY_SEASONS = 3
"""Most recent seasons in the history considered by default."""

SIGNIFICANT_OUT_WEEKS = 3
"""Regular-season weeks listed ``Out`` for a season to count as significant."""

CAUTION_SEASONS = 2
"""Significant seasons, within the window, needed to raise a caution."""


@dataclass(frozen=True, slots=True)
class InjuryRisk:
    """One player's recent injury-report summary.

    Attributes:
        player_id: The ``gsis_id`` summarized.
        out_weeks: Distinct regular-season weeks listed ``Out``, per season in
            the window, newest season first. A season with none maps to ``0``.
        caution: A one-line caution, or ``None`` when the history is clean.
    """

    player_id: str
    out_weeks: dict[int, int]
    caution: str | None


def injury_risk(
    player_id: str,
    injury_history: pl.DataFrame,
    *,
    seasons: int = HISTORY_SEASONS,
    significant_out_weeks: int = SIGNIFICANT_OUT_WEEKS,
    caution_seasons: int = CAUTION_SEASONS,
) -> InjuryRisk:
    """Summarize a player's recent ``Out`` weeks into an informational caution.

    Args:
        player_id: The player's ``gsis_id``.
        injury_history: Rows from
            :func:`nuclearff.nflverse.injuries.load_injury_history` -- needs
            ``season``, ``week``, ``gsis_id`` and ``report_status``. The window
            is the ``seasons`` most recent seasons present in this frame, so a
            season with no rows for the player counts as zero ``Out`` weeks.
        seasons: Window length in seasons.
        significant_out_weeks: Weeks ``Out`` that make a season significant.
        caution_seasons: Significant seasons in the window that raise a
            caution.

    Returns:
        An :class:`InjuryRisk`. ``caution`` is set only when at least
        ``caution_seasons`` seasons in the window each have
        ``significant_out_weeks`` or more distinct ``Out`` weeks.

    Raises:
        ValueError: If ``seasons``, ``significant_out_weeks`` or
            ``caution_seasons`` is below 1.
    """
    if seasons < 1 or significant_out_weeks < 1 or caution_seasons < 1:
        raise ValueError(
            "injury_risk: seasons, significant_out_weeks and caution_seasons "
            "must each be at least 1"
        )

    window = sorted(injury_history["season"].unique().to_list(), reverse=True)[:seasons]
    out = (
        injury_history.filter(
            (pl.col("gsis_id") == player_id) & (pl.col("report_status") == "Out")
        )
        .select("season", "week")
        .unique()
        .group_by("season")
        .len()
    )
    counts = dict(zip(out["season"].to_list(), out["len"].to_list(), strict=True))
    out_weeks = {season: counts.get(season, 0) for season in window}

    significant = {s: w for s, w in out_weeks.items() if w >= significant_out_weeks}
    caution = None
    if len(significant) >= caution_seasons:
        detail = ", ".join(
            f"{weeks} {'week' if weeks == 1 else 'weeks'} in {season}"
            for season, weeks in significant.items()
        )
        caution = f"Listed Out on the injury report for {detail}"
    return InjuryRisk(player_id=player_id, out_weeks=out_weeks, caution=caution)
