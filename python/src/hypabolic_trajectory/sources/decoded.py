"""Decode-seam types (adapter → normalize boundary).

Frozen after PY-04a — changes require an explicit issue.
UNSUPPORTED as a public import path.

Authority: docs/python-implementation-spec.md §4.1 tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.diagnostics import Diagnostic
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedModelInvocation:
    native_record_id: str | None = None
    source_sequence: int | None = None
    source_offset: int | None = None  # segment-relative; absolute applied in normalizer
    provider: str | None = None
    api_family: str | None = None
    requested_model: str | None = None
    response_model: str | None = None
    response_id: str | None = None
    stop_reason: str | None = None
    producer_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    started_at_ms: int | None = None
    started_at_precise: str | None = None
    first_response_at_ms: int | None = None
    first_response_at_precise: str | None = None
    completed_at_ms: int | None = None
    completed_at_precise: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedEvent:
    kind: Literal["message", "reasoning", "tool-call", "tool-result"]
    role: TrajectoryRole
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_json: str | None = None
    is_error: bool | None = None
    input_line: int | None = None  # 1-based
    timestamp_ms: int | None = None
    timestamp_precise: str | None = None
    model: str | None = None
    producer_version: str | None = None
    native_record_id: str | None = None
    source_sequence: int | None = None
    source_offset: int | None = None  # segment-relative
    source_anchor_kind: SourceAnchorKind | None = None
    component_index: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedSession:
    # Field order matches §4.1 DecodedSession freeze table.
    # kw_only=True allows required fields after optionals.
    source: TrajectorySource
    source_name: str
    group_id: str | None = None
    group_resolved: bool  # required
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None
    producer_version: str | None = None
    created_at_ms: int | None = None
    created_at_precise: str | None = None
    events: tuple[DecodedEvent, ...]  # required
    model_invocations: tuple[DecodedModelInvocation, ...]  # required
    diagnostics: tuple[Diagnostic, ...]  # required
