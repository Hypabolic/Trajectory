"""Hermes session-export source adapter (decode-only).

UNSUPPORTED import path. Self-registers as wire name ``hermes`` on package
import under the PY-04a export owner. Does not edit the normalizer dispatcher
or runtime-capabilities claims.

Transcript shapes:
- JSON array of session-store message rows
- JSON object with a ``messages`` array and optional ``session`` object

Authority:
- docs/python-implementation-spec.md PY-06-hermes + §4.1 decode seam
- Peer: Rust ``decode_hermes``, TS ``decodeHermes``, .NET ``HermesJsonSourceAdapter``
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import compact_json
from hypabolic_trajectory.diagnostics import DIAG_INVALID_JSON_LINE, Diagnostic
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.sources.decoded import DecodedEvent, DecodedSession
from hypabolic_trajectory.sources.protocol import register_source_adapter

_CONTENT_JSON_PREFIX: Final[str] = "\u0000json:"
_MSG_INVALID_TRANSCRIPT: Final[str] = (
    "Hermes transcript must be a JSON array of session-store message rows "
    "or an object with a messages array."
)
_INT64_MIN: Final[int] = -(2**63)
_INT64_MAX: Final[int] = 2**63 - 1


class HermesSourceAdapter:
    """Decode-only Hermes session export → ``DecodedSession``."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.HERMES

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        _ = source_context  # group / partial applied by normalizer, not decode
        if type(transcript) is not bytes:
            raise TypeError("transcript must be bytes")
        return decode_hermes(transcript)


def decode_hermes(transcript: bytes) -> DecodedSession:
    """Decode Hermes message-row array or session envelope into a session."""
    if type(transcript) is not bytes:
        raise TypeError("transcript must be bytes")

    diagnostics: list[Diagnostic] = []
    events: list[DecodedEvent] = []
    parsed = _parse_transcript(transcript)

    # Soft-deleted rows (active = 0/false) are rewound history Hermes itself
    # excludes from replay; drop them before ordering and call/result linking.
    rows = [row for row in parsed.messages if not _is_inactive(row)]
    rows = _order_rows(rows)
    calls_by_row = _plan_tool_calls(rows, diagnostics)

    for index, row in enumerate(rows):
        timestamp_ms = _hermes_timestamp(row.get("timestamp"))
        native = _row_id(row)
        component_index = 0

        def emit(event: DecodedEvent) -> None:
            nonlocal component_index
            if native is not None:
                stamped = DecodedEvent(
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
                    native_record_id=native.text,
                    source_sequence=native.numeric,
                    source_offset=None,
                    source_anchor_kind=None,
                    component_index=component_index,
                )
            else:
                stamped = DecodedEvent(
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
                    source_sequence=index,
                    source_offset=index,
                    source_anchor_kind=SourceAnchorKind.ORDINAL,
                    component_index=component_index,
                )
            component_index += 1
            events.append(stamped)

        role = _string_value(row.get("role"))
        if role == "user":
            content = _content_text(row.get("content"))
            if content:
                emit(
                    DecodedEvent(
                        kind="message",
                        role=TrajectoryRole.USER,
                        content=content,
                        timestamp_ms=timestamp_ms,
                        component_index=0,
                    )
                )
            continue

        if role == "assistant":
            reasoning = _reasoning_text(row)
            if reasoning:
                emit(
                    DecodedEvent(
                        kind="reasoning",
                        role=TrajectoryRole.REASONING,
                        content=reasoning,
                        timestamp_ms=timestamp_ms,
                        component_index=0,
                    )
                )
            content = _content_text(row.get("content"))
            if content:
                emit(
                    DecodedEvent(
                        kind="message",
                        role=TrajectoryRole.ASSISTANT,
                        content=content,
                        timestamp_ms=timestamp_ms,
                        component_index=0,
                    )
                )
            for call in calls_by_row.get(index, ()):
                emit(
                    DecodedEvent(
                        kind="tool-call",
                        role=TrajectoryRole.ASSISTANT,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        arguments_json=call.args,
                        timestamp_ms=timestamp_ms,
                        component_index=0,
                    )
                )
            continue

        if role == "tool":
            emit(
                DecodedEvent(
                    kind="tool-result",
                    role=TrajectoryRole.TOOL,
                    content=_content_text(row.get("content")),
                    tool_call_id=_string_value(row.get("tool_call_id")),
                    tool_name=_string_value(row.get("tool_name")),
                    timestamp_ms=timestamp_ms,
                    component_index=0,
                )
            )
        # Other roles (e.g. injected system rows) are harness transport noise.

    session = parsed.session
    model = _non_empty(_string_value(session.get("model")) if session else None)
    cwd = _non_empty(_string_value(session.get("cwd")) if session else None)
    created_at_ms = (
        _hermes_timestamp(session.get("started_at")) if session is not None else None
    )
    group_id = _resolve_group_id(session, parsed.messages)

    return DecodedSession(
        source=TrajectorySource.HERMES,
        source_name="hermes",
        group_id=group_id,
        group_resolved=group_id is not None,
        cwd=cwd,
        model=model,
        created_at_ms=created_at_ms,
        events=tuple(events),
        model_invocations=(),
        diagnostics=tuple(diagnostics),
    )


@dataclass(frozen=True, slots=True)
class _ParsedHermesTranscript:
    session: dict[str, Any] | None
    messages: list[dict[str, Any]]


@dataclass(slots=True)
class _HermesToolCall:
    id: str | None
    name: str | None
    args: str


@dataclass(frozen=True, slots=True)
class _RowIdentity:
    text: str
    numeric: int | None


def _reject_json_constant(_name: str) -> Any:
    raise ValueError("nonstandard json constant")


def _strict_json_loads(text: str) -> Any:
    """Parse JSON rejecting non-standard NaN/Infinity (peer strict parsers)."""
    return json.loads(text, parse_constant=_reject_json_constant)


def _parse_transcript(transcript: bytes) -> _ParsedHermesTranscript:
    domain: TrajectoryError | None = None
    parsed: Any = None
    try:
        parsed = _strict_json_loads(transcript.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        domain = TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT)
    if domain is not None:
        raise domain from None

    if type(parsed) is list:
        if not all(type(item) is dict for item in parsed):
            raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT) from None
        return _ParsedHermesTranscript(session=None, messages=list(parsed))

    if type(parsed) is dict and type(parsed.get("messages")) is list:
        messages = parsed["messages"]
        if not all(type(item) is dict for item in messages):
            raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT) from None
        session_raw = parsed.get("session")
        session = session_raw if type(session_raw) is dict else None
        return _ParsedHermesTranscript(session=session, messages=list(messages))

    raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT) from None


def _order_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    if not all(_is_number_id(row.get("id")) for row in rows):
        return rows
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda item: (_as_int64(item[1].get("id")) or 0, item[0]))
    return [row for _, row in indexed]


def _plan_tool_calls(
    rows: list[dict[str, Any]],
    diagnostics: list[Diagnostic],
) -> dict[int, list[_HermesToolCall]]:
    plan: dict[int, list[_HermesToolCall]] = {}
    for index, row in enumerate(rows):
        if _string_value(row.get("role")) != "assistant":
            continue
        calls = _row_tool_calls(row, index, diagnostics)
        if not calls:
            continue
        idless = [call for call in calls if not call.id]
        if idless:
            claimed = {call.id for call in calls if call.id}
            available: list[str] = []
            for next_row in rows[index + 1 :]:
                if _string_value(next_row.get("role")) != "tool":
                    break
                tool_call_id = _string_value(next_row.get("tool_call_id"))
                if tool_call_id and tool_call_id not in claimed:
                    available.append(tool_call_id)
            if len(available) == len(idless):
                for position, call in enumerate(idless):
                    call.id = available[position]
        plan[index] = calls
    return plan


def _row_tool_calls(
    row: dict[str, Any],
    index: int,
    diagnostics: list[Diagnostic],
) -> list[_HermesToolCall]:
    raw = row.get("tool_calls")
    if raw is None:
        return []
    tool_calls: Any = raw
    if type(raw) is str:
        if not raw:
            return []
        try:
            tool_calls = _strict_json_loads(raw)
        except (json.JSONDecodeError, ValueError):
            diagnostics.append(
                Diagnostic(
                    code=DIAG_INVALID_JSON_LINE,
                    message=f"Skipped undecodable tool_calls on message {index + 1}.",
                    input_line=index + 1,
                )
            )
            return []

    if type(tool_calls) is not list:
        return []

    calls: list[_HermesToolCall] = []
    for entry in tool_calls:
        if type(entry) is not dict:
            continue
        fn = entry.get("function")
        fn_obj = fn if type(fn) is dict else None
        name = _first_string(
            _string_value(fn_obj.get("name")) if fn_obj is not None else None,
            _string_value(entry.get("name")),
        )
        # Codex Responses providers persist call_id alongside or instead of id.
        call_id = _first_string(
            _string_value(entry.get("id")),
            _string_value(entry.get("call_id")),
        )
        if fn_obj is not None and "arguments" in fn_obj:
            args_value = fn_obj.get("arguments")
        else:
            args_value = entry.get("arguments")

        if type(args_value) is str and args_value:
            args = args_value
        elif args_value is not None:
            args = _compact_json_value(args_value)
        else:
            args = "{}"

        calls.append(_HermesToolCall(id=call_id, name=name, args=args))
    return calls


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if type(content) is str:
        if content.startswith(_CONTENT_JSON_PREFIX):
            encoded = content[len(_CONTENT_JSON_PREFIX) :]
            try:
                return _content_text(_strict_json_loads(encoded))
            except (json.JSONDecodeError, ValueError):
                return encoded
        return content
    if type(content) is list:
        return _blocks_text(content)
    if type(content) is dict:
        return _compact_json_value(content)
    if type(content) is bool:
        return "true" if content else "false"
    if type(content) is int or type(content) is float:
        return _compact_json_value(content)
    return str(content)


def _blocks_text(content: list[Any]) -> str:
    parts: list[str] = []
    for item in content:
        if type(item) is not dict:
            continue
        type_name = _string_value(item.get("type"))
        if type_name in ("text", "input_text", "output_text", None):
            text = _string_value(item.get("text"))
            if text:
                parts.append(text)
        elif type_name == "image":
            parts.append("[image]")
    return "\n".join(parts)


def _reasoning_text(row: dict[str, Any]) -> str:
    reasoning_content = _string_value(row.get("reasoning_content"))
    if reasoning_content is not None and reasoning_content.strip():
        return reasoning_content
    reasoning = _string_value(row.get("reasoning"))
    if reasoning is not None and reasoning.strip():
        return reasoning
    return ""


def _hermes_timestamp(value: Any) -> int | None:
    if type(value) is bool:
        return None
    if type(value) is int:
        if value <= 0:
            return None
        ms = value if value > 100_000_000_000 else value * 1000
        return ms if _INT64_MIN <= ms <= _INT64_MAX else None
    if type(value) is float:
        if not math.isfinite(value) or value <= 0.0:
            return None
        milliseconds = value if value > 1e11 else value * 1_000.0
        # Peer .NET MidpointRounding.AwayFromZero.
        rounded = _round_away_from_zero(milliseconds)
        return rounded if _INT64_MIN <= rounded <= _INT64_MAX else None
    if type(value) is str:
        return _parse_timestamp_string(value)
    return None


def _parse_timestamp_string(text: str) -> int | None:
    if not text:
        return None
    # Accept common ISO-8601 forms; treat naive as UTC (peer pin).
    candidate = text
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    ms = int(dt.timestamp() * 1000)
    return ms if _INT64_MIN <= ms <= _INT64_MAX else None


def _round_away_from_zero(value: float) -> int:
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _row_id(row: dict[str, Any]) -> _RowIdentity | None:
    value = row.get("id")
    if type(value) is bool:
        return None
    if type(value) is int:
        return _RowIdentity(text=str(value), numeric=value)
    if type(value) is float and value.is_integer():
        numeric = int(value)
        return _RowIdentity(text=str(numeric), numeric=numeric)
    if type(value) is str and value:
        return _RowIdentity(text=value, numeric=None)
    return None


def _resolve_group_id(
    session: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> str | None:
    if session is not None:
        session_id = _non_empty(_string_value(session.get("id")))
        if session_id is not None:
            return session_id
    for row in messages:
        session_id = _non_empty(_string_value(row.get("session_id")))
        if session_id is not None:
            return session_id
    return None


def _is_inactive(row: dict[str, Any]) -> bool:
    active = row.get("active")
    if active is False:
        return True
    if type(active) is int and not isinstance(active, bool) and active == 0:
        return True
    if type(active) is float and active == 0.0:
        return True
    return False


def _is_number_id(value: Any) -> bool:
    if type(value) is bool:
        return False
    if type(value) is int:
        return True
    if type(value) is float and math.isfinite(value):
        return True
    return False


def _as_int64(value: Any) -> int | None:
    if type(value) is bool:
        return None
    if type(value) is int:
        return value if _INT64_MIN <= value <= _INT64_MAX else None
    if type(value) is float and math.isfinite(value):
        # Ordering key only — truncate toward zero (peer f64→i64).
        truncated = int(value)
        return truncated if _INT64_MIN <= truncated <= _INT64_MAX else None
    return None


def _string_value(value: Any) -> str | None:
    return value if type(value) is str else None


def _non_empty(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _first_string(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _compact_json_value(value: Any) -> str:
    """Emit compact JSON for tool args / object content (peer relaxed_json)."""
    # compact_json requires JsonValue; Hermes tool args are JSON-compatible.
    return compact_json(value)  # type: ignore[arg-type]


# Singleton used for registration and tests.
HERMES_SOURCE_ADAPTER: Final[HermesSourceAdapter] = HermesSourceAdapter()
register_source_adapter(HERMES_SOURCE_ADAPTER)

__all__ = [
    "HERMES_SOURCE_ADAPTER",
    "HermesSourceAdapter",
    "decode_hermes",
]
