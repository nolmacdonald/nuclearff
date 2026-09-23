"""Typed league scoring and roster configuration derived from Sleeper payloads.

A league object fetched from Sleeper (see
:func:`nuclearff.sleeper.snapshot.fetch_league_snapshot`) carries two flat,
loosely typed dictionaries that drive every downstream valuation decision:
``scoring_settings`` and ``roster_positions``. Both vary in shape across real
leagues and across sports — Sleeper adds and removes scoring keys over time,
and a league can mix offense, kicker, defense, and IDP roster slots in one
flat list. This module keeps both generic (a dict wrapped in a small model)
rather than naming a field per possible key, the same choice
:class:`nuclearff.sleeper.models.LeagueSnapshot` makes for the raw payloads it
stores, and layers convenience accessors on top for the keys this WR-focused
tool actually reads.

``LeagueConfig`` is the typed, stable output of that conversion: what the rest
of nuclearff uses for scoring stat lines and for computing the value-based-
drafting replacement rank that anchors valuation across QB/RB/WR/TE.

Replacement-rank math originally shipped WR-only (the tool's Milestone A/B
scope per ``ff_revised.md``) and was generalized to every skill position for
the auction/keeper valuation work — see ``decisions.md`` in the brain for the
why. The generalization is deliberately additive: every default rate below
reduces to the exact original WR formula when ``position="WR"``, so the
league's calibrated WR replacement ranks (VOLS 35, VORP 53 for the real
committed fixture) are unchanged.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from nuclearff.exceptions import ConfigError

logger = logging.getLogger(__name__)

DEFAULT_FLEX_RATES: dict[str, float] = {"RB": 0.35, "WR": 0.50, "TE": 0.15}
"""Assumed share of a FLEX (WR/RB/TE) slot filled by each position, per week.

Starting heuristics, not derived from real data (the same posture as
``bench_wr_fraction`` below) — sums to 1.0 across RB/WR/TE, the three
FLEX-eligible positions. WR's rate (0.50) is the tool's original, unchanged
default. Revisit once real roster/lineup data is available to calibrate the
RB/TE split. Public (not module-private) since
:func:`nuclearff.draft.needs.roster_needs` (GitHub Issue 93) reuses these
same rates for a single roster's remaining starter need, rather than
re-deriving a second flex-allocation heuristic.
"""

DEFAULT_SUPERFLEX_RATES: dict[str, float] = {
    "QB": 0.70,
    "RB": 0.10,
    "WR": 0.15,
    "TE": 0.05,
}
"""Assumed share of a SUPER_FLEX (QB/RB/WR/TE) slot filled by each position.

Starting heuristic, not derived from real data. QB dominates SUPER_FLEX
lineups in practice, hence the skew. The real committed league fixture has
zero SUPER_FLEX slots, so this has no effect on that league's calibrated
numbers regardless of the rate chosen.
"""

_DEFAULT_BENCH_FRACTIONS: dict[str, float] = {
    "QB": 0.10,
    "RB": 0.30,
    "WR": 0.30,
    "TE": 0.20,
}
"""Assumed fraction of total league bench slots stashed at each position, for
the ``"vorp"`` replacement baseline.

Starting heuristic, not derived from real data. WR's rate (0.30) is the
tool's original, unchanged default. QB is deliberately low (single-QB
leagues rarely stash bench QBs); revisit once real roster data is available.
"""


class ScoringSettings(BaseModel):
    """A league's scoring rules, wrapped generically rather than field-by-field.

    Sleeper's ``scoring_settings`` payload is one flat dict mixing offense,
    kicker, defense, and IDP keys, and the exact key set is **not** fixed
    across leagues or sports (a new bonus key can appear, an IDP league adds
    tackle/sack keys a skill-only league never sends). Naming one field per
    key, as an earlier draft of this module's design sketched, would make
    every unrecognized key either a validation failure or a silently dropped
    value. Storing the payload as a generic ``values`` mapping instead means
    an unfamiliar key is simply available under :meth:`get` rather than
    breaking construction.

    This is why, deliberately, ``model_config`` here does **not** set
    ``extra="forbid"`` the way the rest of this project's config models do
    (see ``nuclearff.config.models._Base``): the whole point of this model is
    to tolerate a payload broader than anything named on it. Do not "fix"
    that back to ``extra="forbid"`` — there is no fixed set of extra keys to
    forbid, because ``values`` is where they are meant to land.

    Args:
        values: Every numeric scoring key Sleeper sent, keyed by Sleeper's own
            key names (e.g. ``"rec"``, ``"pass_td"``).

    """

    model_config = ConfigDict(frozen=True)

    values: dict[str, float] = {}

    @classmethod
    def from_sleeper(cls, payload: dict[str, Any] | None) -> ScoringSettings:
        """Build scoring settings from a raw ``scoring_settings`` payload.

        Coercion is defensive: a non-mapping payload yields empty settings,
        and any entry that cannot be coerced to ``float`` (a non-numeric
        value, or a key that no player object has known to matter) is
        dropped rather than raising, because a single unexpected shape
        should not prevent the rest of the league's scoring from loading.

        Args:
            payload: The raw ``scoring_settings`` value from a Sleeper league
                object, or ``None``.

        Returns:
            Scoring settings with every coercible numeric key retained.
        """
        if not isinstance(payload, dict):
            return cls(values={})

        values: dict[str, float] = {}
        for key, raw_value in payload.items():
            if not isinstance(key, str):
                continue
            try:
                values[key] = float(raw_value)
            except (TypeError, ValueError):
                logger.debug(
                    "Dropping non-numeric scoring_settings entry %r=%r", key, raw_value
                )
                continue
        return cls(values=values)

    def get(self, key: str, default: float = 0.0) -> float:
        """Look up one scoring key, defaulting when Sleeper omitted it.

        Args:
            key: Sleeper scoring key, e.g. ``"bonus_rec_te"``.
            default: Value to return when the key is absent. Sleeper omits a
                key entirely when it scores zero, so ``0.0`` is the correct
                default for nearly every caller.

        Returns:
            The scoring value for ``key``.
        """
        return self.values.get(key, default)

    @property
    def rec(self) -> float:
        """Points per reception."""
        return self.get("rec")

    @property
    def rec_yd(self) -> float:
        """Points per receiving yard."""
        return self.get("rec_yd")

    @property
    def rec_td(self) -> float:
        """Points per receiving touchdown."""
        return self.get("rec_td")

    @property
    def bonus_rec_wr(self) -> float:
        """Bonus points per reception, wide receivers only."""
        return self.get("bonus_rec_wr")

    @property
    def bonus_rec_te(self) -> float:
        """Bonus points per reception, tight ends only (TE premium)."""
        return self.get("bonus_rec_te")

    @property
    def bonus_rec_yd_100(self) -> float:
        """Bonus points for a 100+ receiving yard game."""
        return self.get("bonus_rec_yd_100")

    @property
    def bonus_rec_yd_200(self) -> float:
        """Bonus points for a 200+ receiving yard game."""
        return self.get("bonus_rec_yd_200")

    @property
    def rec_fd(self) -> float:
        """Points per receiving first down."""
        return self.get("rec_fd")

    @property
    def rush_yd(self) -> float:
        """Points per rushing yard."""
        return self.get("rush_yd")

    @property
    def rush_td(self) -> float:
        """Points per rushing touchdown."""
        return self.get("rush_td")

    @property
    def pass_yd(self) -> float:
        """Points per passing yard."""
        return self.get("pass_yd")

    @property
    def pass_td(self) -> float:
        """Points per passing touchdown."""
        return self.get("pass_td")

    @property
    def pass_int(self) -> float:
        """Points per interception thrown (typically negative)."""
        return self.get("pass_int")

    @property
    def fum_lost(self) -> float:
        """Points per fumble lost (typically negative)."""
        return self.get("fum_lost")

    @property
    def is_full_ppr(self) -> bool:
        """Whether the league awards exactly one point per reception."""
        return self.rec == 1.0

    @property
    def has_te_premium(self) -> bool:
        """Whether tight ends earn a reception bonus beyond the base PPR rate."""
        return self.bonus_rec_te > 0.0


class RosterSlots(BaseModel):
    """A league's starting-lineup and bench composition, kept as slot counts.

    Sleeper's ``roster_positions`` payload is a flat list of slot-code
    strings (one entry per roster spot, repeated for multiple spots of the
    same type), and real leagues use codes well beyond the common skill
    positions: kicker and defense (``K``, ``DEF``), IDP slots (``DL``,
    ``LB``, ``DB``, ``IDP_FLEX``), a taxi squad (``TAXI``), and a superflex
    slot (``SUPER_FLEX``) all appear in real leagues (see the anomaly
    detection in :mod:`nuclearff.sleeper.snapshot`, which already inspects
    several of these). As with :class:`ScoringSettings`, counting into a
    generic ``counts`` mapping rather than naming a field per code means an
    unrecognized slot is simply absent from the named shortcuts instead of
    breaking construction.

    Args:
        counts: Number of roster spots per slot code, e.g. ``{"WR": 2,
            "FLEX": 3, "BN": 6}``.

    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    counts: dict[str, int] = {}

    @classmethod
    def from_sleeper(cls, roster_positions: list[Any] | None) -> RosterSlots:
        """Count slot codes from a raw ``roster_positions`` payload.

        Args:
            roster_positions: The raw ``roster_positions`` list from a
                Sleeper league object, or ``None``.

        Returns:
            Slot counts keyed by code. A non-list payload, or a non-string
            entry within it, is skipped rather than raising.
        """
        counts: dict[str, int] = {}
        if not isinstance(roster_positions, list):
            return cls(counts=counts)

        for slot in roster_positions:
            if isinstance(slot, str):
                counts[slot] = counts.get(slot, 0) + 1
        return cls(counts=counts)

    def count(self, code: str) -> int:
        """Look up the number of spots for one slot code.

        Args:
            code: A Sleeper roster slot code, e.g. ``"SUPER_FLEX"``.

        Returns:
            The number of spots at that code, or ``0`` if the league does not
            use it. An unrecognized code returns ``0`` rather than raising.
        """
        return self.counts.get(code, 0)

    @property
    def qb(self) -> int:
        """Dedicated quarterback slots."""
        return self.count("QB")

    @property
    def rb(self) -> int:
        """Dedicated running back slots."""
        return self.count("RB")

    @property
    def wr(self) -> int:
        """Dedicated wide receiver slots."""
        return self.count("WR")

    @property
    def te(self) -> int:
        """Dedicated tight end slots."""
        return self.count("TE")

    @property
    def flex(self) -> int:
        """WR/RB/TE flex slots."""
        return self.count("FLEX")

    @property
    def superflex(self) -> int:
        """QB/WR/RB/TE superflex slots.

        Sleeper's slot code for superflex is confirmed as ``"SUPER_FLEX"``
        (verified against DynastyProcess/ffscrapr's ``sleeper_starterpositions``
        parser, which reads this code from real Sleeper league payloads —
        see ``integration_notes`` for citation details). ``count()`` already
        degrades an unrecognized code to ``0`` rather than raising, so this
        property is safe even if a future Sleeper change renames it.
        """
        return self.count("SUPER_FLEX")

    @property
    def bench(self) -> int:
        """Bench slots."""
        return self.count("BN")

    @property
    def ir(self) -> int:
        """Injured reserve slots."""
        return self.count("IR")


class LeagueConfig(BaseModel):
    """Typed league settings derived from a raw Sleeper league object.

    Args:
        league_id: Sleeper league identifier.
        name: League display name.
        season: Season this configuration applies to.
        num_teams: Number of rostered teams in the league.
        scoring: The league's scoring rules.
        roster: The league's roster-slot composition.
        best_ball: Whether lineups are set automatically (no weekly starts).
        league_type: Sleeper's league type code (``0`` redraft, ``1`` keeper,
            ``2`` dynasty).
        draft_id: The league's current draft identifier, when one exists.
        previous_league_id: The prior season's league identifier, for
            leagues that carry one over year to year.
        playoff_week_start: The first week of the fantasy playoffs, from
            Sleeper's own ``settings.playoff_week_start``. ``None`` if the
            league doesn't expose one (e.g. a "Chopped" league, which has
            no bracket at all — see
            :func:`nuclearff.sleeper.standings.is_chopped_league`) — never
            guessed at a default like week 15, since real leagues vary this
            setting (see issues #106/#110/#113/#137, which all
            independently hit this same gap).
        playoff_teams: Number of teams in the playoff bracket, from
            Sleeper's own ``settings.playoff_teams``. Paired with
            ``playoff_week_start`` by :meth:`playoff_weeks` to derive the
            real playoff week range. ``None`` under the same conditions as
            ``playoff_week_start``.

    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    league_id: str
    name: str
    season: int
    num_teams: int
    scoring: ScoringSettings
    roster: RosterSlots
    best_ball: bool
    league_type: int
    draft_id: str | None
    previous_league_id: str | None
    playoff_week_start: int | None = None
    playoff_teams: int | None = None

    def playoff_weeks(self) -> list[int] | None:
        """Return the league's real fantasy-playoff week numbers.

        Derived from ``playoff_week_start`` and the number of
        single-elimination rounds implied by ``playoff_teams``
        (``ceil(log2(playoff_teams))``) — e.g. a 6-team bracket still spans
        3 real weeks even though the top 2 seeds get a first-round bye,
        because the bye removes a *matchup*, not a *round*. Returns
        ``None`` if either input isn't resolvable, rather than guessing a
        fixed range like "15-17" (see issues #106/#110/#113/#137).

        Known limitation: does not account for Sleeper's
        ``settings.playoff_round_type`` (a league whose championship round
        spans two real weeks would need an extra week here). Not confirmed
        against real Sleeper behavior for every ``playoff_round_type``
        value, so left as a stated gap rather than a guessed adjustment.

        Returns:
            Consecutive week numbers starting at ``playoff_week_start``, or
            ``None``.
        """
        if self.playoff_week_start is None or not self.playoff_teams:
            return None
        if self.playoff_teams < 2:
            return None
        rounds = math.ceil(math.log2(self.playoff_teams))
        return list(range(self.playoff_week_start, self.playoff_week_start + rounds))

    def starter_demand(
        self,
        position: str,
        flex_rate: float | None = None,
        superflex_rate: float | None = None,
    ) -> float:
        """Return the expected league-wide weekly starting-slot count at ``position``.

        Locked slots at ``position`` plus the assumed share of FLEX and
        SUPER_FLEX slots filled by ``position``, summed across every team in
        the league. Generalizes :meth:`wr_starter_demand` to any position:
        ``starter_demand("WR", flex_rate=r)`` reduces to the exact same
        formula ``wr_starter_demand(r)`` uses.

        Args:
            position: The position to compute starter demand for, e.g.
                ``"QB"``, ``"RB"``, ``"WR"``, or ``"TE"``. An unrecognized
                position falls back to ``0.0`` FLEX/SUPER_FLEX share (via
                :data:`DEFAULT_FLEX_RATES`/:data:`DEFAULT_SUPERFLEX_RATES`)
                and whatever locked slot count :meth:`RosterSlots.count`
                returns (``0`` if the league has none), the same graceful
                degradation :meth:`RosterSlots.count` itself uses.
            flex_rate: Assumed fraction of FLEX slots filled by ``position``
                in an average week. Defaults to
                :data:`DEFAULT_FLEX_RATES` for ``position``, or ``0.0`` for
                a position with no default (e.g. ``"QB"``, which is not
                FLEX-eligible in a standard WR/RB/TE flex).
            superflex_rate: Assumed fraction of SUPER_FLEX slots filled by
                ``position``. Defaults to :data:`DEFAULT_SUPERFLEX_RATES`
                for ``position``, or ``0.0``.

        Returns:
            The expected number of starting slots at ``position`` across the
            league.
        """
        if flex_rate is None:
            flex_rate = DEFAULT_FLEX_RATES.get(position, 0.0)
        if superflex_rate is None:
            superflex_rate = DEFAULT_SUPERFLEX_RATES.get(position, 0.0)

        return self.num_teams * (
            self.roster.count(position)
            + self.roster.flex * flex_rate
            + self.roster.superflex * superflex_rate
        )

    def wr_starter_demand(self, flex_wr_rate: float = 0.5) -> float:
        """Return the expected league-wide weekly WR starting-slot count.

        Locked WR slots plus the assumed WR share of FLEX slots, summed
        across every team in the league. A thin, unchanged-behavior WR
        shortcut over the generalized :meth:`starter_demand`.

        Args:
            flex_wr_rate: Assumed fraction of FLEX slots filled by a wide
                receiver in an average week.

        Returns:
            The expected number of WR starting slots across the league.
        """
        return self.starter_demand("WR", flex_rate=flex_wr_rate)

    def replacement_rank(
        self,
        position: str,
        baseline: str = "vols",
        flex_rate: float | None = None,
        superflex_rate: float | None = None,
        bench_fraction: float | None = None,
    ) -> int:
        """Return the positional replacement rank used for value-based drafting.

        Generalized across QB/RB/WR/TE (see the module docstring for the
        deviation from this tool's original WR-only scope). Every default
        rate below reduces to the exact original formula for ``"WR"``, so
        calling this for ``"WR"`` with no rate overrides reproduces the
        tool's original, calibrated numbers unchanged.

        For ``baseline="vols"`` (value over last starter), the replacement
        rank is simply :meth:`starter_demand`, rounded to the nearest
        integer rank.

        For ``baseline="vorp"`` (value over replacement — realistic waiver
        depth rather than the last nominal starter), the rank goes deeper
        than VOLS by adding a fraction of the league's total bench slots, on
        the theory that a meaningful share of bench spots at a position are
        speculative stashes. That fraction (``bench_fraction``, default
        :data:`_DEFAULT_BENCH_FRACTIONS` for ``position``) is a **starting
        heuristic, not derived from real data** — it has not been calibrated
        against actual draft or roster behavior. Revisit it once real
        Sleeper draft and roster data is available to check what fraction of
        bench spots are actually stashed at each position.

        Args:
            position: The position to compute a replacement rank for, e.g.
                ``"QB"``, ``"RB"``, ``"WR"``, or ``"TE"``. An unrecognized
                position degrades gracefully (see :meth:`starter_demand`)
                rather than raising.
            baseline: ``"vols"`` or ``"vorp"``.
            flex_rate: Assumed fraction of FLEX slots filled by ``position``
                in an average week. Passed through to
                :meth:`starter_demand`.
            superflex_rate: Assumed fraction of SUPER_FLEX slots filled by
                ``position``. Passed through to :meth:`starter_demand`.
            bench_fraction: Assumed fraction of total league bench slots
                stashed at ``position``, used only for ``baseline="vorp"``.
                Defaults to :data:`_DEFAULT_BENCH_FRACTIONS` for
                ``position``, or ``0.3`` for a position with no default.

        Returns:
            The replacement rank: players at or above this rank are
            starter-caliber under the chosen baseline.

        Raises:
            ValueError: If ``baseline`` is not ``"vols"`` or ``"vorp"``.
        """
        demand = self.starter_demand(position, flex_rate, superflex_rate)

        if baseline == "vols":
            return round(demand)
        if baseline == "vorp":
            if bench_fraction is None:
                bench_fraction = _DEFAULT_BENCH_FRACTIONS.get(position, 0.3)
            return round(demand + self.roster.bench * self.num_teams * bench_fraction)

        raise ValueError(f"Unknown replacement baseline {baseline!r}")


def league_config_from_sleeper(league_json: dict[str, Any]) -> LeagueConfig:
    """Build a :class:`LeagueConfig` from a raw Sleeper league object.

    Args:
        league_json: A raw league object, as captured by
            :func:`nuclearff.sleeper.snapshot.fetch_league_snapshot` (its
            ``.league`` attribute) or fetched directly via
            :meth:`nuclearff.sleeper.client.SleeperClient.get_league`.

    Returns:
        The typed league configuration.

    Raises:
        ConfigError: If a required field is missing or cannot be coerced to
            its expected type.
    """
    settings = league_json.get("settings")
    if not isinstance(settings, dict):
        settings = {}

    num_teams = league_json.get("total_rosters")
    if not isinstance(num_teams, int):
        num_teams = settings.get("num_teams")

    draft_id = league_json.get("draft_id")
    previous_league_id = league_json.get("previous_league_id")

    playoff_week_start = settings.get("playoff_week_start")
    if not isinstance(playoff_week_start, int) or playoff_week_start <= 0:
        # Sleeper omits this for at least "Chopped" leagues (no bracket at
        # all); coerced to `None` rather than a guessed default -- see the
        # field's own docstring.
        playoff_week_start = None

    playoff_teams = settings.get("playoff_teams")
    if not isinstance(playoff_teams, int) or playoff_teams <= 0:
        # Same "Chopped" gap and same posture as playoff_week_start above.
        playoff_teams = None

    try:
        return LeagueConfig(
            league_id=str(league_json["league_id"]),
            name=str(league_json.get("name") or "unknown"),
            season=int(league_json["season"]),
            num_teams=int(num_teams),  # ty: ignore[invalid-argument-type]
            scoring=ScoringSettings.from_sleeper(league_json.get("scoring_settings")),
            roster=RosterSlots.from_sleeper(league_json.get("roster_positions")),
            best_ball=bool(settings.get("best_ball")),
            league_type=int(settings.get("type") or 0),
            draft_id=str(draft_id) if draft_id else None,
            previous_league_id=(
                str(previous_league_id) if previous_league_id else None
            ),
            playoff_week_start=playoff_week_start,
            playoff_teams=playoff_teams,
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ConfigError(
            f"Could not build LeagueConfig from Sleeper payload: {exc}"
        ) from exc


def _canonical_dict(cfg: LeagueConfig) -> dict[str, Any]:
    """Return a deterministic, sorted-key dictionary for YAML serialization.

    Args:
        cfg: The league configuration to convert.

    Returns:
        A nested dictionary with keys sorted at every level, matching the
        deterministic-YAML convention in ``nuclearff.config.loader``.
    """

    def _sort(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): _sort(value[k]) for k in sorted(value, key=str)}
        return value

    return _sort(cfg.model_dump(mode="python"))


def load_league_config(path: str | Path) -> LeagueConfig:
    """Load a league configuration from a YAML file.

    Args:
        path: Path to the YAML file, typically under ``configs/leagues/``.

    Returns:
        The validated league configuration.

    Raises:
        ConfigError: If the file is missing, unreadable, not valid YAML, does
            not contain a mapping at the top level, or fails validation.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"League configuration file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(
            f"Could not read league configuration file {path}: {exc}"
        ) from exc

    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"League configuration file {path} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ConfigError(
            f"League configuration file {path} must contain a mapping at the "
            f"top level, got {type(payload).__name__}"
        )

    try:
        config = LeagueConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid league configuration in {path}:\n{exc}") from exc

    logger.debug("Loaded league configuration from %s", path)
    return config


def dump_league_config(cfg: LeagueConfig, path: str | Path) -> Path:
    """Write a league configuration to a YAML file, creating parent directories.

    Keys are sorted and flow style is disabled, matching
    ``nuclearff.config.loader.to_yaml``/``dump_config`` so a league config
    round-trips deterministically the same way the rest of nuclearff's
    configuration does.

    Args:
        cfg: The league configuration to write.
        path: Destination file.

    Returns:
        The path written.

    Raises:
        ConfigError: If the file cannot be written.
    """
    path = Path(path)
    text = yaml.safe_dump(
        _canonical_dict(cfg), sort_keys=True, default_flow_style=False
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Could not write league configuration to {path}: {exc}"
        ) from exc

    logger.debug("Wrote league configuration to %s", path)
    return path
