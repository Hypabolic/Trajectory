"""Success-path diagnostics (Diagnostic) and stable diagnostic codes.

UNSUPPORTED import path — public surface is root re-export of ``Diagnostic``.
Authority: contracts/spec/diagnostics.md + docs/python-implementation-spec.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Stable diagnostic wire codes (diagnostics.md contract version 1)
# Codes are additive and never repurposed.
# ---------------------------------------------------------------------------

DIAG_INVALID_JSON_LINE: Final[str] = "invalid_json_line"
DIAG_NON_OBJECT_JSON_LINE: Final[str] = "non_object_json_line"
DIAG_INJECTED_CONTEXT_DROPPED: Final[str] = "injected_context_dropped"
DIAG_NOISE_RECORD_DROPPED: Final[str] = "noise_record_dropped"
DIAG_SIDECHAIN_RECORD_DROPPED: Final[str] = "sidechain_record_dropped"
DIAG_UNKNOWN_SEMANTIC_RECORD: Final[str] = "unknown_semantic_record"
DIAG_UNKNOWN_CONTENT_BLOCK: Final[str] = "unknown_content_block"
DIAG_TOOL_CALL_ID_SYNTHESIZED: Final[str] = "tool_call_id_synthesized"
DIAG_DUPLICATE_TOOL_CALL_ID: Final[str] = "duplicate_tool_call_id"
DIAG_ORPHAN_TOOL_RESULT: Final[str] = "orphan_tool_result"
DIAG_DUPLICATE_TOOL_RESULT: Final[str] = "duplicate_tool_result"
DIAG_UNKNOWN_TOOL_NAME: Final[str] = "unknown_tool_name"
DIAG_TOOL_ARGUMENTS_RESHAPED: Final[str] = "tool_arguments_reshaped"
DIAG_TOOL_ARGUMENTS_TRUNCATED: Final[str] = "tool_arguments_truncated"
DIAG_TOOL_RESULT_TRUNCATED: Final[str] = "tool_result_truncated"
DIAG_TIMESTAMPS_SYNTHESIZED: Final[str] = "timestamps_synthesized"
DIAG_TIMESTAMPS_INTERPOLATED: Final[str] = "timestamps_interpolated"

# AHP source diagnostics (contracts/spec/sources/ahp.md) — additive.
DIAG_AHP_VERSION_MISSING: Final[str] = "ahp_version_missing"
DIAG_AHP_ACTIVE_TURN_OMITTED: Final[str] = "ahp_active_turn_omitted"
DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN: Final[str] = "ahp_unknown_message_origin"
DIAG_AHP_INPUT_REQUEST_SKIPPED: Final[str] = "ahp_input_request_skipped"
DIAG_AHP_REASONING_OMITTED: Final[str] = "ahp_reasoning_omitted"
DIAG_AHP_SYSTEM_AS_ASSISTANT: Final[str] = "ahp_system_as_assistant"
DIAG_AHP_UNRESOLVED_CONTENT_REF: Final[str] = "ahp_unresolved_content_ref"
DIAG_AHP_UNKNOWN_ACTION: Final[str] = "ahp_unknown_action"
DIAG_AHP_FOREIGN_CHANNEL: Final[str] = "ahp_foreign_channel"

# Grok Build source diagnostics (docs/grok-build-source-spec.md).
DIAG_IMAGE_CONTENT_DROPPED: Final[str] = "image_content_dropped"
DIAG_BACKEND_TOOL_RESULT_SYNTHESIZED: Final[str] = "backend_tool_result_synthesized"
DIAG_ENCRYPTED_REASONING_INCLUDED: Final[str] = "encrypted_reasoning_included"
# Shared Grok Build / Cursor source diagnostics.
DIAG_UNKNOWN_CONTENT_PART: Final[str] = "unknown_content_part"
DIAG_TOOL_USE_MISSING_NAME: Final[str] = "tool_use_missing_name"
DIAG_TURN_ENDED_ERROR: Final[str] = "turn_ended_error"

# OTEL projection diagnostic (exact fixture message in §4).
DIAG_MODEL_SPAN_OMITTED: Final[str] = "model_span_omitted"
MSG_MODEL_SPAN_OMITTED: Final[str] = (
    "Model span omitted because source-native timing or provider/model metadata is incomplete."
)

DIAGNOSTIC_CODES: Final[frozenset[str]] = frozenset(
    {
        DIAG_INVALID_JSON_LINE,
        DIAG_NON_OBJECT_JSON_LINE,
        DIAG_INJECTED_CONTEXT_DROPPED,
        DIAG_NOISE_RECORD_DROPPED,
        DIAG_SIDECHAIN_RECORD_DROPPED,
        DIAG_UNKNOWN_SEMANTIC_RECORD,
        DIAG_UNKNOWN_CONTENT_BLOCK,
        DIAG_TOOL_CALL_ID_SYNTHESIZED,
        DIAG_DUPLICATE_TOOL_CALL_ID,
        DIAG_ORPHAN_TOOL_RESULT,
        DIAG_DUPLICATE_TOOL_RESULT,
        DIAG_UNKNOWN_TOOL_NAME,
        DIAG_TOOL_ARGUMENTS_RESHAPED,
        DIAG_TOOL_ARGUMENTS_TRUNCATED,
        DIAG_TOOL_RESULT_TRUNCATED,
        DIAG_TIMESTAMPS_SYNTHESIZED,
        DIAG_TIMESTAMPS_INTERPOLATED,
        DIAG_AHP_VERSION_MISSING,
        DIAG_AHP_ACTIVE_TURN_OMITTED,
        DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN,
        DIAG_AHP_INPUT_REQUEST_SKIPPED,
        DIAG_AHP_REASONING_OMITTED,
        DIAG_AHP_SYSTEM_AS_ASSISTANT,
        DIAG_AHP_UNRESOLVED_CONTENT_REF,
        DIAG_AHP_UNKNOWN_ACTION,
        DIAG_AHP_FOREIGN_CHANNEL,
        DIAG_IMAGE_CONTENT_DROPPED,
        DIAG_BACKEND_TOOL_RESULT_SYNTHESIZED,
        DIAG_ENCRYPTED_REASONING_INCLUDED,
        DIAG_UNKNOWN_CONTENT_PART,
        DIAG_TOOL_USE_MISSING_NAME,
        DIAG_TURN_ENDED_ERROR,
        DIAG_MODEL_SPAN_OMITTED,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    """Recoverable normalization diagnostic (success path, in-process).

    Attributes use Python snake_case. Projectors map optional location keys to
    each schema's documented wire casing (see §3 casing matrix).
    """

    code: str
    message: str
    input_line: int | None = None
    record_index: int | None = None
    count: int | None = None
