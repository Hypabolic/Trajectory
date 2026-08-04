"""Single package-version resolve path (importlib.metadata → pyproject stamp)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def resolve_package_version() -> str:
    """Return the installed distribution version, or a local editable fallback."""
    try:
        return version("hypabolic-trajectory")
    except PackageNotFoundError:
        # Editable/dev fallback only when distribution metadata is absent.
        # Must still match python/pyproject.toml [project].version when present.
        return "0.0.0+local"
