"""Read-only Sleeper API client and league snapshot capture."""

from nuclearff.sleeper.client import SleeperClient
from nuclearff.sleeper.leagues import (
    league_config_rows,
    league_rows,
    walk_league_chain,
    write_league_tables,
)
from nuclearff.sleeper.models import (
    Anomaly,
    LeagueSnapshot,
    SnapshotMetadata,
)
from nuclearff.sleeper.players import player_rows, write_players_table
from nuclearff.sleeper.snapshot import (
    detect_anomalies,
    fetch_league_snapshot,
    write_snapshot,
)
from nuclearff.sleeper.standings import (
    bracket_match_rows,
    fetch_and_write_standings,
    resolve_final_ranks,
    roster_display_names,
    standings_rows,
)

__all__ = [
    "Anomaly",
    "LeagueSnapshot",
    "SleeperClient",
    "SnapshotMetadata",
    "bracket_match_rows",
    "detect_anomalies",
    "fetch_and_write_standings",
    "fetch_league_snapshot",
    "league_config_rows",
    "league_rows",
    "player_rows",
    "resolve_final_ranks",
    "roster_display_names",
    "standings_rows",
    "walk_league_chain",
    "write_league_tables",
    "write_players_table",
    "write_snapshot",
]
