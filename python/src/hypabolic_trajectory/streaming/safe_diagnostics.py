"""Content-safe stream diagnostic projection (H2).

At the stream projection boundary, IR/batch normalizer diagnostics may embed
source-native tool IDs or other free-form text. Stream wire diagnostics keep
only the stable code plus safe structural fields (input_line, record_index,
count) and a fixed catalog message — never the raw normalizer message.

Authority: contracts/spec/diagnostics.md content-safety;
docs/live-session-streaming.md §9.
"""

from __future__ import annotations

from typing import Final

from hypabolic_trajectory.streaming.types import StreamDiagnostic

# Fixed catalog messages (no source IDs, paths, payloads, or transcript prose).
_STREAM_DIAG_MESSAGES: Final[dict[str, str]] = {
    "invalid_json_line": "Skipped invalid JSON line.",
    "non_object_json_line": "Skipped non-object JSON line.",
    "injected_context_dropped": "Dropped injected context content.",
    "noise_record_dropped": "Dropped a noise record.",
    "sidechain_record_dropped": "Dropped a sidechain record.",
    "unknown_semantic_record": "Dropped an unknown semantic record.",
    "unknown_content_block": "Dropped an unknown content block.",
    "tool_call_id_synthesized": "Synthesized a tool-call ID.",
    "duplicate_tool_call_id": "Renamed a duplicate tool-call ID.",
    "orphan_tool_result": "Dropped a tool result without a preceding call.",
    "duplicate_tool_result": "Dropped a duplicate tool result.",
    "unknown_tool_name": "Substituted a default name for a missing tool name.",
    "tool_arguments_reshaped": "Reshaped tool-call arguments into a JSON object.",
    "tool_arguments_truncated": "Truncated tool-call arguments.",
    "tool_result_truncated": "Truncated a tool result.",
    "timestamps_synthesized": "Synthesized timestamps for normalized records.",
    "timestamps_interpolated": "Interpolated timestamps for normalized records.",
    "ahp_version_missing": "Snapshot lacks ahpProtocolVersion; assumed pinned 0.7.x.",
    "ahp_active_turn_omitted": "Omitted incomplete activeTurn (snapshot whole-mode policy).",
    "ahp_unknown_message_origin": "Dropped a message with an unknown origin kind.",
    "ahp_input_request_skipped": "Skipped an inputRequest response part.",
    "ahp_reasoning_omitted": "Omitted reasoning content.",
    "ahp_system_as_assistant": "Mapped a system message origin to assistant.",
    "ahp_unresolved_content_ref": (
        "Dropped a resource response part without fetching content-by-reference."
    ),
    "ahp_unknown_action": "Ignored an unknown AHP action type.",
    "ahp_foreign_channel": "Ignored an AHP action for a non-target channel.",
    "image_content_dropped": "Dropped image content.",
    "backend_tool_result_synthesized": "Synthesized a tool result for a backend tool call.",
    "encrypted_reasoning_included": "Included encrypted reasoning content.",
    "model_span_omitted": (
        "Model span omitted because source-native timing or provider/model "
        "metadata is incomplete."
    ),
    # Stream operational codes (already fixed at construction; catalog for safety).
    "stream_buffer_limit": "Stream buffer limit exceeded.",
    "stream_cursor_conflict": "Supplied stream cursor does not match stream state.",
    "stream_source_reset": "Source material changed relative to the active stream.",
    "stream_resync_required": "Stream requires resync.",
    "stream_sequence_gap": "AHP action-log serverSeq gap requires snapshot resync.",
}

# Fatal / error codes projected into StreamUpdate.error.
_STREAM_ERROR_MESSAGES: Final[dict[str, str]] = {
    "invalid_input": "Invalid stream input.",
    "unknown_source": "Unknown or invalid stream source.",
    "unknown_output_schema": "Unknown output schema.",
    "missing_user_records": "Normalized transcript is missing user records.",
    "missing_assistant_records": "Normalized transcript is missing assistant records.",
    "invalid_normalized_transcript": "Normalized transcript is invalid.",
    "listing_unavailable": "Listing is unavailable.",
    "source_group_conflict": "Source group changed relative to the active stream.",
    "source_group_required": "Source group is required.",
    "stream_buffer_limit": "Stream buffer limit exceeded.",
    "stream_cursor_conflict": "Supplied stream cursor does not match stream state.",
    "stream_source_reset": "Source material changed relative to the active stream.",
    "stream_resync_required": "Stream requires resync.",
    "stream_sequence_gap": "AHP action-log serverSeq gap requires snapshot resync.",
}

# Stream-internal fixed messages that are already content-safe and more specific
# than the generic fatal catalog. Allowed through when exact match.
_ALLOWED_FIXED_ERROR_MESSAGES: Final[frozenset[str]] = frozenset(
    {
        "Stream buffer limit exceeded.",
        "Stream buffer limits must be non-negative int64 values.",
        "Supplied stream cursor does not match stream state.",
        "Source group changed relative to the active stream.",
        "Source material is shorter than the committed cursor.",
        "Source material was compacted relative to the committed cursor.",
        "Source material was replaced relative to the committed cursor.",
        "Committed prefix hash does not match supplied material.",
        "Stream input kind is not supported for this source.",
        "AHP stream apply requires source ahp.",
        "Hermes export stream apply requires source hermes.",
        "Hermes export material is not valid session-export JSON.",
        "Stream is already finished.",
        "Unknown or invalid stream source.",
        "AHP action-log serverSeq gap requires snapshot resync.",
        "AHP action batch could not be parsed.",
        "AHP snapshot material is not valid Shape A JSON.",
        "AHP action batch serverSeq order must be strictly increasing.",
        "AHP action batch must not mix sequenced and unsequenced envelopes.",
        "AHP action batch must be JSONL envelopes or a JSON array.",
        "AHP action envelope is missing a valid serverSeq.",
        "reset input requires a StreamResetRequest.",
        "Stream material length exceeds non-negative int64 domain.",
        "Stream cursor serverSeq positions must be non-negative int64 values.",
    }
)


def stream_diagnostic_message(
    code: str,
    *,
    input_line: int | None = None,
    count: int | None = None,
) -> str:
    """Return a content-safe fixed message for a stream diagnostic code.

    Safe structural fields may appear in the message (line number, count) when
    they are already present as diagnostic fields — never source IDs or content.
    """
    if code == "invalid_json_line" and input_line is not None:
        return f"Skipped invalid JSON on line {input_line}."
    if code == "non_object_json_line" and input_line is not None:
        return f"Skipped non-object JSON on line {input_line}."
    if code == "sidechain_record_dropped" and input_line is not None:
        return f"Dropped a sidechain record on line {input_line}."
    if code == "timestamps_synthesized" and count is not None:
        return f"Synthesized timestamps for {count} normalized records."
    if code == "timestamps_interpolated" and count is not None:
        return f"Interpolated timestamps for {count} normalized records."
    return _STREAM_DIAG_MESSAGES.get(code, "Stream diagnostic.")


def stream_error_message(code: str, candidate: str | None = None) -> str:
    """Return a content-safe fixed message for StreamUpdate.error.

    Prefer a known stream-internal fixed candidate; otherwise use the catalog
    for the code. Never pass through free-form normalizer text.
    """
    if candidate is not None and candidate in _ALLOWED_FIXED_ERROR_MESSAGES:
        return candidate
    return _STREAM_ERROR_MESSAGES.get(code, "Stream error.")


def project_stream_diagnostic(
    *,
    code: str,
    message: str | None = None,
    input_line: int | None = None,
    record_index: int | None = None,
    count: int | None = None,
) -> StreamDiagnostic:
    """Project an IR/batch diagnostic into a content-safe StreamDiagnostic.

    ``message`` is ignored (catalog only). Kept as a parameter so call sites
    can pass through IR fields without copying unsafe text by accident.
    """
    del message  # never forward raw normalizer text
    return StreamDiagnostic(
        code=code,
        message=stream_diagnostic_message(
            code, input_line=input_line, count=count
        ),
        input_line=input_line,
        record_index=record_index,
        count=count,
    )
