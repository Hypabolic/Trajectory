"""Cheap, bounded listing title derivation (no full normalize).

Peers: TS ``titleFromUserText`` / ``deriveClaudeTitle`` / ``deriveCodexTitle``,
.NET ``ListingTitle``. Scan limits match peers (64 KiB / 200 lines).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final, Iterator

TITLE_MAX_SCALARS: Final[int] = 120
TITLE_SCAN_MAX_BYTES: Final[int] = 64 * 1024
TITLE_SCAN_MAX_LINES: Final[int] = 200

_NOISE_MARKERS: Final[tuple[str, ...]] = (
    "# agents.md",
    "<instructions>",
    "</instructions>",
    "<environment_context>",
    "<skills_instructions>",
    "<skills>",
    "<permissions instructions>",
    "<user_instructions>",
    "<turn_context>",
    "<collaboration",
    "filesystem sandboxing",
    "<cwd>",
    "you are a coding agent",
    "you are chatgpt",
    "# claude.md",
    "agenthub instructions",
    "<command-name>",
    "<local-command-caveat>",
    "<task-notification",
)

_TAG_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9/_-]+$")


def format_title(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = " ".join(text.strip().split())
    if not collapsed:
        return None
    # Unicode scalar count (code points), same spirit as JS spread.
    return "".join(list(collapsed)[:TITLE_MAX_SCALARS])


def title_from_user_text(text: str) -> str | None:
    return None if _is_listing_noise(text) else format_title(text)


def derive_claude_title(path: str | Path) -> str | None:
    custom_title: str | None = None
    ai_title: str | None = None
    summary: str | None = None
    first_user: str | None = None
    for row in _scan_json_lines(path):
        record_type = _string(row.get("type"))
        if record_type == "custom-title" and custom_title is None:
            custom_title = format_title(
                _string(row.get("customTitle")) or _string(row.get("title"))
            )
        elif record_type == "ai-title" and ai_title is None:
            ai_title = format_title(
                _string(row.get("aiTitle")) or _string(row.get("title"))
            )
        elif record_type == "summary" and summary is None:
            summary = format_title(
                _string(row.get("summary")) or _string(row.get("title"))
            )
        elif record_type == "user" and first_user is None:
            if row.get("isMeta") is True or row.get("isSidechain") is True:
                continue
            message = row.get("message") if isinstance(row.get("message"), dict) else None
            text = _blocks_to_text(message.get("content") if message else None)
            if text is None:
                text = _blocks_to_text(row.get("content")) or ""
            if "tool_use_id" in text:
                continue
            first_user = title_from_user_text(text)
    return custom_title or ai_title or summary or first_user


def derive_codex_title(path: str | Path) -> str | None:
    session_id: str | None = None
    for row in _scan_json_lines(path):
        record_type = _string(row.get("type"))
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else None
        if record_type == "session_meta":
            sid = _string(payload.get("id")) if payload else None
            if sid:
                session_id = sid
            continue
        if record_type == "response_item":
            role = _string(payload.get("role")) if payload else None
            if role in ("developer", "system"):
                continue
            if role == "user":
                text = _blocks_to_text(payload.get("content") if payload else None) or ""
                title = title_from_user_text(text)
                if title is not None:
                    return title
            continue
        if record_type == "event_msg":
            event_type = _string(payload.get("type")) if payload else None
            if event_type in ("user_message", "user_prompt", "message"):
                text = (
                    _blocks_to_text(payload.get("message") if payload else None)
                    or _blocks_to_text(payload.get("content") if payload else None)
                    or (_string(payload.get("text")) if payload else None)
                    or ""
                )
                title = title_from_user_text(text)
                if title is not None:
                    return title
    if session_id is None:
        return None
    return format_title(_short_session_id(session_id))


def derive_generic_user_title(path: str | Path) -> str | None:
    """First non-noise user message title (Pi / OpenClaw family)."""
    for row in _scan_json_lines(path):
        message = row.get("message") if isinstance(row.get("message"), dict) else None
        role = _string(message.get("role") if message else None) or _string(row.get("role"))
        if role != "user":
            continue
        text = (
            _blocks_to_text(message.get("content") if message else None)
            or _blocks_to_text(row.get("content"))
            or ""
        )
        title = title_from_user_text(text)
        if title is not None:
            return title
    return None


def derive_cursor_title(path: str | Path) -> str | None:
    """Return the first Cursor user turn's joined text as a bounded title."""
    for row in _scan_json_lines(path):
        if _string(row.get("role")) != "user":
            continue
        message = row.get("message") if isinstance(row.get("message"), dict) else None
        content = message.get("content") if message else None
        if not isinstance(content, list):
            continue
        text = "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
            and _string(part.get("type")) == "text"
            and isinstance(part.get("text"), str)
        )
        return format_title(text)
    return None


def _scan_json_lines(path: str | Path) -> Iterator[dict[str, Any]]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(TITLE_SCAN_MAX_BYTES)
    except OSError:
        return
    text = data.decode("utf-8", errors="replace")
    lines = 0
    for line in text.splitlines():
        lines += 1
        trimmed = line.strip()
        if trimmed:
            try:
                parsed = json.loads(trimmed)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                yield parsed
        if lines >= TITLE_SCAN_MAX_LINES:
            break


def _blocks_to_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts) if parts else None
    return None


def _is_listing_noise(text: str) -> bool:
    trimmed = text.strip()
    if not trimmed:
        return True
    lower = trimmed.lower()
    for marker in _NOISE_MARKERS:
        if marker in lower:
            return True
    return _count_xmlish_tags(trimmed) >= 3 and len(trimmed) > 80


def _count_xmlish_tags(text: str) -> int:
    count = 0
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "<":
            index += 1
            continue
        start = index + 1
        if start >= length:
            break
        first = text[start]
        if not (first.isalpha() or first in "/_-"):
            index += 1
            continue
        end = text.find(">", start)
        if end < 0:
            break
        name = text[start:end]
        if _TAG_NAME.match(name):
            count += 1
            index = end
        index += 1
    return count


def _short_session_id(session_id: str) -> str:
    dash = session_id.find("-")
    if dash >= 8:
        return session_id[:8]
    return session_id if len(session_id) <= 8 else session_id[:8]


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
