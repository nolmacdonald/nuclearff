"""Typed containers for captured Sleeper payloads.

These models describe the *snapshot*, not the league. Raw endpoint payloads are
kept verbatim as dictionaries so that nothing Sleeper returns is lost on the way
to disk; interpreting them into a typed :class:`LeagueConfig` is a separate
concern handled by the league configuration module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning"]
"""How much attention an anomaly deserves."""


class Anomaly(BaseModel):
    """A league setting that is surprising enough to surface to the user.

    Sleeper permits configurations that are internally inconsistent or that
    contradict a league's stated format. Rather than silently trusting or
    silently correcting them, the snapshot records them for a human to confirm.

    Args:
        code: Stable machine-readable identifier.
        severity: ``"warning"`` if the value may be wrong, ``"info"`` if it is
            merely worth knowing.
        message: What was observed.
        action: What the user should do about it.

    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: Severity
    message: str
    action: str


class SnapshotMetadata(BaseModel):
    """Provenance for a captured snapshot.

    Args:
        league_id: League the snapshot describes.
        fetched_at: UTC timestamp of capture.
        base_url: API root the payloads came from.
        endpoints: Endpoint paths that were successfully retrieved.
        missing: Endpoint paths that were requested but unavailable.

    """

    model_config = ConfigDict(extra="forbid")

    league_id: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    base_url: str
    endpoints: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class LeagueSnapshot(BaseModel):
    """Everything fetched for one league in a single capture.

    Args:
        metadata: Provenance for the capture.
        league: The league object.
        users: League members.
        rosters: League rosters.
        drafts: Every draft associated with the league.
        draft: The league's current draft object, when retrievable.
        draft_picks: Picks made in the current draft.
        traded_picks: Traded picks at the league level.
        state: The sport's current league-year state.
        anomalies: Settings flagged for human confirmation.

    """

    model_config = ConfigDict(extra="forbid")

    metadata: SnapshotMetadata
    league: dict[str, Any]
    users: list[dict[str, Any]] = Field(default_factory=list)
    rosters: list[dict[str, Any]] = Field(default_factory=list)
    drafts: list[dict[str, Any]] = Field(default_factory=list)
    draft: dict[str, Any] | None = None
    draft_picks: list[dict[str, Any]] = Field(default_factory=list)
    traded_picks: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] | None = None
    anomalies: list[Anomaly] = Field(default_factory=list)

    @property
    def league_name(self) -> str:
        """League display name, or ``"unknown"`` if the payload omits it."""
        name = self.league.get("name")
        return name if isinstance(name, str) else "unknown"

    @property
    def warnings(self) -> list[Anomaly]:
        """Only the anomalies that may indicate an incorrect setting."""
        return [a for a in self.anomalies if a.severity == "warning"]
