"""Hermes empty-page listing stub (core SQLite-free policy / PY-06-hermes).

UNSUPPORTED import path. Self-registers on import as wire source ``hermes``.

Core packages stay SQLite-free: presence of ``state.db`` cannot be enumerated
without an embedded SQLite reader. Missing or present stores yield an empty
page. Full session-row export still happens by feeding message JSON to the
decoder (normalize path). Optional provider packages may replace this lister.

``root`` may be the database file itself or the directory containing it
(default peer layout ``~/.hermes/state.db`` is sample-CLI only — library paths
never consult ``$HOME``).

Authority:
- docs/python-implementation-spec.md PY-06-hermes + empty listing policy
- Peer: Rust ``list_hermes_trajectories``, .NET ``HermesTrajectoryLister``,
  TS ``listHermesTrajectories`` / ``discoverHermes``
"""

from __future__ import annotations

from pathlib import Path

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListingPage
from hypabolic_trajectory.listing.common import paginate
from hypabolic_trajectory.listing.protocol import register_lister


class HermesTrajectoryLister:
    """Empty-page lister for Hermes SQLite store locators (core stub)."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.HERMES

    def list_page(
        self,
        *,
        root: str | Path,
        cursor: str | None,
        limit: int,
    ) -> TrajectoryListingPage:
        # Resolve path for parity with peers (side-effect free observation).
        # Without an embedded SQLite reader, presence alone cannot yield rows.
        _ = _resolve_store_path(root)
        # Shared paginate validates limit + opaque cursor even for empty discovery
        # (TS listDiscovered peer pin).
        return paginate((), cursor=cursor, limit=limit)


def _resolve_store_path(root: str | Path) -> Path:
    """Return ``state.db`` path: root itself when it ends with ``.db``, else join.

    Library listing never falls back to ``$HOME`` — callers always pass an
    explicit root (conformance / free-function entry).
    """
    if not isinstance(root, (str, Path)):
        raise TypeError("root must be str or Path")
    path = Path(root)
    if path.suffix.lower() == ".db":
        return path
    return path / "state.db"


# Self-register on import (package root must import this module).
register_lister(HermesTrajectoryLister())


__all__ = ["HermesTrajectoryLister"]
