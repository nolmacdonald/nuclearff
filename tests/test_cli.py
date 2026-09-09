"""Smoke tests for the command-line interface.

Each command is run against a temporary directory so nothing touches the real
data tree, and every network call is mocked.
"""

from __future__ import annotations

import polars as pl
import pytest
import responses

from nuclearff.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    _densify_manager_season_matrix,
    _densify_trade_matrix,
    _densify_trades_by_season,
    build_parser,
    main,
)
from nuclearff.config import load_config
from tests.conftest import LEAGUE_ID, TEST_BASE_URL
from tests.test_sleeper_snapshot import DRAFT_ID


@pytest.fixture(autouse=True)
def _local_sleeper(monkeypatch):
    """Point the CLI's Sleeper client at a fake host with no throttling."""
    import nuclearff.cli as cli
    from nuclearff.sleeper.client import SleeperClient

    def _client(**kwargs):
        kwargs.setdefault("base_url", TEST_BASE_URL)
        kwargs.setdefault("min_interval", 0.0)
        kwargs.setdefault("backoff_factor", 0.0)
        return SleeperClient(**kwargs)

    monkeypatch.setattr(cli, "SleeperClient", _client)


def test_version(capsys):
    """--version prints and exits zero."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == EXIT_OK
    assert "nuclearff" in capsys.readouterr().out


def test_no_group_is_a_usage_error():
    """Invoking with no command group is a usage error, not a crash."""
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == EXIT_USAGE


def test_group_without_command_is_a_usage_error():
    """A group with no subcommand is likewise a usage error."""
    with pytest.raises(SystemExit) as excinfo:
        main(["config"])

    assert excinfo.value.code == EXIT_USAGE


def test_parser_exposes_both_groups():
    """The CLI surface covers configuration and Sleeper access."""
    parser = build_parser()
    groups = parser.parse_args(["config", "show"])

    assert groups.group == "config"
    assert parser.parse_args(["sleeper", "state"]).group == "sleeper"


def test_config_init_writes_a_loadable_file(tmp_path, capsys):
    """config init produces a file that round-trips through the loader."""
    out = tmp_path / "nuclearff.yaml"

    assert main(["config", "init", "-o", str(out)]) == EXIT_OK
    assert out.is_file()
    assert load_config(out).model.weights.volume == 0.50
    assert str(out) in capsys.readouterr().out


def test_config_init_refuses_to_clobber(tmp_path, capsys):
    """Overwriting an existing config requires --force."""
    out = tmp_path / "nuclearff.yaml"
    main(["config", "init", "-o", str(out)])

    assert main(["config", "init", "-o", str(out)]) == EXIT_ERROR
    assert "--force" in capsys.readouterr().err
    assert main(["config", "init", "-o", str(out), "--force"]) == EXIT_OK


def test_config_show_without_a_file_uses_defaults(tmp_path, capsys):
    """A fresh clone with no config file still gets a usable CLI."""
    assert main(["--root", str(tmp_path), "config", "show"]) == EXIT_OK
    assert "wr_default" in capsys.readouterr().out


def test_config_show_reports_a_bad_file(tmp_path, capsys):
    """A malformed config produces an actionable error and a nonzero exit."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("model:\n  weights:\n    volume: 2.0\n", encoding="utf-8")

    assert main(["-c", str(bad), "config", "show"]) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_config_paths_ensure_creates_the_tree(tmp_path, capsys):
    """config paths --ensure creates every managed directory under root."""
    assert main(["--root", str(tmp_path), "config", "paths", "--ensure"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "missing" not in out
    assert (tmp_path / "data" / "cache").is_dir()
    assert (tmp_path / "configs" / "leagues").is_dir()


@responses.activate
def test_sleeper_state(tmp_path, capsys, state_payload):
    """sleeper state prints the current season and week."""
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state_payload)

    assert main(["--root", str(tmp_path), "sleeper", "state"]) == EXIT_OK
    assert f"season={state_payload['season']}" in capsys.readouterr().out


@responses.activate
def test_sleeper_fetch_league(
    tmp_path,
    capsys,
    league_payload,
    draft_payload,
    users_payload,
    rosters_payload,
    state_payload,
):
    """fetch-league writes a snapshot and prints the settings needing review."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=users_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=rosters_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[draft_payload])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=draft_payload)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state_payload)

    exit_code = main(
        ["--root", str(tmp_path), "sleeper", "fetch-league", "--league-id", LEAGUE_ID]
    )

    assert exit_code == EXIT_OK

    out = capsys.readouterr().out
    assert "NUCLEARFF REDRAFT" in out
    assert "draft_rounds_mismatch" in out
    assert "keepers_in_redraft" in out

    snapshots = list((tmp_path / "data" / "raw" / "sleeper" / LEAGUE_ID).iterdir())
    assert len(snapshots) == 1
    assert (snapshots[0] / "snapshot.json").is_file()

    league_cfg_path = tmp_path / "configs" / "leagues" / f"{LEAGUE_ID}.yaml"
    assert league_cfg_path.is_file()
    assert f"League config: {league_cfg_path}" in out

    from nuclearff.config.league import load_league_config

    league_cfg = load_league_config(league_cfg_path)
    assert league_cfg.league_id == LEAGUE_ID
    assert league_cfg.scoring.rec == 1.0


@responses.activate
def test_sleeper_fetch_league_reports_api_failure(tmp_path, capsys):
    """An unreachable league is reported as an error, not a traceback."""
    for _ in range(8):
        responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", status=500)

    exit_code = main(
        ["--root", str(tmp_path), "sleeper", "fetch-league", "--league-id", LEAGUE_ID]
    )

    assert exit_code == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


@responses.activate
def test_sleeper_fetch_league_with_history_walks_and_writes_duckdb(
    tmp_path,
    capsys,
    league_payload,
    draft_payload,
    users_payload,
    rosters_payload,
    state_payload,
):
    """--history walks previous_league_id and persists both tables to DuckDB."""
    import duckdb

    from tests.test_sleeper_leagues import PREVIOUS_LEAGUE_ID

    previous_league_payload = dict(league_payload)
    previous_league_payload["league_id"] = PREVIOUS_LEAGUE_ID
    previous_league_payload["season"] = 2025
    previous_league_payload["previous_league_id"] = None

    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=users_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=rosters_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[draft_payload])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=draft_payload)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state_payload)

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "fetch-league",
            "--league-id",
            LEAGUE_ID,
            "--history",
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "History:  2 season(s) walked" in out
    assert "Configs:  2/2 parsed into LeagueConfig" in out

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        league_ids = {
            row[0]
            for row in conn.execute("SELECT league_id FROM sleeper_leagues").fetchall()
        }
    assert league_ids == {LEAGUE_ID, PREVIOUS_LEAGUE_ID}


@responses.activate
def test_sleeper_fetch_league_with_standings_writes_standings_and_matches(
    tmp_path,
    capsys,
    league_payload,
    draft_payload,
    users_payload,
    rosters_payload,
    state_payload,
):
    """--standings walks history (implicitly), persisting standings + bracket tables."""
    import duckdb

    from tests.test_sleeper_leagues import PREVIOUS_LEAGUE_ID

    previous_league_payload = dict(league_payload)
    previous_league_payload["league_id"] = PREVIOUS_LEAGUE_ID
    previous_league_payload["season"] = 2025
    previous_league_payload["previous_league_id"] = None

    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )
    for league_id in (LEAGUE_ID, PREVIOUS_LEAGUE_ID):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{league_id}/users", json=users_payload
        )
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{league_id}/rosters", json=rosters_payload
        )
        responses.get(f"{TEST_BASE_URL}/v1/league/{league_id}/winners_bracket", json=[])
        responses.get(f"{TEST_BASE_URL}/v1/league/{league_id}/losers_bracket", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[draft_payload])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=draft_payload)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state_payload)

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "fetch-league",
            "--league-id",
            LEAGUE_ID,
            "--standings",
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Standings: 4 roster-season(s)" in out
    assert "Playoffs:  0 bracket match(es)" in out

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        seasons = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT league_id FROM sleeper_standings"
            ).fetchall()
        }
    assert seasons == {LEAGUE_ID, PREVIOUS_LEAGUE_ID}


@responses.activate
def test_sleeper_fetch_league_with_matchups_writes_matchups(
    tmp_path,
    capsys,
    league_payload,
    draft_payload,
    users_payload,
    rosters_payload,
    state_payload,
):
    """--matchups walks history (implicitly) and persists weekly matchups."""
    import duckdb

    from tests.test_sleeper_leagues import PREVIOUS_LEAGUE_ID

    previous_league_payload = dict(league_payload)
    previous_league_payload["league_id"] = PREVIOUS_LEAGUE_ID
    previous_league_payload["season"] = 2025
    previous_league_payload["previous_league_id"] = None

    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=users_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=rosters_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[draft_payload])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=draft_payload)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state_payload)
    for league_id in (LEAGUE_ID, PREVIOUS_LEAGUE_ID):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{league_id}/matchups/1",
            json=[{"roster_id": 1, "matchup_id": 1, "points": 100.0}],
        )
        responses.get(f"{TEST_BASE_URL}/v1/league/{league_id}/matchups/2", json=[])

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "fetch-league",
            "--league-id",
            LEAGUE_ID,
            "--matchups",
            "--max-week",
            "2",
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Matchups: 2 roster-week row(s)" in out

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM sleeper_matchups").fetchone()
    assert count == 2


@responses.activate
def test_sleeper_fetch_league_with_transactions_writes_transactions(
    tmp_path,
    capsys,
    league_payload,
    draft_payload,
    users_payload,
    rosters_payload,
    state_payload,
):
    """--transactions walks history (implicitly) and persists weekly transactions."""
    import duckdb

    from tests.test_sleeper_leagues import PREVIOUS_LEAGUE_ID

    previous_league_payload = dict(league_payload)
    previous_league_payload["league_id"] = PREVIOUS_LEAGUE_ID
    previous_league_payload["season"] = 2025
    previous_league_payload["previous_league_id"] = None

    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=users_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=rosters_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[draft_payload])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=draft_payload)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state_payload)
    for league_id in (LEAGUE_ID, PREVIOUS_LEAGUE_ID):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{league_id}/users", json=users_payload
        )
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{league_id}/rosters", json=rosters_payload
        )
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{league_id}/transactions/1",
            json=[
                {
                    "status": "complete",
                    "type": "waiver",
                    "metadata": None,
                    "created": 1757473191611,
                    "settings": {},
                    "creator": rosters_payload[0]["owner_id"],
                    "transaction_id": f"tx-{league_id}",
                    "adds": {"12490": rosters_payload[0]["roster_id"]},
                    "consenter_ids": [rosters_payload[0]["roster_id"]],
                    "drops": None,
                    "roster_ids": [rosters_payload[0]["roster_id"]],
                    "status_updated": 1757488445297,
                    "waiver_budget": [],
                }
            ],
        )
        responses.get(f"{TEST_BASE_URL}/v1/league/{league_id}/transactions/2", json=[])

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "fetch-league",
            "--league-id",
            LEAGUE_ID,
            "--transactions",
            "--max-week",
            "2",
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Transactions: 2" in out
    assert "Add/drop rows: 2" in out

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM sleeper_transactions").fetchone()
    assert count == 2


@responses.activate
def test_sleeper_fetch_league_with_roster_players_writes_roster_composition(
    tmp_path,
    capsys,
    league_payload,
    draft_payload,
    users_payload,
    state_payload,
):
    """--roster-players walks history (implicitly) and persists roster composition."""
    import duckdb

    from tests.test_sleeper_leagues import PREVIOUS_LEAGUE_ID

    previous_league_payload = dict(league_payload)
    previous_league_payload["league_id"] = PREVIOUS_LEAGUE_ID
    previous_league_payload["season"] = 2025
    previous_league_payload["previous_league_id"] = None

    populated_rosters = [
        {
            "roster_id": 1,
            "owner_id": "u1",
            "players": ["100", "200", "300"],
            "starters": ["100"],
            "reserve": ["200"],
            "taxi": None,
        }
    ]

    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=users_payload)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[draft_payload])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=draft_payload)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state_payload)
    for league_id in (LEAGUE_ID, PREVIOUS_LEAGUE_ID):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{league_id}/rosters", json=populated_rosters
        )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "fetch-league",
            "--league-id",
            LEAGUE_ID,
            "--roster-players",
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Roster players: 6" in out

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM sleeper_roster_players"
        ).fetchone()
        (slot,) = conn.execute(
            "SELECT slot FROM sleeper_roster_players WHERE player_id = '200' "
            f"AND league_id = '{LEAGUE_ID}'"
        ).fetchone()
    assert count == 6
    assert slot == "reserve"


@responses.activate
def test_sleeper_user_leagues_resolves_username_and_lists_leagues(tmp_path, capsys):
    """user-leagues resolves a username to a user id, then lists their leagues."""
    responses.get(
        f"{TEST_BASE_URL}/v1/user/nolmacdonald",
        json={"user_id": "332632476830679040", "display_name": "nolmacdonald"},
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/user/332632476830679040/leagues/nfl/2026",
        json=[
            {"league_id": "1", "name": "Test League One", "status": "in_season"},
            {"league_id": "2", "name": "Test League Two", "status": "pre_draft"},
        ],
    )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "user-leagues",
            "nolmacdonald",
            "--season",
            "2026",
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "nolmacdonald (332632476830679040)" in out
    assert "Leagues: 2 for nfl 2026" in out
    assert "Test League One" in out
    assert "Test League Two" in out


@responses.activate
def test_sleeper_user_drafts_resolves_username_and_lists_drafts(tmp_path, capsys):
    responses.get(
        f"{TEST_BASE_URL}/v1/user/nolmacdonald",
        json={"user_id": "332632476830679040", "display_name": "nolmacdonald"},
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/user/332632476830679040/drafts/nfl/2026",
        json=[
            {
                "draft_id": "1",
                "status": "complete",
                "type": "snake",
                "metadata": {"name": "NUCLEARFF CHOPPED $100"},
            }
        ],
    )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "user-drafts",
            "nolmacdonald",
            "--season",
            "2026",
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Drafts: 1 for nfl 2026" in out
    assert "NUCLEARFF CHOPPED $100" in out
    assert "status=complete" in out


@responses.activate
def test_sleeper_user_drafts_handles_missing_metadata(tmp_path, capsys):
    """A draft with no metadata (or no name in it) doesn't crash the command."""
    responses.get(
        f"{TEST_BASE_URL}/v1/user/nolmacdonald",
        json={"user_id": "332632476830679040", "display_name": "nolmacdonald"},
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/user/332632476830679040/drafts/nfl/2026",
        json=[{"draft_id": "1", "status": "complete", "type": "snake"}],
    )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "sleeper",
            "user-drafts",
            "nolmacdonald",
            "--season",
            "2026",
        ]
    )

    assert exit_code == EXIT_OK
    assert "unknown" in capsys.readouterr().out


@responses.activate
def test_sleeper_trending_without_a_players_table_shows_raw_ids(tmp_path, capsys):
    """With no local player table, trending falls back to raw ids with a hint."""
    responses.get(
        f"{TEST_BASE_URL}/v1/players/nfl/trending/add?lookback_hours=24&limit=25",
        json=[{"player_id": "10235", "count": 208144}],
    )

    exit_code = main(["--root", str(tmp_path), "sleeper", "trending"])

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Trending add (last 24h):" in out
    assert "player_id=10235" in out
    assert "count=208144" in out
    assert "fetch-players" in out


@responses.activate
def test_sleeper_trending_resolves_names_from_the_local_players_table(tmp_path, capsys):
    """With a populated players table, trending prints real names."""
    from nuclearff.sleeper.players import write_players_table

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    write_players_table(
        {
            "10235": {"full_name": "Ja'Marr Chase"},
            # Real team-defense entries have no full_name (confirmed live
            # against Sleeper's actual payload for "LV") -- only first/last.
            "LV": {"first_name": "Las Vegas", "last_name": "Raiders"},
        },
        db_path,
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/players/nfl/trending/add?lookback_hours=24&limit=25",
        json=[
            {"player_id": "10235", "count": 208144},
            {"player_id": "LV", "count": 42024},
        ],
    )

    exit_code = main(["--root", str(tmp_path), "sleeper", "trending"])

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Ja'Marr Chase" in out
    assert "Las Vegas Raiders" in out
    assert "player_id=" not in out
    assert "fetch-players" not in out


@responses.activate
def test_sleeper_fetch_players(tmp_path, capsys):
    """fetch-players stores the player map in DuckDB under the cache dir."""
    import duckdb

    responses.get(
        f"{TEST_BASE_URL}/v1/players/nfl",
        json={"4046": {"full_name": "Patrick Mahomes", "position": "QB"}},
    )

    exit_code = main(["--root", str(tmp_path), "sleeper", "fetch-players"])

    assert exit_code == EXIT_OK

    out = capsys.readouterr().out
    assert "Players:  1" in out

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    assert db_path.is_file()
    with duckdb.connect(str(db_path)) as conn:
        (full_name,) = conn.execute(
            "SELECT full_name FROM sleeper_players WHERE player_id = '4046'"
        ).fetchone()
    assert full_name == "Patrick Mahomes"


@responses.activate
def test_report_playoff_bracket_renders_winners_and_losers_pngs(tmp_path, capsys):
    """playoff-bracket reads the standings/matches tables and writes PNGs."""
    from nuclearff.sleeper import SleeperClient
    from nuclearff.sleeper.standings import fetch_and_write_standings
    from tests.test_sleeper_standings import LEAGUE_ID as BRACKET_LEAGUE_ID
    from tests.test_sleeper_standings import LOSERS_BRACKET, ROSTERS, USERS
    from tests.test_sleeper_standings import WINNERS_BRACKET as BRACKET_WINNERS

    responses.get(
        f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/rosters", json=ROSTERS
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/winners_bracket",
        json=BRACKET_WINNERS,
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/losers_bracket",
        json=LOSERS_BRACKET,
    )

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with SleeperClient(
        cache_dir=tmp_path / "data" / "cache",
        base_url=TEST_BASE_URL,
        min_interval=0.0,
        backoff_factor=0.0,
    ) as client:
        fetch_and_write_standings(
            client, [{"league_id": BRACKET_LEAGUE_ID, "season": 2025}], db_path
        )

    out_dir = tmp_path / "brackets"
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "report",
            "playoff-bracket",
            BRACKET_LEAGUE_ID,
            "--season",
            "2025",
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Winners bracket:" in out
    assert "Losers bracket:" in out
    assert (out_dir / "winners_bracket.png").is_file()
    assert (out_dir / "losers_bracket.png").is_file()


@responses.activate
def test_report_playoff_bracket_with_no_matching_data_reports_and_exits(
    tmp_path, capsys
):
    """A league/season with no stored bracket data gets a message, not a traceback."""
    from nuclearff.sleeper import SleeperClient
    from nuclearff.sleeper.standings import fetch_and_write_standings
    from tests.test_sleeper_standings import LEAGUE_ID as BRACKET_LEAGUE_ID
    from tests.test_sleeper_standings import LOSERS_BRACKET, ROSTERS, USERS
    from tests.test_sleeper_standings import WINNERS_BRACKET as BRACKET_WINNERS

    responses.get(
        f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/rosters", json=ROSTERS
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/winners_bracket",
        json=BRACKET_WINNERS,
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{BRACKET_LEAGUE_ID}/losers_bracket",
        json=LOSERS_BRACKET,
    )

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with SleeperClient(
        cache_dir=tmp_path / "data" / "cache",
        base_url=TEST_BASE_URL,
        min_interval=0.0,
        backoff_factor=0.0,
    ) as client:
        fetch_and_write_standings(
            client, [{"league_id": BRACKET_LEAGUE_ID, "season": 2025}], db_path
        )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "report",
            "playoff-bracket",
            BRACKET_LEAGUE_ID,
            "--season",
            "2099",
        ]
    )

    assert exit_code == EXIT_ERROR
    assert "No sleeper_playoff_matches rows" in capsys.readouterr().out


def test_ids_resolve_gsis_reports_a_missing_players_table(tmp_path, capsys):
    """resolve-gsis tells the user to run fetch-players first, not a traceback."""
    exit_code = main(["--root", str(tmp_path), "ids", "resolve-gsis"])

    assert exit_code == EXIT_ERROR
    assert "nuclearff sleeper fetch-players" in capsys.readouterr().err


def test_ids_resolve_gsis_fills_gaps_and_reports_counts(tmp_path, capsys, monkeypatch):
    """resolve-gsis fills what it can from a mocked crosswalk and reports the rest."""
    import polars as pl

    import nuclearff.cli as cli
    from nuclearff.sleeper.players import write_players_table

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    write_players_table(
        {
            "4046": {"full_name": "Has Sleeper GSIS", "gsis_id": "00-0033873"},
            "200": {"full_name": "Needs Crosswalk", "gsis_id": None},
            "300": {"full_name": "Unmatched", "gsis_id": None},
        },
        db_path,
    )

    ff_ids = pl.DataFrame(
        {
            "sleeper_id": [200],
            "gsis_id": ["00-FROM-CROSSWALK"],
        },
        schema={"sleeper_id": pl.Int64, "gsis_id": pl.Utf8},
    )
    monkeypatch.setattr(cli, "configure_nflverse_cache", lambda cache_dir: None)
    monkeypatch.setattr(cli, "load_ff_playerids", lambda: ff_ids)

    exit_code = main(["--root", str(tmp_path), "ids", "resolve-gsis"])

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Players:                   3" in out
    assert "gsis_id from Sleeper:      1" in out
    assert "gsis_id from ff_playerids: 1" in out
    assert "Still unresolved:          1" in out


# --- report draft-board -----------------------------------------------

_DRAFT_BOARD_DRAFT = {
    "draft_id": DRAFT_ID,
    "league_id": LEAGUE_ID,
    "season": "2026",
    "type": "snake",
    "metadata": {"name": "NUCLEARFF REDRAFT"},
    "draft_order": {"u1": 1, "u2": 2},
    "settings": {"teams": 2, "rounds": 1, "reversal_round": 3},
}

_DRAFT_BOARD_USERS = [
    {"user_id": "u1", "display_name": "Alice"},
    {"user_id": "u2", "display_name": "Bob"},
]

_DRAFT_BOARD_PICKS = [
    {
        "draft_id": DRAFT_ID,
        "pick_no": 1,
        "round": 1,
        "draft_slot": 1,
        "roster_id": 1,
        "picked_by": "u1",
        "player_id": "9221",
        "is_keeper": None,
        "metadata": {
            "position": "RB",
            "first_name": "Jahmyr",
            "last_name": "Gibbs",
            "team": "DET",
        },
    },
    {
        "draft_id": DRAFT_ID,
        "pick_no": 2,
        "round": 1,
        "draft_slot": 2,
        "roster_id": 2,
        "picked_by": "u2",
        "player_id": "9509",
        "is_keeper": None,
        "metadata": {
            "position": "RB",
            "first_name": "Bijan",
            "last_name": "Robinson",
            "team": "ATL",
        },
    },
]


@responses.activate
def test_report_draft_board_renders_a_png_for_an_explicit_draft_id(tmp_path, capsys):
    """--draft-id renders that draft directly, no league-drafts lookup needed."""
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=_DRAFT_BOARD_DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=_DRAFT_BOARD_PICKS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=_DRAFT_BOARD_USERS
    )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "report",
            "draft-board",
            LEAGUE_ID,
            "--draft-id",
            DRAFT_ID,
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Picks:       2" in out

    out_path = (
        tmp_path / "data" / "artifacts" / f"{LEAGUE_ID}-draft-board" / f"{DRAFT_ID}.png"
    )
    assert out_path.is_file()
    assert str(out_path) in out


@responses.activate
def test_report_draft_board_resolves_the_current_draft_when_omitted(tmp_path, capsys):
    """No --draft-id -> resolved from the league's most recent draft."""
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[_DRAFT_BOARD_DRAFT]
    )
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=_DRAFT_BOARD_DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=_DRAFT_BOARD_PICKS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=_DRAFT_BOARD_USERS
    )

    exit_code = main(["--root", str(tmp_path), "report", "draft-board", LEAGUE_ID])

    assert exit_code == EXIT_OK
    assert "Picks:       2" in capsys.readouterr().out


@responses.activate
def test_report_draft_board_reports_no_drafts_for_the_league(tmp_path, capsys):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[])

    exit_code = main(["--root", str(tmp_path), "report", "draft-board", LEAGUE_ID])

    assert exit_code == EXIT_ERROR
    assert "No drafts found" in capsys.readouterr().out


@responses.activate
def test_report_draft_board_reports_no_picks_made_yet(tmp_path, capsys):
    """A draft that exists but has no picks yet is reported, not a traceback."""
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=_DRAFT_BOARD_DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=_DRAFT_BOARD_USERS
    )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "report",
            "draft-board",
            LEAGUE_ID,
            "--draft-id",
            DRAFT_ID,
        ]
    )

    assert exit_code == EXIT_ERROR
    assert "No picks made yet" in capsys.readouterr().out


# --- report trades -----------------------------------------------------

_TRADES_LEAGUE_ID = "1240509989819273216"

_TRADES_ROSTERS = [
    {"roster_id": 3, "owner_id": "u3"},
    {"roster_id": 5, "owner_id": "u5"},
    {"roster_id": 6, "owner_id": "u6"},
]

_TRADES_USERS = [
    {"user_id": "u3", "display_name": "nolmacdonald"},
    {"user_id": "u5", "display_name": "hyoga10"},
    {"user_id": "u6", "display_name": "Donkeysride"},
]

_REAL_TRADE = {
    "status": "complete",
    "type": "trade",
    "metadata": None,
    "created": 1762303727750,
    "settings": {"expires_at": 1762476527},
    "leg": 9,
    "draft_picks": [],
    "creator": "u3",
    "transaction_id": "1291599084301348864",
    "adds": {"7525": 6, "7526": 6, "9509": 3},
    "consenter_ids": [3, 6],
    "drops": {"7525": 3, "7526": 3, "9509": 6},
    "roster_ids": [3, 6],
    "status_updated": 1762457470827,
    "waiver_budget": [],
}


def _mock_trades_endpoints():
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/rosters",
        json=_TRADES_ROSTERS,
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/users", json=_TRADES_USERS
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/winners_bracket", json=[]
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/losers_bracket", json=[]
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/transactions/1",
        json=[_REAL_TRADE],
    )
    for week in range(2, 19):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/transactions/{week}",
            json=[],
        )


def test_densify_trade_matrix_adds_zero_rows_and_columns_for_missing_managers():
    sparse = pl.DataFrame(
        {
            "manager": ["Donkeysride", "nolmacdonald"],
            "Donkeysride": [0, 2],
            "nolmacdonald": [2, 0],
        }
    )

    dense = _densify_trade_matrix(sparse, ["Donkeysride", "hyoga10", "nolmacdonald"])

    assert dense["manager"].to_list() == ["Donkeysride", "hyoga10", "nolmacdonald"]
    assert dense["hyoga10"].to_list() == [0, 0, 0]
    assert dense.filter(pl.col("manager") == "hyoga10")["Donkeysride"].item() == 0
    assert dense.filter(pl.col("manager") == "Donkeysride")["nolmacdonald"].item() == 2


def test_densify_trades_by_season_fills_zero_only_for_rostered_seasons():
    """Issue #48: a real 0 for a rostered non-trading season, no row otherwise."""
    by_season = pl.DataFrame(
        {"manager": ["nolmacdonald"], "season": [2025], "trades": [2]}
    )
    standings = pl.DataFrame(
        {
            "display_name": ["nolmacdonald", "nolmacdonald", "hyoga10"],
            "season": [2025, 2026, 2026],
        }
    )

    dense = _densify_trades_by_season(by_season, standings)
    rows = {(r["manager"], r["season"]): r["trades"] for r in dense.to_dicts()}

    assert rows[("nolmacdonald", 2025)] == 2
    assert rows[("nolmacdonald", 2026)] == 0
    assert rows[("hyoga10", 2026)] == 0
    assert ("hyoga10", 2025) not in rows


def test_densify_manager_season_matrix_fills_every_combination():
    """Issue #49: unlike _densify_trades_by_season, every cell is dense -- no gaps."""
    by_season = pl.DataFrame(
        {"manager": ["nolmacdonald"], "season": [2026], "trades": [3]}
    )

    dense = _densify_manager_season_matrix(
        by_season, ["hyoga10", "nolmacdonald"], [2025, 2026]
    )

    assert dense.filter(pl.col("manager") == "nolmacdonald")["2026"].item() == 3
    assert dense.filter(pl.col("manager") == "nolmacdonald")["2025"].item() == 0
    assert dense.filter(pl.col("manager") == "hyoga10")["2025"].item() == 0
    assert dense.filter(pl.col("manager") == "hyoga10")["2026"].item() == 0


@responses.activate
def test_report_trades_renders_a_png_and_densifies_zero_trade_managers(
    tmp_path, capsys
):
    """hyoga10 never traded but must still appear via the sleeper_standings join."""
    from nuclearff.sleeper import SleeperClient
    from nuclearff.sleeper.standings import fetch_and_write_standings
    from nuclearff.sleeper.transactions import fetch_and_write_transactions

    _mock_trades_endpoints()

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    leagues = [{"league_id": _TRADES_LEAGUE_ID, "season": 2025}]
    with SleeperClient(
        cache_dir=tmp_path / "data" / "cache",
        base_url=TEST_BASE_URL,
        min_interval=0.0,
        backoff_factor=0.0,
    ) as client:
        fetch_and_write_standings(client, leagues, db_path)
        fetch_and_write_transactions(client, leagues, db_path)

    out_dir = tmp_path / "trades"
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "report",
            "trades",
            _TRADES_LEAGUE_ID,
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "Managers:                 3" in out
    assert (out_dir / "trades_by_manager.png").is_file()
    assert (out_dir / "trades_heatmap.png").is_file()
    assert (out_dir / "trade_network.png").is_file()
    assert (out_dir / "trade_leaderboard.png").is_file()
    assert (out_dir / "manager_pair_leaderboard.png").is_file()
    assert (out_dir / "trades_over_time.png").is_file()
    assert (out_dir / "manager_season_heatmap.png").is_file()
    assert (out_dir / "cumulative_trades.png").is_file()


@responses.activate
def test_report_trades_falls_back_without_standings_table(tmp_path, capsys):
    """No sleeper_standings yet -- still renders, using only trading managers."""
    from nuclearff.sleeper import SleeperClient
    from nuclearff.sleeper.transactions import fetch_and_write_transactions

    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/rosters",
        json=_TRADES_ROSTERS,
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/users", json=_TRADES_USERS
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/transactions/1",
        json=[_REAL_TRADE],
    )
    for week in range(2, 19):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{_TRADES_LEAGUE_ID}/transactions/{week}",
            json=[],
        )

    db_path = tmp_path / "data" / "cache" / "nuclearff.duckdb"
    with SleeperClient(
        cache_dir=tmp_path / "data" / "cache",
        base_url=TEST_BASE_URL,
        min_interval=0.0,
        backoff_factor=0.0,
    ) as client:
        fetch_and_write_transactions(
            client, [{"league_id": _TRADES_LEAGUE_ID, "season": 2025}], db_path
        )

    out_dir = tmp_path / "trades"
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "report",
            "trades",
            _TRADES_LEAGUE_ID,
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == EXIT_OK
    assert (
        "Managers:                 2" in capsys.readouterr().out
    )  # only the 2 traders
    assert (out_dir / "trades_by_manager.png").is_file()
    assert (out_dir / "trades_heatmap.png").is_file()
    assert (out_dir / "trade_network.png").is_file()
    assert (out_dir / "trade_leaderboard.png").is_file()
    assert (out_dir / "manager_pair_leaderboard.png").is_file()
    assert (out_dir / "trades_over_time.png").is_file()
    assert (out_dir / "manager_season_heatmap.png").is_file()
    assert (out_dir / "cumulative_trades.png").is_file()


def test_report_trades_reports_a_missing_transactions_table(tmp_path, capsys):
    """No sleeper_transactions at all is reported, not a traceback."""
    exit_code = main(["--root", str(tmp_path), "report", "trades", _TRADES_LEAGUE_ID])

    assert exit_code == EXIT_ERROR
    assert "No sleeper_transactions table found" in capsys.readouterr().out
