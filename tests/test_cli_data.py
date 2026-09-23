"""Smoke tests for the ``nuclearff data`` command group.

Every test injects a fake in place of the real network boundary: a fake
``fetch_player_week`` loader for nflverse, and a fake, in-memory
`ObjectStore` for R2/S3 -- the same seams :func:`nuclearff.cli._cmd_data_publish`
and friends were built around, and the same pattern
`tests/test_data_publisher.py`'s own ``_FakeStore`` already uses. No test
here needs real credentials or a network call.
"""

from __future__ import annotations

import io
from pathlib import Path

import polars as pl
import pytest

import nuclearff.cli as cli
from nuclearff.cli import EXIT_ERROR, EXIT_OK, main
from nuclearff.exceptions import ObjectStoreError


def _raw_fixture(season: int = 2024) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": pl.Series([season, season], dtype=pl.Int32),
            "week": pl.Series([1, 1], dtype=pl.Int32),
            "player_id": ["00-0023459", "00-0023853"],
            "player_name": ["A.Rodgers", "M.Prater"],
            "team": ["NYJ", "ARI"],
        }
    )


class _FakeStore:
    """A minimal, in-memory stand-in for `ObjectStore` -- no network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, object]] = {}

    def put_file(self, local_path: Path, key: str, *, content_type: str, sha256: str):
        payload = local_path.read_bytes()
        self.objects[key] = payload
        self.metadata[key] = {
            "ContentLength": len(payload),
            "Metadata": {"sha256": sha256},
        }

    def put_bytes(self, payload: bytes, key: str, *, content_type: str):
        self.objects[key] = payload

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise ObjectStoreError(key, "not found")
        return self.objects[key]

    def head(self, key: str) -> dict[str, object]:
        return self.metadata[key]

    def download_file(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.get_bytes(key))

    def iter_keys(self, prefix: str):
        return (key for key in sorted(self.objects) if key.startswith(prefix))


@pytest.fixture(autouse=True)
def _fake_loader(monkeypatch):
    monkeypatch.setattr(
        cli, "fetch_player_week", lambda seasons: _raw_fixture(seasons[0])
    )


@pytest.fixture(autouse=True)
def _storage_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PROVIDER", "r2")
    monkeypatch.setenv("STORAGE_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "STORAGE_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com"
    )
    # Independent of --root by design (StorageConfig.from_env reads process
    # env, not NuclearffConfig) -- pinned to a tmp dir so `materialize`
    # never writes into this repo's real data/cache/object-store.
    monkeypatch.setenv("NUCLEARFF_CACHE_DIR", str(tmp_path / "object-store-cache"))
    monkeypatch.delenv("STORAGE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("STORAGE_SECRET_ACCESS_KEY", raising=False)


@pytest.fixture
def fake_store(monkeypatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(cli, "ObjectStore", lambda config: store)
    return store


# --- publish ---------------------------------------------------------------


def test_publish_dry_run_writes_local_parquet_without_touching_the_store(
    tmp_path, capsys, monkeypatch
):
    def _unexpected(config):
        raise AssertionError("dry-run must never construct an ObjectStore")

    monkeypatch.setattr(cli, "ObjectStore", _unexpected)

    exit_code = main(
        ["--root", str(tmp_path), "data", "publish", "--season", "2024", "--dry-run"]
    )

    assert exit_code == EXIT_OK
    written = list(tmp_path.rglob("part-000.parquet"))
    assert len(written) == 1
    assert pl.read_parquet(written[0]).height == 2
    assert "Dry run" in capsys.readouterr().out


def test_publish_without_season_uses_current_season(tmp_path, fake_store, monkeypatch):
    monkeypatch.setattr(cli, "current_season", lambda: 2031)

    exit_code = main(["--root", str(tmp_path), "data", "publish"])

    assert exit_code == EXIT_OK
    curated_key = next(k for k in fake_store.objects if "/curated/player_week/" in k)
    assert "season=2031" in curated_key


def test_publish_uploads_artifacts_and_commits_latest_json(
    tmp_path, capsys, fake_store
):
    exit_code = main(["--root", str(tmp_path), "data", "publish", "--season", "2024"])

    assert exit_code == EXIT_OK
    assert "prod/manifests/latest.json" in fake_store.objects
    curated_keys = [k for k in fake_store.objects if "/curated/player_week/" in k]
    assert len(curated_keys) == 1
    assert "season=2024" in curated_keys[0]
    assert "Published release" in capsys.readouterr().out


def test_publish_through_week_narrows_before_upload(tmp_path, fake_store, monkeypatch):
    def _two_weeks(seasons):
        return pl.DataFrame(
            {
                "season": pl.Series([2024, 2024], dtype=pl.Int32),
                "week": pl.Series([1, 2], dtype=pl.Int32),
                "player_id": ["00-0023459", "00-0023459"],
                "player_name": ["A.Rodgers", "A.Rodgers"],
                "team": ["NYJ", "NYJ"],
            }
        )

    monkeypatch.setattr(cli, "fetch_player_week", _two_weeks)

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "data",
            "publish",
            "--season",
            "2024",
            "--through-week",
            "1",
        ]
    )

    assert exit_code == EXIT_OK
    curated_key = next(k for k in fake_store.objects if "/curated/player_week/" in k)
    uploaded = pl.read_parquet(io.BytesIO(fake_store.objects[curated_key]))
    assert uploaded["week"].to_list() == [1]


def test_publish_without_storage_bucket_fails_with_a_clear_error(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("STORAGE_BUCKET", raising=False)

    exit_code = main(["--root", str(tmp_path), "data", "publish", "--season", "2024"])

    assert exit_code == EXIT_ERROR
    assert "STORAGE_BUCKET" in capsys.readouterr().err


# --- verify-latest / materialize / list-releases / rollback ----------------


def test_verify_latest_prints_the_current_manifest(tmp_path, capsys, fake_store):
    main(["--root", str(tmp_path), "data", "publish", "--season", "2024"])
    capsys.readouterr()

    exit_code = main(["data", "verify-latest"])

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "player_week" in out
    assert "2 rows" in out


def test_materialize_downloads_every_object_in_the_current_release(
    tmp_path, capsys, fake_store
):
    main(["--root", str(tmp_path), "data", "publish", "--season", "2024"])
    capsys.readouterr()

    exit_code = main(["--root", str(tmp_path), "data", "materialize"])

    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert "->" in out


def test_list_releases_lists_every_published_run_id(tmp_path, capsys, fake_store):
    main(["--root", str(tmp_path), "data", "publish", "--season", "2024"])
    first_run_id = (
        next(k for k in fake_store.objects if k.startswith("prod/manifests/history/"))
        .removeprefix("prod/manifests/history/")
        .removesuffix(".json")
    )
    capsys.readouterr()

    exit_code = main(["data", "list-releases"])

    assert exit_code == EXIT_OK
    assert first_run_id in capsys.readouterr().out


def test_rollback_points_latest_back_at_an_older_release(tmp_path, capsys, fake_store):
    main(["--root", str(tmp_path), "data", "publish", "--season", "2024"])
    old_run_id = (
        next(k for k in fake_store.objects if k.startswith("prod/manifests/history/"))
        .removeprefix("prod/manifests/history/")
        .removesuffix(".json")
    )
    capsys.readouterr()

    exit_code = main(["data", "rollback", old_run_id])

    assert exit_code == EXIT_OK
    assert f"run_id={old_run_id}" in capsys.readouterr().out
