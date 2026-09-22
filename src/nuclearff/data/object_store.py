"""A provider-neutral R2/S3 object-store adapter built on boto3.

Cloudflare R2 exposes an S3-compatible API — the same ``boto3`` S3 client
works against both R2 and AWS S3 by changing the endpoint URL and a couple
of client-config knobs (guide §9, GitHub Issue 184). :class:`ObjectStore` is
the one place that boto3 surface is touched; every caller elsewhere in
nuclearff goes through this class, never ``boto3`` directly, so a future S3
cutover (GitHub Issue 182 §22) only needs a different
:class:`~nuclearff.data.config.StorageConfig`, not different call sites.

Every method that takes an exact object key validates it first via
:func:`nuclearff.data.keys.validate_key`, rejecting a key that has escaped
its configured environment prefix before a request is ever issued (guide
§9/§18's security controls). ``iter_keys`` takes a *listing* prefix instead
of an exact key (it may legitimately equal the environment prefix itself,
with no object beneath it yet), so that check doesn't apply there — read
scope for listing is the caller's responsibility.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from nuclearff.data.config import StorageConfig
from nuclearff.data.keys import validate_key
from nuclearff.exceptions import ObjectStoreError

_MAX_ATTEMPTS = 5
"""Bounded retry attempts for a retryable failure, via botocore's own
"standard" retry mode rather than hand-rolled retry logic."""


@contextmanager
def _wrap_errors(key: str | None) -> Iterator[None]:
    """Reraise a botocore failure as :class:`ObjectStoreError`.

    Args:
        key: The object key involved, for the error message.

    Raises:
        ObjectStoreError: Wrapping whatever ``botocore`` raised.
    """
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        raise ObjectStoreError(key, str(exc)) from exc


class ObjectStore:
    """A thin, provider-neutral wrapper over a boto3 S3 client.

    Args:
        config: Provider and credential settings
            (:class:`~nuclearff.data.config.StorageConfig`).

    """

    def __init__(self, config: StorageConfig) -> None:
        self.config = config
        self.client = self._build_client(config)

    @staticmethod
    def _build_client(config: StorageConfig) -> BaseClient:
        """Construct the underlying boto3 S3 client for ``config``'s provider.

        Args:
            config: Provider and credential settings.

        Returns:
            A configured boto3 S3 client. R2 additionally gets ``s3v4``
            signing and path-style addressing — Cloudflare's documented
            requirement for using the AWS SDK against R2.
        """
        botocore_kwargs: dict[str, Any] = {
            "retries": {"mode": "standard", "max_attempts": _MAX_ATTEMPTS},
        }
        if config.provider == "r2":
            botocore_kwargs["signature_version"] = "s3v4"
            botocore_kwargs["s3"] = {"addressing_style": "path"}

        client_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": config.region,
            "config": Config(**botocore_kwargs),
        }
        if config.endpoint_url is not None:
            client_kwargs["endpoint_url"] = config.endpoint_url
        if config.access_key_id is not None:
            client_kwargs["aws_access_key_id"] = config.access_key_id
        if config.secret_access_key is not None:
            client_kwargs["aws_secret_access_key"] = config.secret_access_key
        if config.session_token is not None:
            client_kwargs["aws_session_token"] = config.session_token

        return boto3.client(**client_kwargs)

    def put_file(
        self,
        local_path: Path,
        key: str,
        *,
        content_type: str,
        sha256: str,
    ) -> None:
        """Upload a local file, recording its digest as object metadata.

        Args:
            local_path: File to upload.
            key: Destination object key.
            content_type: MIME type to record on the object.
            sha256: The file's already-computed SHA-256 hex digest, stored
                as ``x-amz-meta-sha256`` — not trusted as verification on its
                own; a caller that needs to *verify* an upload issues a
                separate :meth:`head` and compares.

        Raises:
            ObjectStoreError: If ``key`` is unsafe, or the upload fails.
        """
        validate_key(key, prefix=self.config.prefix)
        with _wrap_errors(key):
            self.client.upload_file(
                str(local_path),
                self.config.bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"sha256": sha256},
                },
            )

    def put_bytes(
        self,
        payload: bytes,
        key: str,
        *,
        content_type: str,
    ) -> None:
        """Upload an in-memory payload (typically a manifest) directly.

        Args:
            payload: Bytes to upload.
            key: Destination object key.
            content_type: MIME type to record on the object.

        Raises:
            ObjectStoreError: If ``key`` is unsafe, or the upload fails.
        """
        validate_key(key, prefix=self.config.prefix)
        with _wrap_errors(key):
            self.client.put_object(
                Bucket=self.config.bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
            )

    def download_file(self, key: str, destination: Path) -> None:
        """Download an object to a local path, atomically.

        Downloads to a sibling ``.part`` path first and renames it into
        place only on success, so a reader can never observe a partially
        written file at ``destination``.

        Args:
            key: Object key to download.
            destination: Local path to write to. Parent directories are
                created if absent.

        Raises:
            ObjectStoreError: If ``key`` is unsafe, or the download fails.
        """
        validate_key(key, prefix=self.config.prefix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        with _wrap_errors(key):
            self.client.download_file(
                self.config.bucket,
                key,
                str(temporary),
            )
        temporary.replace(destination)

    def get_bytes(self, key: str) -> bytes:
        """Read an object's full contents into memory.

        Args:
            key: Object key to read.

        Returns:
            The object's raw bytes.

        Raises:
            ObjectStoreError: If ``key`` is unsafe, or the read fails.
        """
        validate_key(key, prefix=self.config.prefix)
        with _wrap_errors(key):
            response = self.client.get_object(
                Bucket=self.config.bucket,
                Key=key,
            )
            body = response["Body"]
            return body.read()  # type: ignore[no-any-return]

    def head(self, key: str) -> dict[str, Any]:
        """Fetch an object's metadata without downloading its body.

        Args:
            key: Object key to inspect.

        Returns:
            The raw ``head_object`` response (size, content type, the
            ``sha256`` metadata set by :meth:`put_file`, etc.).

        Raises:
            ObjectStoreError: If ``key`` is unsafe, or the object doesn't
                exist, or the request fails.
        """
        validate_key(key, prefix=self.config.prefix)
        with _wrap_errors(key):
            return self.client.head_object(  # type: ignore[no-any-return]
                Bucket=self.config.bucket,
                Key=key,
            )

    def iter_keys(self, prefix: str) -> Iterator[str]:
        """List every object key under a listing prefix.

        Args:
            prefix: The listing prefix, e.g. ``"prod/releases/"`` — unlike
                every other method here, this is a prefix to list under, not
                an exact object key, so :func:`~nuclearff.data.keys.validate_key`
                does not apply (see the module docstring).

        Yields:
            Object keys, in the order the store returns them.

        Raises:
            ObjectStoreError: If listing fails.
        """
        paginator = self.client.get_paginator("list_objects_v2")
        with _wrap_errors(prefix):
            for page in paginator.paginate(
                Bucket=self.config.bucket,
                Prefix=prefix,
            ):
                for item in page.get("Contents", []):
                    yield item["Key"]
