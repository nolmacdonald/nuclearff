"""Provider-neutral storage configuration for R2 and S3.

Nuclear FF's durable datasets (curated Parquet, raw Sleeper snapshots) are
meant to live in a private object-store bucket, not only in a developer's
local DuckDB file — see the cloud data platform epic (GitHub Issue 182) for
the full design. Cloudflare R2 is the initial provider (free tier, S3-
compatible API); AWS S3 is an explicit future migration target. This module
is the one place provider differences (endpoint URL, region, credentials)
live, so the rest of :mod:`nuclearff.data` and every caller can stay
provider-agnostic.

Credential/bucket validation happens in :meth:`StorageConfig.from_env`, not
at import time, so CLI help, documentation builds, and unit tests remain
usable without live credentials — the same posture
:func:`nuclearff.config.league.load_league_config` already takes for its own
required fields.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nuclearff.exceptions import ConfigError

StorageProvider = Literal["r2", "s3"]
"""The two supported object-store backends. See the module docstring."""

_DEFAULT_PROVIDER: StorageProvider = "r2"
_DEFAULT_PREFIX = "prod"
_DEFAULT_CACHE_DIR = "data/cache/object-store"
_REGION_DEFAULTS: dict[StorageProvider, str] = {"r2": "auto", "s3": "us-west-2"}


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Provider-neutral object-store settings.

    Args:
        provider: ``"r2"`` or ``"s3"``.
        bucket: The bucket name.
        prefix: Environment prefix (``"dev"``, ``"staging"``, ``"prod"``)
            separating environments within one bucket.
        region: ``"auto"`` for R2, or the real AWS region for S3.
        endpoint_url: Required for R2, normally omitted (``None``) for S3.
        access_key_id: Credential, or ``None`` to let the SDK's own
            credential chain resolve it (typical for S3 on AWS-aware
            infrastructure).
        secret_access_key: Paired with ``access_key_id``.
        session_token: Optional temporary-credential session token.
        cache_dir: Local directory for the verified object cache (see the
            future verified-cache module tracked in Issue 187).

    """

    provider: StorageProvider
    bucket: str
    prefix: str
    region: str
    endpoint_url: str | None
    access_key_id: str | None
    secret_access_key: str | None
    session_token: str | None
    cache_dir: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> StorageConfig:
        """Build a :class:`StorageConfig` from environment variables.

        Args:
            env: Mapping to read from instead of ``os.environ`` — for tests
                that need isolation from the real process environment
                without monkeypatching it.

        Returns:
            A validated storage configuration.

        Raises:
            ConfigError: If ``STORAGE_PROVIDER`` is not ``"r2"`` or ``"s3"``,
                ``STORAGE_BUCKET`` is missing, or ``STORAGE_ENDPOINT_URL`` is
                missing while ``STORAGE_PROVIDER=r2``.
        """
        source = env if env is not None else os.environ

        provider = source.get("STORAGE_PROVIDER", _DEFAULT_PROVIDER).lower()
        if provider not in ("r2", "s3"):
            raise ConfigError(f"Unsupported STORAGE_PROVIDER: {provider!r}")

        bucket = source.get("STORAGE_BUCKET")
        if not bucket:
            raise ConfigError("STORAGE_BUCKET is required")

        endpoint_url = source.get("STORAGE_ENDPOINT_URL") or None
        if provider == "r2" and endpoint_url is None:
            raise ConfigError(
                "STORAGE_ENDPOINT_URL is required for STORAGE_PROVIDER=r2"
            )

        return cls(
            provider=provider,
            bucket=bucket,
            prefix=source.get("STORAGE_PREFIX", _DEFAULT_PREFIX).strip("/"),
            region=source.get("STORAGE_REGION", _REGION_DEFAULTS[provider]),
            endpoint_url=endpoint_url,
            access_key_id=source.get("STORAGE_ACCESS_KEY_ID") or None,
            secret_access_key=source.get("STORAGE_SECRET_ACCESS_KEY") or None,
            session_token=source.get("STORAGE_SESSION_TOKEN") or None,
            cache_dir=Path(source.get("NUCLEARFF_CACHE_DIR", _DEFAULT_CACHE_DIR)),
        )
