"""Unit tests for provenance hashing, git state, and run manifests.

Git state (:func:`git_commit_sha`, :func:`is_git_dirty`) is exercised against a
real temporary git repository created with real ``git`` subprocess calls
rather than mocked, per this project's preference for real-feeling tests
wherever the dependency is safe to assume (git is always available here and
in CI). None of this touches the network, so the autouse ``_no_network``
fixture in ``conftest.py`` is unaffected.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime

import pytest
from pydantic import ValidationError

from nuclearff.config.models import NuclearffConfig, WeightBlocks
from nuclearff.provenance import (
    RunManifest,
    build_run_manifest,
    git_commit_sha,
    is_git_dirty,
    sha256_file,
    sha256_json,
    write_run_manifest,
)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "nuclearff-tests",
    "GIT_AUTHOR_EMAIL": "tests@nuclearff.invalid",
    "GIT_COMMITTER_NAME": "nuclearff-tests",
    "GIT_COMMITTER_EMAIL": "tests@nuclearff.invalid",
}
"""A fixed committer identity so tests never depend on the host's git config."""


def _git(args: list[str], cwd) -> None:
    """Run a git command in ``cwd``, failing the test loudly if it errors.

    ``commit.gpgsign`` is forced off so a host with commit signing enabled
    globally cannot hang or fail this test waiting on a GPG passphrase.
    """
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        env=_GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo_with_commit(repo_dir) -> None:
    """Create a real git repo at ``repo_dir`` with one tracked file committed."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(["init"], repo_dir)
    (repo_dir / "tracked.txt").write_text("hello\n", encoding="utf-8")
    _git(["add", "tracked.txt"], repo_dir)
    _git(["commit", "-m", "initial commit"], repo_dir)


# --- sha256_file -------------------------------------------------------


def test_sha256_file_matches_a_known_digest(tmp_path):
    """The digest matches hashlib computed independently over the same bytes."""
    path = tmp_path / "input.txt"
    path.write_bytes(b"nuclearff provenance test")

    assert sha256_file(path) == hashlib.sha256(b"nuclearff provenance test").hexdigest()


def test_sha256_file_differs_for_different_contents(tmp_path):
    """Two files with different bytes hash differently."""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")

    assert sha256_file(a) != sha256_file(b)


# --- sha256_json ---------------------------------------------------------


def test_sha256_json_is_deterministic_regardless_of_key_order():
    """Dict key order must not change the hash of a logically equal value."""
    assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})


def test_sha256_json_differs_for_different_values():
    """A materially different value produces a different hash."""
    assert sha256_json({"a": 1}) != sha256_json({"a": 2})


def test_sha256_json_differs_for_different_configs():
    """An altered config must change the run fingerprint.

    This is the plan's explicit B.8 requirement: two configs differing only in
    ``model.weights.volume`` must not collide.
    """
    baseline = NuclearffConfig()
    altered = NuclearffConfig(
        model=baseline.model.model_copy(
            update={
                "weights": WeightBlocks(
                    volume=0.40, efficiency=0.35, expected_td=0.10, situation=0.15
                )
            }
        )
    )

    assert sha256_json(baseline.canonical_dict()) != sha256_json(
        altered.canonical_dict()
    )


def test_sha256_json_composes_with_canonical_dict():
    """sha256_json of a config's canonical_dict is stable across calls."""
    config = NuclearffConfig()

    assert sha256_json(config.canonical_dict()) == sha256_json(config.canonical_dict())


# --- git_commit_sha / is_git_dirty ----------------------------------------


def test_git_commit_sha_returns_a_real_looking_sha(tmp_path):
    """A committed repo reports the actual HEAD SHA."""
    _init_repo_with_commit(tmp_path)

    sha = git_commit_sha(tmp_path)

    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert sha == expected
    assert sha is not None
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_is_git_dirty_is_false_right_after_a_commit(tmp_path):
    """A freshly committed repo has a clean working tree."""
    _init_repo_with_commit(tmp_path)

    assert is_git_dirty(tmp_path) is False


def test_is_git_dirty_flips_true_after_an_uncommitted_edit(tmp_path):
    """Modifying a tracked file without committing marks the tree dirty."""
    _init_repo_with_commit(tmp_path)

    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")

    assert is_git_dirty(tmp_path) is True


def test_git_commit_sha_is_none_outside_a_repo(tmp_path):
    """A plain directory with no .git is not mistaken for a repo."""
    assert git_commit_sha(tmp_path) is None


def test_is_git_dirty_is_none_outside_a_repo(tmp_path):
    """Dirtiness cannot be determined outside a git repository."""
    assert is_git_dirty(tmp_path) is None


# --- write_run_manifest ---------------------------------------------------


def _manifest(run_id: str = "20260101T000000Z", **overrides) -> RunManifest:
    """A minimal, valid manifest for tests that do not care about its fields."""
    fields = {
        "run_id": run_id,
        "created_at": datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        "git_commit": None,
        "git_dirty": None,
        "package_version": "0.1.0",
        "python_version": "3.11.9",
        "config_hash": "deadbeef",
    }
    fields.update(overrides)
    return RunManifest(**fields)


def test_write_run_manifest_writes_under_run_id(tmp_path):
    """The manifest lands at <manifests_dir>/<run_id>.json."""
    manifest = _manifest(run_id="20260215T120000Z")

    target = write_run_manifest(manifest, tmp_path)

    assert target == tmp_path / "20260215T120000Z.json"
    assert target.is_file()


def test_write_run_manifest_refuses_to_overwrite(tmp_path):
    """A second write for the same run_id raises rather than silently clobbering."""
    manifest = _manifest(run_id="20260301T000000Z")
    write_run_manifest(manifest, tmp_path)

    with pytest.raises(FileExistsError):
        write_run_manifest(manifest, tmp_path)


def test_write_run_manifest_is_deterministic_json(tmp_path):
    """Output is sorted-key, indented JSON with a trailing newline."""
    manifest = _manifest(
        run_id="20260401T000000Z", source_hashes={"b_source": "2", "a_source": "1"}
    )

    target = write_run_manifest(manifest, tmp_path)

    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    payload = json.loads(text)
    assert list(payload["source_hashes"].keys()) == ["a_source", "b_source"]


# --- build_run_manifest and the full round trip ---------------------------


def test_build_run_manifest_outside_a_repo_has_null_git_fields(tmp_path):
    """Building a manifest outside a git checkout still succeeds."""
    config = NuclearffConfig()

    manifest = build_run_manifest(
        run_id="20260501T000000Z", config=config, repo_root=tmp_path
    )

    assert manifest.git_commit is None
    assert manifest.git_dirty is None
    assert manifest.config_hash == sha256_json(config.canonical_dict())
    assert manifest.source_hashes == {}
    assert manifest.artifact_paths == []
    assert manifest.seed is None


def test_build_run_manifest_full_round_trip(tmp_path):
    """build -> write -> read back from disk -> fields match what was built."""
    repo_dir = tmp_path / "repo"
    _init_repo_with_commit(repo_dir)
    manifests_dir = tmp_path / "manifests"
    config = NuclearffConfig()
    artifact = tmp_path / "artifacts" / "wr_rankings.csv"

    manifest = build_run_manifest(
        run_id="20260601T093000Z",
        config=config,
        repo_root=repo_dir,
        source_hashes={"sleeper_snapshot": "abc123"},
        seed=2026,
        artifact_paths=[artifact],
    )
    written_path = write_run_manifest(manifest, manifests_dir)

    assert written_path == manifests_dir / "20260601T093000Z.json"
    raw = json.loads(written_path.read_text(encoding="utf-8"))
    reloaded = RunManifest.model_validate(raw)

    assert reloaded == manifest
    assert reloaded.run_id == "20260601T093000Z"
    assert reloaded.git_commit == git_commit_sha(repo_dir)
    assert reloaded.git_dirty is False
    assert reloaded.config_hash == sha256_json(config.canonical_dict())
    assert reloaded.source_hashes == {"sleeper_snapshot": "abc123"}
    assert reloaded.seed == 2026
    assert reloaded.artifact_paths == [artifact.as_posix()]


def test_run_manifest_rejects_unknown_fields():
    """extra='forbid' catches a typo'd field rather than silently ignoring it."""
    with pytest.raises(ValidationError):
        _manifest(unexpected_field="oops")


def test_run_manifest_is_frozen():
    """A manifest cannot be mutated after construction."""
    manifest = _manifest()

    with pytest.raises(ValidationError):
        manifest.run_id = "changed"
