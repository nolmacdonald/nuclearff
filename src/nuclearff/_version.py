"""Version information for nuclearff."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nuclearff")
except PackageNotFoundError:  # pragma: no cover
    # Package is not installed (e.g. running from source without install).
    __version__ = "unknown"
