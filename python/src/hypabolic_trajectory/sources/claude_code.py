"""Claude Code JSONL source adapter (decode-only).

UNSUPPORTED import path. Self-registers as wire name ``claude-code`` on package
import under the PY-04a export owner. Does not edit the normalizer dispatcher
or runtime-capabilities claims.

Authority:
- docs/python-implementation-spec.md PY-05b + §4.1 decode seam
- Peer: TS ``decodeClaudeCode``, .NET ``ClaudeCodeJsonlSourceAdapter``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import (
    INT64_MAX,
    INT64_MIN,
    compact_json,
    escape_json_string,
    utf16_code_units,
    utf16_compare,
)
from hypabolic_trajectory.diagnostics import (
    DIAG_INVALID_JSON_LINE,
    DIAG_NON_OBJECT_JSON_LINE,
    DIAG_SIDECHAIN_RECORD_DROPPED,
    DIAG_UNKNOWN_CONTENT_BLOCK,
    DIAG_UNKNOWN_SEMANTIC_RECORD,
    Diagnostic,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import (
    FATAL_INVALID_INPUT,
    FATAL_SOURCE_GROUP_CONFLICT,
    TrajectoryError,
)
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedModelInvocation,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import register_source_adapter
from hypabolic_trajectory.timestamps import format_ms

_MSG_OFFSET_OUT_OF_RANGE: Final[str] = (
    "Transcript byte offset exceeds signed 64-bit range."
)

_TRANSPORT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "progress",
        "queue-operation",
        "file-history-snapshot",
        "summary",
        "system",
        "pr-link",
        "last-prompt",
        "custom-title",
        "ai-title",
        "agent-name",
        "permission-mode",
        "attachment",
        "mode",
    }
)

_Emit = Callable[..., None]


class ClaudeCodeSourceAdapter:
    """Decode-only Claude Code session JSONL → ``DecodedSession``."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.CLAUDE_CODE

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        _ = source_context  # group / partial applied by normalizer, not decode
        if type(transcript) is not bytes:
            raise TypeError("transcript must be bytes")
        return _decode_transcript(transcript)


def _decode_transcript(transcript: bytes) -> DecodedSession:
    events: list[DecodedEvent] = []
    model_invocations: list[DecodedModelInvocation] = []
    diagnostics: list[Diagnostic] = []
    session_ids: set[str] = set()
    # Earliest context by (timestamp_ms, ordinal tie) — peer .NET Earlier().
    holders: dict[str, _ContextCandidate | None] = {
        "cwd": None,
        "branch": None,
        "version": None,
    }

    offset = 0
    line = 1
    data = transcript
    length = len(data)

    while offset <= length:
        relative_nl = data.find(b"\n", offset)
        end = length if relative_nl < 0 else relative_nl
        line_end = end
        if line_end > offset and data[line_end - 1] == 0x0D:
            line_end -= 1
        slice_bytes = data[offset:line_end]

        if not _is_ascii_whitespace(slice_bytes):
            row = _try_parse_object_line(slice_bytes, line, diagnostics)
            if row is not None:
                _process_row(
                    row,
                    line=line,
                    source_offset=_require_i64_offset(offset),
                    events=events,
                    model_invocations=model_invocations,
                    diagnostics=diagnostics,
                    session_ids=session_ids,
                    holders=holders,
                )

        if end == length:
            break
        offset = end + 1
        line += 1

    if len(session_ids) > 1:
        ordered = sorted(session_ids, key=utf16_code_units)
        formatted = ", ".join(escape_json_string(s) for s in ordered)
        raise TrajectoryError(
            FATAL_SOURCE_GROUP_CONFLICT,
            f"Claude Code transcript contains multiple session ids: {formatted}.",
        ) from None

    group_id = next(iter(session_ids), None)
    cwd = holders["cwd"].value if holders["cwd"] is not None else None
    git_branch = holders["branch"].value if holders["branch"] is not None else None
    producer_version = (
        holders["version"].value if holders["version"] is not None else "unknown"
    )

    return DecodedSession(
        source=TrajectorySource.CLAUDE_CODE,
        source_name="claude-code",
        group_id=group_id,
        group_resolved=group_id is not None,
        cwd=cwd,
        git_branch=git_branch,
        model=None,
        producer_version=producer_version,
        created_at_ms=None,
        created_at_precise=None,
        events=tuple(events),
        model_invocations=tuple(model_invocations),
        diagnostics=tuple(diagnostics),
    )


class _ContextCandidate:
    __slots__ = ("value", "timestamp", "tie")

    def __init__(self, value: str, timestamp: int, tie: str) -> None:
        self.value = value
        self.timestamp = timestamp
        self.tie = tie


def _earlier(
    current: _ContextCandidate | None,
    value: str | None,
    timestamp: int,
    tie: str,
) -> _ContextCandidate | None:
    if value is None or value == "":
        return current
    next_cand = _ContextCandidate(value, timestamp, tie)
    if current is None:
        return next_cand
    if next_cand.timestamp < current.timestamp:
        return next_cand
    if next_cand.timestamp == current.timestamp and utf16_compare(
        next_cand.tie, current.tie
    ) < 0:
        return next_cand
    return current


def _process_row(
    row: dict[str, Any],
    *,
    line: int,
    source_offset: int,
    events: list[DecodedEvent],
    model_invocations: list[DecodedModelInvocation],
    diagnostics: list[Diagnostic],
    session_ids: set[str],
    holders: dict[str, _ContextCandidate | None],
) -> None:
    row_type = _string_value(row.get("type"))
    if row.get("isSidechain") is True:
        diagnostics.append(
            Diagnostic(
                code=DIAG_SIDECHAIN_RECORD_DROPPED,
                message=f"Dropped a Claude Code sidechain record on line {line}.",
                input_line=line,
            )
        )
        return

    if row_type is not None and row_type in _TRANSPORT_TYPES:
        return

    native_id = _string_value(row.get("uuid"))
    timestamp_data = _parse_timestamp(row.get("timestamp"))
    context_ts = timestamp_data[0] if timestamp_data is not None else (2**63 - 1)
    context_tie = native_id if native_id is not None else f"@{source_offset}"

    holders["cwd"] = _earlier(
        holders["cwd"], _string_value(row.get("cwd")), context_ts, context_tie
    )
    holders["branch"] = _earlier(
        holders["branch"],
        _string_value(row.get("gitBranch")),
        context_ts,
        context_tie,
    )
    producer_version = _scalar_string(row.get("version"))
    holders["version"] = _earlier(
        holders["version"], producer_version, context_ts, context_tie
    )

    session_id = _string_value(row.get("sessionId"))
    if session_id:
        session_ids.add(session_id)

    if row_type not in ("user", "assistant"):
        if row_type:
            diagnostics.append(
                Diagnostic(
                    code=DIAG_UNKNOWN_SEMANTIC_RECORD,
                    message=(
                        "Skipped an unknown Claude Code semantic record "
                        f"on line {line}."
                    ),
                    input_line=line,
                )
            )
        return

    message = row.get("message")
    if type(message) is not dict:
        return

    model = _string_value(message.get("model"))
    content = message.get("content")
    component_index = 0

    def emit(
        kind: str,
        role: TrajectoryRole,
        *,
        content_text: str | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        arguments_json: str | None = None,
        is_error: bool | None = None,
        event_model: str | None = None,
    ) -> None:
        nonlocal component_index
        ts_ms: int | None = None
        ts_precise: str | None = None
        if timestamp_data is not None:
            ts_ms, ts_precise = timestamp_data
        events.append(
            DecodedEvent(
                kind=kind,  # type: ignore[arg-type]
                role=role,
                content=content_text,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments_json=arguments_json,
                is_error=is_error,
                input_line=line,
                timestamp_ms=ts_ms,
                timestamp_precise=ts_precise,
                model=event_model,
                producer_version=producer_version,
                native_record_id=native_id,
                source_offset=source_offset,
                source_anchor_kind=SourceAnchorKind.BYTE,
                component_index=component_index,
            )
        )
        component_index += 1

    if row_type == "user":
        _decode_user_content(content, line, diagnostics, emit)
        return

    model_invocations.append(
        _decode_invocation(
            message,
            native_id=native_id,
            producer_version=producer_version,
            source_offset=source_offset,
            timestamp_data=timestamp_data,
            model=model,
        )
    )

    if type(content) is str:
        if content.strip():
            emit(
                "message",
                TrajectoryRole.ASSISTANT,
                content_text=content,
                event_model=model,
            )
        return

    if type(content) is not list:
        return

    for block in content:
        if type(block) is not dict:
            continue
        block_type = _string_value(block.get("type"))
        if block_type == "thinking":
            emit(
                "reasoning",
                TrajectoryRole.REASONING,
                content_text=_string_value(block.get("thinking")) or "",
                event_model=model,
            )
        elif block_type == "text":
            emit(
                "message",
                TrajectoryRole.ASSISTANT,
                content_text=_string_value(block.get("text")) or "",
                event_model=model,
            )
        elif block_type == "tool_use":
            input_value = block.get("input")
            if input_value is None:
                args_json = "{}"
            else:
                try:
                    args_json = compact_json(input_value)  # type: ignore[arg-type]
                except TypeError:
                    args_json = "{}"
            emit(
                "tool-call",
                TrajectoryRole.ASSISTANT,
                tool_call_id=_string_value(block.get("id")),
                tool_name=_string_value(block.get("name")),
                arguments_json=args_json,
                event_model=model,
            )
        elif block_type == "fallback":
            continue
        else:
            diagnostics.append(
                Diagnostic(
                    code=DIAG_UNKNOWN_CONTENT_BLOCK,
                    message=(
                        "Skipped an unknown Claude Code assistant content "
                        f"block on line {line}."
                    ),
                    input_line=line,
                )
            )


def _decode_user_content(
    content: Any,
    line: int,
    diagnostics: list[Diagnostic],
    emit: _Emit,
) -> None:
    if type(content) is str:
        emit("message", TrajectoryRole.USER, content_text=content)
        return
    if type(content) is not list:
        return

    text_parts: list[str] = []
    for block in content:
        if type(block) is not dict:
            continue
        block_type = _string_value(block.get("type"))
        if block_type == "tool_result":
            emit(
                "tool-result",
                TrajectoryRole.TOOL,
                tool_call_id=_string_value(block.get("tool_use_id")),
                content_text=_read_blocks_text(block.get("content")),
                is_error=block.get("is_error") is True,
            )
        elif block_type == "text":
            text = _string_value(block.get("text"))
            if text:
                text_parts.append(text)
        elif block_type == "image":
            text_parts.append("[image]")
        else:
            diagnostics.append(
                Diagnostic(
                    code=DIAG_UNKNOWN_CONTENT_BLOCK,
                    message=(
                        "Skipped an unknown Claude Code user content block "
                        f"on line {line}."
                    ),
                    input_line=line,
                )
            )
    if text_parts:
        emit(
            "message",
            TrajectoryRole.USER,
            content_text="\n".join(text_parts),
        )


def _decode_invocation(
    message: dict[str, Any],
    *,
    native_id: str | None,
    producer_version: str | None,
    source_offset: int,
    timestamp_data: tuple[int, str] | None,
    model: str | None,
) -> DecodedModelInvocation:
    usage = message.get("usage")
    has_usage = type(usage) is dict
    completed_ms: int | None = None
    completed_precise: str | None = None
    if timestamp_data is not None:
        completed_ms, completed_precise = timestamp_data
    return DecodedModelInvocation(
        native_record_id=native_id,
        source_offset=source_offset,
        response_model=model,
        response_id=_string_value(message.get("id")),
        stop_reason=_string_value(message.get("stop_reason"))
        or _string_value(message.get("stopReason")),
        producer_version=producer_version,
        input_tokens=(
            _first_int64(usage, "input_tokens", "input") if has_usage else None
        ),
        output_tokens=(
            _first_int64(usage, "output_tokens", "output") if has_usage else None
        ),
        cache_read_tokens=(
            _first_int64(usage, "cache_read_input_tokens", "cacheRead")
            if has_usage
            else None
        ),
        cache_write_tokens=(
            _first_int64(usage, "cache_creation_input_tokens", "cacheWrite")
            if has_usage
            else None
        ),
        total_tokens=_int64_field(usage, "total_tokens") if has_usage else None,
        completed_at_ms=completed_ms,
        completed_at_precise=completed_precise,
    )


def _require_i64_offset(offset: int) -> int:
    if offset < INT64_MIN or offset > INT64_MAX:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_OFFSET_OUT_OF_RANGE) from None
    return offset


def _is_ascii_whitespace(value: bytes) -> bool:
    return all(b in (0x20, 0x09, 0x0D) for b in value)


def _reject_json_constant(name: str) -> None:
    """Reject NaN/Infinity constants (not valid JSON; peer-strict decode)."""
    raise ValueError(f"JSON constant not allowed: {name}")


def _try_parse_object_line(
    slice_bytes: bytes,
    line: int,
    diagnostics: list[Diagnostic],
) -> dict[str, Any] | None:
    try:
        text = slice_bytes.decode("utf-8")
        parsed: Any = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        diagnostics.append(
            Diagnostic(
                code=DIAG_INVALID_JSON_LINE,
                message=f"Skipped invalid JSON on line {line}.",
                input_line=line,
            )
        )
        return None
    if type(parsed) is not dict:
        diagnostics.append(
            Diagnostic(
                code=DIAG_NON_OBJECT_JSON_LINE,
                message=f"Skipped non-object JSON on line {line}.",
                input_line=line,
            )
        )
        return None
    return parsed


def _string_value(value: Any) -> str | None:
    return value if type(value) is str else None


def _scalar_string(value: Any) -> str | None:
    if type(value) is str:
        return value
    if type(value) is int and not isinstance(value, bool):
        return str(value)
    if type(value) is float:
        if value.is_integer() and INT64_MIN <= value <= INT64_MAX:
            return str(int(value))
        return None
    return None


def _int64_field(obj: dict[str, Any], key: str) -> int | None:
    value = obj.get(key)
    if type(value) is bool:
        return None
    if type(value) is int and INT64_MIN <= value <= INT64_MAX:
        return value
    return None


def _first_int64(obj: dict[str, Any], *keys: str) -> int | None:
    """Return the first present int64 field (0 is valid — do not use ``or``)."""
    for key in keys:
        found = _int64_field(obj, key)
        if found is not None:
            return found
    return None


def _parse_timestamp(value: Any) -> tuple[int, str] | None:
    """Parse dual (ms, precise) like tip TS/Rust parse_timestamp."""
    if type(value) is bool:
        return None
    if type(value) is int and value > 100_000_000_000:
        try:
            precise = format_ms(value).replace("Z", "0000+00:00")
        except TrajectoryError:
            return None
        return value, precise
    if type(value) is not str:
        return None
    text = value
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = dt - epoch
        milliseconds = (
            delta.days * 86_400_000
            + delta.seconds * 1000
            + delta.microseconds // 1000
        )
    except (ValueError, OverflowError, OSError):
        return None
    if milliseconds < INT64_MIN or milliseconds > INT64_MAX:
        return None
    fraction = ""
    if "." in text:
        after = text.split(".", 1)[1]
        digits: list[str] = []
        for ch in after:
            if ch.isdigit():
                digits.append(ch)
                if len(digits) == 7:
                    break
            else:
                break
        fraction = "".join(digits)
    seven = fraction.ljust(7, "0")[:7]
    try:
        base = format_ms(milliseconds)[:19]
    except TrajectoryError:
        return None
    return milliseconds, f"{base}.{seven}+00:00"


def _read_blocks_text(content: Any) -> str:
    if type(content) is str:
        return content
    if type(content) is not list:
        return ""
    parts: list[str] = []
    for item in content:
        if type(item) is not dict:
            continue
        part_type = _string_value(item.get("type"))
        if part_type == "image":
            parts.append("[image]")
        elif part_type in (None, "text", "input_text", "output_text"):
            text = _string_value(item.get("text"))
            if text:
                parts.append(text)
    return "\n".join(parts)


CLAUDE_CODE_SOURCE_ADAPTER: Final[ClaudeCodeSourceAdapter] = ClaudeCodeSourceAdapter()
register_source_adapter(CLAUDE_CODE_SOURCE_ADAPTER)

__all__ = [
    "CLAUDE_CODE_SOURCE_ADAPTER",
    "ClaudeCodeSourceAdapter",
]
