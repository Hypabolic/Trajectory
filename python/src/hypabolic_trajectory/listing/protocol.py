"""TrajectoryLister Protocol + empty registry shell (PY-04a / PY-09a).

UNSUPPORTED import path. Per-source listers self-register by wire name.
``list_trajectories`` only dispatches by registry (PY-09b) and must not be
edited by lister owners.

Authority: docs/python-implementation-spec.md §4.1 listing registry Protocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListingPage

# Wire-name → lister. Empty until per-source owners (PY-05a/05b/06-*) register
# on package import. PY-09a lands the shell only — no built-in listers yet.
_LISTERS: dict[str, TrajectoryLister] = {}


class TrajectoryLister(Protocol):
    @property
    def source(self) -> TrajectorySource: ...

    def list_page(
        self,
        *,
        root: str | Path,
        cursor: str | None,
        limit: int,
    ) -> TrajectoryListingPage: ...


def register_lister(lister: TrajectoryLister) -> None:
    """Register a built-in lister by wire name (idempotent replace)."""
    _LISTERS[lister.source.value] = lister


def get_lister(wire_name: str) -> TrajectoryLister | None:
    """Return the registered lister for ``wire_name``, or None."""
    return _LISTERS.get(wire_name)


def registered_lister_names() -> frozenset[str]:
    """Wire names with a registered lister (for tests / introspection)."""
    return frozenset(_LISTERS.keys())


def clear_listers_for_tests() -> None:
    """Clear the registry. Test helper only — not a public API."""
    _LISTERS.clear()
