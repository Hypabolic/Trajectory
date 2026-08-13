"""Cursor Agent transcript discovery and listing."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.listing.common import paginate
from hypabolic_trajectory.listing.protocol import register_lister
from hypabolic_trajectory.listing.title import derive_cursor_title, format_title
from hypabolic_trajectory.timestamps import format_ms

_META_NAME: Final[str] = "meta.json"


class CursorTrajectoryLister:
    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.CURSOR

    def list_page(
        self, *, root: str | Path, cursor: str | None, limit: int
    ) -> TrajectoryListingPage:
        return paginate(_discover(root), cursor=cursor, limit=limit)


def _discover(root: str | Path) -> list[TrajectoryListing]:
    if not isinstance(root, (str, Path)):
        raise TypeError("root must be str or Path")
    root_path = Path(root)
    try:
        project_entries = list(os.scandir(root_path / "projects"))
    except OSError:
        return []
    meta = _scan_meta(root_path / "chats")
    items: list[TrajectoryListing] = []
    for project in project_entries:
        try:
            if not project.is_dir(follow_symlinks=True):
                continue
            sessions = list(os.scandir(Path(project.path) / "agent-transcripts"))
        except OSError:
            continue
        for session in sessions:
            try:
                if not session.is_dir(follow_symlinks=True):
                    continue
                transcript = Path(session.path) / f"{session.name}.jsonl"
                if not transcript.is_file():
                    continue
                stat = transcript.stat()
                record_meta = meta.get(session.name, {})
                updated = _updated_at(record_meta.get("updatedAtMs"), stat)
                title = _meta_title(record_meta.get("title"))
                if title is None:
                    title = derive_cursor_title(transcript)
                items.append(TrajectoryListing(
                    id=session.name,
                    path=str(transcript.resolve()),
                    updated_at=updated,
                    title=title,
                    size_bytes=stat.st_size if stat.st_size >= 0 else None,
                ))
            except OSError:
                continue
    return items


def _scan_meta(chats: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    try:
        hashes = list(os.scandir(chats))
    except OSError:
        return result
    for hash_entry in hashes:
        try:
            if not hash_entry.is_dir(follow_symlinks=True):
                continue
            for session in os.scandir(hash_entry.path):
                if not session.is_dir(follow_symlinks=True) or session.name in result:
                    continue
                path = Path(session.path) / _META_NAME
                try:
                    data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject)
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    continue
                if isinstance(data, dict):
                    result[session.name] = data
        except OSError:
            continue
    return result


def _updated_at(value: Any, stat: os.stat_result) -> str | None:
    if type(value) is int and value >= 0:
        try:
            return format_ms(value)
        except Exception:
            pass
    try:
        return format_ms(stat.st_mtime_ns // 1_000_000)
    except Exception:
        return None


def _meta_title(value: Any) -> str | None:
    return format_title(value) if isinstance(value, str) else None


def _reject(value: str) -> None:
    raise ValueError(value)


CURSOR_TRAJECTORY_LISTER: Final[CursorTrajectoryLister] = CursorTrajectoryLister()
register_lister(CURSOR_TRAJECTORY_LISTER)

__all__ = ["CURSOR_TRAJECTORY_LISTER", "CursorTrajectoryLister"]
