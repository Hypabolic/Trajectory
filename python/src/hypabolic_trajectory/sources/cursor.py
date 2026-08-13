"""Cursor Agent ``agent-transcripts`` JSONL source adapter."""

from __future__ import annotations

import json
from typing import Any, Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.canonical import compact_json
from hypabolic_trajectory.diagnostics import (
    DIAG_IMAGE_CONTENT_DROPPED,
    DIAG_INVALID_JSON_LINE,
    DIAG_NON_OBJECT_JSON_LINE,
    DIAG_TOOL_USE_MISSING_NAME,
    DIAG_TURN_ENDED_ERROR,
    DIAG_UNKNOWN_CONTENT_PART,
    DIAG_UNKNOWN_SEMANTIC_RECORD,
    Diagnostic,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.sources.decoded import DecodedEvent, DecodedSession
from hypabolic_trajectory.sources.protocol import register_source_adapter

_SOURCE: Final[TrajectorySource] = TrajectorySource.CURSOR
_SOURCE_NAME: Final[str] = "cursor"
_IMAGE_TYPES: Final[frozenset[str]] = frozenset(
    {"image", "image_url", "input_image", "output_image"}
)


class CursorSourceAdapter:
    @property
    def source(self) -> TrajectorySource:
        return _SOURCE

    def decode(
        self, transcript: bytes, *, source_context: SourceContext
    ) -> DecodedSession:
        return decode_cursor(transcript, source_context=source_context)


def decode_cursor(
    transcript: bytes, *, source_context: SourceContext | None = None
) -> DecodedSession:
    if type(transcript) is not bytes:
        raise TypeError("transcript must be bytes")
    diagnostics: list[Diagnostic] = []
    events: list[DecodedEvent] = []

    for line_no, offset, row in _parse_json_lines(transcript, diagnostics):
        role = _string(row, "role")
        row_type = _string(row, "type")
        component_index = 0

        def emit(
            *, kind: str, role_value: TrajectoryRole, content: str | None = None,
            tool_name: str | None = None, arguments_json: str | None = None,
        ) -> None:
            nonlocal component_index
            events.append(
                DecodedEvent(
                    kind=kind,  # type: ignore[arg-type]
                    role=role_value,
                    content=content,
                    tool_name=tool_name,
                    arguments_json=arguments_json,
                    source_offset=offset,
                    source_anchor_kind=SourceAnchorKind.BYTE,
                    input_line=line_no,
                    component_index=component_index,
                )
            )
            component_index += 1

        if role in ("user", "assistant"):
            message = row.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            text_parts: list[str] = []
            if isinstance(content, list):
                tool_parts: list[dict[str, Any]] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = _string(part, "type")
                    if part_type == "text":
                        text = part.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
                    elif role == "assistant" and part_type == "tool_use":
                        tool_parts.append(part)
                    elif part_type == "tool_use":
                        pass
                    elif part_type in _IMAGE_TYPES:
                        diagnostics.append(Diagnostic(
                            code=DIAG_IMAGE_CONTENT_DROPPED,
                            message=(
                                f"Dropped image content on a Cursor record on line {line_no}."
                            ),
                            input_line=line_no,
                        ))
                    elif part_type:
                        diagnostics.append(Diagnostic(
                            code=DIAG_UNKNOWN_CONTENT_PART,
                            message=(
                                f"Skipped an unknown Cursor content part on line {line_no}."
                            ),
                            input_line=line_no,
                        ))
                if text_parts:
                    text = "\n".join(text_parts)
                    if text.strip():
                        emit(
                            kind="message",
                            role_value=(
                                TrajectoryRole.USER if role == "user" else TrajectoryRole.ASSISTANT
                            ),
                            content=text,
                        )
                for part in tool_parts:
                    name = _non_empty(_string(part, "name"))
                    if name is None:
                        diagnostics.append(Diagnostic(
                            code=DIAG_TOOL_USE_MISSING_NAME,
                            message=(
                                f"Skipped a Cursor tool_use part without a name "
                                f"on line {line_no}."
                            ),
                            input_line=line_no,
                        ))
                        continue
                    value = part.get("input")
                    arguments = compact_json(value) if isinstance(value, dict) else "{}"
                    # Tool calls are retained even when the assistant has no text.
                    emit(
                        kind="tool-call",
                        role_value=TrajectoryRole.ASSISTANT,
                        tool_name=name,
                        arguments_json=arguments,
                    )

            continue

        if row_type == "turn_ended":
            if _string(row, "status") == "error":
                diagnostics.append(Diagnostic(
                    code=DIAG_TURN_ENDED_ERROR,
                    message="A Cursor turn ended with an error.",
                    input_line=line_no,
                ))
            continue
        if role or row_type:
            diagnostics.append(Diagnostic(
                code=DIAG_UNKNOWN_SEMANTIC_RECORD,
                message=f"Skipped an unknown Cursor semantic record on line {line_no}.",
                input_line=line_no,
            ))

    return DecodedSession(
        source=_SOURCE,
        source_name=_SOURCE_NAME,
        group_resolved=False,
        model=None,
        events=tuple(events),
        model_invocations=(),
        diagnostics=tuple(diagnostics),
    )


def _parse_json_lines(
    transcript: bytes, diagnostics: list[Diagnostic]
) -> list[tuple[int, int, dict[str, Any]]]:
    parsed: list[tuple[int, int, dict[str, Any]]] = []
    offset = 0
    line_no = 1
    while offset <= len(transcript):
        newline = transcript.find(b"\n", offset)
        end = len(transcript) if newline < 0 else newline
        line = transcript[offset:end]
        if line.endswith(b"\r"):
            line = line[:-1]
        if line.strip():
            try:
                value = json.loads(line, parse_constant=_reject_constant)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                diagnostics.append(Diagnostic(
                    code=DIAG_INVALID_JSON_LINE,
                    message=f"Skipped invalid JSON on line {line_no}.",
                    input_line=line_no,
                ))
            else:
                if not isinstance(value, dict):
                    diagnostics.append(Diagnostic(
                        code=DIAG_NON_OBJECT_JSON_LINE,
                        message=f"Skipped non-object JSON on line {line_no}.",
                        input_line=line_no,
                    ))
                else:
                    parsed.append((line_no, offset, value))
        if newline < 0:
            break
        offset = newline + 1
        line_no += 1
    return parsed


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _string(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    return item if isinstance(item, str) else None


def _non_empty(value: str | None) -> str | None:
    return value if value is not None and value.strip() else None


CURSOR_SOURCE_ADAPTER: Final[CursorSourceAdapter] = CursorSourceAdapter()
register_source_adapter(CURSOR_SOURCE_ADAPTER)

__all__ = ["CURSOR_SOURCE_ADAPTER", "CursorSourceAdapter", "decode_cursor"]
