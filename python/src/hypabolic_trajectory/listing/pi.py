"""Pi trajectory lister (explicit-root discovery).

UNSUPPORTED import path. Self-registers as wire name ``pi`` on package import
under the PY-04a export owner. Registers only — does not edit
``list_trajectories`` dispatcher body or runtime-capabilities claims.

Discovery layout (contracts/spec/listing.md):
``<root>/sessions/<project>/*.jsonl`` — one project-directory level.
Missing / inaccessible stores yield an empty page (skip concurrent removals).
Library paths never consult the process home directory (Rust tip pin).

Authority:
- contracts/spec/listing.md
- docs/python-implementation-spec.md PY-05a
- Peer: Rust ``list_pi_trajectories``, TS ``discoverPi``, .NET ``PiTrajectoryLister``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import INT64_MAX
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.listing.common import paginate
from hypabolic_trajectory.listing.protocol import register_lister
from hypabolic_trajectory.timestamps import format_ms

_SOURCE_LABEL: Final[str] = "Pi"


class PiTrajectoryLister:
    """Discover Pi session JSONL files under an explicit agent root."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.PI

    def list_page(
        self,
        *,
        root: str | Path,
        cursor: str | None,
        limit: int,
    ) -> TrajectoryListingPage:
        items = _discover(root)
        return paginate(items, cursor=cursor, limit=limit)


def _discover(root: str | Path) -> list[TrajectoryListing]:
    if not isinstance(root, (str, Path)):
        raise TypeError("root must be str or Path")
    root_path = Path(root)
    sessions_root = root_path / "sessions"
    items: list[TrajectoryListing] = []

    try:
        project_entries = list(os.scandir(sessions_root))
    except FileNotFoundError:
        return items
    except OSError:
        # Permission / inaccessible — empty page (listing.md).
        return items

    for project in project_entries:
        try:
            is_dir = project.is_dir(follow_symlinks=True)
        except OSError:
            continue
        if not is_dir:
            continue
        try:
            file_entries = list(os.scandir(project.path))
        except OSError:
            continue
        for entry in file_entries:
            try:
                if not entry.is_file(follow_symlinks=True):
                    continue
            except OSError:
                continue
            name = entry.name
            if not name.endswith(".jsonl"):
                continue
            path = Path(entry.path)
            try:
                items.append(_listing_from_path(path))
            except OSError:
                # Inaccessible or concurrently removed only (listing.md).
                # Domain TrajectoryError from _listing_from_path propagates.
                continue
    return items


def _listing_from_path(path: Path) -> TrajectoryListing:
    try:
        stat = path.stat()
    except OSError as error:
        # Re-raise as OSError for caller skip; do not chain into domain errors.
        raise error from None

    stem = path.stem
    # Peer rejects stems that are not valid Unicode (surrogate-escaped OsStr).
    try:
        stem.encode("utf-8")
        str(path).encode("utf-8")
    except UnicodeEncodeError:
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            f"A {_SOURCE_LABEL} transcript filename is not valid Unicode.",
        ) from None

    # Prefer nanosecond mtime → epoch ms (floor).
    try:
        mtime_ns = stat.st_mtime_ns
    except AttributeError:
        mtime_ns = int(stat.st_mtime * 1_000_000_000)
    if mtime_ns < 0:
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            f"A {_SOURCE_LABEL} transcript timestamp precedes the Unix epoch.",
        ) from None
    milliseconds = mtime_ns // 1_000_000
    # format_ms validates int64 range and representable UTC.
    updated_at = format_ms(milliseconds)

    size = stat.st_size
    if type(size) is not int or size < 0 or size > INT64_MAX:
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            f"A {_SOURCE_LABEL} transcript size is out of range.",
        ) from None

    return TrajectoryListing(
        id=stem,
        path=str(path),
        updated_at=updated_at,
        title=None,
        size_bytes=size,
    )


PI_TRAJECTORY_LISTER: Final[PiTrajectoryLister] = PiTrajectoryLister()
register_lister(PI_TRAJECTORY_LISTER)

__all__ = [
    "PI_TRAJECTORY_LISTER",
    "PiTrajectoryLister",
]
