"""Codex rollout JSONL source adapter (decode-only).

UNSUPPORTED import path. Self-registers as wire name ``codex`` on package
import under the PY-04a export owner. Does not edit the normalizer dispatcher
or runtime-capabilities claims.

Authority:
- docs/python-implementation-spec.md PY-05b + §4.1 decode seam
- Peer: TS ``decodeCodex``, .NET ``CodexJsonlSourceAdapter``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import INT64_MAX, INT64_MIN, compact_json
from hypabolic_trajectory.diagnostics import (
    DIAG_INJECTED_CONTEXT_DROPPED,
    DIAG_INVALID_JSON_LINE,
    DIAG_NON_OBJECT_JSON_LINE,
    Diagnostic,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import register_source_adapter
from hypabolic_trajectory.timestamps import format_ms

_MSG_OFFSET_OUT_OF_RANGE: Final[str] = (
    "Transcript byte offset exceeds signed 64-bit range."
)

_INJECTED_PREFIXES: Final[tuple[str, ...]] = (
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "<turn_context>",
)


class CodexSourceAdapter:
    """Decode-only Codex rollout JSONL → ``DecodedSession``."""

    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.CODEX

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        _ = source_context
        if type(transcript) is not bytes:
            raise TypeError("transcript must be bytes")
        return _decode_transcript(transcript)


def _decode_transcript(transcript: bytes) -> DecodedSession:
    events: list[DecodedEvent] = []
    diagnostics: list[Diagnostic] = []
    group_id: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None
    producer_version: str | None = None
    created_at_ms: int | None = None
    created_at_precise: str | None = None

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
                (
                    group_id,
                    cwd,
                    git_branch,
                    model,
                    producer_version,
                    created_at_ms,
                    created_at_precise,
                ) = _process_row(
                    row,
                    line=line,
                    source_offset=_require_i64_offset(offset),
                    events=events,
                    diagnostics=diagnostics,
                    group_id=group_id,
                    cwd=cwd,
                    git_branch=git_branch,
                    model=model,
                    producer_version=producer_version,
                    created_at_ms=created_at_ms,
                    created_at_precise=created_at_precise,
                )

        if end == length:
            break
        offset = end + 1
        line += 1

    return DecodedSession(
        source=TrajectorySource.CODEX,
        source_name="codex",
        group_id=group_id,
        group_resolved=group_id is not None,
        cwd=cwd,
        git_branch=git_branch,
        model=model,
        # Peer .NET defaults missing producer version to "unknown".
        producer_version=producer_version if producer_version is not None else "unknown",
        created_at_ms=created_at_ms,
        created_at_precise=created_at_precise,
        events=tuple(events),
        model_invocations=(),
        diagnostics=tuple(diagnostics),
    )


def _process_row(
    row: dict[str, Any],
    *,
    line: int,
    source_offset: int,
    events: list[DecodedEvent],
    diagnostics: list[Diagnostic],
    group_id: str | None,
    cwd: str | None,
    git_branch: str | None,
    model: str | None,
    producer_version: str | None,
    created_at_ms: int | None,
    created_at_precise: str | None,
) -> tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    int | None,
    str | None,
]:
    record_type = _string_value(row.get("type"))
    timestamp_data = _parse_timestamp(row.get("timestamp"))
    payload_raw = row.get("payload")
    payload: dict[str, Any] = payload_raw if type(payload_raw) is dict else {}
    payload_type = _string_value(payload.get("type"))

    def emit(
        kind: str,
        role: TrajectoryRole,
        *,
        content_text: str | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        arguments_json: str | None = None,
        event_model: str | None = None,
    ) -> None:
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
                input_line=line,
                timestamp_ms=ts_ms,
                timestamp_precise=ts_precise,
                model=event_model,
                producer_version=producer_version,
                source_offset=source_offset,
                source_anchor_kind=SourceAnchorKind.BYTE,
                component_index=0,
            )
        )

    if record_type == "session_meta":
        if cwd is None:
            cwd = _non_empty(_string_value(payload.get("cwd")))
        if group_id is None:
            group_id = _non_empty(_string_value(payload.get("id")))
        if producer_version is None:
            producer_version = _non_empty(_scalar_string(payload.get("cli_version")))
        if created_at_ms is None:
            created = _parse_timestamp(payload.get("timestamp"))
            if created is None:
                created = timestamp_data
            if created is not None:
                created_at_ms, created_at_precise = created
        if git_branch is None:
            git = payload.get("git")
            if type(git) is dict:
                git_branch = _non_empty(_string_value(git.get("branch")))
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if record_type == "turn_context":
        if cwd is None:
            cwd = _non_empty(_string_value(payload.get("cwd")))
        if model is None:
            model = _non_empty(_string_value(payload.get("model")))
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if record_type == "event_msg":
        content = _string_value(payload.get("text"))
        if payload_type == "agent_reasoning" and content is not None and content.strip():
            emit(
                "reasoning",
                TrajectoryRole.REASONING,
                content_text=content,
                event_model=model,
            )
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if record_type != "response_item":
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if payload_type == "message":
        role = _string_value(payload.get("role"))
        content = _read_blocks_text(payload.get("content"))
        if role == "user":
            head = content.lstrip()
            if any(head.startswith(prefix) for prefix in _INJECTED_PREFIXES):
                diagnostics.append(
                    Diagnostic(
                        code=DIAG_INJECTED_CONTEXT_DROPPED,
                        message=(
                            "Dropped Codex system-injected user content "
                            f"on line {line}."
                        ),
                        input_line=line,
                    )
                )
            else:
                emit(
                    "message",
                    TrajectoryRole.USER,
                    content_text=content,
                    event_model=model,
                )
        elif role == "assistant":
            emit(
                "message",
                TrajectoryRole.ASSISTANT,
                content_text=content,
                event_model=model,
            )
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if payload_type == "function_call":
        args = _non_empty(_string_value(payload.get("arguments"))) or "{}"
        emit(
            "tool-call",
            TrajectoryRole.ASSISTANT,
            tool_call_id=_string_value(payload.get("call_id")),
            tool_name=_string_value(payload.get("name")),
            arguments_json=args,
            event_model=model,
        )
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if payload_type == "custom_tool_call":
        input_value = payload.get("input")
        if input_value is None:
            wrapped: Any = ""
        else:
            wrapped = input_value
        try:
            args_json = compact_json({"input": wrapped})
        except TypeError:
            args_json = compact_json({"input": ""})
        emit(
            "tool-call",
            TrajectoryRole.ASSISTANT,
            tool_call_id=_string_value(payload.get("call_id")),
            tool_name=_string_value(payload.get("name")),
            arguments_json=args_json,
            event_model=model,
        )
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if payload_type == "web_search_call":
        filtered: dict[str, Any] = {}
        for key, value in payload.items():
            if key not in ("type", "call_id", "status"):
                filtered[key] = value
        try:
            args_json = compact_json(filtered)
        except TypeError:
            args_json = "{}"
        emit(
            "tool-call",
            TrajectoryRole.ASSISTANT,
            tool_call_id=_string_value(payload.get("call_id")),
            tool_name="web_search",
            arguments_json=args_json,
            event_model=model,
        )
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if payload_type == "tool_search_call":
        arguments_value = payload.get("arguments")
        if type(arguments_value) is str and arguments_value:
            args_json = arguments_value
        elif arguments_value is None:
            # Peer TS: compactJson(argumentsValue ?? null) → "null".
            args_json = "null"
        else:
            try:
                args_json = compact_json(arguments_value)  # type: ignore[arg-type]
            except TypeError:
                args_json = "null"
        emit(
            "tool-call",
            TrajectoryRole.ASSISTANT,
            tool_call_id=_string_value(payload.get("call_id")),
            tool_name="tool_search",
            arguments_json=args_json,
            event_model=model,
        )
        return (
            group_id,
            cwd,
            git_branch,
            model,
            producer_version,
            created_at_ms,
            created_at_precise,
        )

    if payload_type in (
        "function_call_output",
        "custom_tool_call_output",
        "tool_search_output",
    ):
        if payload_type == "tool_search_output":
            tools = payload.get("tools")
            if tools is None:
                content_text = "[]"
            else:
                try:
                    content_text = compact_json(tools)  # type: ignore[arg-type]
                except TypeError:
                    content_text = "[]"
        else:
            content_text = _output_text(payload.get("output"))
        emit(
            "tool-result",
            TrajectoryRole.TOOL,
            tool_call_id=_string_value(payload.get("call_id")),
            content_text=content_text,
            event_model=model,
        )

    return (
        group_id,
        cwd,
        git_branch,
        model,
        producer_version,
        created_at_ms,
        created_at_precise,
    )


def _output_text(value: Any) -> str:
    if type(value) is str:
        return value
    if type(value) is list:
        text = _read_blocks_text(value)
        if text:
            return text
        try:
            return compact_json(value)
        except TypeError:
            return ""
    if type(value) is dict:
        content = _non_empty(_string_value(value.get("content")))
        if content is not None:
            return content
        try:
            return compact_json(value)
        except TypeError:
            return ""
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        return str(value)
    return ""


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


def _non_empty(value: str | None) -> str | None:
    return value if value else None


def _parse_timestamp(value: Any) -> tuple[int, str] | None:
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


CODEX_SOURCE_ADAPTER: Final[CodexSourceAdapter] = CodexSourceAdapter()
register_source_adapter(CODEX_SOURCE_ADAPTER)

__all__ = [
    "CODEX_SOURCE_ADAPTER",
    "CodexSourceAdapter",
]
