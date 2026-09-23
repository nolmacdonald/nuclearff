"""Live draft state: poll Sleeper for picks so far, and pick-order math.

Every other draft-companion issue (#93-#107) needs to know, at any moment
during an in-progress draft, who has already been picked and how many picks
until a given roster is next on the clock. :func:`poll_draft_state` is the
thin, poll-friendly wrapper around :meth:`SleeperClient.get_draft` +
:meth:`~nuclearff.sleeper.client.SleeperClient.get_draft_picks` that answers
the first question; :func:`next_pick_gap` answers the second, for direct use
as :func:`nuclearff.valuation.vorp.vona`'s ``next_pick_gap`` argument.

**Pick-order math, not Sleeper's own resolved value.** Unlike
:mod:`nuclearff.sleeper.draft` (which trusts each *already-made* pick's own
``draft_slot`` rather than computing snake direction -- see that module's
docstring), a *future* pick has no resolved ``draft_slot`` to trust; Sleeper
exposes no "who picks next" endpoint. :func:`draft_slot_for_pick` computes
it from ``settings.teams``/``settings.reversal_round``, confirmed against
this project's real, captured ``reversal_round: 3`` draft (see
``tests/test_sleeper_draft.py``): picks 1-10 (round 1) ascend
``draft_slot`` 1->10, picks 11-20 (round 2) descend 10->1 -- plain
alternating snake so far -- and picks 21-30 (round 3) descend 10->1 again
*instead of* reversing back to ascending, confirmed live by real picks 20
(round 2, slot 1) and 21 (round 3, slot 10). This matches the named
"Nth-round reversal" draft format: at ``reversal_round``, direction repeats
the previous round's direction once, then alternates normally again from
there. Only one real ``reversal_round`` value (3) has been confirmed this
way; the formula below generalizes it to any round number, which is not
independently confirmed for other values but follows directly from the
same named format.

**Traded picks.** :meth:`SleeperClient.get_draft_traded_picks` gives each
traded pick's ``(round, roster_id)`` (its *original* owner, matching
``slot_to_roster_id``) and current ``owner_id``. This project has no real
captured payload for this endpoint yet (unlike ``reversal_round`` above) --
the shape used here is Sleeper's own publicly documented one.
:func:`roster_for_pick` applies it as an override on top of the computed
slot, so naive snake-order math never silently gives the wrong answer for a
pick that changed hands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nuclearff.sleeper.client import SleeperClient
from nuclearff.sleeper.draft import draft_pick_rows


@dataclass(frozen=True, slots=True)
class DraftState:
    """A snapshot of one poll of an in-progress (or not-yet-started) draft.

    Args:
        draft: The raw draft object (:meth:`SleeperClient.get_draft`).
        picks_by_roster: Each ``roster_id`` mapped to its picks so far, each
            row shaped like :func:`nuclearff.sleeper.draft.draft_pick_rows`'s
            output, in pick order.
        drafted_player_ids: Every ``player_id`` already picked -- directly
            usable as :func:`nuclearff.valuation.vorp.vona`'s
            ``drafted_player_ids`` argument.

    """

    draft: dict[str, Any]
    picks_by_roster: dict[int, list[dict[str, Any]]]
    drafted_player_ids: set[str]


def poll_draft_state(client: SleeperClient, draft_id: str) -> DraftState:
    """Fetch a draft and its picks so far, as one poll-friendly snapshot.

    Args:
        client: A configured Sleeper client.
        draft_id: Sleeper draft identifier.

    Returns:
        The current draft state. Calling this again later (e.g. on a
        polling interval) returns a fresh, independent snapshot -- nothing
        here is cached or mutated in place.
    """
    draft = client.get_draft(draft_id)
    picks = client.get_draft_picks(draft_id)
    rows = draft_pick_rows(draft, picks)

    picks_by_roster: dict[int, list[dict[str, Any]]] = {}
    drafted_player_ids: set[str] = set()
    for row in rows:
        roster_id = row.get("roster_id")
        if isinstance(roster_id, int):
            picks_by_roster.setdefault(roster_id, []).append(row)
        player_id = row.get("player_id")
        if isinstance(player_id, str):
            drafted_player_ids.add(player_id)

    return DraftState(
        draft=draft,
        picks_by_roster=picks_by_roster,
        drafted_player_ids=drafted_player_ids,
    )


def _round_is_ascending(round_num: int, reversal_round: int | None) -> bool:
    """Direction of ``round_num``: ``True`` for ascending draft_slot 1->teams.

    Args:
        round_num: The round to resolve (1-indexed).
        reversal_round: ``settings.reversal_round``, or ``None``/``0`` for a
            plain alternating snake with no reversal round at all.

    Returns:
        Whether ``round_num`` ascends. Plain alternating snake (no
        ``reversal_round``, or a round before it): odd rounds ascend, even
        rounds descend. At and after ``reversal_round``: direction repeats
        the round before ``reversal_round`` once (does not flip), then
        alternates normally again for every round after that.
    """
    if reversal_round and round_num >= reversal_round:
        anchor_ascending = (reversal_round - 1) % 2 == 1
        rounds_past_anchor = round_num - reversal_round
        return anchor_ascending if rounds_past_anchor % 2 == 0 else not anchor_ascending
    return round_num % 2 == 1


def _teams_and_rounds(draft: dict[str, Any]) -> tuple[int, int]:
    """Extract and validate ``settings.teams``/``settings.rounds``.

    Args:
        draft: The raw draft object.

    Returns:
        ``(teams, rounds)``.

    Raises:
        ValueError: If either is missing or not a positive integer --
            pick-order math has no sensible fallback for a malformed draft.
    """
    settings = draft.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    teams = settings.get("teams")
    rounds = settings.get("rounds")
    if not isinstance(teams, int) or teams < 1:
        raise ValueError("draft.settings.teams is required for pick-order math")
    if not isinstance(rounds, int) or rounds < 1:
        raise ValueError("draft.settings.rounds is required for pick-order math")
    return teams, rounds


def draft_slot_for_pick(draft: dict[str, Any], pick_no: int) -> int:
    """Compute which ``draft_slot`` is on the clock for ``pick_no``.

    Snake/reversal-round math only -- does not account for trades (see
    :func:`roster_for_pick`, which layers that on top).

    Args:
        draft: The raw draft object.
        pick_no: The overall pick number (1-indexed).

    Returns:
        The ``draft_slot`` (1..``teams``) on the clock at ``pick_no``.

    Raises:
        ValueError: If ``draft.settings.teams`` is missing, or ``type`` is
            neither ``"snake"`` nor ``"linear"`` (auction drafts are out of
            scope -- see the module docstring).
    """
    teams, _rounds = _teams_and_rounds(draft)
    draft_type = draft.get("type", "snake")

    round_num = (pick_no - 1) // teams + 1
    position_in_round = (pick_no - 1) % teams + 1

    if draft_type == "linear":
        return position_in_round
    if draft_type != "snake":
        raise ValueError(f"Unsupported draft type for pick-order math: {draft_type!r}")

    settings = draft.get("settings") or {}
    reversal_round = settings.get("reversal_round") or None
    ascending = _round_is_ascending(round_num, reversal_round)
    return position_in_round if ascending else teams + 1 - position_in_round


def _traded_pick_owners(
    traded_picks: list[dict[str, Any]],
) -> dict[tuple[int, int], int]:
    """Map ``(round, original_roster_id)`` to its current owner.

    Args:
        traded_picks: Raw entries from
            :meth:`SleeperClient.get_draft_traded_picks`.

    Returns:
        Every traded pick's ``(round, roster_id)`` mapped to its current
        ``owner_id``. If a pick appears more than once (re-traded again),
        the last entry in ``traded_picks`` wins.
    """
    owners: dict[tuple[int, int], int] = {}
    for entry in traded_picks:
        round_ = entry.get("round")
        roster_id = entry.get("roster_id")
        owner_id = entry.get("owner_id")
        if (
            isinstance(round_, int)
            and isinstance(roster_id, int)
            and isinstance(owner_id, int)
        ):
            owners[(round_, roster_id)] = owner_id
    return owners


def roster_for_pick(
    draft: dict[str, Any], traded_picks: list[dict[str, Any]], pick_no: int
) -> int | None:
    """Resolve which ``roster_id`` actually owns ``pick_no``, trades included.

    Args:
        draft: The raw draft object.
        traded_picks: Raw entries from
            :meth:`SleeperClient.get_draft_traded_picks`.
        pick_no: The overall pick number (1-indexed).

    Returns:
        The current owner's ``roster_id``, or ``None`` if
        ``draft.slot_to_roster_id`` doesn't (yet) have an entry for the
        computed slot -- a draft whose roster assignment isn't finalized,
        not something to guess at.
    """
    round_num = (pick_no - 1) // _teams_and_rounds(draft)[0] + 1
    slot = draft_slot_for_pick(draft, pick_no)

    slot_to_roster = draft.get("slot_to_roster_id")
    if not isinstance(slot_to_roster, dict):
        return None
    original_roster_id = slot_to_roster.get(str(slot))
    if not isinstance(original_roster_id, int):
        return None

    owners = _traded_pick_owners(traded_picks)
    return owners.get((round_num, original_roster_id), original_roster_id)


def next_pick_gap(
    draft: dict[str, Any],
    picks: list[dict[str, Any]],
    traded_picks: list[dict[str, Any]],
    roster_id: int,
) -> int | None:
    """How many picks from now ``roster_id`` is next on the clock.

    Direct input for :func:`nuclearff.valuation.vorp.vona`'s
    ``next_pick_gap`` argument.

    Args:
        draft: The raw draft object.
        picks: Picks made so far (:meth:`SleeperClient.get_draft_picks`).
            The next pick number is assumed to be ``len(picks) + 1``.
        traded_picks: Raw entries from
            :meth:`SleeperClient.get_draft_traded_picks`.
        roster_id: The roster to find the next pick for.

    Returns:
        ``1`` if ``roster_id`` is on the clock for the very next pick, ``2``
        for the pick after that, and so on -- or ``None`` if ``roster_id``
        has no remaining pick in the draft (traded all of them away, or the
        draft is already complete).
    """
    teams, rounds = _teams_and_rounds(draft)
    total_picks = teams * rounds
    next_pick_no = len(picks) + 1

    for pick_no in range(next_pick_no, total_picks + 1):
        if roster_for_pick(draft, traded_picks, pick_no) == roster_id:
            return pick_no - next_pick_no + 1

    return None
