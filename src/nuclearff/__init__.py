"""nuclearff: Nuclear Fantasy Football.

League-aware fantasy football research and valuation, built on nflverse data
via nflreadpy and league configuration from the Sleeper API.

The package root exports only the version and logging setup. Everything else
lives in the module that owns it:

- :mod:`nuclearff.config` for configuration models and loading
- :mod:`nuclearff.sleeper` for the Sleeper client and league snapshots
- :mod:`nuclearff.exceptions` for the typed exception hierarchy
- :mod:`nuclearff.cli` for the command-line interface
"""

from nuclearff._version import __version__
from nuclearff.logging_config import configure_logging

__all__ = ["__version__", "configure_logging"]
