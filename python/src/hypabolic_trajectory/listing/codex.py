"""Codex trajectory lister (explicit-root discovery).

UNSUPPORTED import path. Self-registers as wire name ``codex`` on package
import under the PY-04a export owner. Registers only — does not edit
``list_trajectories`` dispatcher body or runtime-capabilities claims.

Discovery layout (contracts/spec/listing.md):
``*.jsonl`` recursively under the root to four directory levels.
Missing / inaccessible stores yield an empty page (skip concurrent removals).
Library paths never consult the process home directory.

Authority:
- contracts/spec/listing.md
- docs/python-implementation-spec.md PY-05b
- Peer: TS ``discoverCodex`` / ``collectCodex``, .NET ``CodexTrajectoryLister``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.listing.common import paginate
from hypabolic_trajectory.listing.protocol import register_lister
from hypabolic_trajectory.listing.title import derive_codex_title
from hypabolic_trajectory.timestamps import format_ms

_SOURCE_LABEL: Final[str] = "Codex"
_MAX_DEPTH: Final[int] = 4


class CodexTrajectoryLister:
    """Discover Codex session JSONL files under an explicit root."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.CODEX

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
    items: list[TrajectoryListing] = []
    _collect(Path(root), remaining_depth=_MAX_DEPTH, items=items)
    return items


def _collect(
    directory: Path,
    *,
    remaining_depth: int,
    items: list[TrajectoryListing],
) -> None:
    if remaining_depth < 0:
        return
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return
    except OSError:
        return

    for entry in entries:
        path = Path(entry.path)
        try:
            is_file = entry.is_file(follow_symlinks=True)
            is_dir = entry.is_dir(follow_symlinks=True)
        except OSError:
            continue
        if is_file and entry.name.endswith(".jsonl"):
            try:
                items.append(_listing_from_path(path))
            except TrajectoryError:
                continue
            except OSError:
                continue
        elif is_dir and remaining_depth > 0:
            _collect(path, remaining_depth=remaining_depth - 1, items=items)


def _listing_from_path(path: Path) -> TrajectoryListing:
    try:
        stat = path.stat()
    except OSError as error:
        raise error from None

    stem = path.stem
    try:
        stem.encode("utf-8")
        str(path).encode("utf-8")
    except UnicodeEncodeError:
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            f"A {_SOURCE_LABEL} transcript filename is not valid Unicode.",
        ) from None
    if "\x00" in stem:
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            f"A {_SOURCE_LABEL} transcript filename is not valid Unicode.",
        ) from None

    try:
        mtime_ns = stat.st_mtime_ns
    except AttributeError:
        mtime_ns = int(stat.st_mtime * 1_000_000_000)
    # Pre-epoch mtimes are representable via format_ms; only drop the clock
    # (not the listing) when the instant is outside the formatter's range.
    milliseconds = mtime_ns // 1_000_000
    try:
        updated_at: str | None = format_ms(milliseconds)
    except TrajectoryError:
        updated_at = None

    size = stat.st_size
    size_bytes: int | None
    if type(size) is int and size >= 0:
        size_bytes = size
    else:
        size_bytes = None

    return TrajectoryListing(
        id=stem,
        path=str(path),
        updated_at=updated_at,
        title=derive_codex_title(path),
        size_bytes=size_bytes,
    )


CODEX_TRAJECTORY_LISTER: Final[CodexTrajectoryLister] = CodexTrajectoryLister()
register_lister(CODEX_TRAJECTORY_LISTER)

__all__ = [
    "CODEX_TRAJECTORY_LISTER",
    "CodexTrajectoryLister",
]
