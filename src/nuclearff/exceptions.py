"""Typed exceptions for nuclearff.

Every failure that crosses a module boundary raises one of these rather than a
bare ``Exception``, so callers can distinguish a configuration mistake from a
transient upstream outage.
"""

from __future__ import annotations


class NuclearffError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(NuclearffError):
    """Raised when configuration is missing, malformed, or internally inconsistent."""


class SleeperAPIError(NuclearffError):
    """Base class for failures talking to the Sleeper API."""


class SleeperHTTPError(SleeperAPIError):
    """Raised when a Sleeper request fails with a non-success status code.

    Args:
        url: The request URL that failed.
        status_code: The HTTP status code returned, if a response was received.
        message: Optional additional detail.

    Attributes:
        url: The request URL that failed.
        status_code: The HTTP status code returned, or ``None`` on a transport error.
    """

    def __init__(
        self, url: str, status_code: int | None = None, message: str | None = None
    ) -> None:
        self.url = url
        self.status_code = status_code

        detail = f" ({message})" if message else ""
        status = status_code if status_code is not None else "no response"
        super().__init__(f"Sleeper request to {url} failed with {status}{detail}")


class SleeperResponseError(SleeperAPIError):
    """Raised when a Sleeper response is well-formed HTTP but not the expected shape.

    Args:
        url: The request URL whose payload was unexpected.
        message: What was expected and what arrived instead.

    Attributes:
        url: The request URL whose payload was unexpected.
    """

    def __init__(self, url: str, message: str) -> None:
        self.url = url
        super().__init__(f"Unexpected Sleeper response from {url}: {message}")


class StorageError(NuclearffError):
    """Raised when a local DuckDB table cannot be read as expected.

    Args:
        table_name: The table that could not be read.
        db_path: The database file it was expected in.
        message: What went wrong, or what to do about it.

    Attributes:
        table_name: The table that could not be read.
        db_path: The database file it was expected in.
    """

    def __init__(self, table_name: str, db_path: str, message: str) -> None:
        self.table_name = table_name
        self.db_path = db_path
        super().__init__(
            f"Could not read table {table_name!r} from {db_path}: {message}"
        )


class ObjectStoreError(NuclearffError):
    """Raised when an R2/S3 object-store operation fails or a key is unsafe.

    Covers both an unsafe object key (:func:`nuclearff.data.keys.validate_key`
    — a key that escapes its configured prefix before any request is made)
    and a failed live request (:mod:`nuclearff.data.object_store` wrapping a
    ``botocore`` exception) under one type, so callers do not need to know
    which layer rejected the operation.

    Args:
        key: The object key involved, if any.
        message: What went wrong.

    Attributes:
        key: The object key involved, or ``None``.
    """

    def __init__(self, key: str | None, message: str) -> None:
        self.key = key
        detail = f" (key={key!r})" if key is not None else ""
        super().__init__(f"Object store error{detail}: {message}")
