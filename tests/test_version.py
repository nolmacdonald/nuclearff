"""Unit tests for nuclearff version."""

from __future__ import annotations

import nuclearff


def test_version_exists():
    """__version__ is defined."""
    assert hasattr(nuclearff, "__version__")


def test_version_string():
    """__version__ is a non-empty string."""
    assert isinstance(nuclearff.__version__, str)
    assert nuclearff.__version__
