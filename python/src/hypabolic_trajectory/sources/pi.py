"""Pi JSONL source adapter (decode-only).

UNSUPPORTED import path. Self-registers as wire name ``pi`` on package import
under the PY-04a export owner. Does not edit the normalizer dispatcher or
runtime-capabilities claims.

Authority:
- docs/python-implementation-spec.md PY-05a + §4.1 decode seam
- Peer: Rust ``decode_pi_session``, TS ``decodePiSession``, .NET ``PiJsonlSourceAdapter``
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import INT64_MAX, INT64_MIN, compact_json
from hypabolic_trajectory.diagnostics import (
    DIAG_INVALID_JSON_LINE,
    DIAG_NON_OBJECT_JSON_LINE,
    Diagnostic,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedModelInvocation,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import register_source_adapter
from hypabolic_trajectory.timestamps import format_ms

_SOURCE_LABEL: Final[str] = "Pi"
_MSG_INVALID_TRANSCRIPT: Final[str] = (
    "Pi transcript must be session JSONL containing a session header or message entries."
)
_MSG_OFFSET_OUT_OF_RANGE: Final[str] = (
    "Transcript byte offset exceeds signed 64-bit range."
)
_MSG_SEQUENCE_OUT_OF_RANGE: Final[str] = (
    "Transcript line sequence exceeds signed 64-bit range."
)

_UNIX_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Offset-bearing RFC-3339 (chrono ``parse_from_rfc3339`` subset used by peers).
# Rejects naive, date-only, space-separated, and basic forms that fromisoformat
# would otherwise accept as silent UTC.
_RFC3339_OFFSET: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)


class PiSourceAdapter:
    """Decode-only Pi session JSONL → ``DecodedSession``."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.PI

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        _ = source_context  # group / partial applied by normalizer, not decode
        if type(transcript) is not bytes:
            raise TypeError("transcript must be bytes")

        events: list[DecodedEvent] = []
        model_invocations: list[DecodedModelInvocation] = []
        diagnostics: list[Diagnostic] = []
        group_id: str | None = None
        cwd: str | None = None
        producer_version: str | None = None
        requested_provider: str | None = None
        requested_model: str | None = None
        created_at_ms: int | None = None
        created_at_precise: str | None = None
        saw_message = False

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
                    row_type = _string_value(row.get("type"))
                    if row_type == "session":
                        if cwd is None:
                            cwd = _string_value(row.get("cwd"))
                        if group_id is None:
                            group_id = _string_value(row.get("id"))
                        if created_at_ms is None:
                            created = _parse_timestamp(row.get("timestamp"))
                            if created is not None:
                                created_at_ms, created_at_precise = created
                        if producer_version is None:
                            producer_version = _scalar_string(row.get("version"))
                    elif row_type == "model_change":
                        requested_provider = _string_value(row.get("provider"))
                        requested_model = _string_value(row.get("modelId"))
                    elif row_type == "message":
                        message = row.get("message")
                        if isinstance(message, dict):
                            saw_message = True
                            source_offset = _require_i64_offset(offset)
                            role = _string_value(message.get("role"))
                            if role == "assistant":
                                model_invocations.append(
                                    _decode_invocation(
                                        row,
                                        message,
                                        source_offset=source_offset,
                                        line=line,
                                        requested_provider=requested_provider,
                                        requested_model=requested_model,
                                    )
                                )
                            _decode_message_events(
                                row,
                                message,
                                line=line,
                                source_offset=source_offset,
                                events=events,
                            )

            if end == length:
                break
            offset = end + 1
            line += 1

        if not saw_message and group_id is None:
            raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT) from None

        return DecodedSession(
            source=TrajectorySource.PI,
            source_name="pi",
            group_id=group_id,
            group_resolved=group_id is not None,
            cwd=cwd,
            git_branch=None,
            model=None,
            producer_version=producer_version,
            created_at_ms=created_at_ms,
            created_at_precise=created_at_precise,
            events=tuple(events),
            model_invocations=tuple(model_invocations),
            diagnostics=tuple(diagnostics),
        )


def _require_i64_offset(offset: int) -> int:
    if offset < INT64_MIN or offset > INT64_MAX:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_OFFSET_OUT_OF_RANGE) from None
    return offset


def _require_i64_sequence(line: int) -> int:
    sequence = line - 1
    if sequence < INT64_MIN or sequence > INT64_MAX:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_SEQUENCE_OUT_OF_RANGE) from None
    return sequence


def _is_ascii_whitespace(value: bytes) -> bool:
    return all(b in (0x20, 0x09, 0x0D) for b in value)


def _reject_json_constant(name: str) -> None:
    """``json.loads`` hook: refuse NaN / Infinity / -Infinity (not JSON; peers reject)."""
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
        # Match peer scalarString / scalar_string number path without float noise.
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


def _bool_true(obj: dict[str, Any], key: str) -> bool:
    return obj.get(key) is True


def _parse_timestamp(value: Any) -> tuple[int, str] | None:
    """Parse dual (ms, precise) like tip Rust/TS ``parse_timestamp``.

    - integer ms > 1e11 → precise = ``format_ms`` with ``Z`` → ``0000+00:00``
    - offset-bearing RFC-3339 string → ms + seven-digit fractional pad ``+00:00``
    """
    if type(value) is bool:
        return None
    if type(value) is int and value > 100_000_000_000:
        domain: TrajectoryError | None = None
        precise: str | None = None
        try:
            precise = format_ms(value).replace("Z", "0000+00:00")
        except TrajectoryError as err:
            domain = err
        if domain is not None:
            return None
        assert precise is not None
        return value, precise

    if type(value) is not str:
        return None
    text = value
    if not _RFC3339_OFFSET.fullmatch(text):
        return None
    domain_parse: Exception | None = None
    dt: datetime | None = None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return None
        dt = dt.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError) as err:
        domain_parse = err
    if domain_parse is not None or dt is None:
        return None
    try:
        delta = dt - _UNIX_EPOCH_UTC
        ms = delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
    except (OverflowError, OSError, ValueError):
        return None
    if ms < INT64_MIN or ms > INT64_MAX:
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
    domain_fmt: TrajectoryError | None = None
    sec_base: str | None = None
    try:
        sec_base = format_ms(ms)[:19]
    except TrajectoryError as err:
        domain_fmt = err
    if domain_fmt is not None or sec_base is None:
        return None
    return ms, f"{sec_base}.{seven}+00:00"


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


def _tool_arguments_json(arguments: Any) -> str:
    if arguments is None:
        return "{}"
    if type(arguments) is str:
        return arguments
    try:
        return compact_json(arguments)  # type: ignore[arg-type]
    except TypeError:
        # Non-JSON tree — emit empty object rather than inventing shapes.
        return "{}"


def _decode_invocation(
    row: dict[str, Any],
    message: dict[str, Any],
    *,
    source_offset: int,
    line: int,
    requested_provider: str | None,
    requested_model: str | None,
) -> DecodedModelInvocation:
    usage = message.get("usage")
    has_usage = type(usage) is dict
    usage_obj: dict[str, Any] = usage if has_usage else {}

    raw_model = _string_value(message.get("model"))
    provider = _string_value(message.get("provider")) or requested_provider
    started = _parse_timestamp(message.get("startTimestamp")) or _parse_timestamp(
        message.get("requestTimestamp")
    )
    first_response = _parse_timestamp(message.get("firstResponseTimestamp"))
    completed = _parse_timestamp(message.get("timestamp")) or _parse_timestamp(
        row.get("timestamp")
    )

    return DecodedModelInvocation(
        native_record_id=_string_value(row.get("id")),
        source_sequence=_require_i64_sequence(line),
        source_offset=source_offset,
        provider=provider,
        api_family=_string_value(message.get("api")),
        requested_model=requested_model,
        response_model=raw_model,
        response_id=_string_value(message.get("responseId")),
        stop_reason=_string_value(message.get("stopReason")),
        producer_version=None,
        input_tokens=_int64_field(usage_obj, "input") if has_usage else None,
        output_tokens=_int64_field(usage_obj, "output") if has_usage else None,
        cache_read_tokens=_int64_field(usage_obj, "cacheRead") if has_usage else None,
        cache_write_tokens=_int64_field(usage_obj, "cacheWrite") if has_usage else None,
        total_tokens=_int64_field(usage_obj, "totalTokens") if has_usage else None,
        started_at_ms=started[0] if started else None,
        started_at_precise=started[1] if started else None,
        first_response_at_ms=first_response[0] if first_response else None,
        first_response_at_precise=first_response[1] if first_response else None,
        completed_at_ms=completed[0] if completed else None,
        completed_at_precise=completed[1] if completed else None,
    )


def _decode_message_events(
    row: dict[str, Any],
    message: dict[str, Any],
    *,
    line: int,
    source_offset: int,
    events: list[DecodedEvent],
) -> None:
    role = _string_value(message.get("role"))
    native_record_id = _string_value(row.get("id"))
    timestamp = _parse_timestamp(row.get("timestamp")) or _parse_timestamp(
        message.get("timestamp")
    )
    model = _string_value(message.get("model"))
    source_sequence = _require_i64_sequence(line)
    component_index = 0

    def emit(
        *,
        kind: str,
        event_role: TrajectoryRole,
        content: str | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        arguments_json: str | None = None,
        is_error: bool | None = None,
        include_model: bool = False,
    ) -> None:
        nonlocal component_index
        events.append(
            DecodedEvent(
                kind=kind,  # type: ignore[arg-type]
                role=event_role,
                content=content,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments_json=arguments_json,
                is_error=is_error,
                input_line=line,
                timestamp_ms=timestamp[0] if timestamp else None,
                timestamp_precise=timestamp[1] if timestamp else None,
                model=model if include_model else None,
                producer_version=None,
                native_record_id=native_record_id,
                source_sequence=source_sequence,
                source_offset=source_offset,
                source_anchor_kind=SourceAnchorKind.BYTE,
                component_index=component_index,
            )
        )
        component_index += 1

    if role == "user":
        content = _read_blocks_text(message.get("content"))
        if content:
            emit(
                kind="message",
                event_role=TrajectoryRole.USER,
                content=content,
            )
        return

    if role == "assistant":
        content = message.get("content")
        if type(content) is str:
            if content:
                emit(
                    kind="message",
                    event_role=TrajectoryRole.ASSISTANT,
                    content=content,
                    include_model=True,
                )
            return
        if type(content) is not list:
            return
        for part in content:
            if type(part) is not dict:
                continue
            part_type = _string_value(part.get("type"))
            if part_type == "thinking":
                thinking = _string_value(part.get("thinking"))
                if thinking:
                    emit(
                        kind="reasoning",
                        event_role=TrajectoryRole.REASONING,
                        content=thinking,
                        include_model=True,
                    )
            elif part_type == "text":
                text = _string_value(part.get("text"))
                if text:
                    emit(
                        kind="message",
                        event_role=TrajectoryRole.ASSISTANT,
                        content=text,
                        include_model=True,
                    )
            elif part_type == "toolCall":
                emit(
                    kind="tool-call",
                    event_role=TrajectoryRole.ASSISTANT,
                    tool_call_id=_string_value(part.get("id")),
                    tool_name=_string_value(part.get("name")),
                    arguments_json=_tool_arguments_json(part.get("arguments")),
                    include_model=True,
                )
        return

    if role in ("toolResult", "tool"):
        content = _read_blocks_text(message.get("content"))
        is_error = _bool_true(message, "isError")
        if is_error and not content.lower().startswith("error"):
            content = f"Error: {content}"
        emit(
            kind="tool-result",
            event_role=TrajectoryRole.TOOL,
            content=content,
            tool_call_id=_string_value(message.get("toolCallId")),
            tool_name=_string_value(message.get("toolName")),
            is_error=is_error,
        )


# Singleton used for registration and tests.
PI_SOURCE_ADAPTER: Final[PiSourceAdapter] = PiSourceAdapter()
register_source_adapter(PI_SOURCE_ADAPTER)

__all__ = [
    "PI_SOURCE_ADAPTER",
    "PiSourceAdapter",
]
