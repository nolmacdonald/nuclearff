"""Smoke tests for the command-line interface.

Each command is run against a temporary directory so nothing touches the real
data tree, and every network call is mocked.
"""

from __future__ import annotations

import pytest
import responses

from nuclearff.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, build_parser, main
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
