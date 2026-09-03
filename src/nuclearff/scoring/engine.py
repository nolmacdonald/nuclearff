"""League-agnostic fantasy scoring: apply Sleeper scoring rules to nflverse stats.

This is the vocabulary bridge the rest of nuclearff's valuation pipeline sits
on top of. A :class:`~nuclearff.config.league.ScoringSettings` (built from a
Sleeper league's ``scoring_settings`` payload — see
:mod:`nuclearff.config.league`) names its coefficients with **Sleeper's**
scoring-key vocabulary (``rec``, ``rec_yd``, ``rec_td``, ``fum_lost``, ...). A
stat line from :mod:`nuclearff.nflverse.stats` (``load_weekly_receiving``,
``load_seasonal_receiving``) names its columns with **nflverse's** vocabulary
(``receptions``, ``receiving_yards``, ``receiving_tds``,
``receiving_fumbles_lost``, ...). Neither side shares a name with the other.
:class:`ScoringEngine` exists to translate between them correctly — not to
hardcode any one league's math (full PPR, half PPR, 6pt-passing-TD, etc.) —
so nuclearff can score *any* league's rules, not just the one it happens to
be built around.

Verified live column names
---------------------------
Every column referenced by the mapping tables below was confirmed to exist,
under this exact spelling, in ``nflreadpy.load_player_stats(seasons=[2024],
summary_level="week")`` (nflreadpy 0.1.5 — the same version and loader
:func:`nuclearff.nflverse.stats.load_weekly_receiving` wraps), by direct
column-membership inspection during this module's development:
``receptions``, ``receiving_yards``, ``receiving_tds``,
``receiving_first_downs``, ``receiving_2pt_conversions``, ``rushing_yards``,
``rushing_tds``, ``rushing_first_downs``, ``rushing_2pt_conversions``,
``passing_yards``, ``passing_tds``, ``passing_interceptions``,
``passing_2pt_conversions``, ``completions``, ``attempts`` (passing
attempts — a *different* column from ``carries``, rushing attempts),
``carries``, ``receiving_fumbles_lost``, ``rushing_fumbles_lost``,
``sack_fumbles_lost``, ``receiving_fumbles``, ``rushing_fumbles``, and
``sack_fumbles``. Do not assume these hold for an nflreadpy version far from
0.1.5 without re-checking — see the "nflreadpy" gotcha entries in
``brain/gotchas.md`` for two other cases where this project's assumptions
about nflreadpy's schema were wrong on first guess.

Composite Sleeper keys
----------------------
Two Sleeper keys are not a 1:1 column match and are summed from multiple
nflverse columns instead:

- ``fum_lost`` (the key that actually matters for scoring in essentially
  every league) = ``receiving_fumbles_lost`` + ``rushing_fumbles_lost`` +
  ``sack_fumbles_lost``.
- ``fum`` (total fumbles, lost or not — a nice-to-have, few leagues score
  it) = ``receiving_fumbles`` + ``rushing_fumbles`` + ``sack_fumbles``.

Threshold/bonus semantics
--------------------------
The ``bonus_*`` keys (``bonus_rec_yd_100``, ``bonus_pass_yd_300``, etc.) are
flat bonuses that trigger once a stat crosses a threshold **within one row**
of the stat frame passed in. That is the correct semantic for weekly data
(:func:`nuclearff.nflverse.stats.load_weekly_receiving`): a bonus fires once
per qualifying game. Passed a *seasonal* aggregate row
(:func:`nuclearff.nflverse.stats.load_seasonal_receiving`) instead, the same
logic fires the bonus once against the season total — a cruder semantic
("did this player's whole season cross 1,000 yards") than "how many games
did they cross 100 yards", and it will silently under-count a player who had
several 100-yard games. This engine does not attempt to reconstruct
per-game bonus counts from a seasonal row (that needs weekly data, which the
caller already has available via :func:`load_weekly_receiving` if the
distinction matters) — callers scoring season-long value with per-game
bonuses in the league's rules should score weekly rows and sum, not score
the seasonal row directly.

Position-conditional reception bonuses (``bonus_rec_wr``, ``bonus_rec_te``,
``bonus_rec_rb``) only apply to receptions by a player at that position, so
they need a ``position`` value to key off. :meth:`ScoringEngine.score_stat_line`
reads it from a ``"position"`` key in the stat-line dict (nflverse's own
column name, so a row pulled straight from a stat frame already has it);
:meth:`ScoringEngine.score_frame` reads it from a ``"position"`` column when
the frame has one. Either entry point silently contributes zero for these
bonuses when no position information is available, rather than guessing —
so a WR bonus can never leak onto an RB's receptions or vice versa.

Unmapped scoring keys
----------------------
A real league's ``scoring_settings`` routinely includes keys this WR-focused
tool has no stat source for at all: per-catch yardage-bucket keys
(``rec_20_29``, ``rec_40p``, ...) that would need play-by-play data this
project does not load, and kicker/defense/IDP keys (``fgm``, ``def_td``,
``tkl_solo``, ...) with no matching loader in this project whatsoever. A
league using those is a completely normal, legitimate case — not an error —
so this engine never raises over them. It does, however, refuse to be quiet
about them: :meth:`ScoringEngine.unscored_keys` lists every nonzero
``scoring.values`` key with no mapping, and the constructor logs a warning
naming them (with their coefficients) so a league that actually depends on
one of these keys is never silently under-scored without a trace in the
logs.
"""

from __future__ import annotations

import logging
from typing import Any

import polars as pl

from nuclearff.config.league import ScoringSettings

logger = logging.getLogger(__name__)

_LINEAR_MAP: dict[str, str] = {
    "rec": "receptions",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
    "rec_fd": "receiving_first_downs",
    "rec_2pt": "receiving_2pt_conversions",
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "rush_fd": "rushing_first_downs",
    "rush_2pt": "rushing_2pt_conversions",
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "pass_int": "passing_interceptions",
    "pass_2pt": "passing_2pt_conversions",
    "pass_cmp": "completions",
    "pass_att": "attempts",
}
"""Direct one-to-one Sleeper scoring key -> nflverse stat column pairs."""

_COMPOSITE_MAP: dict[str, tuple[str, ...]] = {
    "fum_lost": ("receiving_fumbles_lost", "rushing_fumbles_lost", "sack_fumbles_lost"),
    "fum": ("receiving_fumbles", "rushing_fumbles", "sack_fumbles"),
}
"""Sleeper scoring keys that sum several nflverse columns, not just one."""

_SINGLE_COLUMN_THRESHOLDS: tuple[tuple[str, str, float], ...] = (
    ("bonus_rec_yd_100", "receiving_yards", 100.0),
    ("bonus_rec_yd_200", "receiving_yards", 200.0),
    ("bonus_rush_yd_100", "rushing_yards", 100.0),
    ("bonus_rush_yd_200", "rushing_yards", 200.0),
    ("bonus_pass_yd_300", "passing_yards", 300.0),
    ("bonus_pass_yd_400", "passing_yards", 400.0),
    ("bonus_pass_cmp_25", "completions", 25.0),
    ("bonus_rush_att_20", "carries", 20.0),
)
"""(Sleeper key, nflverse column, threshold) flat bonuses keyed off one stat."""

_COMBINED_COLUMN_THRESHOLDS: tuple[tuple[str, tuple[str, str], float], ...] = (
    ("bonus_rush_rec_yd_100", ("rushing_yards", "receiving_yards"), 100.0),
    ("bonus_rush_rec_yd_200", ("rushing_yards", "receiving_yards"), 200.0),
)
"""(Sleeper key, nflverse columns, threshold) bonuses keyed off a summed stat."""

_POSITION_RECEPTION_BONUS: dict[str, str] = {
    "bonus_rec_wr": "WR",
    "bonus_rec_te": "TE",
    "bonus_rec_rb": "RB",
}
"""Sleeper scoring key -> the single position its per-reception bonus applies to."""

_HANDLED_KEYS: frozenset[str] = frozenset(
    set(_LINEAR_MAP)
    | set(_COMPOSITE_MAP)
    | {key for key, _, _ in _SINGLE_COLUMN_THRESHOLDS}
    | {key for key, _, _ in _COMBINED_COLUMN_THRESHOLDS}
    | set(_POSITION_RECEPTION_BONUS)
)
"""Every Sleeper scoring key this engine knows how to apply, of any kind."""


class ScoringEngine:
    """Applies one league's :class:`ScoringSettings` to nflverse stat lines.

    Args:
        scoring: The league's scoring rules, as built by
            :class:`nuclearff.config.league.LeagueConfig` (typically
            ``league_config.scoring``).

    """

    def __init__(self, scoring: ScoringSettings) -> None:
        self._scoring = scoring

        unscored = self.unscored_keys()
        if unscored:
            logger.warning(
                "ScoringEngine has no mapping for %d nonzero scoring "
                "key(s); these will not be counted toward fantasy points: %s",
                len(unscored),
                ", ".join(f"{key}={scoring.get(key)}" for key in unscored),
            )

    def score_stat_line(self, line: dict[str, Any]) -> float:
        """Compute fantasy points for one stat line under this league's rules.

        Pure Python, and intentionally the reference/hand-verifiable path —
        see :meth:`score_frame` for the vectorized equivalent used on real
        multi-row data.

        Args:
            line: A single stat line keyed by **nflverse** column names
                (e.g. ``{"receptions": 8, "receiving_yards": 100,
                "receiving_tds": 1}``). A stat this player did not record is
                expected to be an absent key, not a key holding ``None`` or
                ``0`` explicitly — either works, since a missing key is
                treated as zero. Include a ``"position"`` key (e.g.
                ``"WR"``) to have position-conditional reception bonuses
                (``bonus_rec_wr``/``bonus_rec_te``/``bonus_rec_rb``) apply;
                without it, those bonuses contribute zero.

        Returns:
            Total fantasy points for this stat line.
        """
        total = 0.0

        for sleeper_key, column in _LINEAR_MAP.items():
            total += self._scoring.get(sleeper_key) * line.get(column, 0)

        for sleeper_key, columns in _COMPOSITE_MAP.items():
            total += self._scoring.get(sleeper_key) * sum(
                line.get(column, 0) for column in columns
            )

        for sleeper_key, column, threshold in _SINGLE_COLUMN_THRESHOLDS:
            if line.get(column, 0) >= threshold:
                total += self._scoring.get(sleeper_key)

        for sleeper_key, columns, threshold in _COMBINED_COLUMN_THRESHOLDS:
            if sum(line.get(column, 0) for column in columns) >= threshold:
                total += self._scoring.get(sleeper_key)

        position = line.get("position")
        for sleeper_key, bonus_position in _POSITION_RECEPTION_BONUS.items():
            if position == bonus_position:
                total += self._scoring.get(sleeper_key) * line.get("receptions", 0)

        return total

    def score_frame(self, df: pl.DataFrame) -> pl.DataFrame:
        """Vectorized scoring of a stat DataFrame under this league's rules.

        Args:
            df: A stat DataFrame using nflverse column names, as returned by
                :func:`nuclearff.nflverse.stats.load_weekly_receiving` or
                :func:`nuclearff.nflverse.stats.load_seasonal_receiving` (see
                the module docstring for why a seasonal frame gets a cruder
                threshold-bonus semantic than a weekly one). A ``"position"``
                column, when present, drives the position-conditional
                reception bonuses; without one, those bonuses contribute
                zero for every row.

        Returns:
            ``df`` with a new ``"fantasy_points"`` column added. A stat
            column entirely absent from ``df`` (e.g. no rushing columns at
            all) contributes zero rather than raising — real nflverse frames
            already carry every column this engine looks for, but a
            synthetic or narrowed frame need not.
        """
        present = set(df.columns)

        def stat(column: str) -> pl.Expr:
            if column not in present:
                return pl.lit(0.0)
            return pl.col(column).fill_null(0)

        terms: list[pl.Expr] = []

        for sleeper_key, column in _LINEAR_MAP.items():
            terms.append(self._scoring.get(sleeper_key) * stat(column))

        for sleeper_key, columns in _COMPOSITE_MAP.items():
            summed = stat(columns[0])
            for column in columns[1:]:
                summed = summed + stat(column)
            terms.append(self._scoring.get(sleeper_key) * summed)

        for sleeper_key, column, threshold in _SINGLE_COLUMN_THRESHOLDS:
            coeff = self._scoring.get(sleeper_key)
            terms.append(
                pl.when(stat(column) >= threshold)
                .then(pl.lit(coeff))
                .otherwise(pl.lit(0.0))
            )

        for sleeper_key, columns, threshold in _COMBINED_COLUMN_THRESHOLDS:
            coeff = self._scoring.get(sleeper_key)
            summed = stat(columns[0]) + stat(columns[1])
            terms.append(
                pl.when(summed >= threshold).then(pl.lit(coeff)).otherwise(pl.lit(0.0))
            )

        if "position" in present:
            for sleeper_key, bonus_position in _POSITION_RECEPTION_BONUS.items():
                coeff = self._scoring.get(sleeper_key)
                terms.append(
                    pl.when(pl.col("position") == bonus_position)
                    .then(stat("receptions") * coeff)
                    .otherwise(pl.lit(0.0))
                )

        return df.with_columns(pl.sum_horizontal(*terms).alias("fantasy_points"))

    def unscored_keys(self) -> list[str]:
        """List nonzero scoring keys this engine has no mapping for at all.

        Independent of any specific stat line — inspects
        ``scoring.values`` directly, so it can be checked at league-setup
        time (this is also what the constructor logs a warning from).

        Returns:
            Sorted Sleeper scoring keys with a nonzero coefficient and no
            entry in this engine's linear, composite, threshold, or
            position-bonus mappings (e.g. kicker keys like ``fgm``, defense
            keys like ``def_td``, or per-catch yardage-bucket keys like
            ``rec_20_29``, for a league that scores any of those).
        """
        return sorted(
            key
            for key, value in self._scoring.values.items()
            if value != 0.0 and key not in _HANDLED_KEYS
        )
