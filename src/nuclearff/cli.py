"""Command-line interface for nuclearff.

Commands are grouped by concern (``config``, ``sleeper``). Every command reads
its settings from a configuration file so that a run can be reproduced from a
git SHA plus a config, and every command returns an exit code rather than
calling :func:`sys.exit` directly, which keeps them testable.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from nuclearff._version import __version__
from nuclearff.config import default_config, dump_config, load_config
from nuclearff.config.loader import to_yaml
from nuclearff.config.models import NuclearffConfig
from nuclearff.exceptions import NuclearffError
from nuclearff.logging_config import configure_logging
from nuclearff.sleeper import (
    SleeperClient,
    fetch_league_snapshot,
    write_players_table,
    write_snapshot,
)

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
        args: Parsed arguments carrying ``league_id``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    config.paths.ensure()

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        snapshot = fetch_league_snapshot(client, args.league_id)

    target = write_snapshot(snapshot, config.paths.raw_dir)

    print(f"League:   {snapshot.league_name} ({snapshot.metadata.league_id})")
    print(
        f"Season:   {snapshot.league.get('season')} "
        f"status={snapshot.league.get('status')}"
    )
    print(f"Teams:    {snapshot.league.get('total_rosters')}")
    print(f"Snapshot: {target}")

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
