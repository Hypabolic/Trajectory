"""OpenClaw trajectory lister — ``<root>/agents/*/sessions/*.jsonl``.

UNSUPPORTED public import path. Registers on package import (PY-06-openclaw).

Authority:
  - contracts/spec/listing.md (OpenClaw discovery)
  - Peer: Rust ``list_openclaw_trajectories``; .NET ``OpenClawTrajectoryLister``
  - docs/python-implementation-spec.md PY-06-openclaw + §3 listing DTOs

Library listing always receives an explicit ``root`` (no home-dir default here;
sample-CLI owns default-state discovery). Missing stores → empty page.
Does not edit runtime-capabilities.json.
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
from hypabolic_trajectory.timestamps import format_ms

_SOURCE_LABEL: Final[str] = "OpenClaw"


class OpenClawTrajectoryLister:
    """Discover OpenClaw session JSONL files under an explicit state root."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.OPENCLAW

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
    """Scan ``<root>/agents/<agentId>/sessions/*.jsonl`` (one sessions level)."""
    if not isinstance(root, (str, Path)):
        raise TypeError("root must be str or Path")
    root_path = Path(root)
    agents_root = root_path / "agents"
    items: list[TrajectoryListing] = []

    try:
        agent_entries = list(os.scandir(agents_root))
    except FileNotFoundError:
        return items
    except OSError:
        # Permission / inaccessible — empty page (listing.md).
        return items

    for agent in agent_entries:
        try:
            is_dir = agent.is_dir(follow_symlinks=True)
        except OSError:
            continue
        if not is_dir:
            continue
        sessions = Path(agent.path) / "sessions"
        try:
            file_entries = list(os.scandir(sessions))
        except FileNotFoundError:
            continue
        except OSError:
            continue
        for entry in file_entries:
            try:
                if not entry.is_file(follow_symlinks=True):
                    continue
            except OSError:
                continue
            name = entry.name
            # Case-sensitive ``.jsonl`` suffix (peer pin).
            if not name.endswith(".jsonl"):
                continue
            path = Path(entry.path)
            try:
                items.append(_listing_from_path(path))
            except TrajectoryError:
                # Bad stem / pre-epoch / out-of-range mtime: skip one file.
                continue
            except OSError:
                continue
    return items


def _listing_from_path(path: Path) -> TrajectoryListing:
    try:
        stat = path.stat()
    except OSError as error:
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
    updated_at = format_ms(milliseconds)

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
        title=None,
        size_bytes=size_bytes,
    )


OPENCLAW_TRAJECTORY_LISTER: Final[OpenClawTrajectoryLister] = OpenClawTrajectoryLister()
register_lister(OPENCLAW_TRAJECTORY_LISTER)

__all__ = [
    "OPENCLAW_TRAJECTORY_LISTER",
    "OpenClawTrajectoryLister",
]
