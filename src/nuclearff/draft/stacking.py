"""Same-team stack and handcuff candidates for a roster mid-draft (issue #98).

Two cheap, same-NFL-team signals that no other draft module surfaces:

* :func:`stack_candidates` -- for each QB already on the roster, the available
  WR/TE on that QB's team (a QB+pass-catcher stack).
* :func:`handcuff_candidates` -- for each RB already on the roster, the
  available RBs on that RB's team (the backup who inherits the work if the
  starter is hurt).

Both only *surface* candidates. Neither ranks a stack or handcuff against the
general recommendation ranking; whether to prioritize one is left to the
caller.

Inputs reuse shapes already in the codebase. ``roster_picks`` is one roster's
picks, each shaped like :func:`nuclearff.sleeper.draft.draft_pick_rows`'s
output (``player_id``, ``position``, ``team`` -- typically
``DraftState.picks_by_roster[roster_id]``). ``available_players`` is the
still-undrafted pool as a list of dicts carrying the same three keys plus,
optionally, ``rank`` (lower is better). Team abbreviations are compared as-is
after upper-casing, so both inputs must use the same abbreviation scheme.
"""

from __future__ import annotations

from typing import Any

_STACK_POSITIONS = frozenset({"WR", "TE"})


def _team(player: dict[str, Any]) -> str | None:
    team = player.get("team")
    return team.upper() if isinstance(team, str) and team else None


def _rank_key(player: dict[str, Any]) -> tuple[bool, float]:
    rank = player.get("rank")
    has_rank = isinstance(rank, (int, float))
    return (not has_rank, float(rank) if has_rank else 0.0)


def _pairings(
    roster_picks: list[dict[str, Any]],
    available_players: list[dict[str, Any]],
    anchor_position: str,
    candidate_positions: frozenset[str],
) -> list[dict[str, Any]]:
    """Pair each rostered ``anchor_position`` player with same-team candidates."""
    rostered_ids = {pick.get("player_id") for pick in roster_picks}
    pool = [
        player
        for player in available_players
        if player.get("position") in candidate_positions
        and player.get("player_id") not in rostered_ids
        and _team(player) is not None
    ]
    pool.sort(key=_rank_key)

    pairings: list[dict[str, Any]] = []
    for pick in roster_picks:
        team = _team(pick)
        if pick.get("position") != anchor_position or team is None:
            continue
        pick_rank = pick.get("rank")
        for player in pool:
            if _team(player) != team:
                continue
            player_rank = player.get("rank")
            # A handcuff is a *lower*-ranked backup; when both ranks are known,
            # an available RB ranked above the rostered one is not a cuff.
            if (
                anchor_position == "RB"
                and isinstance(pick_rank, (int, float))
                and isinstance(player_rank, (int, float))
                and player_rank <= pick_rank
            ):
                continue
            pairings.append(
                {
                    "team": team,
                    "rostered_player_id": pick.get("player_id"),
                    "candidate": player,
                }
            )
    return pairings


def stack_candidates(
    roster_picks: list[dict[str, Any]], available_players: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Available WR/TE on the same NFL team as a QB the roster already has.

    Args:
        roster_picks: This roster's picks so far.
        available_players: The undrafted pool. A player whose ``player_id``
            appears in ``roster_picks`` is excluded even if present here.

    Returns:
        One dict per ``(rostered QB, available WR/TE)`` pairing, in roster pick
        order and then best ``rank`` first (unranked last): ``team``,
        ``rostered_player_id`` (the QB), and ``candidate`` (the available
        player's dict, unchanged). Empty when the roster has no QB, or the QB
        has no ``team``.
    """
    return _pairings(roster_picks, available_players, "QB", _STACK_POSITIONS)


def handcuff_candidates(
    roster_picks: list[dict[str, Any]], available_players: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Available RBs on the same NFL team as an RB the roster already has.

    Args:
        roster_picks: This roster's picks so far.
        available_players: The undrafted pool. A player whose ``player_id``
            appears in ``roster_picks`` is excluded even if present here.

    Returns:
        One dict per ``(rostered RB, available RB)`` pairing with the same
        shape as :func:`stack_candidates`. When both the rostered RB and the
        candidate carry a numeric ``rank``, only a candidate ranked below
        (numerically greater than) the rostered RB is returned; without ranks,
        every same-team available RB is. Empty when the roster has no RB.
    """
    return _pairings(roster_picks, available_players, "RB", frozenset({"RB"}))
