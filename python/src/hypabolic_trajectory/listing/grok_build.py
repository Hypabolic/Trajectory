"""Grok Build trajectory lister (explicit-root discovery).

UNSUPPORTED import path. Self-registers as wire name ``grok-build``.

Discovery layout:
``<root>/<cwd-dir>/<session-uuid>/chat_history.jsonl``

Optional ``summary.json`` beside the history supplies ``updated_at`` / title
(``generated_title`` or ``session_summary``).

Missing / inaccessible stores yield an empty page.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.listing.common import paginate
from hypabolic_trajectory.listing.protocol import register_lister
from hypabolic_trajectory.timestamps import format_ms

_SOURCE_LABEL: Final[str] = "Grok Build"
_HISTORY_NAME: Final[str] = "chat_history.jsonl"
_SUMMARY_NAME: Final[str] = "summary.json"


class GrokBuildTrajectoryLister:
    """Discover Grok Build sessions under an explicit sessions root."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.GROK_BUILD

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
    absolute = Path(root)
    items: list[TrajectoryListing] = []
    try:
        cwd_dirs = list(os.scandir(absolute))
    except FileNotFoundError:
        return items
    except OSError:
        return items

    for cwd_entry in cwd_dirs:
        try:
            if not cwd_entry.is_dir(follow_symlinks=True):
                continue
        except OSError:
            continue
        try:
            session_dirs = list(os.scandir(cwd_entry.path))
        except OSError:
            continue
        for session_entry in session_dirs:
            try:
                if not session_entry.is_dir(follow_symlinks=True):
                    continue
            except OSError:
                continue
            history = Path(session_entry.path) / _HISTORY_NAME
            if not history.is_file():
                continue
            try:
                items.append(_listing_from_session(Path(session_entry.path), history))
            except OSError:
                continue
    return items


def _listing_from_session(session_dir: Path, history: Path) -> TrajectoryListing:
    stat = history.stat()
    try:
        mtime_ns = stat.st_mtime_ns
    except AttributeError:
        mtime_ns = int(stat.st_mtime * 1_000_000_000)
    fallback_ms = mtime_ns // 1_000_000
    try:
        fallback_updated = format_ms(fallback_ms)
    except Exception:
        fallback_updated = None

    summary_updated, title = _read_summary_meta(session_dir / _SUMMARY_NAME)
    updated_at = summary_updated or fallback_updated

    size = stat.st_size
    size_bytes = size if type(size) is int and size >= 0 else None

    return TrajectoryListing(
        id=session_dir.name,
        path=str(history.resolve()) if history.exists() else str(history),
        updated_at=updated_at,
        title=title,
        size_bytes=size_bytes,
    )


def _read_summary_meta(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None

    title = _str_field(data, "generated_title")
    if not title or not title.strip():
        title = _str_field(data, "session_summary")
    if title is not None and not title.strip():
        title = None

    updated_at = _parse_timestamp_field(data, "last_active_at") or _parse_timestamp_field(
        data, "updated_at"
    )
    return updated_at, title


def _parse_timestamp_field(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if type(value) is not str or not value:
        return None
    # Accept RFC-3339-ish strings; re-format via format_ms when parseable as epoch ms.
    # Peers keep original ISO when valid; for listing equality we prefer ISO from source.
    # Use the string as-is when it looks like an offset datetime (contains T).
    if "T" in value:
        return value
    return value


def _str_field(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


GROK_BUILD_TRAJECTORY_LISTER: Final[GrokBuildTrajectoryLister] = GrokBuildTrajectoryLister()
register_lister(GROK_BUILD_TRAJECTORY_LISTER)

__all__ = [
    "GROK_BUILD_TRAJECTORY_LISTER",
    "GrokBuildTrajectoryLister",
]
