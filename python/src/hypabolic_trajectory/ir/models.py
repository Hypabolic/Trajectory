"""Public IR models (frozen dataclasses + StrEnums).

UNSUPPORTED as a direct import path for consumers — use
``hypabolic_trajectory.ir`` or the package root re-exports.

Authority: docs/python-implementation-spec.md §3 IR public type + §4.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.diagnostics import Diagnostic


class RecordKind(StrEnum):
    META = "meta"
    MESSAGE = "message"
    ASSISTANT_TOOL_CALLS = "assistant_tool_calls"
    TOOL_RESULT = "tool_result"


class TrajectoryRole(StrEnum):
    META = "meta"
    USER = "user"
    REASONING = "reasoning"
    ASSISTANT = "assistant"
    TOOL = "tool"


class SourceIdentityKind(StrEnum):
    NATIVE = "native"
    LOCATION = "location"
    CONTENT = "content"
    SYNTHETIC = "synthetic"


class SourceAnchorKind(StrEnum):
    BYTE = "byte"
    ORDINAL = "ordinal"
    ROW = "row"
    SEQUENCE = "sequence"


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelTokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelInvocation:
    id: str
    native_record_id: str | None = None
    source_sequence: int | None = None
    source_offset: int | None = None  # ABSOLUTE after base_byte_offset (§4)
    provider: str | None = None
    api_family: str | None = None
    requested_model: str | None = None
    response_model: str | None = None
    response_id: str | None = None
    stop_reason: str | None = None
    producer_version: str | None = None
    usage: ModelTokenUsage | None = None  # omit when all token fields absent
    started_at_ms: int | None = None
    started_at_precise: str | None = None
    first_response_at_ms: int | None = None
    first_response_at_precise: str | None = None
    completed_at_ms: int | None = None
    completed_at_precise: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowInvocation:
    id: str
    name: str | None = None
    native_record_id: str | None = None
    started_at_ms: int | None = None
    started_at_precise: str | None = None
    completed_at_ms: int | None = None
    completed_at_precise: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TrajectoryExecution:
    model_invocations: tuple[ModelInvocation, ...]
    workflow_invocations: tuple[WorkflowInvocation, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AppliedBounds:
    tool_arguments_max_characters: int | None
    tool_results_max_characters: int | None
    tool_results_strategy: Literal["head", "head-tail"]


@dataclass(frozen=True, slots=True, kw_only=True)
class AppliedFilters:
    tool_results: Literal["include", "omit"]


@dataclass(frozen=True, slots=True, kw_only=True)
class AppliedConfig:
    bounds: AppliedBounds
    filters: AppliedFilters
    group_id: str | None
    base_byte_offset: int
    partial: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    stable_source_record_id: str
    source_identity_kind: SourceIdentityKind
    source_order_id: str
    component_key: str
    component_index: int
    component_type_ordinal: int
    native_record_id: str | None = None
    producer_version: str | None = None
    source_sequence: int | None = None
    source_offset: int | None = None  # segment-relative on provenance
    source_anchor_kind: SourceAnchorKind | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordHashes:
    content_sha256: str
    record_sha256: str


@dataclass(frozen=True, slots=True, kw_only=True)
class IrRecord:
    id: str
    kind: RecordKind
    role: TrajectoryRole
    order: int
    provenance: Provenance
    hashes: RecordHashes
    source_timestamp_ms: int | None = None
    source_timestamp_precise: str | None = None
    timestamp_ms: int | None = None  # filled/synthesized ms
    content: str | None = None
    source_name: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None
    producer_version: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TrajectoryIR:
    """Immutable multi-project IR returned by ``normalize_to_ir``."""

    source: TrajectorySource  # NOT bare str — enum member equal to wire name
    source_name: str
    group_id: str
    source_group_resolved: bool
    records: tuple[IrRecord, ...]
    diagnostics: tuple[Diagnostic, ...]
    config: AppliedConfig
    execution: TrajectoryExecution
    producer_version: str | None = None
