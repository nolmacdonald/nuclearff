"""Read-only Sleeper API client and league snapshot capture."""

from nuclearff.sleeper.client import SleeperClient
from nuclearff.sleeper.leagues import (
    league_config_rows,
    league_rows,
    walk_league_chain,
    write_league_tables,
)
from nuclearff.sleeper.matchups import fetch_and_write_matchups, matchup_rows
from nuclearff.sleeper.models import (
    Anomaly,
    LeagueSnapshot,
    SnapshotMetadata,
)
from nuclearff.sleeper.players import player_rows, write_players_table
from nuclearff.sleeper.roster_players import (
    fetch_and_write_roster_players,
    roster_player_rows,
)
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
from nuclearff.sleeper.transactions import (
    fetch_and_write_transactions,
    transaction_player_rows,
    transaction_rows,
)

__all__ = [
    "Anomaly",
    "LeagueSnapshot",
    "SleeperClient",
    "SnapshotMetadata",
    "bracket_match_rows",
    "detect_anomalies",
    "fetch_and_write_matchups",
    "fetch_and_write_roster_players",
    "fetch_and_write_standings",
    "fetch_and_write_transactions",
    "fetch_league_snapshot",
    "league_config_rows",
    "league_rows",
    "matchup_rows",
    "player_rows",
    "resolve_final_ranks",
    "roster_display_names",
    "roster_player_rows",
    "standings_rows",
    "transaction_player_rows",
    "transaction_rows",
    "walk_league_chain",
    "write_league_tables",
    "write_players_table",
    "write_snapshot",
]
