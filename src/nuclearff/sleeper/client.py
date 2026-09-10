"""Read-only client for the public Sleeper API.

The Sleeper API requires no authentication and is read-only, but it does ask
callers to stay under roughly 1000 requests per minute and to fetch the ~5 MB
player map no more than once per day. This client enforces both: requests are
paced by a minimum interval, and the player map is cached on disk with a TTL.

References:
    https://docs.sleeper.com
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

import requests

from nuclearff.exceptions import SleeperHTTPError, SleeperResponseError

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app"
"""Root of the public Sleeper API."""

AVATAR_URL = "https://sleepercdn.com/avatars"
"""Root for avatar images, which are served from the CDN rather than the API."""

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
"""Status codes worth retrying: rate limiting and transient server errors."""

_DEFAULT_MIN_INTERVAL = 0.06
"""Seconds between requests, pacing to roughly 1000 per minute."""


class SleeperClient:
    """Read-only Sleeper API client with session reuse, retries, and caching.

    The client is safe to use as a context manager, which closes the underlying
    session on exit.

    Args:
        cache_dir: Directory for the on-disk player-map cache.
        players_ttl_hours: How long a cached player map stays fresh.
        max_retries: Retry attempts for rate limits and transient server errors.
        backoff_factor: Base seconds for exponential backoff between retries.
        timeout: Per-request timeout in seconds.
        min_interval: Minimum seconds between requests.
        base_url: API root, overridable for testing.
        session: An existing :class:`requests.Session` to reuse.

    Attributes:
        cache_dir: Directory for the on-disk player-map cache.
        players_ttl_hours: How long a cached player map stays fresh.
        max_retries: Retry attempts for rate limits and transient server errors.
        base_url: API root.
    """

    def __init__(
        self,
        cache_dir: str | Path = "data/cache",
        players_ttl_hours: int = 24,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        timeout: float = 15.0,
        min_interval: float = _DEFAULT_MIN_INTERVAL,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.players_ttl_hours = players_ttl_hours
        self.max_retries = max_retries
        self.base_url = base_url.rstrip("/")

        self._backoff_factor = backoff_factor
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request_at = 0.0
        self._owns_session = session is None
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": "nuclearff (+read-only)"})

    def __enter__(self) -> SleeperClient:
        """Return self so the client can be used as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the session if this client created it."""
        self.close()

    def close(self) -> None:
        """Close the underlying session if this client created it."""
        if self._owns_session:
            self._session.close()

    def _throttle(self) -> None:
        """Sleep just long enough to respect the configured request pace."""
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _get(self, path: str) -> Any:
        """Perform a GET request and decode the JSON body.

        Retries rate limits and transient server errors with exponential
        backoff. A 404 is not retried: Sleeper uses it for genuinely absent
        resources.

        Args:
            path: Path relative to the API root, beginning with a slash.

        Returns:
            The decoded JSON payload.

        Raises:
            SleeperHTTPError: If the request fails after exhausting retries, or
                fails with a status code that is not worth retrying.
            SleeperResponseError: If the response body is not valid JSON.
        """
        url = f"{self.base_url}{path}"
        last_status: int | None = None
        last_detail: str | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self._session.get(url, timeout=self._timeout)
                self._last_request_at = time.monotonic()
            except requests.RequestException as exc:
                last_status, last_detail = None, str(exc)
                logger.warning(
                    "Sleeper request to %s failed (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise SleeperResponseError(
                            url, f"response body was not valid JSON ({exc})"
                        ) from exc

                last_status, last_detail = response.status_code, response.reason
                if response.status_code not in _RETRY_STATUS:
                    raise SleeperHTTPError(url, response.status_code, response.reason)

                logger.warning(
                    "Sleeper request to %s returned %d (attempt %d/%d)",
                    url,
                    response.status_code,
                    attempt + 1,
                    self.max_retries + 1,
                )

            if attempt < self.max_retries:
                time.sleep(self._backoff_factor * (2**attempt))

        raise SleeperHTTPError(url, last_status, last_detail)

    def _get_dict(self, path: str) -> dict[str, Any]:
        """GET a path that must return a JSON object.

        Args:
            path: Path relative to the API root.

        Returns:
            The decoded object.

        Raises:
            SleeperResponseError: If the payload is not an object. Sleeper
                returns a bare ``null`` for unknown league and draft IDs, which
                lands here rather than as a 404.
        """
        payload = self._get(path)
        if not isinstance(payload, dict):
            raise SleeperResponseError(
                f"{self.base_url}{path}",
                f"expected a JSON object, got {type(payload).__name__}",
            )
        return payload

    def _get_list(self, path: str) -> list[dict[str, Any]]:
        """GET a path that must return a JSON array of objects.

        Args:
            path: Path relative to the API root.

        Returns:
            The decoded array.

        Raises:
            SleeperResponseError: If the payload is not an array of objects.
        """
        payload = self._get(path)
        if not isinstance(payload, list):
            raise SleeperResponseError(
                f"{self.base_url}{path}",
                f"expected a JSON array, got {type(payload).__name__}",
            )
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise SleeperResponseError(
                    f"{self.base_url}{path}",
                    f"expected array of objects, item {index} is {type(item).__name__}",
                )
        return payload

    def get_state(self, sport: str = "nfl") -> dict[str, Any]:
        """Return the current league-year state for a sport.

        Args:
            sport: Sport key, such as ``"nfl"``.

        Returns:
            The state object, including ``season``, ``week``, and
            ``season_type``.
        """
        return self._get_dict(f"/v1/state/{sport}")

    def get_user(self, username_or_id: str) -> dict[str, Any]:
        """Return a user object, the entry point from a Sleeper username.

        Args:
            username_or_id: A Sleeper username or numeric user id; both
                resolve the same way.

        Returns:
            The user object, including ``user_id`` and ``display_name``.
        """
        return self._get_dict(f"/v1/user/{username_or_id}")

    def get_user_leagues(
        self, user_id: str, season: int | str, sport: str = "nfl"
    ) -> list[dict[str, Any]]:
        """Return every league a user belongs to for a season.

        Args:
            user_id: Sleeper user identifier (from :meth:`get_user`).
            season: Season year, e.g. ``2026``.
            sport: Sport key, such as ``"nfl"``.

        Returns:
            One league object per league the user belongs to that season.
        """
        return self._get_list(f"/v1/user/{user_id}/leagues/{sport}/{season}")

    def get_user_drafts(
        self, user_id: str, season: int | str, sport: str = "nfl"
    ) -> list[dict[str, Any]]:
        """Return every draft a user is in for a season.

        Args:
            user_id: Sleeper user identifier (from :meth:`get_user`).
            season: Season year, e.g. ``2026``.
            sport: Sport key, such as ``"nfl"``.

        Returns:
            One draft object per draft the user is in that season.
        """
        return self._get_list(f"/v1/user/{user_id}/drafts/{sport}/{season}")

    def get_league(self, league_id: str) -> dict[str, Any]:
        """Return a league object.

        Args:
            league_id: Sleeper league identifier.

        Returns:
            The league object, including ``scoring_settings``,
            ``roster_positions``, and ``settings``.
        """
        return self._get_dict(f"/v1/league/{league_id}")

    def get_users(self, league_id: str) -> list[dict[str, Any]]:
        """Return the users in a league.

        Args:
            league_id: Sleeper league identifier.

        Returns:
            One object per league member.
        """
        return self._get_list(f"/v1/league/{league_id}/users")

    def get_rosters(self, league_id: str) -> list[dict[str, Any]]:
        """Return the rosters in a league.

        Args:
            league_id: Sleeper league identifier.

        Returns:
            One object per roster. Before a draft these have empty
            ``players`` lists.
        """
        return self._get_list(f"/v1/league/{league_id}/rosters")

    def get_matchups(self, league_id: str, week: int) -> list[dict[str, Any]]:
        """Return matchups for one week.

        Args:
            league_id: Sleeper league identifier.
            week: Week number.

        Returns:
            One object per roster for that week.
        """
        return self._get_list(f"/v1/league/{league_id}/matchups/{week}")

    def get_projections(
        self,
        season: int | str,
        week: int,
        *,
        positions: Sequence[str] = ("QB", "RB", "WR", "TE"),
        season_type: str = "regular",
        sport: str = "nfl",
    ) -> list[dict[str, Any]]:
        """Return per-player weekly projections.

        **Not part of the documented public API** (https://docs.sleeper.com
        covers ``/v1/...`` only) — this is the same endpoint Sleeper's own
        app reads. Confirmed live 2026-09-10 against 2025 week 1: a single
        source (``company="rotowire"``), exactly one row per requested
        position with no duplicate ``player_id`` values. See
        ``brain/decisions.md``'s 2026-09-10 entry for why this project uses
        an undocumented endpoint here despite the "public, documented API
        only" posture the rest of this client holds to.

        Args:
            season: Season year, e.g. ``2026``.
            week: Week number.
            positions: Positions to request. Sleeper requires at least one
                ``position[]`` query parameter — omitting it entirely
                returns every position, including kickers/defenses this
                project has no use for.
            season_type: Sleeper season type, e.g. ``"regular"``.
            sport: Sport key, such as ``"nfl"``.

        Returns:
            One object per projected player: ``player_id``, ``team``,
            ``opponent``, ``category`` (``"proj"``), ``stats`` (a dict
            keyed by Sleeper's own scoring vocabulary — ``rec``, ``rec_yd``,
            ``pass_td``, ...), and ``company`` (the projection source).
        """
        query = "&".join(f"position[]={position}" for position in positions)
        return self._get_list(
            f"/projections/{sport}/{season}/{week}?season_type={season_type}&{query}"
        )

    def get_transactions(self, league_id: str, week: int) -> list[dict[str, Any]]:
        """Return transactions for one week.

        Args:
            league_id: Sleeper league identifier.
            week: Week number, which Sleeper calls the transaction round.

        Returns:
            One object per transaction.
        """
        return self._get_list(f"/v1/league/{league_id}/transactions/{week}")

    def get_traded_picks(self, league_id: str) -> list[dict[str, Any]]:
        """Return traded draft picks for a league.

        Args:
            league_id: Sleeper league identifier.

        Returns:
            One object per traded pick; empty when no picks have been traded.
        """
        return self._get_list(f"/v1/league/{league_id}/traded_picks")

    def get_league_drafts(self, league_id: str) -> list[dict[str, Any]]:
        """Return every draft associated with a league.

        Args:
            league_id: Sleeper league identifier.

        Returns:
            One object per draft, newest first.
        """
        return self._get_list(f"/v1/league/{league_id}/drafts")

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        """Return a draft object.

        Args:
            draft_id: Sleeper draft identifier.

        Returns:
            The draft object, including ``type``, ``draft_order``, and
            ``settings`` such as ``rounds`` and ``pick_timer``.
        """
        return self._get_dict(f"/v1/draft/{draft_id}")

    def get_draft_picks(self, draft_id: str) -> list[dict[str, Any]]:
        """Return the picks made in a draft.

        Args:
            draft_id: Sleeper draft identifier.

        Returns:
            One object per pick; empty before the draft starts.
        """
        return self._get_list(f"/v1/draft/{draft_id}/picks")

    def get_draft_traded_picks(self, draft_id: str) -> list[dict[str, Any]]:
        """Return traded picks within a draft.

        Args:
            draft_id: Sleeper draft identifier.

        Returns:
            One object per traded pick.
        """
        return self._get_list(f"/v1/draft/{draft_id}/traded_picks")

    def get_winners_bracket(self, league_id: str) -> list[dict[str, Any]]:
        """Return the winners' playoff bracket for a league.

        Args:
            league_id: Sleeper league identifier.

        Returns:
            One object per bracket match: ``m`` (match id), ``r`` (round),
            ``t1``/``t2`` (roster ids, or unresolved until an earlier match
            decides them), ``w``/``l`` (winner/loser roster id once decided),
            ``p`` (final placement awarded by this match, when present), and
            ``t1_from``/``t2_from`` (references to an earlier match's winner
            or loser, for rounds whose participants aren't known roster ids
            yet). Empty before the league has a playoff bracket.
        """
        return self._get_list(f"/v1/league/{league_id}/winners_bracket")

    def get_losers_bracket(self, league_id: str) -> list[dict[str, Any]]:
        """Return the losers' (consolation) playoff bracket for a league.

        Same shape as :meth:`get_winners_bracket`. Not every league runs a
        losers bracket; empty when it doesn't.

        Args:
            league_id: Sleeper league identifier.

        Returns:
            One object per bracket match.
        """
        return self._get_list(f"/v1/league/{league_id}/losers_bracket")

    def get_trending(
        self, kind: str = "add", lookback_hours: int = 24, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Return trending players by adds or drops.

        Args:
            kind: Either ``"add"`` or ``"drop"``.
            lookback_hours: Window in hours.
            limit: Maximum number of players to return.

        Returns:
            One object per trending player.

        Raises:
            ValueError: If ``kind`` is not ``"add"`` or ``"drop"``.
        """
        if kind not in {"add", "drop"}:
            raise ValueError(f"kind must be 'add' or 'drop', got {kind!r}")
        return self._get_list(
            f"/v1/players/nfl/trending/{kind}"
            f"?lookback_hours={lookback_hours}&limit={limit}"
        )

    @property
    def players_cache_path(self) -> Path:
        """Location of the cached player map."""
        return self.cache_dir / "sleeper_players_nfl.json"

    def _players_cache_age_hours(self) -> float | None:
        """Return the age of the player cache in hours, or ``None`` if absent."""
        path = self.players_cache_path
        if not path.is_file():
            return None
        return (time.time() - path.stat().st_mtime) / 3600.0

    def get_players(
        self,
        force_refresh: bool = False,
        *,
        position: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        """Return the NFL player map, served from disk cache when fresh.

        The unfiltered payload is roughly 5 MB, which Sleeper asks callers to
        fetch no more than once per day; it is cached on disk and only
        re-fetched once the cache exceeds ``players_ttl_hours``. Passing
        ``position`` or ``active`` asks Sleeper to filter server-side instead
        — confirmed live to cut the payload from ~14.6 MB to ~435 KB for
        ``position="QB", active=True`` — but bypasses the disk cache entirely,
        since the cache's contract is specifically the full unfiltered map;
        caching every filter combination separately isn't worth the
        complexity for what nuclearff currently needs this for.

        Args:
            force_refresh: Fetch from the API even if the cache is still
                fresh. Ignored when ``position`` or ``active`` is given, since
                those calls never use the cache.
            position: Only return players whose fantasy positions include
                this position, e.g. ``"QB"``.
            active: When ``True``, only return active players.

        Returns:
            A mapping of Sleeper player ID to player object.
        """
        if position is not None or active is not None:
            query: list[str] = []
            if position is not None:
                query.append(f"position={position}")
            if active is not None:
                query.append(f"active={'true' if active else 'false'}")
            return self._get_dict(f"/v1/players/nfl?{'&'.join(query)}")

        age = self._players_cache_age_hours()
        if not force_refresh and age is not None and age < self.players_ttl_hours:
            logger.debug(
                "Serving Sleeper player map from cache (%.1fh old)",
                age,
            )
            return json.loads(self.players_cache_path.read_text(encoding="utf-8"))

        logger.info(
            "Fetching Sleeper player map (~5MB); this is cached for %dh",
            self.players_ttl_hours,
        )
        players = self._get_dict("/v1/players/nfl")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.players_cache_path.write_text(json.dumps(players), encoding="utf-8")
        return players

    @staticmethod
    def avatar_url(avatar_id: str, thumbnail: bool = False) -> str:
        """Return the CDN URL for an avatar.

        Args:
            avatar_id: Avatar identifier from a user or league object.
            thumbnail: Return the smaller thumbnail variant.

        Returns:
            The fully qualified image URL.
        """
        suffix = "/thumbs" if thumbnail else ""
        return f"{AVATAR_URL}{suffix}/{avatar_id}"
