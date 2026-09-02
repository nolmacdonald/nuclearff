"""Shared pytest fixtures.

Every fixture here is offline. Sleeper payloads come from JSON captured from the
real league and committed under ``tests/fixtures/sleeper``; HTTP is mocked with
``responses``. No test may reach the network.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SLEEPER_FIXTURES = FIXTURE_DIR / "sleeper"

TEST_BASE_URL = "https://sleeper.test"
"""Base URL used by mocked clients so a real request is unmistakable."""

LEAGUE_ID = "1367225133634191360"
"""The league captured in the fixtures."""


def load_sleeper_fixture(name: str) -> Any:
    """Load a captured Sleeper payload by file name.

    Args:
        name: File name under ``tests/fixtures/sleeper``.

    Returns:
        The decoded JSON payload.
    """
    return json.loads((SLEEPER_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def league_payload() -> dict[str, Any]:
    """The real league object for the 2026 NUCLEARFF REDRAFT league."""
    return load_sleeper_fixture("league.json")


@pytest.fixture
def draft_payload() -> dict[str, Any]:
    """The real draft object, which reports the authoritative round count."""
    return load_sleeper_fixture("draft.json")


@pytest.fixture
def users_payload() -> list[dict[str, Any]]:
    """A two-entry sample of league members."""
    return load_sleeper_fixture("users.json")


@pytest.fixture
def rosters_payload() -> list[dict[str, Any]]:
    """A two-entry sample of league rosters."""
    return load_sleeper_fixture("rosters.json")


@pytest.fixture
def state_payload() -> dict[str, Any]:
    """The captured NFL league-year state."""
    return load_sleeper_fixture("state.json")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail any test that tries to open a socket.

    ``responses`` covers requests made through ``requests``, but this guard is
    broader: it catches a new dependency reaching the network directly, which
    would otherwise pass locally and fail in CI.
    """

    def _blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError(
            "Tests must not access the network. Mock HTTP with `responses` or "
            "add a fixture under tests/fixtures/."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
