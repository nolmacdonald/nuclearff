"""Command-line interface for nuclearff.

Commands are grouped by concern (``config``, ``sleeper``, ``ids``, ``report``).
Every command reads its settings from a configuration file so that a run can
be reproduced from a git SHA plus a config, and every command returns an
exit code rather than calling :func:`sys.exit` directly, which keeps them
testable.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from nuclearff._version import __version__
from nuclearff.config import default_config, dump_config, load_config
from nuclearff.config.league import dump_league_config, league_config_from_sleeper
from nuclearff.config.loader import to_yaml
from nuclearff.config.models import NuclearffConfig
from nuclearff.exceptions import NuclearffError, StorageError
from nuclearff.ids import (
    ambiguous_sleeper_ids,
    read_sleeper_players,
    resolve_missing_gsis_ids,
    write_player_id_map,
)
from nuclearff.logging_config import configure_logging
from nuclearff.nflverse import configure_cache as configure_nflverse_cache
from nuclearff.nflverse import load_ff_playerids
from nuclearff.sleeper import (
    SleeperClient,
    fetch_and_write_matchups,
    fetch_and_write_roster_players,
    fetch_and_write_standings,
    fetch_and_write_transactions,
    fetch_league_snapshot,
    walk_league_chain,
    write_league_tables,
    write_players_table,
    write_snapshot,
)
from nuclearff.sleeper.leagues import DEFAULT_MAX_SEASONS
from nuclearff.sleeper.matchups import DEFAULT_MAX_WEEK

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("configs/nuclearff.yaml")
"""Configuration file used when ``--config`` is not supplied."""

EXIT_OK = 0
"""Successful completion."""

EXIT_ERROR = 1
"""A nuclearff error the user can act on."""

EXIT_USAGE = 2
"""Invalid command-line usage; matches argparse's own convention."""


def _resolve_config(args: argparse.Namespace) -> NuclearffConfig:
    """Build the configuration for a command invocation.

    Falls back to defaults when no configuration file exists at the default
    path, so the CLI is usable on a fresh clone. An explicitly supplied
    ``--config`` that is missing is an error, not a fallback.

    Args:
        args: Parsed arguments carrying ``config`` and ``root``.

    Returns:
        The resolved configuration.

    Raises:
        ConfigError: If an explicitly requested configuration file is missing or
            invalid.
    """
    path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH

    if args.config is None and not path.is_file():
        config = default_config()
        if args.root is not None:
            config = config.model_copy(
                update={
                    "paths": config.paths.model_copy(update={"root": Path(args.root)})
                }
            )
        return config

    return load_config(path, root=args.root)


def _cmd_config_init(args: argparse.Namespace) -> int:
    """Write a configuration file populated with documented defaults.

    Args:
        args: Parsed arguments carrying ``out`` and ``force``.

    Returns:
        An exit code.
    """
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists; pass --force to overwrite", file=sys.stderr)
        return EXIT_ERROR

    dump_config(default_config(), out)
    print(f"Wrote default configuration to {out}")
    return EXIT_OK


def _cmd_config_show(args: argparse.Namespace) -> int:
    """Print the resolved configuration as YAML.

    Args:
        args: Parsed arguments.

    Returns:
        An exit code.
    """
    print(to_yaml(_resolve_config(args)), end="")
    return EXIT_OK


def _cmd_config_paths(args: argparse.Namespace) -> int:
    """Print the managed directories, optionally creating them.

    Args:
        args: Parsed arguments carrying ``ensure``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    if args.ensure:
        config.paths.ensure()

    for directory in config.paths.all_dirs():
        marker = "ok     " if directory.is_dir() else "missing"
        print(f"{marker}  {directory}")
    return EXIT_OK


def _cmd_sleeper_state(args: argparse.Namespace) -> int:
    """Print the current NFL league-year state from Sleeper.

    Args:
        args: Parsed arguments.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        state = client.get_state()

    season = state.get("season")
    week = state.get("week")
    season_type = state.get("season_type")
    print(f"season={season} week={week} season_type={season_type}")
    return EXIT_OK


def _cmd_sleeper_fetch_league(args: argparse.Namespace) -> int:
    """Capture an immutable snapshot of a league and report any anomalies.

    Args:
        args: Parsed arguments carrying ``league_id``, ``history``,
            ``standings``, ``matchups``, ``transactions``, ``roster_players``,
            ``max_seasons``, and ``max_week``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    config.paths.ensure()

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        snapshot = fetch_league_snapshot(client, args.league_id)

        if (
            args.history
            or args.standings
            or args.matchups
            or args.transactions
            or args.roster_players
        ):
            leagues = walk_league_chain(
                client, args.league_id, max_seasons=args.max_seasons
            )
            db_path = config.paths.cache_dir / "nuclearff.duckdb"

        if args.history:
            raw_count, config_count = write_league_tables(leagues, db_path)

        if args.standings:
            standings_count, matches_count = fetch_and_write_standings(
                client, leagues, db_path
            )

        if args.matchups:
            matchup_count = fetch_and_write_matchups(
                client, leagues, db_path, max_week=args.max_week
            )

        if args.transactions:
            transaction_count, transaction_player_count = fetch_and_write_transactions(
                client, leagues, db_path, max_week=args.max_week
            )

        if args.roster_players:
            roster_player_count = fetch_and_write_roster_players(
                client, leagues, db_path
            )

    target = write_snapshot(snapshot, config.paths.raw_dir)

    print(f"League:   {snapshot.league_name} ({snapshot.metadata.league_id})")
    print(
        f"Season:   {snapshot.league.get('season')} "
        f"status={snapshot.league.get('status')}"
    )
    print(f"Teams:    {snapshot.league.get('total_rosters')}")
    print(f"Snapshot: {target}")

    try:
        league_cfg = league_config_from_sleeper(snapshot.league)
        league_cfg_path = dump_league_config(
            league_cfg, config.paths.leagues_dir / f"{league_cfg.league_id}.yaml"
        )
    except NuclearffError as exc:
        # The raw snapshot above is the durable, valuable artifact; a league
        # this tool cannot yet type shouldn't make the whole command fail.
        logger.warning("Could not derive a LeagueConfig from this snapshot: %s", exc)
        print(f"League config: skipped ({exc})")
    else:
        print(f"League config: {league_cfg_path}")

    if snapshot.metadata.missing:
        print(f"\nEndpoints unavailable ({len(snapshot.metadata.missing)}):")
        for path in snapshot.metadata.missing:
            print(f"  - {path}")

    if snapshot.anomalies:
        print("\nSettings to review:")
        for anomaly in snapshot.anomalies:
            label = anomaly.severity.upper()
            print(f"  [{label}] {anomaly.code}: {anomaly.message}")
            print(f"           -> {anomaly.action}")

    if args.history:
        print(f"\nHistory:  {len(leagues)} season(s) walked")
        print(f"Configs:  {config_count}/{raw_count} parsed into LeagueConfig")
        print(f"Database: {db_path}")

    if args.standings:
        print(f"\nStandings: {standings_count} roster-season(s)")
        print(f"Playoffs:  {matches_count} bracket match(es)")
        if not args.history:
            print(f"Database:  {db_path}")

    if args.matchups:
        print(f"\nMatchups: {matchup_count} roster-week row(s)")
        if not (args.history or args.standings):
            print(f"Database: {db_path}")

    if args.transactions:
        print(f"\nTransactions: {transaction_count}")
        print(f"Add/drop rows: {transaction_player_count}")
        if not (args.history or args.standings or args.matchups):
            print(f"Database: {db_path}")

    if args.roster_players:
        print(f"\nRoster players: {roster_player_count}")
        if not (
            args.history or args.standings or args.matchups or args.transactions
        ):
            print(f"Database: {db_path}")

    return EXIT_OK


def _cmd_sleeper_fetch_players(args: argparse.Namespace) -> int:
    """Fetch the Sleeper NFL player map and store it in DuckDB for mapping.

    Args:
        args: Parsed arguments carrying ``force_refresh``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    config.paths.ensure()

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        players = client.get_players(force_refresh=args.force_refresh)

    db_path = config.paths.cache_dir / "nuclearff.duckdb"
    count = write_players_table(players, db_path)

    print(f"Players:  {count}")
    print(f"Database: {db_path}")
    return EXIT_OK


def _cmd_sleeper_user_leagues(args: argparse.Namespace) -> int:
    """Resolve a Sleeper username to a user id and list their leagues for a season.

    The primary entry point for "I have a username, what leagues are they
    in" — without this, nuclearff can only start from an already-known
    ``league_id``.

    Args:
        args: Parsed arguments carrying ``username``, ``season``, and
            ``sport``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        user = client.get_user(args.username)
        leagues = client.get_user_leagues(
            user["user_id"], args.season, sport=args.sport
        )

    print(f"User:    {user.get('display_name')} ({user['user_id']})")
    print(f"Leagues: {len(leagues)} for {args.sport} {args.season}")
    for league in leagues:
        print(
            f"  {league.get('league_id')}  {league.get('name')}  "
            f"status={league.get('status')}"
        )
    return EXIT_OK


def _cmd_sleeper_user_drafts(args: argparse.Namespace) -> int:
    """Resolve a Sleeper username to a user id and list their drafts for a season.

    Args:
        args: Parsed arguments carrying ``username``, ``season``, and
            ``sport``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        user = client.get_user(args.username)
        drafts = client.get_user_drafts(user["user_id"], args.season, sport=args.sport)

    print(f"User:   {user.get('display_name')} ({user['user_id']})")
    print(f"Drafts: {len(drafts)} for {args.sport} {args.season}")
    for draft in drafts:
        metadata = draft.get("metadata") or {}
        league_name = metadata.get("name") or "unknown"
        print(
            f"  {draft.get('draft_id')}  {league_name}  "
            f"status={draft.get('status')}  type={draft.get('type')}"
        )
    return EXIT_OK


def _cmd_sleeper_trending(args: argparse.Namespace) -> int:
    """Print trending players by adds or drops, resolved to names when possible.

    Trending only ever returns a bare ``player_id``. Names are resolved
    against the local ``sleeper_players`` table (written by
    ``nuclearff sleeper fetch-players``) when it exists; otherwise the raw id
    is printed with a hint to run that command first, rather than failing.

    Args:
        args: Parsed arguments carrying ``kind``, ``lookback_hours``, and
            ``limit``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        trending = client.get_trending(
            kind=args.kind, lookback_hours=args.lookback_hours, limit=args.limit
        )

    db_path = config.paths.cache_dir / "nuclearff.duckdb"
    names: dict[str, str] | None
    try:
        players = read_sleeper_players(db_path)
        names = {}
        for row in players.iter_rows(named=True):
            # Team defenses carry no full_name in Sleeper's real payload
            # (confirmed live: "LV" has only first_name="Las Vegas",
            # last_name="Raiders") -- fall back to combining those.
            name = row.get("full_name") or " ".join(
                part for part in (row.get("first_name"), row.get("last_name")) if part
            )
            if name:
                names[row["player_id"]] = name
    except StorageError:
        names = None

    print(f"Trending {args.kind} (last {args.lookback_hours}h):")
    for entry in trending:
        player_id = entry.get("player_id")
        label = names.get(player_id) if names and isinstance(player_id, str) else None
        label = label or f"player_id={player_id}"
        print(f"  {label:<28} count={entry.get('count')}")

    if names is None:
        print(
            "\n(Player names unresolved -- run `nuclearff sleeper fetch-players` "
            "first.)"
        )
    return EXIT_OK


def _cmd_ids_resolve_gsis(args: argparse.Namespace) -> int:
    """Fill missing Sleeper ``gsis_id`` values via the nflverse crosswalk.

    Reads the ``sleeper_players`` DuckDB table (written by
    ``nuclearff sleeper fetch-players``), fetches the ff_playerids crosswalk
    from nflverse, and writes a ``player_id_map`` table with every player's
    best-known ``gsis_id`` and where it came from.

    Args:
        args: Parsed arguments.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    config.paths.ensure()

    db_path = config.paths.cache_dir / "nuclearff.duckdb"
    players = read_sleeper_players(db_path)

    configure_nflverse_cache(config.paths.cache_dir)
    ff_ids = load_ff_playerids()

    ambiguous = ambiguous_sleeper_ids(ff_ids)
    resolved = resolve_missing_gsis_ids(players, ff_ids)
    write_player_id_map(resolved, db_path)

    from_sleeper = resolved.filter(pl.col("gsis_id_source") == "sleeper").height
    from_crosswalk = resolved.filter(pl.col("gsis_id_source") == "ff_playerids").height
    unresolved = resolved.height - from_sleeper - from_crosswalk

    print(f"Players:                   {resolved.height}")
    print(f"gsis_id from Sleeper:      {from_sleeper}")
    print(f"gsis_id from ff_playerids: {from_crosswalk}")
    print(f"Still unresolved:          {unresolved}")

    if ambiguous.height:
        ambiguous_ids = ambiguous.select("sleeper_id").unique().height
        print(
            f"\nSkipped {ambiguous_ids} ambiguous crosswalk sleeper_id value(s) "
            "(maps to more than one player; not used to fill gaps)."
        )

    return EXIT_OK


def _cmd_report_auction_board(args: argparse.Namespace) -> int:
    """Build the auction draft board and write the CSV, tables, and report.

    Args:
        args: Parsed arguments.

    Returns:
        An exit code.
    """
    from nuclearff.pipeline import build_auction_board
    from nuclearff.report import write_report

    config = _resolve_config(args)
    config.paths.ensure()
    configure_nflverse_cache(config.paths.cache_dir)

    seasons = [int(season) for season in args.seasons]
    client = SleeperClient(cache_dir=config.paths.cache_dir)
    board, context = build_auction_board(
        args.league_id,
        seasons=seasons,
        as_of_season=args.as_of_season,
        client=client,
        baseline=args.baseline,
    )

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else config.paths.artifacts_dir / f"{args.league_id}-{args.as_of_season}"
    )
    report_path = write_report(
        board,
        context,
        out_dir,
        top_n=args.top,
        render_tables=not args.no_tables,
    )

    in_pool = board.filter(pl.col("in_draft_pool")).height
    print(f"League:          {context['league_name']} ({context['season']})")
    print(
        f"Budget:          ${context['budget_per_team']}/team x "
        f"{context['num_teams']} teams"
    )
    print(f"Players valued:  {board.height}")
    print(f"In draft pool:   {in_pool}")
    print(f"Report:          {report_path}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for every command group.

    Returns:
        The configured top-level parser.
    """
    parser = argparse.ArgumentParser(
        prog="nuclearff",
        description="League-aware fantasy football research and valuation.",
    )
    parser.add_argument(
        "--version", action="version", version=f"nuclearff {__version__}"
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help=f"Configuration file (default: {DEFAULT_CONFIG_PATH} when present)",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Override paths.root; every managed directory resolves beneath it",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Console log level (default: the configured run.log_level)",
    )

    groups = parser.add_subparsers(dest="group", metavar="<group>")
    groups.required = True

    config_parser = groups.add_parser("config", help="Inspect and create configuration")
    config_commands = config_parser.add_subparsers(dest="command", metavar="<command>")
    config_commands.required = True

    init = config_commands.add_parser("init", help="Write a default configuration file")
    init.add_argument(
        "-o", "--out", default=str(DEFAULT_CONFIG_PATH), help="Destination file"
    )
    init.add_argument("--force", action="store_true", help="Overwrite an existing file")
    init.set_defaults(func=_cmd_config_init)

    show = config_commands.add_parser("show", help="Print the resolved configuration")
    show.set_defaults(func=_cmd_config_show)

    paths = config_commands.add_parser("paths", help="Print managed directories")
    paths.add_argument(
        "--ensure", action="store_true", help="Create any missing directories"
    )
    paths.set_defaults(func=_cmd_config_paths)

    sleeper_parser = groups.add_parser("sleeper", help="Read-only Sleeper API access")
    sleeper_commands = sleeper_parser.add_subparsers(
        dest="command", metavar="<command>"
    )
    sleeper_commands.required = True

    state = sleeper_commands.add_parser("state", help="Print the current NFL state")
    state.set_defaults(func=_cmd_sleeper_state)

    fetch = sleeper_commands.add_parser(
        "fetch-league", help="Capture an immutable league snapshot"
    )
    fetch.add_argument("--league-id", required=True, help="Sleeper league identifier")
    fetch.add_argument(
        "--history",
        action="store_true",
        help=(
            "Also walk previous_league_id back through prior seasons and "
            "write raw + parsed league history to DuckDB"
        ),
    )
    fetch.add_argument(
        "--standings",
        action="store_true",
        help=(
            "Also compute standings and playoff results per season "
            "(implies --history) and write them to DuckDB"
        ),
    )
    fetch.add_argument(
        "--matchups",
        action="store_true",
        help=(
            "Also fetch weekly matchups per season (implies --history) and "
            "write them to DuckDB"
        ),
    )
    fetch.add_argument(
        "--transactions",
        action="store_true",
        help=(
            "Also fetch weekly transactions per season (implies --history) "
            "and write them to DuckDB"
        ),
    )
    fetch.add_argument(
        "--roster-players",
        action="store_true",
        help=(
            "Also categorize each roster's players by slot "
            "(starter/reserve/taxi/bench) per season (implies --history) "
            "and write them to DuckDB"
        ),
    )
    fetch.add_argument(
        "--max-seasons",
        type=int,
        default=DEFAULT_MAX_SEASONS,
        help=(
            f"Maximum seasons to walk when --history, --standings, "
            f"--matchups, --transactions, or --roster-players is set "
            f"(default: {DEFAULT_MAX_SEASONS})"
        ),
    )
    fetch.add_argument(
        "--max-week",
        type=int,
        default=DEFAULT_MAX_WEEK,
        help=(
            f"Maximum week to fetch per season when --matchups or "
            f"--transactions is set (default: {DEFAULT_MAX_WEEK})"
        ),
    )
    fetch.set_defaults(func=_cmd_sleeper_fetch_league)

    fetch_players = sleeper_commands.add_parser(
        "fetch-players", help="Fetch the NFL player map into a local DuckDB table"
    )
    fetch_players.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-fetch even if the on-disk player cache is still fresh",
    )
    fetch_players.set_defaults(func=_cmd_sleeper_fetch_players)

    user_leagues = sleeper_commands.add_parser(
        "user-leagues", help="List a Sleeper user's leagues for a season"
    )
    user_leagues.add_argument("username", help="Sleeper username or user id")
    user_leagues.add_argument("--season", required=True, help="Season year, e.g. 2026")
    user_leagues.add_argument("--sport", default="nfl", help="Sport key (default: nfl)")
    user_leagues.set_defaults(func=_cmd_sleeper_user_leagues)

    user_drafts = sleeper_commands.add_parser(
        "user-drafts", help="List a Sleeper user's drafts for a season"
    )
    user_drafts.add_argument("username", help="Sleeper username or user id")
    user_drafts.add_argument("--season", required=True, help="Season year, e.g. 2026")
    user_drafts.add_argument("--sport", default="nfl", help="Sport key (default: nfl)")
    user_drafts.set_defaults(func=_cmd_sleeper_user_drafts)

    trending = sleeper_commands.add_parser(
        "trending", help="Print trending players by adds or drops"
    )
    trending.add_argument(
        "--kind", choices=("add", "drop"), default="add", help="Trending kind"
    )
    trending.add_argument(
        "--lookback-hours", type=int, default=24, help="Lookback window in hours"
    )
    trending.add_argument(
        "--limit", type=int, default=25, help="Maximum players to return"
    )
    trending.set_defaults(func=_cmd_sleeper_trending)

    ids_parser = groups.add_parser(
        "ids", help="Cross-source player identity resolution"
    )
    ids_commands = ids_parser.add_subparsers(dest="command", metavar="<command>")
    ids_commands.required = True

    resolve_gsis = ids_commands.add_parser(
        "resolve-gsis",
        help="Fill missing Sleeper gsis_id values via the nflverse crosswalk",
    )
    resolve_gsis.set_defaults(func=_cmd_ids_resolve_gsis)

    report_parser = groups.add_parser("report", help="Build draft boards and reports")
    report_commands = report_parser.add_subparsers(dest="command", metavar="<command>")
    report_commands.required = True

    auction_board = report_commands.add_parser(
        "auction-board",
        help="Build an auction draft board (CSV, position tables, report.md)",
    )
    auction_board.add_argument(
        "league_id", help="Sleeper league identifier (must have an auction draft)"
    )
    auction_board.add_argument(
        "--seasons",
        nargs="+",
        required=True,
        help="Historical seasons to score, oldest first (e.g. 2023 2024 2025)",
    )
    auction_board.add_argument(
        "--as-of-season",
        type=int,
        required=True,
        help="The season being drafted for (e.g. 2026)",
    )
    auction_board.add_argument(
        "--baseline",
        choices=("vols", "vorp"),
        default="vols",
        help="Replacement baseline (default: vols)",
    )
    auction_board.add_argument(
        "--top", type=int, default=12, help="Players per position table (default: 12)"
    )
    auction_board.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: <artifacts>/<league_id>-<season>)",
    )
    auction_board.add_argument(
        "--no-tables",
        action="store_true",
        help="Skip PNG tables (avoids the plottable/matplotlib dev extra)",
    )
    auction_board.set_defaults(func=_cmd_report_auction_board)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface.

    Args:
        argv: Arguments to parse, defaulting to :data:`sys.argv`.

    Returns:
        A process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    level = getattr(logging, args.log_level or "INFO")
    configure_logging(level=level, force=True)

    try:
        return int(args.func(args))
    except NuclearffError as exc:
        logger.debug("Command failed", exc_info=True)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
