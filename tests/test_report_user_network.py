"""Unit tests for nuclearff.report.user_network (GitHub Issue 209)."""

from __future__ import annotations

import polars as pl

from nuclearff.report.user_network import RenderingUnavailableError, render_user_network
from nuclearff.sleeper.network import UserNetwork


def _network() -> UserNetwork:
    users = pl.DataFrame(
        {
            "user_id": ["U1", "U2", "U3"],
            "display_name": ["seed", "alice", "bob"],
            "hop": [0, 1, 1],
            "league_count": [1, 2, 2],
            "shared_league_count": [1, 1, 1],
        }
    )
    leagues = pl.DataFrame(
        {
            "league_id": ["L1", "L2", "L3"],
            "name": ["League One", "League Two", "League Three"],
            "season": ["2026", "2026", "2026"],
            "sport": ["nfl", "nfl", "nfl"],
            "status": ["in_season", "in_season", "in_season"],
            "total_rosters": [3, 4, 4],
            "hop": [0, 1, 1],
            "known_member_count": [3, 1, 1],
        }
    )
    memberships = pl.DataFrame(
        {
            "user_id": ["U1", "U2", "U2", "U3", "U3"],
            "league_id": ["L1", "L1", "L2", "L1", "L3"],
            "hop": [0, 1, 1, 1, 1],
        }
    )
    return UserNetwork(
        seed_user_id="U1", leagues=leagues, users=users, memberships=memberships
    )


def test_render_user_network_writes_a_png(tmp_path):
    out_path = tmp_path / "user_network.png"

    result = render_user_network(_network(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_user_network_handles_a_single_user_with_no_co_members(tmp_path):
    """A seed with zero leagues (so no co-members) still renders -- one
    isolated node, matching render_trade_network's single-manager case."""
    network = UserNetwork(
        seed_user_id="U1",
        leagues=pl.DataFrame(
            schema={
                "league_id": pl.String,
                "name": pl.String,
                "season": pl.String,
                "sport": pl.String,
                "status": pl.String,
                "total_rosters": pl.Int64,
                "hop": pl.Int64,
                "known_member_count": pl.Int64,
            }
        ),
        users=pl.DataFrame(
            {
                "user_id": ["U1"],
                "display_name": ["seed"],
                "hop": [0],
                "league_count": [0],
                "shared_league_count": [0],
            }
        ),
        memberships=pl.DataFrame(
            schema={"user_id": pl.String, "league_id": pl.String, "hop": pl.Int64}
        ),
    )
    out_path = tmp_path / "user_network.png"

    result = render_user_network(network, out_path)

    assert result.is_file()


def test_render_user_network_handles_a_missing_display_name(tmp_path):
    """A null display_name (real for some Sleeper accounts) falls back to
    the user id as its label rather than raising."""
    network = _network()
    network = network._replace(
        users=network.users.with_columns(
            pl.when(pl.col("user_id") == "U2")
            .then(None)
            .otherwise(pl.col("display_name"))
            .alias("display_name")
        )
    )
    out_path = tmp_path / "user_network.png"

    result = render_user_network(network, out_path)

    assert result.is_file()


def test_rendering_unavailable_error_is_an_import_error():
    assert issubclass(RenderingUnavailableError, ImportError)
