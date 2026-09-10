"""Read-only Sleeper API client and league snapshot capture."""

from nuclearff.sleeper.client import SleeperClient
from nuclearff.sleeper.draft import (
    draft_order_stats,
    draft_pick_rows,
    fetch_and_write_all_drafts,
    fetch_and_write_draft_picks,
)
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
from nuclearff.sleeper.trades import (
    cumulative_trade_counts,
    load_trades,
    manager_trade_counts,
    pairwise_trade_matrix,
    trades_by_season,
)
from nuclearff.sleeper.transactions import (
    fetch_and_write_transactions,
    transaction_player_rows,
    transaction_rows,
)
from nuclearff.sleeper.users import roster_owners

__all__ = [
    "Anomaly",
    "LeagueSnapshot",
    "SleeperClient",
    "SnapshotMetadata",
    "bracket_match_rows",
    "cumulative_trade_counts",
    "detect_anomalies",
    "draft_order_stats",
    "draft_pick_rows",
    "fetch_and_write_all_drafts",
    "fetch_and_write_draft_picks",
    "fetch_and_write_matchups",
    "fetch_and_write_roster_players",
    "fetch_and_write_standings",
    "fetch_and_write_transactions",
    "fetch_league_snapshot",
    "league_config_rows",
    "league_rows",
    "load_trades",
    "manager_trade_counts",
    "matchup_rows",
    "pairwise_trade_matrix",
    "player_rows",
    "resolve_final_ranks",
    "roster_display_names",
    "roster_owners",
    "roster_player_rows",
    "standings_rows",
    "trades_by_season",
    "transaction_player_rows",
    "transaction_rows",
    "walk_league_chain",
    "write_league_tables",
    "write_players_table",
    "write_snapshot",
]
