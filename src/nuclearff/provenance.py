"""Provenance primitives: content hashes, git state, and immutable run manifests.

Every future pipeline run should be traceable back to the exact code, config,
and data that produced it. This module builds the reusable primitives for that
record — content hashing, git-state inspection, and an immutable
:class:`RunManifest` — but nothing calls it yet. It becomes load-bearing once a
pipeline command (for example a future ``rank-wr``) exists to build and write
one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import nuclearff
from nuclearff.config.models import NuclearffConfig

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 5
"""Seconds to wait for a git subprocess before giving up on it."""

_READ_CHUNK_SIZE = 65_536
"""Bytes read per chunk when hashing a file, to avoid loading it whole."""


def sha256_file(path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file's raw bytes.

    Args:
        path: File to hash.

    Returns:
        The lowercase hex digest of the file's contents.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    """Compute the SHA-256 hex digest of a JSON-serializable value's canonical form.

    The value is encoded with sorted keys and compact separators first, so the
    same logical value hashes identically regardless of dict key order. This is
    meant to compose with :meth:`NuclearffConfig.canonical_dict`, which already
    produces a deterministic, JSON-safe dict — call this as
    ``sha256_json(config.canonical_dict())`` rather than re-canonicalizing a
    raw ``model_dump()``.

    Args:
        value: A JSON-serializable value.

    Returns:
        The lowercase hex digest of the canonical JSON encoding.
    """
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_git(
    args: Sequence[str], repo_root: str | Path
) -> subprocess.CompletedProcess[str] | None:
    """Run a git subprocess in ``repo_root``, swallowing every failure mode.

    Args:
        args: Arguments to pass to ``git``, excluding the executable itself.
        repo_root: Directory to run git in.

    Returns:
        The completed process, or ``None`` if git could not be run at all
        (missing executable, or it timed out).
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "Could not run 'git %s' in %s: %s", " ".join(args), repo_root, exc
        )
        return None


def git_commit_sha(repo_root: str | Path = ".") -> str | None:
    """Return the current commit SHA of the git repository at ``repo_root``.

    Args:
        repo_root: Directory to inspect.

    Returns:
        The full commit SHA, or ``None`` if ``repo_root`` is not inside a git
        repository or git is unavailable. Never raises: a manifest must still
        be writable outside a git checkout, just with this field null.
    """
    result = _run_git(["rev-parse", "HEAD"], repo_root)
    if result is None or result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def is_git_dirty(repo_root: str | Path = ".") -> bool | None:
    """Return whether the git repository at ``repo_root`` has uncommitted changes.

    Args:
        repo_root: Directory to inspect.

    Returns:
        ``True`` if ``git status --porcelain`` reports any changes, ``False``
        if the working tree is clean, or ``None`` if this cannot be determined
        (not a repository, or git is unavailable). Never raises: a manifest
        whose commit SHA does not reflect a dirty working tree would be
        misleading, so this is surfaced explicitly rather than assumed clean.
    """
    result = _run_git(["status", "--porcelain"], repo_root)
    if result is None or result.returncode != 0:
        return None
    return bool(result.stdout.strip())


class RunManifest(BaseModel):
    """Immutable record of the code, config, and data behind one pipeline run.

    A manifest is the answer to "what produced this ranking?" months later: the
    exact commit, whether the working tree was clean, the package and Python
    versions, a fingerprint of the configuration, and hashes of whatever inputs
    mattered. Nothing in this project builds one yet — it is a library for a
    future pipeline command to call.

    Args:
        run_id: Caller-supplied stable identifier for this run. By convention
            this matches the ``%Y%m%dT%H%M%SZ`` timestamp-string format
            :mod:`nuclearff.sleeper.snapshot` already uses for its own
            timestamps.
        created_at: UTC timestamp the manifest was assembled.
        git_commit: The current commit SHA, or ``None`` outside a git checkout.
        git_dirty: Whether the working tree had uncommitted changes, or
            ``None`` if that could not be determined.
        package_version: ``nuclearff.__version__`` for this run.
        python_version: The interpreter version this run executed under.
        config_hash: ``sha256_json(config.canonical_dict())`` for whatever
            :class:`NuclearffConfig` produced this run.
        source_hashes: Mapping of a human-readable source label (for example
            ``"sleeper_snapshot"`` or ``"ff_playerids_crosswalk"``) to a
            content hash for whatever inputs actually mattered to this run.
            This class does not second-guess what is worth hashing.
        seed: Random seed used for this run, if any. Nothing calls this with a
            real seed yet; Monte Carlo work will need it later.
        artifact_paths: Paths this run produced, rendered as POSIX strings.

    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    created_at: datetime
    git_commit: str | None
    git_dirty: bool | None
    package_version: str
    python_version: str
    config_hash: str
    source_hashes: dict[str, str] = Field(default_factory=dict)
    seed: int | None = None
    artifact_paths: list[str] = Field(default_factory=list)


def build_run_manifest(
    *,
    run_id: str,
    config: NuclearffConfig,
    repo_root: str | Path = ".",
    source_hashes: Mapping[str, str] | None = None,
    seed: int | None = None,
    artifact_paths: Sequence[str | Path] | None = None,
) -> RunManifest:
    """Assemble a :class:`RunManifest` for the current environment and config.

    Args:
        run_id: Stable identifier for this run.
        config: The configuration that produced this run.
        repo_root: Directory to inspect for git state.
        source_hashes: Mapping of a human-readable source label to a content
            hash for inputs that mattered to this run.
        seed: Random seed used for this run, if any.
        artifact_paths: Paths this run produced.

    Returns:
        A fully populated, immutable run manifest.
    """
    return RunManifest(
        run_id=run_id,
        created_at=datetime.now(tz=UTC),
        git_commit=git_commit_sha(repo_root),
        git_dirty=is_git_dirty(repo_root),
        package_version=nuclearff.__version__,
        python_version=platform.python_version(),
        config_hash=sha256_json(config.canonical_dict()),
        source_hashes=dict(source_hashes) if source_hashes else {},
        seed=seed,
        artifact_paths=[Path(p).as_posix() for p in (artifact_paths or [])],
    )


def write_run_manifest(manifest: RunManifest, manifests_dir: str | Path) -> Path:
    """Write a manifest as deterministic JSON under ``<manifests_dir>/<run_id>.json``.

    A manifest is supposed to be an immutable record, so this refuses to
    overwrite one that already exists for the same ``run_id`` rather than
    silently clobbering it — the same "write once" contract
    :func:`nuclearff.sleeper.snapshot.write_snapshot` enforces for raw
    snapshots.

    Args:
        manifest: The manifest to write.
        manifests_dir: Destination directory, created if absent. See
            :attr:`nuclearff.config.models.PathsConfig.manifests_dir`.

    Returns:
        The path the manifest was written to.

    Raises:
        FileExistsError: If a manifest for this ``run_id`` already exists.
    """
    manifests_dir = Path(manifests_dir)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    target = manifests_dir / f"{manifest.run_id}.json"

    payload = json.loads(manifest.model_dump_json())
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    logger.info("Wrote run manifest to %s", target)
    return target
