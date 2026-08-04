"""Single package-version resolve path + cycle-safe wire version pins.

Leaf module — safe for project/normalize/identity layers without importing the
package root (avoids root → api → project cycles).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Final

# Wire / identity contract version (canonical envelope normalizer_version).
NORMALIZER_CONTRACT_VERSION: Final[str] = "0.2.0"

# Embedded wire version for Hypabolic envelope normalizer.version and OTEL
# instrumentation_version. MUST match other tip runtimes on the same git tag.
# Today tip + goldens pin "0.1.0". Do NOT unilaterally bind this to
# PACKAGE_VERSION until all runtimes + goldens move.
WIRE_PACKAGE_VERSION: Final[str] = "0.1.0"


def resolve_package_version() -> str:
    """Return the installed distribution version, or a local editable fallback."""
    try:
        return version("hypabolic-trajectory")
    except PackageNotFoundError:
        # Editable/dev fallback only when distribution metadata is absent.
        # Must still match python/pyproject.toml [project].version when present.
        return "0.0.0+local"
