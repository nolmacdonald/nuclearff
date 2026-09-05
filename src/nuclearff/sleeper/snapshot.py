"""Capture a complete, immutable snapshot of a Sleeper league.

A snapshot is the raw input to everything downstream. It is written once and
never modified in place, so a ranking can always be traced back to the exact
league payload it was built from.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from nuclearff.exceptions import SleeperAPIError
from nuclearff.sleeper.client import SleeperClient
from nuclearff.sleeper.models import Anomaly, LeagueSnapshot, SnapshotMetadata

logger = logging.getLogger(__name__)

_NON_ROSTER_SLOTS = frozenset({"BN", "IR", "TAXI"})
"""Roster position codes that are not part of a weekly starting lineup."""

_UNDRAFTED_SLOTS = frozenset({"IR", "TAXI"})
"""Roster position codes that are not normally filled during a draft."""


def _slot_counts(league: dict[str, Any]) -> dict[str, int]:
    """Count each roster position code in a league object.

    Args:
        league: A raw Sleeper league object.

    Returns:
        A mapping of position code to the number of slots.
    """
    counts: dict[str, int] = {}
    positions = league.get("roster_positions") or []
    if not isinstance(positions, list):
        return counts
    for slot in positions:
        if isinstance(slot, str):
            counts[slot] = counts.get(slot, 0) + 1
    return counts


def detect_anomalies(
    league: dict[str, Any], draft: dict[str, Any] | None = None
) -> list[Anomaly]:
    """Flag league settings that are surprising, inconsistent, or load-bearing.

    Two of these checks exist because the technical plan could not retrieve the
    draft object and had to leave the settings unresolved. When the draft object
    is supplied, it is treated as authoritative over the league object's
    ``draft_rounds``.

    Args:
        league: A raw Sleeper league object.
        draft: The league's draft object, when available.

    Returns:
        Anomalies ordered warnings-first, then informational notes.
    """
    settings = league.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}

    warnings: list[Anomaly] = []
    notes: list[Anomaly] = []
    counts = _slot_counts(league)

    draftable = sum(n for slot, n in counts.items() if slot not in _UNDRAFTED_SLOTS)
    league_rounds = settings.get("draft_rounds")
    draft_settings = (draft or {}).get("settings") or {}
    draft_rounds = (
        draft_settings.get("rounds") if isinstance(draft_settings, dict) else None
    )

    if isinstance(draft_rounds, int) and isinstance(league_rounds, int):
        if draft_rounds != league_rounds:
            warnings.append(
                Anomaly(
                    code="draft_rounds_mismatch",
                    severity="warning",
                    message=(
                        f"League settings report draft_rounds={league_rounds} but the "
                        f"draft object reports rounds={draft_rounds}."
                    ),
                    action=(
                        "Trust the draft object. The league field is stale; use "
                        f"{draft_rounds} rounds for pick-gap and VONA math."
                    ),
                )
            )
        else:
            notes.append(
                Anomaly(
                    code="draft_rounds_confirmed",
                    severity="info",
                    message=(
                        f"League and draft objects agree on {draft_rounds} rounds "
                        f"for {draftable} draftable roster spots."
                    ),
                    action="No action needed.",
                )
            )

    if (
        draft_rounds is None
        and isinstance(league_rounds, int)
        and league_rounds < draftable
    ):
        warnings.append(
            Anomaly(
                code="draft_rounds_below_roster_size",
                severity="warning",
                message=(
                    f"League settings report draft_rounds={league_rounds} but there "
                    f"are {draftable} draftable roster spots."
                ),
                action=(
                    "Fetch the draft object and confirm the real draft length in "
                    "the Sleeper UI before relying on it for pick-gap math."
                ),
            )
        )

    league_type = settings.get("type")
    max_keepers = settings.get("max_keepers")
    if league_type == 0 and isinstance(max_keepers, int) and max_keepers >= 1:
        warnings.append(
            Anomaly(
                code="keepers_in_redraft",
                severity="warning",
                message=(f"League type is 0 (redraft) but max_keepers={max_keepers}."),
                action=(
                    "Treat the league as redraft, but confirm in the Sleeper UI "
                    "whether a keeper is actually in play."
                ),
            )
        )

    if settings.get("league_average_match") == 1:
        notes.append(
            Anomaly(
                code="median_scoring",
                severity="info",
                message=(
                    "Each team also plays the weekly league median "
                    "(league_average_match=1)."
                ),
                action=(
                    "Weight weekly floor more heavily than ceiling when breaking "
                    "ties between players."
                ),
            )
        )

    if settings.get("best_ball") == 1:
        notes.append(
            Anomaly(
                code="best_ball",
                severity="info",
                message="Lineups are set automatically (best_ball=1).",
                action="Prefer VORP over VOLS as the replacement baseline.",
            )
        )

    if counts and not ({"K", "DEF", "DL", "LB", "DB", "IDP_FLEX"} & counts.keys()):
        notes.append(
            Anomaly(
                code="no_kicker_or_defense",
                severity="info",
                message="No kicker, defense, or IDP roster slots.",
                action=(
                    "Kicker and defensive scoring keys in the payload are inert "
                    "for lineup construction."
                ),
            )
        )

    return warnings + notes


def fetch_league_snapshot(
    client: SleeperClient, league_id: str, *, include_state: bool = True
) -> LeagueSnapshot:
    """Fetch every read-only endpoint that describes a league.

    Endpoints that are genuinely unavailable are recorded in
    :attr:`SnapshotMetadata.missing` rather than aborting the capture, so a
    partially available league still produces a usable snapshot.

    Args:
        client: A configured Sleeper client.
        league_id: Sleeper league identifier.
        include_state: Also capture the sport's current league-year state.

    Returns:
        The captured snapshot, with anomalies already detected.

    Raises:
        SleeperAPIError: If the league object itself cannot be retrieved. Every
            other endpoint degrades to a recorded miss.
    """
    endpoints: list[str] = []
    missing: list[str] = []

    league = client.get_league(league_id)
    endpoints.append(f"/v1/league/{league_id}")

    def _optional(path: str, fetch: Any, default: Any) -> Any:
        """Fetch an endpoint, recording rather than raising on failure."""
        try:
            value = fetch()
        except SleeperAPIError as exc:
            logger.warning("Could not fetch %s: %s", path, exc)
            missing.append(path)
            return default
        endpoints.append(path)
        return value

    users = _optional(
        f"/v1/league/{league_id}/users", lambda: client.get_users(league_id), []
    )
    rosters = _optional(
        f"/v1/league/{league_id}/rosters", lambda: client.get_rosters(league_id), []
    )
    traded_picks = _optional(
        f"/v1/league/{league_id}/traded_picks",
        lambda: client.get_traded_picks(league_id),
        [],
    )
    drafts = _optional(
        f"/v1/league/{league_id}/drafts",
        lambda: client.get_league_drafts(league_id),
        [],
    )

    draft: dict[str, Any] | None = None
    draft_picks: list[dict[str, Any]] = []
    draft_traded_picks: list[dict[str, Any]] = []
    draft_id = league.get("draft_id")
    if isinstance(draft_id, str) and draft_id:
        draft = _optional(
            f"/v1/draft/{draft_id}", lambda: client.get_draft(draft_id), None
        )
        draft_picks = _optional(
            f"/v1/draft/{draft_id}/picks", lambda: client.get_draft_picks(draft_id), []
        )
        draft_traded_picks = _optional(
            f"/v1/draft/{draft_id}/traded_picks",
            lambda: client.get_draft_traded_picks(draft_id),
            [],
        )

    state: dict[str, Any] | None = None
    if include_state:
        state = _optional("/v1/state/nfl", client.get_state, None)

    metadata = SnapshotMetadata(
        league_id=league_id,
        base_url=client.base_url,
        endpoints=endpoints,
        missing=missing,
    )

    return LeagueSnapshot(
        metadata=metadata,
        league=league,
        users=users,
        rosters=rosters,
        drafts=drafts,
        draft=draft,
        draft_picks=draft_picks,
        draft_traded_picks=draft_traded_picks,
        traded_picks=traded_picks,
        state=state,
        anomalies=detect_anomalies(league, draft),
    )


def _write_json(path: Path, payload: Any) -> None:
    """Write a payload as deterministic, human-diffable JSON.

    Args:
        path: Destination file.
        payload: A JSON-serializable value.
    """
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_snapshot(snapshot: LeagueSnapshot, raw_dir: str | Path) -> Path:
    """Write a snapshot to an immutable, timestamped directory.

    Each endpoint is written as its own file so payloads stay readable and
    diffable, alongside a combined ``snapshot.json`` carrying metadata and
    anomalies.

    Args:
        snapshot: The captured snapshot.
        raw_dir: The raw data directory; the snapshot is written beneath
            ``<raw_dir>/sleeper/<league_id>/<timestamp>/``.

    Returns:
        The directory the snapshot was written to.

    Raises:
        FileExistsError: If the target directory already exists. Raw snapshots
            are never overwritten.
    """
    stamp = snapshot.metadata.fetched_at.strftime("%Y%m%dT%H%M%SZ")
    target = Path(raw_dir) / "sleeper" / snapshot.metadata.league_id / stamp
    target.mkdir(parents=True, exist_ok=False)

    _write_json(target / "league.json", snapshot.league)
    _write_json(target / "users.json", snapshot.users)
    _write_json(target / "rosters.json", snapshot.rosters)
    _write_json(target / "drafts.json", snapshot.drafts)
    _write_json(target / "draft_picks.json", snapshot.draft_picks)
    _write_json(target / "draft_traded_picks.json", snapshot.draft_traded_picks)
    _write_json(target / "traded_picks.json", snapshot.traded_picks)
    if snapshot.draft is not None:
        _write_json(target / "draft.json", snapshot.draft)
    if snapshot.state is not None:
        _write_json(target / "state.json", snapshot.state)

    _write_json(
        target / "snapshot.json",
        json.loads(snapshot.model_dump_json(exclude_none=True)),
    )

    logger.info("Wrote Sleeper snapshot to %s", target)
    return target
