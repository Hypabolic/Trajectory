"""TrajectoryLister Protocol (signature pin — freeze PY-04a / PY-09a).

UNSUPPORTED import path. Per-source listers self-register by wire name.
``list_trajectories`` only dispatches by registry (PY-09b).

Authority: docs/python-implementation-spec.md §4.1 listing registry Protocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListingPage

# Wire-name → lister. Empty shell until PY-09a/per-source owners register.
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
