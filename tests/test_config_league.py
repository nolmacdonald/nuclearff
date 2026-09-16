"""Unit tests for nuclearff.config.league."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nuclearff.config import (
    LeagueConfig,
    RosterSlots,
    ScoringSettings,
    dump_league_config,
    league_config_from_sleeper,
    load_league_config,
)
from nuclearff.exceptions import ConfigError

# --- ScoringSettings ---------------------------------------------------


def test_scoring_settings_from_sleeper_coerces_to_float():
    """Every numeric scoring key is coerced to float, whatever type it arrived as."""
    scoring = ScoringSettings.from_sleeper({"rec": 1, "pass_td": 6.0, "pass_int": -2})

    assert scoring.get("rec") == 1.0
    assert isinstance(scoring.get("rec"), float)
    assert scoring.get("pass_int") == -2.0


def test_scoring_settings_from_sleeper_drops_non_numeric_entries():
    """A non-numeric value in scoring_settings is dropped, not fatal."""
    scoring = ScoringSettings.from_sleeper(
        {"rec": 1.0, "weird_key": "not-a-number", "another": None}
    )

    assert scoring.get("rec") == 1.0
    assert scoring.get("weird_key") == 0.0
    assert scoring.get("another") == 0.0
    assert "weird_key" not in scoring.values
    assert "another" not in scoring.values


def test_scoring_settings_from_sleeper_handles_non_dict_payload():
    """A malformed (non-dict) payload yields empty settings instead of crashing."""
    scoring = ScoringSettings.from_sleeper(None)

    assert scoring.values == {}
    assert scoring.get("rec") == 0.0

    scoring = ScoringSettings.from_sleeper([1, 2, 3])  # type: ignore[arg-type]
    assert scoring.values == {}


def test_scoring_settings_get_defaults_to_zero_for_missing_key():
    """A key Sleeper omitted (because it scores zero) defaults to 0.0."""
    scoring = ScoringSettings.from_sleeper({"rec": 1.0})

    assert scoring.get("bonus_rec_te") == 0.0
    assert scoring.get("nonexistent_key", default=-1.0) == -1.0


def test_scoring_settings_shortcut_properties_match_get():
    """Named shortcut properties read the same values as get()."""
    payload = {
        "rec": 1.0,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "bonus_rec_wr": 0.5,
        "bonus_rec_te": 0.5,
        "bonus_rec_yd_100": 3.0,
        "bonus_rec_yd_200": 6.0,
        "rec_fd": 0.25,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "fum_lost": -2.0,
    }
    scoring = ScoringSettings.from_sleeper(payload)

    for key, value in payload.items():
        assert getattr(scoring, key) == value


def test_is_full_ppr_true_only_at_exactly_one_point_per_reception():
    """is_full_ppr is strictly rec == 1.0, not merely truthy."""
    assert ScoringSettings.from_sleeper({"rec": 1.0}).is_full_ppr is True
    assert ScoringSettings.from_sleeper({"rec": 0.5}).is_full_ppr is False
    assert ScoringSettings.from_sleeper({}).is_full_ppr is False


def test_has_te_premium_true_only_when_bonus_is_positive():
    """has_te_premium is strictly bonus_rec_te > 0.0."""
    assert ScoringSettings.from_sleeper({"bonus_rec_te": 0.5}).has_te_premium is True
    assert ScoringSettings.from_sleeper({"bonus_rec_te": 0.0}).has_te_premium is False
    assert ScoringSettings.from_sleeper({}).has_te_premium is False


def test_scoring_settings_is_frozen():
    """Scoring settings are immutable once built."""
    scoring = ScoringSettings.from_sleeper({"rec": 1.0})

    with pytest.raises(ValidationError):
        scoring.values = {}


# --- RosterSlots ---------------------------------------------------------


def test_roster_slots_from_sleeper_counts_repeated_codes():
    """Each repeated slot code in roster_positions is tallied."""
    roster = RosterSlots.from_sleeper(
        ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "FLEX", "BN", "BN"]
    )

    assert roster.qb == 1
    assert roster.rb == 2
    assert roster.wr == 2
    assert roster.te == 1
    assert roster.flex == 3
    assert roster.bench == 2
    assert roster.ir == 0


def test_roster_slots_from_sleeper_handles_malformed_payload():
    """A non-list payload, or non-string entries, do not crash counting."""
    roster = RosterSlots.from_sleeper(None)
    assert roster.counts == {}

    roster = RosterSlots.from_sleeper(["QB", None, 7, "WR"])  # type: ignore[list-item]
    assert roster.qb == 1
    assert roster.wr == 1


def test_roster_slots_count_is_defensive_for_unknown_codes():
    """An unrecognized slot code returns 0 rather than raising."""
    roster = RosterSlots.from_sleeper(["QB", "DL", "LB", "DB", "IDP_FLEX", "TAXI"])

    assert roster.count("NOT_A_REAL_CODE") == 0
    assert roster.count("DL") == 1
    assert roster.count("IDP_FLEX") == 1
    assert roster.count("TAXI") == 1


def test_roster_slots_superflex_and_ir_shortcuts():
    """SUPER_FLEX and IR map through count() like every other shortcut."""
    roster = RosterSlots.from_sleeper(["QB", "SUPER_FLEX", "IR", "IR"])

    assert roster.superflex == 1
    assert roster.ir == 2


def test_roster_slots_is_frozen():
    """Roster slots are immutable once built."""
    roster = RosterSlots.from_sleeper(["QB"])

    with pytest.raises(ValidationError):
        roster.counts = {}


# --- LeagueConfig: derived value-based-drafting math ----------------------


def _league_config(**overrides: object) -> LeagueConfig:
    """Build a minimal LeagueConfig for the derived-math tests."""
    defaults: dict[str, object] = {
        "league_id": "1",
        "name": "Test League",
        "season": 2026,
        "num_teams": 10,
        "scoring": ScoringSettings.from_sleeper({"rec": 1.0}),
        "roster": RosterSlots.from_sleeper(
            ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "FLEX", "BN"] * 1
        ),
        "best_ball": False,
        "league_type": 0,
        "draft_id": "d1",
        "previous_league_id": None,
    }
    defaults.update(overrides)
    return LeagueConfig(**defaults)  # type: ignore[arg-type]


def test_wr_starter_demand_combines_locked_wr_and_flex_share():
    """Demand is num_teams * (wr + flex * flex_wr_rate)."""
    cfg = _league_config(
        roster=RosterSlots.from_sleeper(["WR", "WR", "FLEX", "FLEX", "FLEX"]),
        num_teams=10,
    )

    assert cfg.wr_starter_demand(flex_wr_rate=0.5) == 10 * (2 + 3 * 0.5)
    assert cfg.wr_starter_demand(flex_wr_rate=0.0) == 20
    assert cfg.wr_starter_demand(flex_wr_rate=1.0) == 50


def test_replacement_rank_vols_is_rounded_wr_starter_demand():
    """VOLS replacement rank is just the rounded starter demand."""
    cfg = _league_config(
        roster=RosterSlots.from_sleeper(["WR", "WR", "FLEX", "FLEX", "FLEX"]),
        num_teams=10,
    )

    assert cfg.replacement_rank("WR", "vols") == round(cfg.wr_starter_demand(0.5))
    assert cfg.replacement_rank("WR", "vols") == 35


def test_replacement_rank_vorp_adds_bench_wr_fraction():
    """VORP replacement rank goes deeper than VOLS by 30% of total bench slots."""
    cfg = _league_config(
        roster=RosterSlots.from_sleeper(
            ["WR", "WR", "FLEX", "FLEX", "FLEX", "BN", "BN", "BN", "BN", "BN", "BN"]
        ),
        num_teams=10,
    )

    vols = cfg.replacement_rank("WR", "vols")
    vorp = cfg.replacement_rank("WR", "vorp")

    assert vorp > vols
    assert vorp == round(cfg.wr_starter_demand(0.5) + 6 * 10 * 0.3)


def test_starter_demand_generalizes_wr_starter_demand():
    """starter_demand("WR", flex_rate=r) reproduces wr_starter_demand(r) exactly."""
    cfg = _league_config(
        roster=RosterSlots.from_sleeper(["WR", "WR", "FLEX", "FLEX", "FLEX"]),
        num_teams=10,
    )

    for rate in (0.0, 0.5, 1.0):
        assert cfg.starter_demand("WR", flex_rate=rate) == cfg.wr_starter_demand(rate)


def test_starter_demand_combines_locked_flex_and_superflex_shares():
    """Demand is num_teams * (locked + flex*flex_rate + superflex*superflex_rate)."""
    cfg = _league_config(
        roster=RosterSlots.from_sleeper(
            ["QB", "RB", "RB", "FLEX", "FLEX", "SUPER_FLEX"]
        ),
        num_teams=10,
    )

    assert cfg.starter_demand("RB", flex_rate=0.4, superflex_rate=0.1) == 10 * (
        2 + 2 * 0.4 + 1 * 0.1
    )
    assert cfg.starter_demand("QB", flex_rate=0.0, superflex_rate=0.6) == 10 * (
        1 + 1 * 0.6
    )


def test_starter_demand_unrecognized_position_degrades_to_zero():
    """A position code with no default rate and no locked slots returns 0."""
    cfg = _league_config()

    assert cfg.starter_demand("NOT_A_REAL_POSITION") == 0.0


def test_replacement_rank_now_supports_qb_rb_te_without_raising():
    """The original WR-only gate was deliberately removed for auction/keeper valuation.

    See decisions.md for why: value-based drafting across a whole roster
    needs a replacement rank at every rostered position, not just WR.
    """
    cfg = _league_config(
        roster=RosterSlots.from_sleeper(
            ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "FLEX", "BN"] * 2
        ),
        num_teams=10,
    )

    for position in ("QB", "RB", "WR", "TE"):
        rank = cfg.replacement_rank(position, "vols")
        assert rank > 0


def test_replacement_rank_wr_defaults_unchanged_by_generalization():
    """WR's calibrated VOLS/VORP numbers must be byte-identical after generalizing.

    This is the regression guard for the decision recorded in decisions.md:
    generalizing replacement_rank to QB/RB/TE must not silently change any
    already-calibrated WR number.
    """
    cfg = _league_config(
        roster=RosterSlots.from_sleeper(
            ["WR", "WR", "FLEX", "FLEX", "FLEX", "BN", "BN", "BN", "BN", "BN", "BN"]
        ),
        num_teams=10,
    )

    assert cfg.replacement_rank("WR", "vols") == 35
    assert cfg.replacement_rank("WR", "vorp") == 53


def test_replacement_rank_rejects_unknown_baseline():
    """An unrecognized baseline string raises rather than silently defaulting."""
    cfg = _league_config()

    with pytest.raises(ValueError, match="Unknown replacement baseline"):
        cfg.replacement_rank("WR", "not_a_real_baseline")


def test_league_config_is_frozen():
    """League configuration is immutable once built."""
    cfg = _league_config()

    with pytest.raises(ValidationError):
        cfg.num_teams = 12


def test_league_config_allows_null_draft_and_previous_league_id():
    """draft_id and previous_league_id are legitimately nullable in real leagues."""
    cfg = _league_config(draft_id=None, previous_league_id=None)

    assert cfg.draft_id is None
    assert cfg.previous_league_id is None


def test_league_config_playoff_week_start_defaults_to_none():
    """A LeagueConfig built without playoff_week_start defaults to None.

    Backward-compatible with every existing caller (and fixture) built
    before this field existed.
    """
    cfg = _league_config()

    assert cfg.playoff_week_start is None


# --- league_config_from_sleeper: the real fixture (acceptance benchmark) --


def test_league_config_from_real_fixture_matches_known_scoring(league_payload):
    """The real captured league is full PPR with no TE premium."""
    cfg = league_config_from_sleeper(league_payload)

    assert cfg.scoring.rec == 1.0
    assert cfg.scoring.bonus_rec_te == 0.0
    assert cfg.scoring.is_full_ppr is True
    assert cfg.scoring.has_te_premium is False


def test_league_config_from_real_fixture_replacement_rank_in_expected_range(
    league_payload,
):
    """This is the technical plan's own acceptance benchmark for this issue.

    A 10-team league with 2 locked WR slots and 3 FLEX slots must land VOLS
    WR replacement rank in [33, 36]. Do not weaken this range to make a
    different implementation pass. ``replacement_rank`` was later generalized
    beyond WR (see decisions.md) for the auction/keeper valuation work, but
    that generalization is additive by construction — this benchmark still
    holds unchanged for ``position="WR"``.
    """
    cfg = league_config_from_sleeper(league_payload)

    assert 33 <= cfg.replacement_rank("WR", "vols") <= 36


def test_league_config_from_real_fixture_roster_and_identity(league_payload):
    """Sanity-check the rest of the conversion against the real fixture."""
    cfg = league_config_from_sleeper(league_payload)

    assert cfg.league_id == "1367225133634191360"
    assert cfg.name == "NUCLEARFF REDRAFT"
    assert cfg.season == 2026
    assert cfg.num_teams == 10
    assert cfg.roster.wr == 2
    assert cfg.roster.flex == 3
    assert cfg.roster.bench == 6
    assert cfg.best_ball is False
    assert cfg.league_type == 0
    assert cfg.draft_id == "1367225133646778368"
    assert cfg.previous_league_id == "1240509989819273216"


def test_league_config_from_sleeper_handles_null_draft_and_previous_league_id(
    league_payload,
):
    """A league with no active draft and no carried-over prior season."""
    payload = dict(league_payload)
    payload["draft_id"] = None
    payload["previous_league_id"] = None

    cfg = league_config_from_sleeper(payload)

    assert cfg.draft_id is None
    assert cfg.previous_league_id is None


def test_league_config_from_real_fixture_has_real_playoff_week_start(league_payload):
    """The real committed fixture's playoff_week_start (15) is parsed through."""
    cfg = league_config_from_sleeper(league_payload)

    assert cfg.playoff_week_start == 15


def test_league_config_from_sleeper_missing_playoff_week_start_is_none():
    """A league missing playoff_week_start (e.g. Chopped) resolves to None."""
    payload = _payload_without_playoff_week_start()

    cfg = league_config_from_sleeper(payload)

    assert cfg.playoff_week_start is None


def _payload_without_playoff_week_start() -> dict[str, object]:
    """A minimal, otherwise-valid league payload with no playoff_week_start setting."""
    return {
        "league_id": "1",
        "name": "No Bracket League",
        "season": 2026,
        "total_rosters": 10,
        "settings": {"num_teams": 10},
        "scoring_settings": {"rec": 1.0},
        "roster_positions": ["QB", "RB", "WR", "BN"],
        "draft_id": None,
        "previous_league_id": None,
    }


def test_league_config_from_sleeper_rejects_non_positive_playoff_week_start():
    """A malformed (0 or negative) playoff_week_start coerces to None, not a bad int."""
    payload = _payload_without_playoff_week_start()
    payload["settings"] = {"num_teams": 10, "playoff_week_start": 0}

    cfg = league_config_from_sleeper(payload)

    assert cfg.playoff_week_start is None


def test_league_config_from_sleeper_raises_config_error_on_missing_field():
    """A payload missing a required field is a ConfigError, not a raw exception."""
    with pytest.raises(ConfigError):
        league_config_from_sleeper({"league_id": "1"})


# --- YAML round trip -------------------------------------------------------


def test_league_config_yaml_round_trip(tmp_path, league_payload):
    """dump_league_config -> load_league_config reproduces an equal LeagueConfig."""
    original = league_config_from_sleeper(league_payload)
    path = tmp_path / "leagues" / f"{original.league_id}.yaml"

    written = dump_league_config(original, path)
    reloaded = load_league_config(written)

    assert reloaded == original


def test_league_config_yaml_round_trip_with_null_draft_and_previous_league(tmp_path):
    """The round trip preserves None for both nullable identifiers."""
    original = _league_config(draft_id=None, previous_league_id=None)
    path = tmp_path / "league.yaml"

    dump_league_config(original, path)
    reloaded = load_league_config(path)

    assert reloaded == original
    assert reloaded.draft_id is None
    assert reloaded.previous_league_id is None


def test_dump_league_config_creates_parent_directories(tmp_path):
    """dump_league_config creates configs/leagues/-style parent dirs on demand."""
    cfg = _league_config()
    path = tmp_path / "nested" / "leagues" / f"{cfg.league_id}.yaml"

    result = dump_league_config(cfg, path)

    assert result == path
    assert path.is_file()


def test_load_league_config_missing_file_raises_config_error(tmp_path):
    """A requested league config file that does not exist is a ConfigError."""
    with pytest.raises(ConfigError, match="not found"):
        load_league_config(tmp_path / "absent.yaml")


def test_load_league_config_invalid_yaml_raises_config_error(tmp_path):
    """Unparseable YAML is reported as a configuration problem."""
    path = tmp_path / "league.yaml"
    path.write_text("league_id: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load_league_config(path)


def test_load_league_config_non_mapping_yaml_raises_config_error(tmp_path):
    """A YAML list is not a league configuration."""
    path = tmp_path / "league.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="mapping at the top level"):
        load_league_config(path)


def test_load_league_config_rejects_unknown_keys(tmp_path):
    """A typo in a league config file fails loudly instead of being ignored."""
    cfg = _league_config()
    path = tmp_path / "league.yaml"
    dump_league_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "not_a_real_field: 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid league configuration"):
        load_league_config(path)


def test_dump_league_config_output_is_deterministic(tmp_path):
    """Two dumps of the same config produce byte-identical YAML."""
    original = _league_config()

    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    dump_league_config(original, first)
    dump_league_config(original, second)

    assert first.read_bytes() == second.read_bytes()
