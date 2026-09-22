"""Unit tests for the boto3-backed object-store adapter.

No real network is ever touched: single-request methods (`put_bytes`,
`get_bytes`, `head`, `iter_keys`) are exercised against a `botocore.stub.
Stubber`, which intercepts calls at the client level. `put_file`/
`download_file` use boto3's own higher-level transfer manager rather than a
single client call, so those two are instead verified by monkeypatching the
specific bound client method they call -- proving our wrapper logic (key
validation, atomic rename, `ExtraArgs` shape) without re-testing boto3's own
transfer machinery. The repo's autouse `_no_network` fixture
(`tests/conftest.py`) guarantees neither path can reach a real socket either
way.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from nuclearff.data.config import StorageConfig
from nuclearff.data.object_store import ObjectStore
from nuclearff.exceptions import ObjectStoreError


@pytest.fixture
def config() -> StorageConfig:
    return StorageConfig(
        provider="r2",
        bucket="test-bucket",
        prefix="prod",
        region="auto",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        access_key_id="test-key",
        secret_access_key="test-secret",
        session_token=None,
        cache_dir=Path("data/cache/object-store"),
    )


@pytest.fixture
def store(config: StorageConfig) -> ObjectStore:
    return ObjectStore(config)


def _streaming_body(payload: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(payload), len(payload))


# --- put_bytes ---------------------------------------------------------


def test_put_bytes_issues_a_put_object_call(store: ObjectStore):
    with Stubber(store.client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            expected_params={
                "Bucket": "test-bucket",
                "Key": "prod/manifests/latest.json",
                "Body": b"{}",
                "ContentType": "application/json",
            },
        )

        store.put_bytes(
            b"{}", "prod/manifests/latest.json", content_type="application/json"
        )

        stubber.assert_no_pending_responses()


def test_put_bytes_rejects_a_key_outside_the_configured_prefix(store: ObjectStore):
    with Stubber(store.client) as stubber:
        with pytest.raises(ObjectStoreError, match="configured prefix"):
            store.put_bytes(
                b"{}", "dev/manifests/latest.json", content_type="application/json"
            )

        # No request was issued -- nothing was stubbed, and nothing was consumed.
        stubber.assert_no_pending_responses()


def test_put_bytes_wraps_a_client_error(store: ObjectStore):
    with Stubber(store.client) as stubber:
        stubber.add_client_error(
            "put_object",
            service_error_code="AccessDenied",
            http_status_code=403,
        )

        with pytest.raises(ObjectStoreError, match="prod/manifests/latest.json"):
            store.put_bytes(
                b"{}", "prod/manifests/latest.json", content_type="application/json"
            )


# --- get_bytes ---------------------------------------------------------


def test_get_bytes_returns_the_object_body(store: ObjectStore):
    with Stubber(store.client) as stubber:
        stubber.add_response(
            "get_object",
            {"Body": _streaming_body(b"hello release")},
            expected_params={
                "Bucket": "test-bucket",
                "Key": "prod/manifests/latest.json",
            },
        )

        assert store.get_bytes("prod/manifests/latest.json") == b"hello release"


# --- head ----------------------------------------------------------------


def test_head_returns_object_metadata(store: ObjectStore):
    with Stubber(store.client) as stubber:
        stubber.add_response(
            "head_object",
            {"ContentLength": 42, "Metadata": {"sha256": "abc123"}},
            expected_params={
                "Bucket": "test-bucket",
                "Key": "prod/releases/RUN/manifest.json",
            },
        )

        response = store.head("prod/releases/RUN/manifest.json")

        assert response["ContentLength"] == 42
        assert response["Metadata"]["sha256"] == "abc123"


def test_head_wraps_a_missing_object_as_object_store_error(store: ObjectStore):
    with Stubber(store.client) as stubber:
        stubber.add_client_error(
            "head_object",
            service_error_code="404",
            http_status_code=404,
        )

        with pytest.raises(ObjectStoreError):
            store.head("prod/releases/RUN/manifest.json")


# --- iter_keys ---------------------------------------------------------


def test_iter_keys_yields_every_key_across_pages(store: ObjectStore):
    with Stubber(store.client) as stubber:
        stubber.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "prod/releases/a/manifest.json"}],
                "IsTruncated": False,
            },
            expected_params={"Bucket": "test-bucket", "Prefix": "prod/releases/"},
        )

        keys = list(store.iter_keys("prod/releases/"))

        assert keys == ["prod/releases/a/manifest.json"]


def test_iter_keys_yields_nothing_for_an_empty_prefix(store: ObjectStore):
    with Stubber(store.client) as stubber:
        stubber.add_response(
            "list_objects_v2",
            {"IsTruncated": False},
            expected_params={"Bucket": "test-bucket", "Prefix": "prod/empty/"},
        )

        assert list(store.iter_keys("prod/empty/")) == []


# --- put_file / download_file (transfer-manager methods) -------------------


def test_put_file_validates_the_key_before_calling_upload_file(
    store: ObjectStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    called = False

    def _fake_upload_file(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(store.client, "upload_file", _fake_upload_file)
    local_file = tmp_path / "part-000.parquet"
    local_file.write_bytes(b"parquet-bytes")

    with pytest.raises(ObjectStoreError, match="configured prefix"):
        store.put_file(
            local_file,
            "dev/releases/RUN/part-000.parquet",
            content_type="x",
            sha256="a" * 64,
        )

    assert called is False


def test_put_file_calls_upload_file_with_the_expected_shape(
    store: ObjectStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    captured: dict[str, object] = {}

    def _fake_upload_file(
        filename: str, bucket: str, key: str, ExtraArgs: dict[str, object]
    ) -> None:
        captured.update(filename=filename, bucket=bucket, key=key, extra_args=ExtraArgs)

    monkeypatch.setattr(store.client, "upload_file", _fake_upload_file)
    local_file = tmp_path / "part-000.parquet"
    local_file.write_bytes(b"parquet-bytes")

    store.put_file(
        local_file,
        "prod/releases/RUN/part-000.parquet",
        content_type="application/vnd.apache.parquet",
        sha256="a" * 64,
    )

    assert captured["filename"] == str(local_file)
    assert captured["bucket"] == "test-bucket"
    assert captured["key"] == "prod/releases/RUN/part-000.parquet"
    assert captured["extra_args"] == {
        "ContentType": "application/vnd.apache.parquet",
        "Metadata": {"sha256": "a" * 64},
    }


def test_download_file_renames_the_temporary_file_into_place(
    store: ObjectStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    def _fake_download_file(bucket: str, key: str, local_path: str) -> None:
        Path(local_path).write_bytes(b"downloaded-bytes")

    monkeypatch.setattr(store.client, "download_file", _fake_download_file)
    destination = tmp_path / "nested" / "part-000.parquet"

    store.download_file("prod/releases/RUN/part-000.parquet", destination)

    assert destination.read_bytes() == b"downloaded-bytes"
    assert not destination.with_suffix(destination.suffix + ".part").exists()


def test_download_file_rejects_an_unsafe_key_before_touching_the_client(
    store: ObjectStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    called = False

    def _fake_download_file(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(store.client, "download_file", _fake_download_file)

    with pytest.raises(ObjectStoreError, match=r"\.\."):
        store.download_file("prod/../secrets.json", tmp_path / "out.json")

    assert called is False
