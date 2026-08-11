"""Grok Build ``chat_history.jsonl`` source adapter (decode-only).

UNSUPPORTED public import path. Registers on package import.

Authority:
  - docs/grok-build-source-spec.md
  - Peer: .NET ``GrokBuildJsonlSourceAdapter``, TS ``decodeGrokBuild``,
    Rust ``decode_grok_build``
  - Conformance: ``conformance/cases/grok-build/*``

Decode-only; does not invent response IDs from model fingerprints.
"""

from __future__ import annotations

import json
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import compact_json
from hypabolic_trajectory.diagnostics import (
    DIAG_BACKEND_TOOL_RESULT_SYNTHESIZED,
    DIAG_ENCRYPTED_REASONING_INCLUDED,
    DIAG_IMAGE_CONTENT_DROPPED,
    DIAG_INVALID_JSON_LINE,
    DIAG_NON_OBJECT_JSON_LINE,
    DIAG_UNKNOWN_SEMANTIC_RECORD,
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

_SOURCE: Final[TrajectorySource] = TrajectorySource.GROK_BUILD
_SOURCE_NAME: Final[str] = "grok-build"
_INT64_MIN: Final[int] = -(2**63)
_INT64_MAX: Final[int] = 2**63 - 1


class GrokBuildSourceAdapter:
    """Native Grok Build chat-history JSONL decoder (built-in)."""

    @property
    def source(self) -> TrajectorySource:
        return _SOURCE

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        if type(transcript) is not bytes:
            raise TypeError("transcript must be bytes")
        return decode_grok_build(transcript, source_context=source_context)


def decode_grok_build(
    transcript: bytes,
    *,
    source_context: SourceContext | None = None,
) -> DecodedSession:
    """Decode Grok Build ``chat_history.jsonl`` into a ``DecodedSession``."""
    if type(transcript) is not bytes:
        raise TypeError("transcript must be bytes")

    ctx = source_context or SourceContext()
    include_encrypted = bool(ctx.include_encrypted_reasoning)
    diagnostics: list[Diagnostic] = []
    events: list[DecodedEvent] = []
    model_invocations: list[DecodedModelInvocation] = []
    first_model: str | None = None
    encrypted_included = 0

    lines = _parse_json_lines(transcript, diagnostics)
    tool_result_lines = _collect_tool_result_lines(lines)

    for line_no, offset, row in lines:
        row_type = _string(row, "type")
        if not row_type:
            # Empty / missing type is ignored (non-empty unknown types diagnose).
            continue

        component_index = 0

        def emit(
            *,
            kind: str,
            role: TrajectoryRole,
            content: str | None = None,
            tool_call_id: str | None = None,
            tool_name: str | None = None,
            arguments_json: str | None = None,
            model: str | None = None,
            native_record_id: str | None = None,
        ) -> None:
            nonlocal component_index
            _require_i64_offset(offset)
            events.append(
                DecodedEvent(
                    kind=kind,  # type: ignore[arg-type]
                    role=role,
                    content=content,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments_json=arguments_json,
                    model=model,
                    native_record_id=native_record_id,
                    source_offset=offset,
                    source_anchor_kind=SourceAnchorKind.BYTE,
                    component_index=component_index,
                    input_line=line_no,
                )
            )
            component_index += 1

        if row_type == "system":
            content = _string(row, "content") or ""
            if content.strip():
                emit(kind="message", role=TrajectoryRole.META, content=content)
            continue

        if row_type == "user":
            synthetic = _string(row, "synthetic_reason")
            text, dropped_image = _join_content_parts(row.get("content"))
            if dropped_image:
                diagnostics.append(
                    Diagnostic(
                        code=DIAG_IMAGE_CONTENT_DROPPED,
                        message=(
                            f"Dropped image content on Grok Build user record "
                            f"on line {line_no}."
                        ),
                        input_line=line_no,
                    )
                )
            if not text.strip():
                continue
            emit(
                kind="message",
                role=TrajectoryRole.META if synthetic else TrajectoryRole.USER,
                content=text,
            )
            continue

        if row_type == "assistant":
            model_id = _non_empty(_string(row, "model_id"))
            if model_id:
                if first_model is None:
                    first_model = model_id
                model_invocations.append(
                    DecodedModelInvocation(
                        source_offset=offset,
                        response_model=model_id,
                    )
                )
            content = _content_as_text(row.get("content"))
            if content.strip():
                emit(
                    kind="message",
                    role=TrajectoryRole.ASSISTANT,
                    content=content,
                    model=model_id,
                )
            tool_calls = row.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    emit(
                        kind="tool-call",
                        role=TrajectoryRole.ASSISTANT,
                        tool_call_id=_string(call, "id"),
                        tool_name=_string(call, "name"),
                        arguments_json=_read_arguments_as_stored(call),
                        model=model_id,
                    )
            continue

        if row_type == "tool_result":
            call_id = _string(row, "tool_call_id")
            content = _string(row, "content") or ""
            images = row.get("images")
            if isinstance(images, list) and len(images) > 0:
                diagnostics.append(
                    Diagnostic(
                        code=DIAG_IMAGE_CONTENT_DROPPED,
                        message=(
                            f"Dropped image content on Grok Build tool result "
                            f"on line {line_no}."
                        ),
                        input_line=line_no,
                    )
                )
            emit(
                kind="tool-result",
                role=TrajectoryRole.TOOL,
                tool_call_id=call_id,
                content=content,
            )
            continue

        if row_type == "reasoning":
            summary_text = _reasoning_summary_text(row)
            encrypted = _string(row, "encrypted_content")
            do_include = include_encrypted and bool(encrypted)
            body: str | None = None
            if summary_text.strip() and do_include:
                body = (
                    f"{summary_text}\n\n<encrypted_content>\n"
                    f"{encrypted}\n</encrypted_content>"
                )
                encrypted_included += 1
            elif summary_text.strip():
                body = summary_text
            elif do_include:
                body = f"<encrypted_content>\n{encrypted}\n</encrypted_content>"
                encrypted_included += 1
            if not body or not body.strip():
                continue
            reasoning_id = _non_empty(_string(row, "id"))
            emit(
                kind="reasoning",
                role=TrajectoryRole.REASONING,
                content=body,
                native_record_id=reasoning_id,
            )
            continue

        if row_type == "backend_tool_call":
            kind_obj = row.get("kind")
            if not isinstance(kind_obj, dict):
                continue
            tool_type = _string(kind_obj, "tool_type") or "unknown_tool"
            call_id = _string(kind_obj, "id")
            args = _backend_arguments(kind_obj)
            status = _string(kind_obj, "status")
            emit(
                kind="tool-call",
                role=TrajectoryRole.ASSISTANT,
                tool_call_id=call_id,
                tool_name=tool_type,
                arguments_json=args,
            )
            completed = status is None or status == "completed"
            has_later = False
            if call_id and call_id in tool_result_lines:
                for result_line in tool_result_lines[call_id]:
                    if result_line > line_no:
                        has_later = True
                        break
            if completed and call_id and not has_later:
                summary = _backend_result_summary(tool_type, kind_obj)
                emit(
                    kind="tool-result",
                    role=TrajectoryRole.TOOL,
                    tool_call_id=call_id,
                    content=summary,
                )
                diagnostics.append(
                    Diagnostic(
                        code=DIAG_BACKEND_TOOL_RESULT_SYNTHESIZED,
                        message="Synthesized a tool result for a backend tool call.",
                        input_line=line_no,
                    )
                )
            continue

        diagnostics.append(
            Diagnostic(
                code=DIAG_UNKNOWN_SEMANTIC_RECORD,
                message=(
                    f"Skipped an unknown Grok Build semantic record on line {line_no}."
                ),
                input_line=line_no,
            )
        )

    if encrypted_included > 0:
        diagnostics.append(
            Diagnostic(
                code=DIAG_ENCRYPTED_REASONING_INCLUDED,
                message=(
                    f"Included encrypted reasoning content for "
                    f"{encrypted_included} item(s)."
                ),
                count=encrypted_included,
            )
        )

    if not events and not diagnostics:
        # Empty transcript is valid; peers accept empty chat history.
        pass

    return DecodedSession(
        source=_SOURCE,
        source_name=_SOURCE_NAME,
        group_resolved=False,
        model=first_model,
        events=tuple(events),
        model_invocations=tuple(model_invocations),
        diagnostics=tuple(diagnostics),
    )


def _parse_json_lines(
    transcript: bytes,
    diagnostics: list[Diagnostic],
) -> list[tuple[int, int, dict[str, Any]]]:
    parsed: list[tuple[int, int, dict[str, Any]]] = []
    offset = 0
    line_number = 1
    data = transcript
    while offset <= len(data):
        newline = data.find(b"\n", offset)
        if newline < 0:
            end = len(data)
        else:
            end = newline
        line = data[offset:end]
        if line.endswith(b"\r"):
            line = line[:-1]
        if line.strip():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                diagnostics.append(
                    Diagnostic(
                        code=DIAG_INVALID_JSON_LINE,
                        message=f"Skipped invalid JSON on line {line_number}.",
                        input_line=line_number,
                    )
                )
            else:
                if not isinstance(value, dict):
                    diagnostics.append(
                        Diagnostic(
                            code=DIAG_NON_OBJECT_JSON_LINE,
                            message=f"Skipped non-object JSON on line {line_number}.",
                            input_line=line_number,
                        )
                    )
                else:
                    parsed.append((line_number, offset, value))
        if newline < 0:
            break
        offset = newline + 1
        line_number += 1
    return parsed


def _collect_tool_result_lines(
    lines: list[tuple[int, int, dict[str, Any]]],
) -> dict[str, list[int]]:
    mapping: dict[str, list[int]] = {}
    for line_no, _offset, row in lines:
        if _string(row, "type") != "tool_result":
            continue
        call_id = _string(row, "tool_call_id")
        if not call_id:
            continue
        mapping.setdefault(call_id, []).append(line_no)
    return mapping


def _backend_arguments(kind: dict[str, Any]) -> str:
    if "action" in kind and kind["action"] is not None:
        return compact_json({"action": kind["action"]})
    fields: dict[str, Any] = {}
    for name in ("query", "input", "code"):
        if name in kind and kind[name] is not None:
            fields[name] = kind[name]
    if not fields:
        return "{}"
    return compact_json(fields)


def _backend_result_summary(tool_type: str, kind: dict[str, Any]) -> str:
    detail: str | None = None
    action = kind.get("action")
    if isinstance(action, dict):
        action_type = _string(action, "type") or "action"
        query = (
            _string(action, "query")
            or _string(action, "input")
            or _string(action, "code")
        )
        detail = action_type if query is None else f"{action_type}: {query}"
    else:
        detail = (
            _string(kind, "query")
            or _string(kind, "input")
            or _string(kind, "code")
        )
    if detail is None:
        return f"[backend {tool_type}]"
    return f"[backend {tool_type}] {detail}"


def _reasoning_summary_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = row.get("summary")
    if isinstance(summary, list):
        for item in summary:
            if not isinstance(item, dict):
                continue
            item_type = _string(item, "type")
            if item_type in ("summary_text", "text", None):
                text = _string(item, "text")
                if text:
                    parts.append(text)
    content = _content_as_text(row.get("content"))
    if content.strip():
        parts.append(content)
    return "\n".join(parts)


def _join_content_parts(content: Any) -> tuple[str, bool]:
    if content is None:
        return "", False
    if isinstance(content, str):
        return content, False
    if not isinstance(content, list):
        return "", False
    parts: list[str] = []
    dropped_image = False
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = _string(part, "type")
        if part_type in ("text", "input_text", "output_text", None):
            text = _string(part, "text")
            if text:
                parts.append(text)
        elif part_type == "image":
            dropped_image = True
    return "\n".join(parts), dropped_image


def _content_as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _join_content_parts(content)[0]
    return ""


def _read_arguments_as_stored(call: dict[str, Any]) -> str:
    if "arguments" not in call:
        return "{}"
    arguments = call["arguments"]
    if isinstance(arguments, str):
        return arguments if arguments else "{}"
    if arguments is None:
        return "{}"
    return compact_json(arguments)


def _string(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if isinstance(value, str):
        return value
    return None


def _non_empty(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _require_i64_offset(offset: int) -> None:
    if type(offset) is not int or isinstance(offset, bool):
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            "Transcript byte offset exceeds signed 64-bit range.",
        )
    if offset < _INT64_MIN or offset > _INT64_MAX:
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            "Transcript byte offset exceeds signed 64-bit range.",
        )


GROK_BUILD_SOURCE_ADAPTER: Final[GrokBuildSourceAdapter] = GrokBuildSourceAdapter()
register_source_adapter(GROK_BUILD_SOURCE_ADAPTER)

__all__ = [
    "GROK_BUILD_SOURCE_ADAPTER",
    "GrokBuildSourceAdapter",
    "decode_grok_build",
]
