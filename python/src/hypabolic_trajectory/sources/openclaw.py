"""OpenClaw session-JSONL source adapter (Pi-family + delivery-mirror mask).

UNSUPPORTED public import path. Registers on package import (PY-06-openclaw).

Authority:
  - contracts/spec/listing.md (openclaw discovery is listing-side)
  - Peer: Rust ``decode_pi_session`` / ``PiFamilyOptions::openclaw``;
    TS ``decodeOpenClaw``; .NET ``OpenClawJsonlSourceAdapter``
  - docs/python-implementation-spec.md PY-06-openclaw
  - Conformance: ``conformance/cases/openclaw/*``

Decode-only; does not edit runtime-capabilities.json (claim-writer is later).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import compact_json
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

_INT64_MIN: Final[int] = -(2**63)
_INT64_MAX: Final[int] = 2**63 - 1

_SOURCE: Final[TrajectorySource] = TrajectorySource.OPENCLAW
_SOURCE_NAME: Final[str] = "openclaw"
_SOURCE_LABEL: Final[str] = "OpenClaw"
_EXCLUDED_MODELS: Final[frozenset[str]] = frozenset({"delivery-mirror"})

_MSG_INVALID_TRANSCRIPT: Final[str] = (
    "OpenClaw transcript must be session JSONL containing a session header "
    "or message entries."
)
_MSG_BYTE_OFFSET_OOR: Final[str] = "Transcript byte offset exceeds signed 64-bit range."
_MSG_LINE_SEQUENCE_OOR: Final[str] = "Transcript line sequence exceeds signed 64-bit range."

_UNIX_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Offset-bearing RFC-3339 (chrono ``parse_from_rfc3339`` subset used by peers).
# Rejects naive, date-only, space-separated, and basic forms that fromisoformat
# would otherwise accept as silent UTC.
_RFC3339_OFFSET: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)


class OpenClawSourceAdapter:
    """Native OpenClaw session JSONL decoder (built-in)."""

    @property
    def source(self) -> TrajectorySource:
        return _SOURCE

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        _ = source_context
        if type(transcript) is not bytes:
            raise TypeError("transcript must be bytes")
        return _decode_openclaw_session(transcript)


def _decode_openclaw_session(transcript: bytes) -> DecodedSession:
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
    length = len(transcript)

    while offset <= length:
        newline = transcript.find(b"\n", offset)
        end = length if newline < 0 else newline
        line_end = end
        if line_end > offset and transcript[line_end - 1] == 0x0D:
            line_end -= 1
        slice_bytes = transcript[offset:line_end]
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
                        ts = _parse_timestamp(row.get("timestamp"))
                        if ts is not None:
                            created_at_ms, created_at_precise = ts
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
                                    source_sequence=_require_i64_sequence(line - 1),
                                    requested_provider=requested_provider,
                                    requested_model=requested_model,
                                )
                            )
                        _decode_message(
                            row,
                            message,
                            line=line,
                            source_offset=source_offset,
                            events=events,
                        )

        if newline < 0:
            break
        offset = end + 1
        line += 1

    if not saw_message and group_id is None:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT) from None

    return DecodedSession(
        source=_SOURCE,
        source_name=_SOURCE_NAME,
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


def _decode_message(
    row: dict[str, Any],
    message: dict[str, Any],
    *,
    line: int,
    source_offset: int,
    events: list[DecodedEvent],
) -> None:
    role = _string_value(message.get("role"))
    native_id = _string_value(row.get("id"))
    timestamp = _parse_timestamp(row.get("timestamp")) or _parse_timestamp(
        message.get("timestamp")
    )
    timestamp_ms = timestamp[0] if timestamp else None
    timestamp_precise = timestamp[1] if timestamp else None
    raw_model = _string_value(message.get("model"))
    model = None if raw_model in _EXCLUDED_MODELS else raw_model
    source_sequence = _require_i64_sequence(line - 1)
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
        with_model: bool = False,
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
                timestamp_ms=timestamp_ms,
                timestamp_precise=timestamp_precise,
                model=model if with_model else None,
                producer_version=None,
                native_record_id=native_id,
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
        content_value = message.get("content")
        if type(content_value) is str:
            if content_value:
                emit(
                    kind="message",
                    event_role=TrajectoryRole.ASSISTANT,
                    content=content_value,
                    with_model=True,
                )
            return
        if not isinstance(content_value, list):
            return
        for part in content_value:
            if not isinstance(part, dict):
                continue
            part_type = _string_value(part.get("type"))
            if part_type == "thinking":
                thinking = _string_value(part.get("thinking"))
                if thinking:
                    emit(
                        kind="reasoning",
                        event_role=TrajectoryRole.REASONING,
                        content=thinking,
                        with_model=True,
                    )
            elif part_type == "text":
                text = _string_value(part.get("text"))
                if text:
                    emit(
                        kind="message",
                        event_role=TrajectoryRole.ASSISTANT,
                        content=text,
                        with_model=True,
                    )
            elif part_type == "toolCall":
                arguments = part.get("arguments")
                if arguments is None:
                    arguments_json = "{}"
                elif type(arguments) is str:
                    arguments_json = arguments
                else:
                    try:
                        arguments_json = compact_json(arguments)  # type: ignore[arg-type]
                    except TypeError:
                        # Non-JSON tree / non-finite — empty object, not invented shapes.
                        arguments_json = "{}"
                emit(
                    kind="tool-call",
                    event_role=TrajectoryRole.ASSISTANT,
                    tool_call_id=_string_value(part.get("id")),
                    tool_name=_string_value(part.get("name")),
                    arguments_json=arguments_json,
                    with_model=True,
                )
        return

    if role in ("toolResult", "tool"):
        content = _read_blocks_text(message.get("content"))
        is_error = message.get("isError") is True
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


def _decode_invocation(
    row: dict[str, Any],
    message: dict[str, Any],
    *,
    source_offset: int,
    source_sequence: int,
    requested_provider: str | None,
    requested_model: str | None,
) -> DecodedModelInvocation:
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
    raw_model = _string_value(message.get("model"))
    response_model = None if raw_model in _EXCLUDED_MODELS else raw_model
    started = _parse_timestamp(message.get("startTimestamp")) or _parse_timestamp(
        message.get("requestTimestamp")
    )
    first_response = _parse_timestamp(message.get("firstResponseTimestamp"))
    completed = _parse_timestamp(message.get("timestamp")) or _parse_timestamp(
        row.get("timestamp")
    )
    provider = _string_value(message.get("provider")) or requested_provider
    return DecodedModelInvocation(
        native_record_id=_string_value(row.get("id")),
        source_sequence=source_sequence,
        source_offset=source_offset,
        provider=provider,
        api_family=_string_value(message.get("api")),
        requested_model=requested_model,
        response_model=response_model,
        response_id=_string_value(message.get("responseId")),
        stop_reason=_string_value(message.get("stopReason")),
        producer_version=None,
        input_tokens=_int64_field(usage, "input") if usage is not None else None,
        output_tokens=_int64_field(usage, "output") if usage is not None else None,
        cache_read_tokens=_int64_field(usage, "cacheRead") if usage is not None else None,
        cache_write_tokens=_int64_field(usage, "cacheWrite") if usage is not None else None,
        total_tokens=_int64_field(usage, "totalTokens") if usage is not None else None,
        started_at_ms=started[0] if started else None,
        started_at_precise=started[1] if started else None,
        first_response_at_ms=first_response[0] if first_response else None,
        first_response_at_precise=first_response[1] if first_response else None,
        completed_at_ms=completed[0] if completed else None,
        completed_at_precise=completed[1] if completed else None,
    )


def _reject_json_constant(_name: str) -> Any:
    """``json.loads`` hook: refuse NaN / Infinity / -Infinity (not JSON; peers reject)."""
    raise ValueError("non-standard JSON constant")


def _try_parse_object_line(
    slice_bytes: bytes,
    line: int,
    diagnostics: list[Diagnostic],
) -> dict[str, Any] | None:
    """Parse one non-empty JSONL line; append diagnostics on recoverable failures."""
    # Invalid UTF-8, invalid JSON, and non-standard constants → invalid_json_line.
    domain_diag: Diagnostic | None = None
    parsed: Any = None
    try:
        text = slice_bytes.decode("utf-8")
        parsed = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        domain_diag = Diagnostic(
            code=DIAG_INVALID_JSON_LINE,
            message=f"Skipped invalid JSON on line {line}.",
            input_line=line,
        )
    if domain_diag is not None:
        diagnostics.append(domain_diag)
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


def _is_ascii_whitespace(value: bytes) -> bool:
    for b in value:
        if b not in (0x20, 0x09, 0x0D):
            return False
    return True


def _string_value(value: object) -> str | None:
    if type(value) is str:
        return value
    return None


def _scalar_string(value: object) -> str | None:
    if type(value) is str:
        return value
    if type(value) is int and not isinstance(value, bool):
        return str(value)
    if type(value) is float:
        if value.is_integer() and _INT64_MIN <= value <= _INT64_MAX:
            return str(int(value))
        return None
    return None


def _int64_field(obj: dict[str, Any], name: str) -> int | None:
    value = obj.get(name)
    if type(value) is bool:
        return None
    if type(value) is int and _INT64_MIN <= value <= _INT64_MAX:
        return value
    return None


def _require_i64_offset(offset: int) -> int:
    if offset < _INT64_MIN or offset > _INT64_MAX:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_BYTE_OFFSET_OOR) from None
    return offset


def _require_i64_sequence(sequence: int) -> int:
    if sequence < _INT64_MIN or sequence > _INT64_MAX:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_LINE_SEQUENCE_OOR) from None
    return sequence


def _dt_to_unix_ms(dt: datetime) -> int | None:
    """Convert aware datetime to epoch ms via integer arithmetic (no float)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    delta = dt - _UNIX_EPOCH_UTC
    ms = delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
    if ms < _INT64_MIN or ms > _INT64_MAX:
        return None
    return ms


def _parse_timestamp(value: object) -> tuple[int, str] | None:
    """Parse a source timestamp into ``(ms, precise)`` dual fields.

    Peer pin (Rust ``parse_timestamp``):
    - integer ms > 1e11 → precise = ``format_ms`` with ``Z`` → ``0000+00:00``
    - offset-bearing RFC-3339 string → ms + seven-digit fractional pad ``+00:00``
    """
    if type(value) is bool:
        return None
    if type(value) is int and value > 100_000_000_000:
        domain: TrajectoryError | None = None
        precise: str | None = None
        try:
            # Validate representable range via format_ms; pad Z → 0000+00:00.
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
        # Normalize trailing Z for fromisoformat portability.
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return None
        dt = dt.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError) as err:
        domain_parse = err
    if domain_parse is not None or dt is None:
        return None
    # Integer arithmetic (avoid float timestamp * 1000).
    try:
        delta = dt - _UNIX_EPOCH_UTC
        ms = delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
    except (OverflowError, OSError, ValueError):
        return None
    if ms < _INT64_MIN or ms > _INT64_MAX:
        return None
    # Fraction digits from original text (peer takes up to 7, pad to 7).
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
    # Base clock from filled ms (UTC), not the source offset spelling.
    domain_fmt: TrajectoryError | None = None
    sec_base: str | None = None
    try:
        # format_ms → yyyy-MM-ddTHH:mm:ss.fffZ; peer uses %Y-%m-%dT%H:%M:%S.
        sec_base = format_ms(ms)[:19]
    except TrajectoryError as err:
        domain_fmt = err
    if domain_fmt is not None or sec_base is None:
        return None
    return ms, f"{sec_base}.{seven}+00:00"


def _read_blocks_text(value: object) -> str:
    if type(value) is str:
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = _string_value(item.get("type"))
        if item_type in ("text", "input_text", "output_text", None):
            text = _string_value(item.get("text"))
            if text:
                parts.append(text)
        elif item_type == "image":
            parts.append("[image]")
    return "\n".join(parts)


# Module-level singleton registration (import-time, export-owner hook).
OPENCLAW_SOURCE_ADAPTER: Final[OpenClawSourceAdapter] = OpenClawSourceAdapter()
register_source_adapter(OPENCLAW_SOURCE_ADAPTER)

__all__ = [
    "OPENCLAW_SOURCE_ADAPTER",
    "OpenClawSourceAdapter",
]
