"""Command-line interface for nuclearff.

Commands are grouped by concern (``config``, ``sleeper``, ``ids``, ``report``).
Every command reads its settings from a configuration file so that a run can
be reproduced from a git SHA plus a config, and every command returns an
exit code rather than calling :func:`sys.exit` directly, which keeps them
testable.
"""

from __future__ import annotations

import argparse
import json
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
    crawl_user_network,
    fetch_and_write_all_drafts,
    fetch_and_write_matchups,
    fetch_and_write_projections_range,
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
from nuclearff.sleeper.network import DEFAULT_MAX_HOPS, DEFAULT_MAX_USERS

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
            ``drafts``, ``max_seasons``, and ``max_week``.

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
            or args.drafts
        ):
            db_path = config.paths.cache_dir / "nuclearff.duckdb"
            leagues = walk_league_chain(
                client, args.league_id, max_seasons=args.max_seasons, db_path=db_path
            )

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

        if args.drafts:
            draft_pick_count = fetch_and_write_all_drafts(client, leagues, db_path)

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
        if not (args.history or args.standings or args.matchups or args.transactions):
            print(f"Database: {db_path}")

    if args.drafts:
        print(f"\nDraft picks: {draft_pick_count}")
        if not (
            args.history
            or args.standings
            or args.matchups
            or args.transactions
            or args.roster_players
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


def _cmd_sleeper_fetch_projections(args: argparse.Namespace) -> int:
    """Fetch one or more weeks of player projections and store them in DuckDB.

    Not tied to any one league — see
    :mod:`nuclearff.sleeper.projections`'s module docstring for why this is
    a standalone command rather than a ``fetch-league`` flag.

    A week that fails to fetch is logged and reported, but does not abort
    the rest of the range — its previously-written rows, if any, survive
    untouched (see
    :func:`nuclearff.sleeper.projections.fetch_and_write_projections_range`).

    Args:
        args: Parsed arguments carrying ``season``, ``week``, ``positions``,
            and ``through_week`` (fetches ``week``..``through_week``
            inclusive when set, one Sleeper request per week — a whole
            season's worth without shelling out to this command once per
            week by hand).

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    config.paths.ensure()
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    season = int(args.season)
    last_week = args.through_week if args.through_week is not None else args.week
    if last_week < args.week:
        print(f"--through-week ({last_week}) must be >= --week ({args.week})")
        return EXIT_ERROR

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        per_week_counts = fetch_and_write_projections_range(
            client,
            season,
            range(args.week, last_week + 1),
            db_path,
            positions=tuple(args.positions),
        )

    for week in range(args.week, last_week + 1):
        if week in per_week_counts:
            print(f"Week {week}: {per_week_counts[week]} projections fetched")
        else:
            print(f"Week {week}: failed to fetch, previous rows (if any) untouched")

    print(f"Season:   {season}")
    print(f"Weeks:    {args.week}-{last_week}")
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


def _cmd_sleeper_user_network(args: argparse.Namespace) -> int:
    """Crawl the Sleeper network reachable from a seed user and print a summary.

    See :func:`nuclearff.sleeper.network.crawl_user_network` -- the seed
    user's own leagues, then (by default) every co-member of those leagues
    and their own leagues in turn. Also writes the three result frames
    (leagues, users, memberships) as CSVs so the "detailed stats table"
    survives past the terminal (issue #209).

    Args:
        args: Parsed arguments carrying ``username``, ``season``, ``sport``,
            ``max_hops``, ``max_users``, and ``out_dir``.

    Returns:
        An exit code.
    """
    config = _resolve_config(args)
    config.paths.ensure()

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        network = crawl_user_network(
            client,
            args.username,
            args.season,
            sport=args.sport,
            max_hops=args.max_hops,
            max_users=args.max_users,
        )

    seed = network.users.filter(pl.col("user_id") == network.seed_user_id).row(
        0, named=True
    )
    print(f"User:    {seed['display_name']} ({network.seed_user_id})")
    print(
        f"Network: {network.leagues.height} leagues, "
        f"{network.users.height} users (max_hops={args.max_hops}, "
        f"{args.sport} {args.season})"
    )
    print("\nTop leagues by known overlap:")
    top_leagues = network.leagues.sort("known_member_count", descending=True).head(10)
    for row in top_leagues.iter_rows(named=True):
        print(
            f"  {row['league_id']}  {row['name']}  "
            f"known={row['known_member_count']}/{row['total_rosters']}  "
            f"hop={row['hop']}"
        )

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else config.paths.artifacts_dir
        / "user-network"
        / f"{network.seed_user_id}-{args.season}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    network.leagues.write_csv(out_dir / "leagues.csv")
    network.users.write_csv(out_dir / "users.csv")
    network.memberships.write_csv(out_dir / "memberships.csv")
    print(f"\nWrote {out_dir}/{{leagues,users,memberships}}.csv")

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


def _cmd_report_playoff_bracket(args: argparse.Namespace) -> int:
    """Render a season's winners/losers playoff bracket as PNG trees.

    Reads ``sleeper_playoff_matches`` and ``sleeper_standings`` (written by
    ``sleeper fetch-league --standings``) from the local database.

    Args:
        args: Parsed arguments.

    Returns:
        An exit code.
    """
    import duckdb

    from nuclearff.duckdb_io import read_table_for_league
    from nuclearff.report import render_playoff_brackets

    config = _resolve_config(args)
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    # `league_id` alone already scopes to one season (Sleeper mints a new
    # league_id each year), so pushing just that filter down to SQL --
    # rather than reading every league's rows to discard most of them --
    # covers both tables; the `season` check stays as a cheap client-side
    # sanity check on the now-narrow result, not the real filter.
    with duckdb.connect(str(db_path), read_only=True) as conn:
        matches = (
            read_table_for_league(
                db_path, "sleeper_playoff_matches", [args.league_id], connection=conn
            )
            .filter(pl.col("season") == args.season)
            .to_dicts()
        )
        if not matches:
            print(
                f"No sleeper_playoff_matches rows for league {args.league_id}, season "
                f"{args.season}. Run `sleeper fetch-league --standings` first."
            )
            return EXIT_ERROR

        standings = read_table_for_league(
            db_path, "sleeper_standings", [args.league_id], connection=conn
        ).filter(pl.col("season") == args.season)
    names = dict(zip(standings["roster_id"], standings["display_name"], strict=True))

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else config.paths.artifacts_dir / f"{args.league_id}-{args.season}" / "brackets"
    )
    written = render_playoff_brackets(
        matches, names, out_dir, league_name=args.league_name or ""
    )

    for bracket, path in written.items():
        print(f"{bracket.title()} bracket: {path}")
    return EXIT_OK


def _cached_pick_count(db_path: Path, draft_id: str) -> int:
    """Count already-cached ``sleeper_draft_picks`` rows for one draft.

    Tolerant of a missing database file or table (a draft never fetched
    before) -- returns ``0`` rather than raising, matching "nothing cached
    yet" rather than treating it as an error.

    Args:
        db_path: Path to the DuckDB database file.
        draft_id: Sleeper draft identifier.

    Returns:
        The number of cached pick rows for ``draft_id``.
    """
    import duckdb

    from nuclearff.sleeper.draft import TABLE_NAME

    if not Path(db_path).is_file():
        return 0
    try:
        with duckdb.connect(str(db_path), read_only=True) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE draft_id = ?", [draft_id]
            ).fetchone()
    except duckdb.Error:
        return 0
    return row[0] if row is not None else 0


def _cmd_report_draft_board(args: argparse.Namespace) -> int:
    """Fetch a draft's picks, persist them, and render a snake-order grid.

    **Skips the live picks refetch (issue #150)** when a draft is already
    fully picked (``cached rows == teams * rounds``) -- a completed draft's
    picks never change, so a completed draft is read straight from the
    cache instead of hitting ``get_draft_picks`` again on every render.
    ``get_draft``/``get_users`` are still fetched live either way: neither
    a draft's settings/order/name nor its users are persisted anywhere
    this command can read back (only the picks are), and both are cheap,
    single, non-per-item calls -- not the N-per-render cost this issue is
    about.

    Args:
        args: Parsed arguments carrying ``league_id``, ``draft_id``, ``out``.

    Returns:
        An exit code.
    """
    from nuclearff.duckdb_io import read_table
    from nuclearff.report import render_draft_board
    from nuclearff.sleeper.draft import TABLE_NAME, fetch_and_write_draft_picks

    config = _resolve_config(args)
    config.paths.ensure()
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        draft_id = args.draft_id
        if draft_id is None:
            drafts = client.get_league_drafts(args.league_id)
            if not drafts:
                print(f"No drafts found for league {args.league_id}.")
                return EXIT_ERROR
            draft_id = drafts[0]["draft_id"]

        draft = client.get_draft(draft_id)
        users = client.get_users(args.league_id)

        settings = draft.get("settings") or {}
        teams = settings.get("teams")
        rounds = settings.get("rounds")

        cached = _cached_pick_count(db_path, draft_id)
        if (
            isinstance(teams, int)
            and isinstance(rounds, int)
            and cached >= teams * rounds
        ):
            pick_count = cached
        else:
            pick_count = fetch_and_write_draft_picks(
                client, draft_id, db_path, draft=draft
            )

    if pick_count == 0:
        print(f"No picks made yet in draft {draft_id}.")
        return EXIT_ERROR

    if not isinstance(teams, int) or not isinstance(rounds, int):
        print(f"Draft {draft_id} has no teams/rounds settings; cannot lay out a grid.")
        return EXIT_ERROR

    draft_order = draft.get("draft_order") or {}
    names_by_user = {u.get("user_id"): u.get("display_name") for u in users}
    names = {
        slot: names_by_user.get(user_id) or f"Slot {slot}"
        for user_id, slot in draft_order.items()
        if isinstance(slot, int)
    }

    picks = (
        read_table(db_path, TABLE_NAME)
        .filter(pl.col("draft_id") == draft_id)
        .to_dicts()
    )

    league_name = (draft.get("metadata") or {}).get("name") or args.league_id
    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir
        / f"{args.league_id}-draft-board"
        / f"{draft_id}.png"
    )
    written = render_draft_board(
        picks,
        names,
        out_path,
        teams=teams,
        rounds=rounds,
        title=f"{league_name} — Draft Board",
    )

    print(f"Picks:       {pick_count}")
    print(f"Draft board: {written}")
    return EXIT_OK


def _cmd_report_user_leagues(args: argparse.Namespace) -> int:
    """Render a Sleeper user's leagues for a season as a PNG table.

    Args:
        args: Parsed arguments carrying ``username`` (username or user id),
            ``season``, ``sport``, and ``out``.

    Returns:
        An exit code.
    """
    from nuclearff.report import render_user_leagues_table

    config = _resolve_config(args)
    config.paths.ensure()

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        user = client.get_user(args.username)
        leagues = client.get_user_leagues(
            user["user_id"], args.season, sport=args.sport
        )

    if not leagues:
        print(f"No leagues found for {args.username} in {args.sport} {args.season}.")
        return EXIT_ERROR

    display_name = user.get("display_name") or args.username
    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir
        / f"{user['user_id']}-leagues"
        / f"{args.season}.png"
    )
    written = render_user_leagues_table(
        leagues,
        out_path,
        title=f"{display_name}'s Leagues",
        subtitle=f"{len(leagues)} leagues  |  {args.sport}  |  {args.season}",
        cache_dir=config.paths.cache_dir / "avatars",
    )

    print(f"User:    {display_name} ({user['user_id']})")
    print(f"Leagues: {written}")
    return EXIT_OK


def _cmd_report_user_network(args: argparse.Namespace) -> int:
    """Crawl the Sleeper network reachable from a seed user and render a PNG.

    Args:
        args: Parsed arguments carrying ``username``, ``season``, ``sport``,
            ``max_hops``, ``max_users``, and ``out``.

    Returns:
        An exit code.
    """
    from nuclearff.report.user_network import render_user_network

    config = _resolve_config(args)
    config.paths.ensure()

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        network = crawl_user_network(
            client,
            args.username,
            args.season,
            sport=args.sport,
            max_hops=args.max_hops,
            max_users=args.max_users,
        )

    seed = network.users.filter(pl.col("user_id") == network.seed_user_id).row(
        0, named=True
    )
    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir
        / f"{network.seed_user_id}-network"
        / f"{args.season}.png"
    )
    written = render_user_network(
        network, out_path, title=f"{seed['display_name']}'s Network"
    )

    print(f"User:    {seed['display_name']} ({network.seed_user_id})")
    print(f"Network: {network.leagues.height} leagues, {network.users.height} users")
    print(f"Wrote:   {written}")
    return EXIT_OK


def _current_league_managers(standings: pl.DataFrame, league_id: str) -> set[str]:
    """The real manager display names rostered in one specific season.

    ``sleeper_standings`` has one row per ``(league_id, roster_id)``, and
    each season has its own distinct ``league_id`` (every multi-season
    fetch in this project already keys off that) -- so filtering to a
    single ``league_id`` already gives exactly that season's real roster,
    with no new Sleeper fetching. Backs the ``all_users=False`` default
    (GitHub Issue 87): every multi-manager report defaulted to *every*
    manager who has ever appeared in the league's history, an accident of
    "don't filter" rather than a deliberate choice, since a manager's
    display name was pulled from the full multi-season ``sleeper_standings``
    table with no ``league_id`` filter at all.

    Args:
        standings: ``sleeper_standings`` rows, e.g.
            :func:`nuclearff.duckdb_io.read_table`'s output for that table.
        league_id: The specific season's league id to restrict to.

    Returns:
        Real display names for that season. A roster with no resolvable
        display name is excluded, matching every other manager-identity
        lookup in this project.
    """
    return {
        name
        for name in standings.filter(pl.col("league_id") == league_id)[
            "display_name"
        ].to_list()
        if name
    }


def _densify_trade_matrix(
    matrix: pl.DataFrame, all_managers: list[str]
) -> pl.DataFrame:
    """Expand a pairwise trade matrix to include every manager in ``all_managers``.

    :func:`nuclearff.sleeper.trades.pairwise_trade_matrix` is dense only over
    managers who appear in trade data (by its own module's design — see its
    docstring); a manager with zero trades needs an explicit all-zero
    row/column added here, same join-in-the-CLI pattern already used for
    :func:`render_trades_by_manager`'s ``counts`` above.

    Args:
        matrix: Output of ``pairwise_trade_matrix``, possibly missing rows
            and columns for managers who never traded.
        all_managers: The full manager roster to densify against.

    Returns:
        A matrix with one row and one column per manager in ``all_managers``,
        ``0`` for any pair not present in ``matrix``.
    """
    existing_rows = {row["manager"]: row for row in matrix.to_dicts()}
    rows = []
    for row_manager in all_managers:
        existing_row = existing_rows.get(row_manager)
        row: dict[str, object] = {"manager": row_manager}
        for col_manager in all_managers:
            if row_manager == col_manager:
                row[col_manager] = 0
            elif existing_row is not None and col_manager in existing_row:
                row[col_manager] = existing_row[col_manager]
            else:
                row[col_manager] = 0
        rows.append(row)
    return pl.DataFrame(rows)


def _densify_trades_by_season(
    by_season: pl.DataFrame, standings: pl.DataFrame
) -> pl.DataFrame:
    """Expand ``trades_by_season`` to every season each manager actually rostered.

    :func:`nuclearff.sleeper.trades.trades_by_season` is dense only over
    seasons with at least one trade (by its own module's design — see its
    docstring). Joining against ``sleeper_standings`` here fills an explicit
    ``0`` for a season the manager rostered but didn't trade in, while
    leaving no row at all for a season before/after the manager was
    actually in the league — a real gap in the resulting line chart, not a
    misleading connect-the-dots across seasons that never happened for
    them.

    Args:
        by_season: Output of ``trades_by_season``.
        standings: The ``sleeper_standings`` table (needs ``season`` and
            ``display_name``).

    Returns:
        One row per (manager, season) the manager actually rostered:
        ``manager``, ``season``, ``trades`` (``0`` if they didn't trade
        that season).
    """
    manager_seasons = (
        standings.select(pl.col("display_name").alias("manager"), "season")
        .filter(pl.col("manager").is_not_null())
        .unique()
    )
    return (
        manager_seasons.join(by_season, on=["manager", "season"], how="left")
        .with_columns(pl.col("trades").fill_null(0))
        .sort(["manager", "season"])
    )


def _densify_manager_season_matrix(
    by_season: pl.DataFrame, all_managers: list[str], all_seasons: list[int]
) -> pl.DataFrame:
    """Pivot ``trades_by_season`` into a dense manager x season matrix.

    Unlike :func:`_densify_trades_by_season` (which deliberately leaves a
    gap for a season a manager didn't roster, for a line chart where
    connecting across it would be misleading), issue #49's heatmap wants
    every manager x season cell filled — a season before/after a manager
    was in the league renders as the same explicit ``0`` as a season they
    rostered but didn't trade in, since a heatmap has no "connect the
    dots" failure mode to avoid.

    Args:
        by_season: Output of ``trades_by_season`` (sparse — only
            combinations with at least one trade).
        all_managers: The full manager roster.
        all_seasons: Every season in the league's history.

    Returns:
        One ``manager`` column plus one column per season in
        ``all_seasons`` (as a string column name), ``0`` for any
        combination not present in ``by_season``.
    """
    lookup = {
        (row["manager"], row["season"]): row["trades"] for row in by_season.to_dicts()
    }
    rows = []
    for manager in all_managers:
        row: dict[str, object] = {"manager": manager}
        for season in all_seasons:
            row[str(season)] = lookup.get((manager, season), 0)
        rows.append(row)
    return pl.DataFrame(rows)


def _cmd_report_trades(args: argparse.Namespace) -> int:
    """Render the manager trade network from stored trade history.

    Reads ``sleeper_transactions`` (written by ``sleeper fetch-league
    --transactions``) for trade data. Also reads ``sleeper_standings``
    (written by ``--standings``) for the manager roster, so a manager with
    zero trades still appears rather than being silently absent — if that
    table doesn't exist yet, falls back to only the managers who appear in
    trade data, with a warning.

    Args:
        args: Parsed arguments carrying ``league_id``, ``out_dir``, and
            ``all_users`` (default ``False``: only managers rostered in
            ``league_id``'s own season; ``True``: every manager across the
            league's full history, this command's behavior before issue
            #87).

    Returns:
        An exit code.
    """
    from nuclearff.duckdb_io import read_table
    from nuclearff.report import (
        render_chord_diagram,
        render_cumulative_trades,
        render_manager_pair_leaderboard,
        render_manager_season_heatmap,
        render_trade_leaderboard,
        render_trade_network,
        render_trade_partner_diversity,
        render_trades_by_manager,
        render_trades_heatmap,
        render_trades_over_time,
    )
    from nuclearff.sleeper.trades import (
        cumulative_trade_counts,
        load_trades,
        manager_trade_counts,
        pairwise_trade_matrix,
        top_manager_pairs,
        total_trades_by_season,
        trades_by_season,
    )

    config = _resolve_config(args)
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    try:
        edges = load_trades(db_path)
    except StorageError:
        print(
            "No sleeper_transactions table found. Run "
            "`nuclearff sleeper fetch-league --transactions` first."
        )
        return EXIT_ERROR

    try:
        standings = read_table(db_path, "sleeper_standings")
        if args.all_users:
            all_managers = sorted(
                {name for name in standings["display_name"].to_list() if name}
            )
        else:
            all_managers = sorted(_current_league_managers(standings, args.league_id))
            # Every visualization on this page is either a per-manager
            # aggregate or a pairwise structure (heatmap/network/chord/pair
            # leaderboard) with no way to render an edge to a manager who
            # isn't a modeled node -- filtering `edges` once, up front,
            # keeps every downstream computation consistent with a single
            # rule ("a trade counts when `all_users=False` only if both
            # sides are current") rather than patching each visualization's
            # output separately. Real trade-off, stated directly rather
            # than silently: a current manager's own total can be smaller
            # than their real all-time count if some of their trades were
            # with a manager who has since left the league.
            edges = edges.filter(
                pl.col("manager_a").is_in(all_managers)
                & pl.col("manager_b").is_in(all_managers)
            )
    except StorageError:
        logger.warning(
            "sleeper_standings not found — a manager with zero trades won't "
            "appear, and --all-users has no effect. Run `sleeper fetch-league "
            "--standings` for the full roster."
        )
        all_managers = None
        standings = None

    counts = manager_trade_counts(edges)
    if all_managers is None:
        all_managers = counts["manager"].to_list()

    # Densified over every column manager_trade_counts produces, not just
    # `trades` -- the leaderboard table needs unique_partners/
    # most_frequent_partner/trades_with_partner too. A zero-trade manager
    # never appears in the raw output at all, so those three columns are
    # null after the join for them; render_trade_leaderboard displays that
    # as "—", not as an error.
    counts = (
        pl.DataFrame({"manager": all_managers})
        .join(
            counts.select(
                "manager",
                "trades",
                "unique_partners",
                "most_frequent_partner",
                "trades_with_partner",
            ),
            on="manager",
            how="left",
        )
        .with_columns(
            pl.col("trades").fill_null(0),
            pl.col("unique_partners").fill_null(0),
            pl.col("trades_with_partner").fill_null(0),
        )
    )

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else config.paths.artifacts_dir / f"{args.league_id}-trades"
    )
    out_path = render_trades_by_manager(
        counts.select("manager", "trades"), out_dir / "trades_by_manager.png"
    )

    matrix = _densify_trade_matrix(pairwise_trade_matrix(edges), all_managers)
    heatmap_path = render_trades_heatmap(matrix, out_dir / "trades_heatmap.png")
    network_path = render_trade_network(
        counts.select("manager", "trades"), matrix, out_dir / "trade_network.png"
    )
    leaderboard_path = render_trade_leaderboard(
        counts, out_dir / "trade_leaderboard.png"
    )
    pair_leaderboard_path = render_manager_pair_leaderboard(
        top_manager_pairs(edges), out_dir / "manager_pair_leaderboard.png"
    )

    by_season_raw = trades_by_season(edges)
    by_season = (
        _densify_trades_by_season(by_season_raw, standings)
        if standings is not None
        else by_season_raw
    )
    over_time_path = render_trades_over_time(
        by_season, total_trades_by_season(edges), out_dir / "trades_over_time.png"
    )

    all_seasons = sorted(
        standings["season"].unique().to_list()
        if standings is not None
        else by_season_raw["season"].unique().to_list()
    )
    season_matrix = _densify_manager_season_matrix(
        by_season_raw, all_managers, all_seasons
    )
    season_heatmap_path = render_manager_season_heatmap(
        season_matrix, out_dir / "manager_season_heatmap.png"
    )

    cumulative_path = render_cumulative_trades(
        cumulative_trade_counts(edges), out_dir / "cumulative_trades.png"
    )
    diversity_path = render_trade_partner_diversity(
        counts.select("manager", "trades", "unique_partners"),
        out_dir / "trade_partner_diversity.png",
    )
    chord_path = render_chord_diagram(
        counts.select("manager", "trades"), matrix, out_dir / "chord_diagram.png"
    )

    print(f"Managers:                 {counts.height}")
    print(f"Trades by manager:        {out_path}")
    print(f"Trades heatmap:           {heatmap_path}")
    print(f"Trade network:            {network_path}")
    print(f"Trade leaderboard:        {leaderboard_path}")
    print(f"Manager-pair leaderboard: {pair_leaderboard_path}")
    print(f"Manager x season heatmap: {season_heatmap_path}")
    print(f"Trades over time:         {over_time_path}")
    print(f"Cumulative trades:        {cumulative_path}")
    print(f"Trade-partner diversity:  {diversity_path}")
    print(f"Chord diagram:            {chord_path}")
    return EXIT_OK


def _cmd_report_wins(args: argparse.Namespace) -> int:
    """Render cumulative wins over time from stored matchup/standings history.

    Reads ``sleeper_matchups`` (``sleeper fetch-league --matchups``) and
    ``sleeper_standings`` (``--standings``) -- both required, unlike
    ``report trades``'s optional standings fallback, since a manager's
    display name and a per-week result both come from these two tables.

    Args:
        args: Parsed arguments carrying ``league_id``, ``out``, and
            ``all_users`` (default ``False``: only managers rostered in
            ``league_id``'s own season; ``True``: every manager across the
            league's full history, this command's behavior before issue
            #87).

    Returns:
        An exit code.
    """
    import duckdb

    from nuclearff.duckdb_io import read_table
    from nuclearff.report import render_cumulative_wins
    from nuclearff.sleeper.wins import cumulative_wins, weekly_results

    config = _resolve_config(args)
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    if not db_path.is_file():
        print(
            "No sleeper_matchups table found. Run "
            "`nuclearff sleeper fetch-league --matchups` first."
        )
        return EXIT_ERROR

    with duckdb.connect(str(db_path), read_only=True) as conn:
        try:
            matchups = read_table(db_path, "sleeper_matchups", connection=conn)
        except StorageError:
            print(
                "No sleeper_matchups table found. Run "
                "`nuclearff sleeper fetch-league --matchups` first."
            )
            return EXIT_ERROR

        try:
            standings = read_table(db_path, "sleeper_standings", connection=conn)
        except StorageError:
            print(
                "No sleeper_standings table found. Run "
                "`nuclearff sleeper fetch-league --standings` first."
            )
            return EXIT_ERROR

        # Avatars are cosmetic (a missing/placeholder image, never a hard
        # failure) and only ever get written as a side effect of a standings
        # fetch (issue #150) -- a cache from before that shipped, or one that
        # simply hasn't re-fetched since, has no such table yet.
        try:
            avatars_table = read_table(db_path, "sleeper_user_avatars", connection=conn)
        except StorageError:
            avatars_table = None

    cumulative = cumulative_wins(weekly_results(matchups), standings)
    if not args.all_users:
        # A win/loss is a personal fact about the manager who earned it, not
        # a pairwise structure the way a trade is -- their full historical
        # count (including games against a since-departed opponent) stays
        # intact, only which *managers* get a line changes.
        current = _current_league_managers(standings, args.league_id)
        cumulative = cumulative.filter(pl.col("manager").is_in(current))
    if cumulative.height == 0:
        print("No completed matchups found for any manager.")
        return EXIT_ERROR

    # One owner_id per manager -- their most recent season's -- resolved
    # against the avatars already persisted by a standings fetch (issue
    # #150), not a live per-manager Sleeper call. Only for managers
    # actually being rendered: with `all_users=False`, a departed manager's
    # avatar is never looked up at all.
    rendered_managers = cumulative["manager"].unique().to_list()
    owner_ids = (
        standings.filter(
            pl.col("display_name").is_not_null()
            & pl.col("display_name").is_in(rendered_managers)
        )
        .sort("season", descending=True)
        .group_by("display_name", maintain_order=True)
        .agg(pl.col("owner_id").first())
    )
    avatar_by_owner: dict[str, str | None] = (
        dict(zip(avatars_table["owner_id"], avatars_table["avatar"], strict=True))
        if avatars_table is not None and avatars_table.height
        else {}
    )
    avatar_ids: dict[str, str | None] = {
        row["display_name"]: avatar_by_owner.get(row["owner_id"])
        for row in owner_ids.iter_rows(named=True)
    }

    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir / f"{args.league_id}-wins.png"
    )
    written = render_cumulative_wins(
        cumulative,
        avatar_ids,
        out_path,
        cache_dir=config.paths.cache_dir / "avatars",
    )

    print(f"Managers: {cumulative['manager'].n_unique()}")
    print(f"Wins:     {written}")
    return EXIT_OK


def _cmd_report_draft_order(args: argparse.Namespace) -> int:
    """Render a manager's historical draft-order table.

    Reads ``sleeper_draft_picks`` (``sleeper fetch-league --drafts``) and
    ``sleeper_standings`` (``--standings``) -- both required, same posture
    as ``report wins``.

    Args:
        args: Parsed arguments carrying ``league_id``, ``out``, and
            ``all_users`` (default ``False``: only managers rostered in
            ``league_id``'s own season; ``True``: every manager across the
            league's full history, this command's behavior before issue
            #87).

    Returns:
        An exit code.
    """
    import duckdb

    from nuclearff.duckdb_io import read_table
    from nuclearff.report import render_draft_order_table
    from nuclearff.sleeper.draft import draft_order_stats

    config = _resolve_config(args)
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    if not db_path.is_file():
        print(
            "No sleeper_draft_picks table found. Run "
            "`nuclearff sleeper fetch-league --drafts` first."
        )
        return EXIT_ERROR

    with duckdb.connect(str(db_path), read_only=True) as conn:
        try:
            picks = read_table(db_path, "sleeper_draft_picks", connection=conn)
        except StorageError:
            print(
                "No sleeper_draft_picks table found. Run "
                "`nuclearff sleeper fetch-league --drafts` first."
            )
            return EXIT_ERROR

        try:
            standings = read_table(db_path, "sleeper_standings", connection=conn)
        except StorageError:
            print(
                "No sleeper_standings table found. Run "
                "`nuclearff sleeper fetch-league --standings` first."
            )
            return EXIT_ERROR

    stats = draft_order_stats(picks, standings)
    if not args.all_users:
        # A draft position is a personal fact about the manager who held
        # it, not a pairwise structure -- their full historical average
        # stays intact, only which managers get a row changes.
        current = _current_league_managers(standings, args.league_id)
        stats = stats.filter(pl.col("manager").is_in(current))
    if stats.height == 0:
        print("No resolvable round-1 draft picks found for any manager.")
        return EXIT_ERROR

    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir / f"{args.league_id}-draft-order.png"
    )
    written = render_draft_order_table(stats, out_path)

    print(f"Managers:    {stats.height}")
    print(f"Draft order: {written}")
    return EXIT_OK


def _waiver_budgets_by_league(
    leagues: pl.DataFrame, league_ids: list[str]
) -> dict[str, int]:
    """``league_id`` -> that season's real FAAB budget, from raw ``settings``.

    A ``league_id`` whose ``settings`` has no real ``waiver_budget`` (a
    priority-waiver league, not FAAB) is simply absent from the returned
    mapping -- :func:`nuclearff.archive.on_this_day.transaction_summary_rows`
    treats that as "no FAAB note," not zero.

    Args:
        leagues: ``sleeper_leagues`` rows (``league_id``, ``settings``).
        league_ids: Restrict to these seasons.

    Returns:
        ``league_id`` -> ``waiver_budget`` for every season where the raw
        ``settings`` JSON has a truthy one.
    """
    budgets: dict[str, int] = {}
    rows = leagues.filter(pl.col("league_id").is_in(league_ids)).to_dicts()
    for row in rows:
        settings = json.loads(row.get("settings") or "{}")
        budget = settings.get("waiver_budget")
        if budget:
            budgets[row["league_id"]] = budget
    return budgets


def _cmd_report_on_this_day(args: argparse.Namespace) -> int:
    """Render transactions that happened on a real calendar date.

    Reads ``sleeper_transactions`` (``sleeper fetch-league --transactions``),
    scoped to ``league_id``'s full multi-season chain via
    ``sleeper_leagues`` (``sleeper fetch-league --history``) when that table
    exists -- falls back to just ``league_id``'s own season, with a warning,
    otherwise. Player names in the rendered summary resolve from
    ``sleeper_players`` (``sleeper fetch-players``) if present, else fall
    back to the raw Sleeper player id. A waiver add gets a real
    ``$<bid> ($<budget>)`` note when that season's league actually used FAAB
    (``sleeper_leagues.settings.waiver_budget``), nothing otherwise.

    Args:
        args: Parsed arguments carrying ``league_id``, ``date`` (ISO
            ``YYYY-MM-DD``, default today), and ``out``.

    Returns:
        An exit code.
    """
    from datetime import date as date_cls

    from nuclearff.archive.on_this_day import (
        transaction_summary_rows,
        transactions_on_this_day,
    )
    from nuclearff.duckdb_io import read_table
    from nuclearff.report import render_on_this_day_table
    from nuclearff.sleeper.leagues import league_chain_ids

    config = _resolve_config(args)
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    try:
        transactions = read_table(db_path, "sleeper_transactions")
    except StorageError:
        print(
            "No sleeper_transactions table found. Run "
            "`nuclearff sleeper fetch-league --transactions` first."
        )
        return EXIT_ERROR

    waiver_budgets: dict[str, int] = {}
    try:
        leagues = read_table(db_path, "sleeper_leagues")
        chain_ids = league_chain_ids(leagues, args.league_id)
        transactions = transactions.filter(pl.col("league_id").is_in(chain_ids))
        waiver_budgets = _waiver_budgets_by_league(leagues, chain_ids)
    except StorageError:
        transactions = transactions.filter(pl.col("league_id") == args.league_id)
        print(
            "Warning: no sleeper_leagues table found, scoping to this "
            "season only (not the full franchise history). Run "
            "`nuclearff sleeper fetch-league --history` for multi-season "
            "on-this-day coverage."
        )

    today = date_cls.fromisoformat(args.date) if args.date else date_cls.today()
    matches = transactions_on_this_day(transactions, today)

    players = None
    try:
        players = read_table(db_path, "sleeper_players")
    except StorageError:
        pass

    summary = transaction_summary_rows(matches, players, waiver_budgets)

    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir
        / f"{args.league_id}-on-this-day-{today:%m-%d}.png"
    )
    written = render_on_this_day_table(summary, today, out_path)

    print(f"Date:        {today:%B %-d}")
    print(f"Matches:     {summary.height}")
    print(f"On this day: {written}")
    return EXIT_OK


def _read_performance_tables(
    db_path: Path, *, fetch_projections_hint: str, league_id: str | None = None
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame] | None:
    """Read the four tables both performance report commands need.

    Shared by :func:`_cmd_report_performance` and
    :func:`_cmd_report_season_performance` so the required-table list and
    error messages can't drift between the two.

    All four reads share one DuckDB connection rather than each opening its
    own (issue #149). ``sleeper_matchups``/``sleeper_standings`` are also
    filtered to ``league_id`` in SQL when it's already known -- only
    :func:`_cmd_report_performance` can pass it: :func:`_cmd_report_season_performance`
    doesn't know which ``league_id`` a season resolves to until *after* it
    has seen every ``league_id`` in the full ``sleeper_matchups`` table, so it
    must keep reading unfiltered (``league_id=None``).
    ``sleeper_players``/``sleeper_projections`` carry no ``league_id`` column
    at all (global across leagues), so they're always read in full.

    Args:
        db_path: Path to the DuckDB database file.
        fetch_projections_hint: The exact ``sleeper fetch-projections``
            invocation to suggest if that table is missing -- the weekly
            and season commands suggest a different one.
        league_id: Restrict ``sleeper_matchups``/``sleeper_standings`` to
            this league, pushed down to SQL. ``None`` reads every league
            already cached, for the season command's own resolution step.

    Returns:
        ``(matchups, standings, players, projections)`` in that order, or
        ``None`` if any table is missing (an explanatory message is
        printed before returning).
    """
    import duckdb

    from nuclearff.duckdb_io import read_table, read_table_for_league

    db_path = Path(db_path)
    if not db_path.is_file():
        print(
            "No sleeper_matchups table found. Run "
            "`nuclearff sleeper fetch-league --matchups` first."
        )
        return None

    with duckdb.connect(str(db_path), read_only=True) as conn:

        def _read(table: str) -> pl.DataFrame:
            if league_id is not None and table in {
                "sleeper_matchups",
                "sleeper_standings",
            }:
                return read_table_for_league(
                    db_path, table, [league_id], connection=conn
                )
            return read_table(db_path, table, connection=conn)

        try:
            matchups = _read("sleeper_matchups")
        except StorageError:
            print(
                "No sleeper_matchups table found. Run "
                "`nuclearff sleeper fetch-league --matchups` first."
            )
            return None

        try:
            standings = _read("sleeper_standings")
        except StorageError:
            print(
                "No sleeper_standings table found. Run "
                "`nuclearff sleeper fetch-league --standings` first."
            )
            return None

        try:
            players = _read("sleeper_players")
        except StorageError:
            print(
                "No sleeper_players table found. Run "
                "`nuclearff sleeper fetch-players` first."
            )
            return None

        try:
            projections = _read("sleeper_projections")
        except StorageError:
            print(
                f"No sleeper_projections table found. Run {fetch_projections_hint} "
                "first."
            )
            return None

    return matchups, standings, players, projections


def _cmd_report_performance(args: argparse.Namespace) -> int:
    """Render a weekly over/underperformer table: actual vs. projected points.

    Reads ``sleeper_matchups`` (``sleeper fetch-league --matchups``),
    ``sleeper_standings`` (``--standings``), ``sleeper_players``
    (``sleeper fetch-players``), and ``sleeper_projections`` (``sleeper
    fetch-projections``) -- all four required. The league's scoring rules
    are fetched live via a single ``get_league`` call rather than read from
    a possibly-stale ``configs/leagues/`` YAML, the same live-fetch posture
    ``report auction-board`` already takes for anything scoring-sensitive.

    Args:
        args: Parsed arguments carrying ``league_id``, ``week``, ``out``,
            ``top_n``, and ``all_players`` (default ``False``: only a
            roster's actual starters; ``True``: bench players too).

    Returns:
        An exit code.
    """
    from nuclearff.config.league import league_config_from_sleeper
    from nuclearff.report import render_weekly_performance_table
    from nuclearff.sleeper.performance import weekly_actuals, weekly_performance

    config = _resolve_config(args)
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    tables = _read_performance_tables(
        db_path,
        fetch_projections_hint=(
            f"`nuclearff sleeper fetch-projections --season <season> "
            f"--week {args.week}`"
        ),
        league_id=args.league_id,
    )
    if tables is None:
        return EXIT_ERROR
    matchups, standings, players, projections = tables

    actuals = weekly_actuals(matchups, standings, args.league_id, args.week)
    if actuals.height == 0:
        print(
            f"No completed matchups found for league {args.league_id} week {args.week}."
        )
        return EXIT_ERROR

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        league_json = client.get_league(args.league_id)
    league_config = league_config_from_sleeper(league_json)

    performance = weekly_performance(
        actuals,
        projections,
        players,
        league_config.scoring,
        starters_only=not args.all_players,
    )
    if performance.height == 0:
        print(
            "No players with both a completed result and a Sleeper "
            "projection were found for that week."
        )
        return EXIT_ERROR

    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir
        / f"{args.league_id}-week{args.week}-performance.png"
    )
    written = render_weekly_performance_table(
        performance, out_path, week=args.week, top_n=args.top_n
    )

    print(f"Players:  {performance.height}")
    print(f"Report:   {written}")
    return EXIT_OK


def _cmd_report_season_performance(args: argparse.Namespace) -> int:
    """Render a season over/underperformer table: avg actual vs. avg projected points.

    Unlike ``report performance`` (which takes a specific ``league_id``,
    since the single ongoing week's league_id is usually already known),
    this takes ``--season`` directly and, when exactly one league_id
    fetched that season, resolves it from ``sleeper_matchups`` -- every
    multi-season fetch in this project already keys each season's rows by
    that season's own distinct ``league_id`` (the same fact
    ``_current_league_managers`` relies on), so no positional ``league_id``
    argument is needed for the common case. An account tracking more than
    one concurrent league in the same season (confirmed live 2026-09-10)
    makes that ambiguous; pass ``--league-id`` to disambiguate.

    Reads ``sleeper_matchups``, ``sleeper_standings``, ``sleeper_players``,
    and ``sleeper_projections`` -- all four required, same as ``report
    performance``. ``sleeper_projections`` needs every week of the season
    already fetched (``sleeper fetch-projections --season --week 1
    --through-week <N>``) -- a week missing from that table simply
    contributes no rows for any player, the same "never guess" inner-join
    posture :func:`nuclearff.sleeper.performance.weekly_performance`
    already takes for a single week.

    Prints a warning (does not block) when ``--season`` matches Sleeper's
    own current season, since an in-progress week's not-yet-played players
    read as a real ``0.0`` actual, skewing the averages -- see
    :func:`nuclearff.sleeper.performance.season_summary`'s docstring for
    why that ambiguity has no per-row guard the way the weekly report does.

    Args:
        args: Parsed arguments carrying ``season``, ``league_id`` (optional
            disambiguator), ``out``, ``top_n``, ``min_games``, and
            ``all_players`` (default ``False``: only a roster's actual
            starters; ``True``: bench players too).

    Returns:
        An exit code.
    """
    from nuclearff.config.league import league_config_from_sleeper
    from nuclearff.report import render_season_performance_table
    from nuclearff.sleeper.performance import (
        season_actuals,
        season_summary,
        weekly_performance,
    )

    config = _resolve_config(args)
    db_path = config.paths.cache_dir / "nuclearff.duckdb"

    tables = _read_performance_tables(
        db_path,
        fetch_projections_hint=(
            f"`nuclearff sleeper fetch-projections --season {args.season} "
            f"--week 1 --through-week <N>`"
        ),
    )
    if tables is None:
        return EXIT_ERROR
    matchups, standings, players, projections = tables

    season_league_ids = sorted(
        matchups.filter(pl.col("season") == args.season)["league_id"].unique().to_list()
    )
    if not season_league_ids:
        print(f"No sleeper_matchups rows found for season {args.season}.")
        return EXIT_ERROR
    if args.league_id is not None:
        if args.league_id not in season_league_ids:
            print(
                f"league_id {args.league_id} has no sleeper_matchups rows "
                f"for season {args.season}. Leagues found for that season: "
                f"{season_league_ids}."
            )
            return EXIT_ERROR
        league_id = args.league_id
    elif len(season_league_ids) > 1:
        # A real, previously-unhandled case: an account tracking more than
        # one concurrent league in the same season (confirmed live
        # 2026-09-10 -- three real leagues on one account, all season
        # 2026) makes "one league per season" false, the assumption this
        # command's whole no-positional-league_id design rested on.
        print(
            f"Season {args.season} spans more than one league_id in "
            f"sleeper_matchups ({season_league_ids}) -- pass --league-id "
            "to pick one."
        )
        return EXIT_ERROR
    else:
        league_id = season_league_ids[0]

    actuals = season_actuals(matchups, standings, league_id)
    if actuals.height == 0:
        print(f"No completed matchups found for season {args.season}.")
        return EXIT_ERROR

    with SleeperClient(cache_dir=config.paths.cache_dir) as client:
        league_json = client.get_league(league_id)
        state = client.get_state()
    league_config = league_config_from_sleeper(league_json)

    # A season this project can't yet know is fully complete: Sleeper's own
    # `state.season` is still this one. Averaging in an in-progress week is
    # the same real problem `report performance`'s own Underperformers list
    # already guards against for a single week (a not-yet-played game reads
    # as a real `0.0` actual, indistinguishable from a genuine bust) --
    # season_summary has no per-row guard for it (a completed season has no
    # such rows to begin with), so this is a warning, not a silent average.
    if str(args.season) == str(state.get("season")):
        print(
            f"Warning: season {args.season} is Sleeper's current season "
            f"(week {state.get('week')}, {state.get('season_type')}) -- "
            "any week that hasn't finished yet will show a real 0.0 actual "
            "point total, which this report can't distinguish from a "
            "genuine bust, and that will skew the averages below."
        )

    performance = weekly_performance(
        actuals,
        projections,
        players,
        league_config.scoring,
        starters_only=not args.all_players,
    )
    if performance.height == 0:
        print(
            "No players with both a completed result and a Sleeper "
            "projection were found for that season."
        )
        return EXIT_ERROR

    summary = season_summary(performance, min_games=args.min_games)
    if summary.height == 0:
        print(
            f"No player reached --min-games {args.min_games} for season {args.season}."
        )
        return EXIT_ERROR

    out_path = (
        Path(args.out)
        if args.out
        else config.paths.artifacts_dir
        / f"{league_id}-{args.season}-season-performance.png"
    )
    written = render_season_performance_table(
        summary, out_path, season=args.season, top_n=args.top_n
    )

    print(f"League:   {league_id}")
    print(f"Players:  {summary.height}")
    print(f"Report:   {written}")
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
        "--drafts",
        action="store_true",
        help=(
            "Also fetch every draft per season (implies --history) and "
            "write picks to DuckDB"
        ),
    )
    fetch.add_argument(
        "--max-seasons",
        type=int,
        default=DEFAULT_MAX_SEASONS,
        help=(
            f"Maximum seasons to walk when --history, --standings, "
            f"--matchups, --transactions, --roster-players, or --drafts is "
            f"set (default: {DEFAULT_MAX_SEASONS})"
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

    fetch_projections = sleeper_commands.add_parser(
        "fetch-projections",
        help="Fetch one or more weeks of player projections into a local DuckDB table",
    )
    fetch_projections.add_argument(
        "--season", required=True, help="Season year, e.g. 2026"
    )
    fetch_projections.add_argument(
        "--week", required=True, type=int, help="First (or only) week number"
    )
    fetch_projections.add_argument(
        "--through-week",
        type=int,
        default=None,
        help="Fetch --week..--through-week inclusive (default: just --week)",
    )
    fetch_projections.add_argument(
        "--positions",
        nargs="+",
        default=["QB", "RB", "WR", "TE"],
        help="Positions to fetch (default: QB RB WR TE)",
    )
    fetch_projections.set_defaults(func=_cmd_sleeper_fetch_projections)

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

    user_network = sleeper_commands.add_parser(
        "user-network",
        help="Crawl the Sleeper network reachable from a seed user",
    )
    user_network.add_argument("username", help="Sleeper username or user id")
    user_network.add_argument("--season", required=True, help="Season year, e.g. 2026")
    user_network.add_argument("--sport", default="nfl", help="Sport key (default: nfl)")
    user_network.add_argument(
        "--max-hops",
        type=int,
        default=DEFAULT_MAX_HOPS,
        help=(
            "Rounds of user -> co-members -> their leagues beyond the "
            f"seed's own leagues (default: {DEFAULT_MAX_HOPS}). Each extra "
            "hop multiplies the API call count."
        ),
    )
    user_network.add_argument(
        "--max-users",
        type=int,
        default=DEFAULT_MAX_USERS,
        help=f"Hard cap on discovered users (default: {DEFAULT_MAX_USERS})",
    )
    user_network.add_argument(
        "--out-dir",
        default=None,
        help=(
            "Output directory for leagues/users/memberships CSVs "
            "(default: <artifacts>/user-network/<user_id>-<season>/)"
        ),
    )
    user_network.set_defaults(func=_cmd_sleeper_user_network)

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

    playoff_bracket = report_commands.add_parser(
        "playoff-bracket",
        help="Render a season's winners/losers playoff bracket as PNG trees",
    )
    playoff_bracket.add_argument("league_id", help="Sleeper league identifier")
    playoff_bracket.add_argument(
        "--season", type=int, required=True, help="Season to render (e.g. 2025)"
    )
    playoff_bracket.add_argument(
        "--league-name", default=None, help="Used in each figure's title"
    )
    playoff_bracket.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: <artifacts>/<league_id>-<season>/brackets)",
    )
    playoff_bracket.set_defaults(func=_cmd_report_playoff_bracket)

    draft_board = report_commands.add_parser(
        "draft-board",
        help="Render a draft as a snake-order grid of position-colored pick cards",
    )
    draft_board.add_argument("league_id", help="Sleeper league identifier")
    draft_board.add_argument(
        "--draft-id",
        default=None,
        help="Draft to render (default: the league's most recent draft)",
    )
    draft_board.add_argument(
        "--out",
        default=None,
        help=(
            "Output PNG path "
            "(default: <artifacts>/<league_id>-draft-board/<draft_id>.png)"
        ),
    )
    draft_board.set_defaults(func=_cmd_report_draft_board)

    all_users_help = (
        "Include every manager across the league's full history, not just "
        "those rostered in league_id's own season (default: current "
        "members only)"
    )

    trades = report_commands.add_parser(
        "trades",
        help="Render the manager trade network from stored trade history",
    )
    trades.add_argument("league_id", help="Sleeper league identifier")
    trades.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: <artifacts>/<league_id>-trades)",
    )
    trades.add_argument("--all-users", action="store_true", help=all_users_help)
    trades.set_defaults(func=_cmd_report_trades)

    wins = report_commands.add_parser(
        "wins",
        help="Render cumulative wins over time from stored matchup history",
    )
    wins.add_argument("league_id", help="Sleeper league identifier")
    wins.add_argument(
        "--out",
        default=None,
        help="Output PNG path (default: <artifacts>/<league_id>-wins.png)",
    )
    wins.add_argument("--all-users", action="store_true", help=all_users_help)
    wins.set_defaults(func=_cmd_report_wins)

    draft_order = report_commands.add_parser(
        "draft-order",
        help="Render a manager's historical draft-order table",
    )
    draft_order.add_argument("league_id", help="Sleeper league identifier")
    draft_order.add_argument(
        "--out",
        default=None,
        help="Output PNG path (default: <artifacts>/<league_id>-draft-order.png)",
    )
    draft_order.add_argument("--all-users", action="store_true", help=all_users_help)
    draft_order.set_defaults(func=_cmd_report_draft_order)

    on_this_day = report_commands.add_parser(
        "on-this-day",
        help="Render transactions that happened on this calendar date, across history",
    )
    on_this_day.add_argument(
        "league_id",
        help="Sleeper league identifier (any season in the franchise chain)",
    )
    on_this_day.add_argument(
        "--date", default=None, help="Date to look up, YYYY-MM-DD (default: today)"
    )
    on_this_day.add_argument(
        "--out",
        default=None,
        help=(
            "Output PNG path (default: <artifacts>/<league_id>-on-this-day-<MM-DD>.png)"
        ),
    )
    on_this_day.set_defaults(func=_cmd_report_on_this_day)

    performance = report_commands.add_parser(
        "performance",
        help=(
            "Render a week's overachievers/underperformers: actual vs. projected points"
        ),
    )
    performance.add_argument("league_id", help="Sleeper league identifier")
    performance.add_argument("--week", required=True, type=int, help="Week number")
    performance.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="How many players to show per section (default: 10)",
    )
    performance.add_argument(
        "--all-players",
        action="store_true",
        help="Include bench players, not just starters (default: starters only)",
    )
    performance.add_argument(
        "--out",
        default=None,
        help=(
            "Output PNG path (default: "
            "<artifacts>/<league_id>-week<week>-performance.png)"
        ),
    )
    performance.set_defaults(func=_cmd_report_performance)

    season_performance = report_commands.add_parser(
        "season-performance",
        help=(
            "Render a season's overachievers/underperformers: avg actual "
            "vs. avg projected points per game"
        ),
    )
    season_performance.add_argument(
        "--season", required=True, type=int, help="Season year, e.g. 2025"
    )
    season_performance.add_argument(
        "--league-id",
        default=None,
        help=(
            "Disambiguate which league_id to use for --season -- only "
            "needed if more than one league was fetched for that season "
            "(e.g. tracking multiple concurrent leagues on one account)"
        ),
    )
    season_performance.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="How many players to show per section (default: 10)",
    )
    season_performance.add_argument(
        "--min-games",
        type=int,
        default=3,
        help=(
            "Minimum weeks with both a real result and a real projection "
            "to appear at all (default: 3) -- keeps a small sample from "
            "one huge single-week delta from dominating a season ranking"
        ),
    )
    season_performance.add_argument(
        "--all-players",
        action="store_true",
        help="Include bench players, not just starters (default: starters only)",
    )
    season_performance.add_argument(
        "--out",
        default=None,
        help=(
            "Output PNG path (default: "
            "<artifacts>/<league_id>-<season>-season-performance.png)"
        ),
    )
    season_performance.set_defaults(func=_cmd_report_season_performance)

    user_leagues_report = report_commands.add_parser(
        "user-leagues",
        help="Render a Sleeper user's leagues for a season as a PNG table",
    )
    user_leagues_report.add_argument("username", help="Sleeper username or user id")
    user_leagues_report.add_argument(
        "--season", required=True, help="Season year, e.g. 2026"
    )
    user_leagues_report.add_argument(
        "--sport", default="nfl", help="Sport key (default: nfl)"
    )
    user_leagues_report.add_argument(
        "--out",
        default=None,
        help=("Output PNG path (default: <artifacts>/<user_id>-leagues/<season>.png)"),
    )
    user_leagues_report.set_defaults(func=_cmd_report_user_leagues)

    user_network_report = report_commands.add_parser(
        "user-network",
        help="Render the Sleeper network reachable from a seed user as a PNG",
    )
    user_network_report.add_argument("username", help="Sleeper username or user id")
    user_network_report.add_argument(
        "--season", required=True, help="Season year, e.g. 2026"
    )
    user_network_report.add_argument(
        "--sport", default="nfl", help="Sport key (default: nfl)"
    )
    user_network_report.add_argument(
        "--max-hops",
        type=int,
        default=DEFAULT_MAX_HOPS,
        help=(
            "Rounds of user -> co-members -> their leagues "
            f"(default: {DEFAULT_MAX_HOPS})"
        ),
    )
    user_network_report.add_argument(
        "--max-users",
        type=int,
        default=DEFAULT_MAX_USERS,
        help=f"Hard cap on discovered users (default: {DEFAULT_MAX_USERS})",
    )
    user_network_report.add_argument(
        "--out",
        default=None,
        help="Output PNG path (default: <artifacts>/<user_id>-network/<season>.png)",
    )
    user_network_report.set_defaults(func=_cmd_report_user_network)

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
