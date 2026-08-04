"""AHP Shape A offline snapshot decode (protocol 0.7.x).

UNSUPPORTED import path. Self-registers on import as wire source ``ahp``.

Authority:
- contracts/spec/sources/ahp.md (Phase 1 Shape A)
- Peer: .NET ``AhpJsonSourceAdapter``, Rust ``decode_ahp``, TS ``decodeAhp``
- docs/python-implementation-spec.md PY-06-ahp

Shape B action-log reduce and live host clients are out of scope.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import INT64_MAX, INT64_MIN, canonical_json, compact_json
from hypabolic_trajectory.diagnostics import (
    DIAG_AHP_ACTIVE_TURN_OMITTED,
    DIAG_AHP_INPUT_REQUEST_SKIPPED,
    DIAG_AHP_SYSTEM_AS_ASSISTANT,
    DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN,
    DIAG_AHP_UNRESOLVED_CONTENT_REF,
    DIAG_AHP_VERSION_MISSING,
    Diagnostic,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError, raise_trajectory_error
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedModelInvocation,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import register_source_adapter

# ---------------------------------------------------------------------------
# Fixed messages (content-safe; peer pin)
# ---------------------------------------------------------------------------

_MSG_INVALID_SNAPSHOT: str = (
    "AHP snapshot must be a JSON object with a chat object (Shape A export)."
)
_MSG_VERSION_NOT_STRING: str = "AHP ahpProtocolVersion must be a string."
_MSG_VERSION_MISSING: str = "Snapshot lacks ahpProtocolVersion; assumed pinned 0.7.x."
_MSG_ACTIVE_TURN_OMITTED: str = (
    "Omitted incomplete activeTurn (snapshot whole-mode policy)."
)
_MSG_UNKNOWN_ORIGIN: str = "Dropped a message with an unknown origin kind."
_MSG_SYSTEM_AS_ASSISTANT: str = "Mapped a system message origin to assistant."
_MSG_INPUT_REQUEST_SKIPPED: str = "Skipped an inputRequest response part."
_MSG_UNRESOLVED_RESOURCE: str = (
    "Dropped a resource response part without fetching content-by-reference."
)
# Content-safe fixed message for tool parameter / structuredContent emit failures
# (transcript values must not appear in the exception text).
_MSG_INVALID_TOOL_JSON: str = "AHP tool field is not valid Trajectory JSON."

_Emit = Callable[[DecodedEvent], None]


class AhpSourceAdapter:
    """Shape A offline ChatState snapshot decoder (wire name ``ahp``)."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.AHP

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        # Any non-zero base_byte_offset implies partial (contract + normalizer pin).
        partial = bool(source_context.partial) or (
            type(source_context.base_byte_offset) is int
            and source_context.base_byte_offset != 0
        )
        return decode_ahp_snapshot(transcript, partial=partial)


def decode_ahp_snapshot(transcript: bytes, *, partial: bool = False) -> DecodedSession:
    """Decode AHP Shape A ``{ ahpProtocolVersion?, chat, session? }`` bytes."""
    if type(transcript) is not bytes:
        raise TypeError("AHP transcript must be bytes.")

    diagnostics: list[Diagnostic] = []
    root = _parse_root_object(transcript)
    _validate_protocol_version(root, diagnostics)

    chat = root.get("chat")
    if type(chat) is not dict:
        raise_trajectory_error(FATAL_INVALID_INPUT, _MSG_INVALID_SNAPSHOT)

    session_raw = root.get("session")
    session: dict[str, Any] | None = session_raw if type(session_raw) is dict else None

    return _decode_chat(chat, session, partial=partial, diagnostics=diagnostics)


def is_compatible_ahp_version(version: str) -> bool:
    """True when *version* is ``0.7.x`` (optional pre-release suffix)."""
    if type(version) is not str or not version:
        return False
    core = version.split("-", 1)[0]
    parts = core.split(".")
    if len(parts) < 2:
        return False
    if parts[0] != "0" or parts[1] != "7":
        return False
    # ASCII digits only — peers reject Unicode Nd category digits.
    return all(
        part != "" and part.isascii() and part.isdigit() for part in parts
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_root_object(transcript: bytes) -> dict[str, Any]:
    domain: TrajectoryError | None = None
    text: str | None = None
    try:
        text = transcript.decode("utf-8")
    except UnicodeDecodeError:
        domain = TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_SNAPSHOT)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)

    assert text is not None
    parsed: Any = None
    try:
        # Reject Python-only NaN/Infinity constants (not JSON; peers reject).
        parsed = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError, RecursionError):
        domain = TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_SNAPSHOT)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)

    if type(parsed) is not dict:
        raise_trajectory_error(FATAL_INVALID_INPUT, _MSG_INVALID_SNAPSHOT)
    return parsed


def _reject_json_constant(_name: str) -> Any:
    """``json.loads`` hook: refuse NaN / Infinity / -Infinity."""
    raise ValueError("non-standard JSON constant")


def _validate_protocol_version(
    root: dict[str, Any], diagnostics: list[Diagnostic]
) -> None:
    if "ahpProtocolVersion" not in root or root["ahpProtocolVersion"] is None:
        diagnostics.append(
            Diagnostic(code=DIAG_AHP_VERSION_MISSING, message=_MSG_VERSION_MISSING)
        )
        return

    version = root["ahpProtocolVersion"]
    if type(version) is not str:
        raise_trajectory_error(FATAL_INVALID_INPUT, _MSG_VERSION_NOT_STRING)
    if not is_compatible_ahp_version(version):
        raise_trajectory_error(
            FATAL_INVALID_INPUT,
            f"Unsupported AHP protocol version '{version}'. Expected 0.7.x.",
        )


def _append_event(
    events: list[DecodedEvent],
    event: DecodedEvent,
    component_index: int,
) -> None:
    """Append *event* with peer Shape A sequence/offset policy + component index."""
    if event.native_record_id is not None:
        events.append(
            DecodedEvent(
                kind=event.kind,
                role=event.role,
                content=event.content,
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                arguments_json=event.arguments_json,
                is_error=event.is_error,
                input_line=event.input_line,
                timestamp_ms=event.timestamp_ms,
                timestamp_precise=event.timestamp_precise,
                model=event.model,
                producer_version=event.producer_version,
                native_record_id=event.native_record_id,
                source_sequence=0,
                source_offset=0,
                source_anchor_kind=SourceAnchorKind.BYTE,
                component_index=component_index,
            )
        )
        return
    seq = len(events)
    events.append(
        DecodedEvent(
            kind=event.kind,
            role=event.role,
            content=event.content,
            tool_call_id=event.tool_call_id,
            tool_name=event.tool_name,
            arguments_json=event.arguments_json,
            is_error=event.is_error,
            input_line=event.input_line,
            timestamp_ms=event.timestamp_ms,
            timestamp_precise=event.timestamp_precise,
            model=event.model,
            producer_version=event.producer_version,
            native_record_id=None,
            source_sequence=seq,
            source_offset=seq,
            source_anchor_kind=None,
            component_index=component_index,
        )
    )


def _decode_chat(
    chat: dict[str, Any],
    session: dict[str, Any] | None,
    *,
    partial: bool,
    diagnostics: list[Diagnostic],
) -> DecodedSession:
    events: list[DecodedEvent] = []
    model_invocations: list[DecodedModelInvocation] = []

    group_id = _non_empty(_string(chat, "resource"))
    cwd = _first_working_directory(chat, session)
    provider = (
        _non_empty(_string(session, "provider")) if session is not None else None
    )
    model: str | None = None
    created_at_ms: int | None = None
    created_at_precise: str | None = None

    turns = _collect_turns(chat, partial=partial, diagnostics=diagnostics)
    for turn in turns:
        turn_id = _non_empty(_string(turn, "id"))
        started_raw = _string(turn, "startedAt")
        timestamp_ms = _ahp_timestamp_ms(started_raw)
        timestamp_precise = _non_empty(started_raw)
        if created_at_ms is None and timestamp_ms is not None:
            created_at_ms = timestamp_ms
            created_at_precise = timestamp_precise

        turn_model: str | None = None
        # Fresh component-index cell per turn.
        component_cell = [0]

        def emit(event: DecodedEvent, *, _cell: list[int] = component_cell) -> None:
            idx = _cell[0]
            _cell[0] = idx + 1
            _append_event(events, event, idx)

        message = turn.get("message")
        if type(message) is dict:
            message_model = _emit_message(
                message,
                turn_id=turn_id,
                timestamp_ms=timestamp_ms,
                timestamp_precise=timestamp_precise,
                emit=emit,
                diagnostics=diagnostics,
            )
            if message_model is not None:
                turn_model = message_model
                if model is None:
                    model = message_model

        parts = turn.get("responseParts")
        if type(parts) is list:
            _emit_response_parts(
                parts,
                turn_id=turn_id,
                timestamp_ms=timestamp_ms,
                timestamp_precise=timestamp_precise,
                emit=emit,
                diagnostics=diagnostics,
            )

        usage = turn.get("usage")
        if type(usage) is dict:
            usage_model = _non_empty(_string(usage, "model"))
            if usage_model is not None and model is None:
                model = usage_model
            resolved_model = usage_model if usage_model is not None else turn_model
            model_invocations.append(
                DecodedModelInvocation(
                    native_record_id=turn_id,
                    provider=provider,
                    requested_model=resolved_model,
                    response_model=resolved_model,
                    input_tokens=_as_int64(usage.get("inputTokens")),
                    output_tokens=_as_int64(usage.get("outputTokens")),
                    cache_read_tokens=_as_int64(usage.get("cacheReadTokens")),
                    started_at_ms=timestamp_ms,
                    started_at_precise=timestamp_precise,
                    completed_at_ms=timestamp_ms,
                    completed_at_precise=timestamp_precise,
                )
            )

    return DecodedSession(
        source=TrajectorySource.AHP,
        source_name="ahp",
        group_id=group_id,
        group_resolved=group_id is not None,
        cwd=cwd,
        model=model,
        created_at_ms=created_at_ms,
        created_at_precise=created_at_precise,
        events=tuple(events),
        model_invocations=tuple(model_invocations),
        diagnostics=tuple(diagnostics),
    )


def _collect_turns(
    chat: dict[str, Any],
    *,
    partial: bool,
    diagnostics: list[Diagnostic],
) -> list[dict[str, Any]]:
    raw_turns: list[tuple[dict[str, Any], int | None, str]] = []
    turns_val = chat.get("turns")
    if type(turns_val) is list:
        for turn in turns_val:
            if type(turn) is not dict:
                continue
            turn_id = _string(turn, "id") or ""
            started = _ahp_timestamp_ms(_string(turn, "startedAt"))
            raw_turns.append((turn, started, turn_id))

    # Nulls-last on startedAt, then UTF-8 byte order of id (peer pin).
    def _sort_key(item: tuple[dict[str, Any], int | None, str]) -> tuple[int, int, bytes]:
        _turn, started, turn_id = item
        present = 0 if started is not None else 1
        ms = started if started is not None else 0
        return (present, ms, _utf8_sort_key(turn_id))

    raw_turns.sort(key=_sort_key)

    active = chat.get("activeTurn")
    if type(active) is dict:
        if partial:
            raw_turns.append(
                (
                    active,
                    _ahp_timestamp_ms(_string(active, "startedAt")),
                    _string(active, "id") or "",
                )
            )
        else:
            diagnostics.append(
                Diagnostic(
                    code=DIAG_AHP_ACTIVE_TURN_OMITTED,
                    message=_MSG_ACTIVE_TURN_OMITTED,
                )
            )

    return [item[0] for item in raw_turns]


def _emit_message(
    message: dict[str, Any],
    *,
    turn_id: str | None,
    timestamp_ms: int | None,
    timestamp_precise: str | None,
    emit: _Emit,
    diagnostics: list[Diagnostic],
) -> str | None:
    origin = message.get("origin")
    origin_kind = _string(origin, "kind") if type(origin) is dict else None
    if origin_kind is None:
        diagnostics.append(
            Diagnostic(code=DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN, message=_MSG_UNKNOWN_ORIGIN)
        )
        return None
    if origin_kind == "tool":
        # Tool outputs are carried by toolCall response parts.
        return None

    if origin_kind == "user":
        role = TrajectoryRole.USER
    elif origin_kind in ("agent", "assistant"):
        role = TrajectoryRole.ASSISTANT
    elif origin_kind in ("system", "systemNotification"):
        role = TrajectoryRole.ASSISTANT
        diagnostics.append(
            Diagnostic(
                code=DIAG_AHP_SYSTEM_AS_ASSISTANT, message=_MSG_SYSTEM_AS_ASSISTANT
            )
        )
    else:
        # Fixed message only — do not echo free-form origin.kind (content-safety).
        diagnostics.append(
            Diagnostic(code=DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN, message=_MSG_UNKNOWN_ORIGIN)
        )
        return None

    model_obj = message.get("model")
    turn_model: str | None = None
    if type(model_obj) is dict:
        turn_model = _non_empty(_string(model_obj, "id"))

    text = _string(message, "text") or ""
    if text == "":
        return turn_model

    emit(
        DecodedEvent(
            kind="message",
            role=role,
            content=text,
            timestamp_ms=timestamp_ms,
            timestamp_precise=timestamp_precise,
            native_record_id=turn_id,
            model=turn_model,
            component_index=0,
        )
    )
    return turn_model


def _emit_response_parts(
    parts: list[Any],
    *,
    turn_id: str | None,
    timestamp_ms: int | None,
    timestamp_precise: str | None,
    emit: _Emit,
    diagnostics: list[Diagnostic],
) -> None:
    markdown_buffer: list[tuple[str, str]] = []

    def flush_markdown() -> None:
        if not markdown_buffer:
            return
        content = "".join(item[1] for item in markdown_buffer)
        first_id = markdown_buffer[0][0]
        markdown_buffer.clear()
        if content == "":
            return
        native_id = _non_empty(first_id) or turn_id
        emit(
            DecodedEvent(
                kind="message",
                role=TrajectoryRole.ASSISTANT,
                content=content,
                timestamp_ms=timestamp_ms,
                timestamp_precise=timestamp_precise,
                native_record_id=native_id,
                component_index=0,
            )
        )

    for part in parts:
        if type(part) is not dict:
            continue
        kind = _string(part, "kind")
        if kind == "markdown":
            part_id = _string(part, "id") or _string(part, "partId") or ""
            content = _string(part, "content") or ""
            markdown_buffer.append((part_id, content))
            continue

        flush_markdown()

        if kind == "reasoning":
            content = _string(part, "content") or ""
            if content.strip() != "":
                part_id = (
                    _non_empty(_string(part, "id"))
                    or _non_empty(_string(part, "partId"))
                    or turn_id
                )
                emit(
                    DecodedEvent(
                        kind="reasoning",
                        role=TrajectoryRole.REASONING,
                        content=content,
                        timestamp_ms=timestamp_ms,
                        timestamp_precise=timestamp_precise,
                        native_record_id=part_id,
                        component_index=0,
                    )
                )
            continue

        if kind == "toolCall":
            _emit_tool_call(
                part,
                timestamp_ms=timestamp_ms,
                timestamp_precise=timestamp_precise,
                emit=emit,
            )
            continue

        if kind == "inputRequest":
            diagnostics.append(
                Diagnostic(
                    code=DIAG_AHP_INPUT_REQUEST_SKIPPED,
                    message=_MSG_INPUT_REQUEST_SKIPPED,
                )
            )
            continue

        if kind == "resource":
            diagnostics.append(
                Diagnostic(
                    code=DIAG_AHP_UNRESOLVED_CONTENT_REF,
                    message=_MSG_UNRESOLVED_RESOURCE,
                )
            )
            continue
        # systemNotification and unknown kinds: non-identity meta; ignore body for v1.

    flush_markdown()


def _emit_tool_call(
    part: dict[str, Any],
    *,
    timestamp_ms: int | None,
    timestamp_precise: str | None,
    emit: _Emit,
) -> None:
    tool_call = part.get("toolCall")
    if type(tool_call) is not dict:
        return

    tool_call_id = _non_empty(_string(tool_call, "toolCallId"))
    tool_name = _non_empty(_string(tool_call, "toolName"))
    arguments_json = _tool_arguments_json(tool_call)

    emit(
        DecodedEvent(
            kind="tool-call",
            role=TrajectoryRole.ASSISTANT,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_json=arguments_json,
            timestamp_ms=timestamp_ms,
            timestamp_precise=timestamp_precise,
            native_record_id=tool_call_id,
            component_index=0,
        )
    )

    status = _string(tool_call, "status")
    success = _boolean(tool_call, "success")
    is_terminal = status in ("completed", "cancelled", "denied", "error")
    if not is_terminal and success is None:
        return

    is_error = success is False or status in ("cancelled", "denied", "error")
    result_content = _tool_result_content(tool_call, is_error=is_error)
    emit(
        DecodedEvent(
            kind="tool-result",
            role=TrajectoryRole.TOOL,
            content=result_content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            is_error=is_error,
            timestamp_ms=timestamp_ms,
            timestamp_precise=timestamp_precise,
            native_record_id=tool_call_id,
            component_index=0,
        )
    )


def _tool_arguments_json(tool_call: dict[str, Any]) -> str | None:
    parameters = tool_call.get("parameters")
    if type(parameters) is dict or type(parameters) is list:
        return _emit_json(parameters, sort_objects=False)
    tool_input = _non_empty(_string(tool_call, "toolInput"))
    if tool_input is not None:
        return tool_input
    return None


def _tool_result_content(tool_call: dict[str, Any], *, is_error: bool) -> str:
    content = tool_call.get("content")
    if type(content) is list:
        parts: list[str] = []
        for block in content:
            if type(block) is not dict:
                continue
            block_type = _string(block, "type")
            if block_type is not None and block_type != "text":
                continue
            text = _non_empty(_string(block, "text"))
            if text is not None:
                parts.append(text)
        if parts:
            return "\n".join(parts)

    if "structuredContent" in tool_call and tool_call["structuredContent"] is not None:
        return _emit_json(tool_call["structuredContent"], sort_objects=True)

    past = _string_or_markdown(tool_call, "pastTenseMessage")
    if past is not None:
        return past

    if is_error:
        reason_message = _string_or_markdown(tool_call, "reasonMessage")
        if reason_message is not None:
            return reason_message
        reason = _non_empty(_string(tool_call, "reason"))
        if reason is not None:
            return reason
        error = tool_call.get("error")
        if type(error) is dict:
            error_message = _non_empty(_string(error, "message"))
            if error_message is not None:
                return error_message
        status = _string(tool_call, "status")
        return "cancelled" if status in ("cancelled", "denied") else "error"

    return ""


def _emit_json(value: Any, *, sort_objects: bool) -> str:
    """Serialize tool JSON with fixed domain errors (no low-level TypeError leak)."""
    domain: TrajectoryError | None = None
    text: str | None = None
    try:
        if sort_objects:
            text = canonical_json(value)
        else:
            text = compact_json(value)
    except (TypeError, RecursionError):
        # Deep nesting can blow the recursion ceiling in compact/canonical emit.
        domain = TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TOOL_JSON)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)
    assert text is not None
    return text


def _string_or_markdown(obj: dict[str, Any], key: str) -> str | None:
    """AHP StringOrMarkdown: plain string or ``{ "markdown": "..." }``."""
    if key not in obj:
        return None
    value = obj[key]
    if type(value) is str:
        return _non_empty(value)
    if type(value) is dict:
        return _non_empty(_string(value, "markdown"))
    return None


def _first_working_directory(
    chat: dict[str, Any], session: dict[str, Any] | None
) -> str | None:
    for source in (chat, session):
        if source is None:
            continue
        dirs = source.get("workingDirectories")
        if type(dirs) is not list:
            continue
        for entry in dirs:
            if type(entry) is not str or entry == "":
                continue
            if entry.startswith("file://"):
                path = entry[len("file://") :]
                return path if path != "" else entry
            return entry
    return None


def _ahp_timestamp_ms(text: str | None) -> int | None:
    if text is None or text == "":
        return None
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return int(dt.timestamp() * 1000)
    except (OverflowError, OSError, ValueError):
        return None


def _as_int64(value: Any) -> int | None:
    """Lossless signed-int64 only (no fractional truncation; .NET peer pin).

    Python ``json.loads`` yields ``int`` for whole numbers that fit; fractional
    or non-finite floats are omitted (never invented / never truncated).
    """
    if type(value) is bool:
        return None
    if type(value) is int:
        if INT64_MIN <= value <= INT64_MAX:
            return value
        return None
    if type(value) is float:
        if not math.isfinite(value):
            return None
        # Reject fractional values; accept only lossless whole numbers in range.
        truncated = math.trunc(value)
        if float(truncated) != value:
            return None
        if INT64_MIN <= truncated <= INT64_MAX:
            return int(truncated)
        return None
    return None


def _string(obj: dict[str, Any] | Any, key: str) -> str | None:
    if type(obj) is not dict:
        return None
    value = obj.get(key)
    return value if type(value) is str else None


def _boolean(obj: dict[str, Any], key: str) -> bool | None:
    value = obj.get(key)
    if type(value) is bool:
        return value
    return None


def _non_empty(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _utf8_sort_key(value: str) -> bytes:
    """UTF-8 bytes for turn-id ordering; tolerate lone surrogates without raising.

    JSON can carry ``\\uD800``-style escapes that Python keeps as lone surrogates.
    Strict ``encode("utf-8")`` would raise ``UnicodeEncodeError`` and leak a
    non-domain exception. ``surrogatepass`` yields a deterministic key without
    reflecting transcript text into errors.
    """
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        return value.encode("utf-8", errors="surrogatepass")


# Self-register on import (package root must import this module).
register_source_adapter(AhpSourceAdapter())


__all__ = [
    "AhpSourceAdapter",
    "decode_ahp_snapshot",
    "is_compatible_ahp_version",
]
