"""AHP empty-page listing stub (Phase 1 / PY-06-ahp).

UNSUPPORTED import path. Self-registers on import as wire source ``ahp``.

Full export-tree discovery is Phase 3. Phase 1 returns an empty page for any
explicit root so ``show --path`` remains the supported discovery path.

Authority:
- contracts/spec/sources/ahp.md §7 (sketch only)
- Peer: Rust ``list_ahp_trajectories``, .NET ``AhpTrajectoryLister``
- docs/python-implementation-spec.md PY-06-ahp
"""

from __future__ import annotations

from pathlib import Path

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListingPage
from hypabolic_trajectory.listing.common import validate_limit
from hypabolic_trajectory.listing.protocol import register_lister


class AhpTrajectoryLister:
    """Empty-page lister for AHP export directories (Phase 1 stub)."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.AHP

    def list_page(
        self,
        *,
        root: str | Path,
        cursor: str | None,
        limit: int,
    ) -> TrajectoryListingPage:
        # Phase 1: snapshot normalize only. Explicit-root export layout listing
        # is Phase 3; return empty so show --path remains the supported path.
        _ = root
        _ = cursor
        validate_limit(limit)
        return TrajectoryListingPage(items=(), next_cursor=None)


# Self-register on import (package root must import this module).
register_lister(AhpTrajectoryLister())


__all__ = ["AhpTrajectoryLister"]
