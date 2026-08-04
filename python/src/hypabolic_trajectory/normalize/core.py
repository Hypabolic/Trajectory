"""``normalize_to_ir`` — entry validation + full normalization behaviour (PY-04b).

Authority:
  - docs/python-implementation-spec.md §4 (behaviour pins + model-invocation formula)
  - contracts/spec/normalization.md, identity.md, timestamps.md, diagnostics.md
  - tip Rust/TS/.NET normalizers for observable parity
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Final

from hypabolic_trajectory.canonical import canonical_json, compact_json
from hypabolic_trajectory.diagnostics import (
    DIAG_DUPLICATE_TOOL_CALL_ID,
    DIAG_DUPLICATE_TOOL_RESULT,
    DIAG_NOISE_RECORD_DROPPED,
    DIAG_ORPHAN_TOOL_RESULT,
    DIAG_TIMESTAMPS_INTERPOLATED,
    DIAG_TIMESTAMPS_SYNTHESIZED,
    DIAG_TOOL_ARGUMENTS_RESHAPED,
    DIAG_TOOL_ARGUMENTS_TRUNCATED,
    DIAG_TOOL_CALL_ID_SYNTHESIZED,
    DIAG_TOOL_RESULT_TRUNCATED,
    DIAG_UNKNOWN_TOOL_NAME,
    Diagnostic,
)
from hypabolic_trajectory.dto import (
    Bounds,
    Filters,
    NormalizeOptions,
    NormalizeRequest,
    SourceContext,
    ToolArgumentBounds,
    ToolResultBounds,
)
from hypabolic_trajectory.errors import (
    FATAL_INVALID_INPUT,
    FATAL_MISSING_ASSISTANT_RECORDS,
    FATAL_MISSING_USER_RECORDS,
    FATAL_SOURCE_GROUP_CONFLICT,
    FATAL_UNKNOWN_SOURCE,
    TrajectoryError,
    raise_trajectory_error,
)
from hypabolic_trajectory.identity import (
    location_identity,
    model_invocation_id,
    record_id,
    sha256_hex,
)
from hypabolic_trajectory.ir.models import (
    AppliedBounds,
    AppliedConfig,
    AppliedFilters,
    IrRecord,
    ModelInvocation,
    ModelTokenUsage,
    Provenance,
    RecordHashes,
    RecordKind,
    SourceAnchorKind,
    SourceIdentityKind,
    ToolCall,
    TrajectoryExecution,
    TrajectoryIR,
    TrajectoryRole,
)
from hypabolic_trajectory.normalize.bounds import shrink_arguments, truncate_result
from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedModelInvocation,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import get_source_adapter
from hypabolic_trajectory.timestamps import format_ms
from hypabolic_trajectory._enums import TrajectorySource

# Signed int64 range for domain entry checks (base_byte_offset, sequences, …).
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

# contracts/spec/timestamps.md — synthetic base when no anchors and no created_at.
_SYNTHETIC_BASE_MS: Final[int] = 1_767_225_600_000  # 2026-01-01T00:00:00.000Z

_NOISE_PREFIXES: Final[tuple[str, ...]] = (
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<task-notification",
)

_MSG_INVALID_BASE_BYTE_OFFSET = "base_byte_offset is out of range."
_MSG_INVALID_TRANSCRIPT_ENCODE = "Transcript could not be encoded as UTF-8."
_MSG_INVALID_ARGUMENT_BOUNDS = "Tool argument max_characters is invalid."
_MSG_INVALID_RESULT_BOUNDS = "Tool result max_characters is invalid."
_MSG_INVALID_FILTERS = "filters.tool_results is invalid."
_MSG_UNKNOWN_SOURCE = "Unknown source."
_MSG_NORMALIZE_NOT_IMPLEMENTED = (
    "normalize_to_ir behaviour is not implemented for this source yet."
)
_MSG_BYTE_ANCHOR_OOR = "Byte anchor is out of range."
_MSG_MODEL_BYTE_ANCHOR_OOR = "Model invocation byte anchor is out of range."
_MSG_MISSING_USER = "Transcript did not contain any normalizable user records."
_MSG_MISSING_ASSISTANT = (
    "Transcript did not contain any normalizable assistant records."
)
_MSG_TS_SYNTH_OOR = "Synthesized timestamp is out of range."
_MSG_TS_INTERP_OOR = "Interpolated timestamp is out of range."
_MSG_RECORD_ORDER_OOR = "Record order exceeds signed 64-bit range."


def _is_exact_int(value: object) -> bool:
    """True for exact ``int`` (not bool subclass)."""
    return type(value) is int


def _is_signed_int64(value: object) -> bool:
    return _is_exact_int(value) and _INT64_MIN <= value <= _INT64_MAX  # type: ignore[operator]


def _non_empty(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _quote(value: str) -> str:
    """JSON-encode *value* as a quoted string (diagnostic messages)."""
    return compact_json(value)


def resolve_source(source: TrajectorySource | str) -> TrajectorySource:
    """Resolve request source to a ``TrajectorySource`` enum member.

    Unknown wire names raise ``TrajectoryError(code="unknown_source")``.
    """
    if isinstance(source, TrajectorySource):
        return source
    if type(source) is not str:
        raise TypeError("source must be TrajectorySource or str")
    # Construct domain error outside ``except`` so ``__context__`` is not set
    # (content-safety pin — no low-level exception chain).
    domain: TrajectoryError | None = None
    try:
        return TrajectorySource(source)
    except ValueError:
        domain = TrajectoryError(FATAL_UNKNOWN_SOURCE, _MSG_UNKNOWN_SOURCE)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)


def encode_transcript(transcript: bytes | str) -> bytes:
    """Return UTF-8 transcript bytes. ``str`` is encoded once as UTF-8 strict."""
    if type(transcript) is bytes:
        return transcript
    if type(transcript) is not str:
        raise TypeError("transcript must be bytes or str")
    domain: TrajectoryError | None = None
    encoded: bytes | None = None
    try:
        encoded = transcript.encode("utf-8")
    except UnicodeEncodeError:
        domain = TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT_ENCODE)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)
    assert encoded is not None
    return encoded


def validate_normalize_entry(request: NormalizeRequest) -> tuple[TrajectorySource, bytes]:
    """Domain entry checks for normalize free functions / engine.

    Raises:
        TypeError: programmer type mistakes on request fields (checked first).
        TrajectoryError: domain contract failures (int64, bounds, unknown source).
    """
    if not isinstance(request, NormalizeRequest):
        raise TypeError("request must be NormalizeRequest")

    # ---- Python type boundary (before domain work) ----
    ctx = request.source_context
    if not isinstance(ctx, SourceContext):
        raise TypeError("source_context must be SourceContext")
    options = request.options
    if not isinstance(options, NormalizeOptions):
        raise TypeError("options must be NormalizeOptions")
    if not isinstance(options.bounds, Bounds):
        raise TypeError("options.bounds must be Bounds")
    if not isinstance(options.filters, Filters):
        raise TypeError("options.filters must be Filters")
    if not isinstance(options.bounds.tool_arguments, ToolArgumentBounds):
        raise TypeError("tool_arguments must be ToolArgumentBounds")
    if not isinstance(options.bounds.tool_results, ToolResultBounds):
        raise TypeError("tool_results must be ToolResultBounds")

    if ctx.group_id is not None and type(ctx.group_id) is not str:
        raise TypeError("source_context.group_id must be str or None")
    if type(ctx.partial) is not bool:
        raise TypeError("source_context.partial must be bool")
    if type(ctx.base_byte_offset) is not int:
        raise TypeError("source_context.base_byte_offset must be int")

    if not isinstance(request.source, TrajectorySource) and type(request.source) is not str:
        raise TypeError("source must be TrajectorySource or str")

    arg_max = options.bounds.tool_arguments.max_characters
    if arg_max is not None and type(arg_max) is not int:
        raise TypeError("tool_arguments.max_characters must be int or None")
    res_max = options.bounds.tool_results.max_characters
    if res_max is not None and type(res_max) is not int:
        raise TypeError("tool_results.max_characters must be int or None")
    strategy = options.bounds.tool_results.strategy
    if type(strategy) is not str:
        raise TypeError("tool_results.strategy must be str")
    tool_results_filter = options.filters.tool_results
    if type(tool_results_filter) is not str:
        raise TypeError("filters.tool_results must be str")

    # Transcript type before source domain (mixed-invalid: TypeError before unknown_source).
    transcript = encode_transcript(request.transcript)

    # ---- Domain contract ----
    source = resolve_source(request.source)

    if not _is_signed_int64(ctx.base_byte_offset):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_BASE_BYTE_OFFSET) from None

    # Argument max_characters == 1 or <= 0 (non-None) → invalid_input
    if arg_max is not None and (arg_max == 1 or arg_max <= 0):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_ARGUMENT_BOUNDS) from None

    # Result max_characters <= 0 (non-None) → invalid_input
    if res_max is not None and res_max <= 0:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_RESULT_BOUNDS) from None

    if strategy not in ("head", "head-tail"):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_RESULT_BOUNDS) from None

    if tool_results_filter not in ("include", "omit"):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_FILTERS) from None

    return source, transcript


def _apply_config(request: NormalizeRequest) -> AppliedConfig:
    """Build AppliedConfig from request (defaults already on DTOs)."""
    bounds = request.options.bounds
    filters = request.options.filters
    ctx = request.source_context
    return AppliedConfig(
        bounds=AppliedBounds(
            tool_arguments_max_characters=bounds.tool_arguments.max_characters,
            tool_results_max_characters=bounds.tool_results.max_characters,
            tool_results_strategy=bounds.tool_results.strategy,
        ),
        filters=AppliedFilters(tool_results=filters.tool_results),
        group_id=ctx.group_id,
        base_byte_offset=ctx.base_byte_offset,
        partial=ctx.partial,
    )


def _checked_add_i64(left: int, right: int, message: str) -> int:
    """Signed int64 addition; overflow → invalid_input."""
    total = left + right
    if total < _INT64_MIN or total > _INT64_MAX:
        raise TrajectoryError(FATAL_INVALID_INPUT, message) from None
    return total


# ---------------------------------------------------------------------------
# Tool-call planning
# ---------------------------------------------------------------------------


@dataclass
class _PlannedCall:
    source_id: str
    final_id: str
    synthesized: bool
    renamed: bool
    consumed: bool = False


@dataclass
class _Plan:
    calls: dict[int, _PlannedCall] = field(default_factory=dict)
    open_calls: dict[str, list[_PlannedCall]] = field(default_factory=dict)
    ordinals: list[int] = field(default_factory=list)


def _semantic_bucket(event: DecodedEvent) -> str:
    if event.kind == "tool-call":
        return "tool_call"
    if event.kind == "tool-result":
        return "tool_result"
    return event.kind  # message | reasoning


def plan_events(events: tuple[DecodedEvent, ...] | list[DecodedEvent]) -> _Plan:
    """Plan tool-call IDs and component type ordinals (tip algorithm)."""
    plan = _Plan()
    used: set[str] = set()
    seen: dict[str, int] = {}
    occurrence = -1
    for index, event in enumerate(events):
        if event.component_index == 0:
            occurrence += 1
        key = f"{occurrence}:{_semantic_bucket(event)}"
        ordinal = seen.get(key, 0)
        plan.ordinals.append(ordinal)
        seen[key] = ordinal + 1
        if event.kind != "tool-call":
            continue
        source_id = event.tool_call_id if event.tool_call_id else f"call_{index + 1}"
        final_id = source_id
        synthesized = event.tool_call_id is None or event.tool_call_id == ""
        if synthesized:
            source_id = f"call_{index + 1}"
            final_id = source_id
        renamed = False
        if final_id in used:
            suffix = 2
            while f"{source_id}__{suffix}" in used:
                suffix += 1
            final_id = f"{source_id}__{suffix}"
            renamed = True
        used.add(final_id)
        call = _PlannedCall(
            source_id=source_id,
            final_id=final_id,
            synthesized=synthesized,
            renamed=renamed,
        )
        plan.calls[index] = call
        plan.open_calls.setdefault(source_id, []).append(call)
    return plan


# ---------------------------------------------------------------------------
# Group resolution
# ---------------------------------------------------------------------------


def resolve_group_id(
    detected: str | None,
    provided: str | None,
) -> tuple[str, bool]:
    """Return ``(resolved_group_id, source_group_resolved)``.

    Conflict → ``source_group_conflict``. Empty strings treated as absent (tip).
    """
    det = _non_empty(detected)
    prov = _non_empty(provided)
    if det is not None and prov is not None and det != prov:
        raise TrajectoryError(
            FATAL_SOURCE_GROUP_CONFLICT,
            (
                f"Detected source group {_quote(det)} conflicts with the "
                f"provided source context group {_quote(prov)}."
            ),
        ) from None
    if det is not None:
        return det, True
    if prov is not None:
        return prov, True
    return "default", False


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def fill_timestamps(
    count: int,
    anchors: dict[int, int],
    created_at_ms: int | None,
    diagnostics: list[Diagnostic],
) -> list[int]:
    """Fill body timestamps per contracts/spec/timestamps.md (tip algorithm)."""
    if count == 0:
        return []
    if not anchors:
        diagnostics.append(
            Diagnostic(
                code=DIAG_TIMESTAMPS_SYNTHESIZED,
                message=f"Synthesized timestamps for {count} normalized records.",
                count=count,
            )
        )
        start = created_at_ms if created_at_ms is not None else _SYNTHETIC_BASE_MS
        out: list[int] = []
        for index in range(count):
            step = index * 15_000
            total = start + step
            if total < _INT64_MIN or total > _INT64_MAX:
                raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_TS_SYNTH_OOR) from None
            out.append(total)
        return out

    output = [0] * count
    indexes = sorted(anchors.keys())
    first = indexes[0]
    last = indexes[-1]
    for index in range(first):
        distance = first - index
        value = anchors[first] - distance * 1_000
        if value < _INT64_MIN or value > _INT64_MAX:
            raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_TS_INTERP_OOR) from None
        output[index] = value
    for cursor in range(len(indexes) - 1):
        start_index = indexes[cursor]
        end_index = indexes[cursor + 1]
        start = anchors[start_index]
        span = anchors[end_index] - start
        output[start_index] = start
        gap = end_index - start_index
        for index in range(start_index + 1, end_index):
            # Truncate toward zero (Python // truncates toward -inf for negatives;
            # match tip: integer division toward zero for positive spans; use
            # int(float) style via trunc div that matches Rust i128 /).
            numerator = span * (index - start_index)
            value = start + _div_toward_zero(numerator, gap)
            if value < _INT64_MIN or value > _INT64_MAX:
                raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_TS_INTERP_OOR) from None
            output[index] = value
    output[last] = anchors[last]
    for index in range(last + 1, count):
        distance = index - last
        value = anchors[last] + distance * 1_000
        if value < _INT64_MIN or value > _INT64_MAX:
            raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_TS_INTERP_OOR) from None
        output[index] = value
    interpolated = count - len(anchors)
    if interpolated > 0:
        diagnostics.append(
            Diagnostic(
                code=DIAG_TIMESTAMPS_INTERPOLATED,
                message=(
                    f"Interpolated timestamps for {interpolated} normalized records."
                ),
                count=interpolated,
            )
        )
    return output


def _div_toward_zero(numerator: int, denominator: int) -> int:
    """Integer division truncating toward zero (peer ``i128`` / ``Math.trunc``)."""
    if denominator == 0:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_TS_INTERP_OOR) from None
    # True division + int() truncates toward zero for both signs.
    return int(numerator / denominator)


# ---------------------------------------------------------------------------
# Identity / hashing
# ---------------------------------------------------------------------------


def _record_type_name(kind: RecordKind, role: TrajectoryRole) -> str:
    if kind is RecordKind.META:
        return "meta"
    if kind is RecordKind.ASSISTANT_TOOL_CALLS:
        return "assistant-tool-call"
    if kind is RecordKind.TOOL_RESULT:
        return "tool"
    if role is TrajectoryRole.USER:
        return "user"
    if role is TrajectoryRole.REASONING:
        return "reasoning"
    return "assistant"


def _decoded_record_type(event: DecodedEvent) -> str:
    if event.kind == "reasoning":
        return "reasoning"
    if event.kind == "tool-call":
        return "assistant-tool-call"
    if event.kind == "tool-result":
        return "tool"
    if event.role is TrajectoryRole.USER:
        return "user"
    return "assistant"


def _content_hash_for_semantic(record_type: str, semantic: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({"type": record_type, "content": semantic}))


def _source_order_id(
    timestamp_ms: int | None,
    sequence: int | None,
    stable_id: str,
) -> str:
    time_text = (
        "0000-00-00T00:00:00.001Z"
        if timestamp_ms is None
        else format_ms(timestamp_ms)
    )
    seq = 0 if sequence is None else sequence
    return f"1|{time_text}|{seq:020d}|{stable_id}"


def _resolve_stable_identity(
    event: DecodedEvent,
    group_id: str,
    base_byte_offset: int,
    content_hash: str,
) -> tuple[str, SourceIdentityKind, int | None]:
    """Return ``(stable_id, kind, provenance_source_offset)``.

    Provenance offset: segment-relative when native; absolute for location/byte.
    """
    native = _non_empty(event.native_record_id)
    if native is not None:
        return native, SourceIdentityKind.NATIVE, event.source_offset

    if event.source_offset is not None:
        anchor = event.source_anchor_kind or SourceAnchorKind.ORDINAL
        if anchor is SourceAnchorKind.BYTE:
            absolute = _checked_add_i64(
                event.source_offset, base_byte_offset, _MSG_BYTE_ANCHOR_OOR
            )
            offset_for_id = absolute
            provenance_offset = absolute
        else:
            offset_for_id = event.source_offset
            provenance_offset = event.source_offset
        stable = location_identity(group_id, anchor.value, offset_for_id)
        return stable, SourceIdentityKind.LOCATION, provenance_offset

    if event.source_sequence is not None:
        stable = location_identity(
            group_id, SourceAnchorKind.SEQUENCE.value, event.source_sequence
        )
        return stable, SourceIdentityKind.LOCATION, None

    # Content fallback (identity.md).
    record_type = _decoded_record_type(event)
    literal = (
        f"{group_id}|content|{record_type}|{content_hash}|{event.component_index}"
    )
    return sha256_hex(literal), SourceIdentityKind.CONTENT, None


def _to_letta_record(record: IrRecord) -> dict[str, Any]:
    """Canonical message-trajectory record JSON (excludes hashes/provenance)."""
    if record.kind is RecordKind.META:
        out: dict[str, Any] = {
            "role": "meta",
            "source": record.source_name if record.source_name is not None else "",
        }
        if record.cwd is not None:
            out["cwd"] = record.cwd
        if record.git_branch is not None:
            out["git_branch"] = record.git_branch
        if record.model is not None:
            out["model"] = record.model
        return out
    if record.kind is RecordKind.ASSISTANT_TOOL_CALLS:
        call = record.tool_calls[0]
        assert record.timestamp_ms is not None
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "args": call.arguments_json,
                }
            ],
            "timestamp": format_ms(record.timestamp_ms),
        }
    if record.kind is RecordKind.TOOL_RESULT:
        assert record.timestamp_ms is not None
        return {
            "role": "tool",
            "tool_call_id": record.tool_call_id or "",
            "content": record.content or "",
            "timestamp": format_ms(record.timestamp_ms),
        }
    # message
    assert record.timestamp_ms is not None
    return {
        "role": record.role.value,
        "content": record.content or "",
        "timestamp": format_ms(record.timestamp_ms),
    }


def _semantic_content(record: IrRecord) -> dict[str, Any]:
    if record.kind is RecordKind.META:
        value: dict[str, Any] = {
            "source": record.source_name if record.source_name is not None else "",
        }
        if record.cwd is not None:
            value["cwd"] = record.cwd
        if record.git_branch is not None:
            value["git_branch"] = record.git_branch
        if record.model is not None:
            value["model"] = record.model
        return value
    if record.kind is RecordKind.ASSISTANT_TOOL_CALLS:
        call = record.tool_calls[0]
        return {"name": call.name, "args": call.arguments_json}
    return {"content": record.content or ""}


def hash_record(record: IrRecord) -> RecordHashes:
    """Compute content_sha256 + record_sha256 for a filled record."""
    record_type = _record_type_name(record.kind, record.role)
    semantic = _semantic_content(record)
    content_sha = sha256_hex(
        canonical_json({"type": record_type, "content": semantic})
    )
    record_sha = sha256_hex(canonical_json(_to_letta_record(record)))
    return RecordHashes(content_sha256=content_sha, record_sha256=record_sha)


def _create_record(
    *,
    event: DecodedEvent,
    record_index: int,
    group_id: str,
    ordinal: int,
    component_key: str,
    role: TrajectoryRole,
    content: str | None,
    tool_calls: tuple[ToolCall, ...],
    tool_call_id: str | None,
    tool_name: str | None,
    is_error: bool | None,
    base_byte_offset: int,
    content_hash: str,
) -> IrRecord:
    stable_id, identity_kind, provenance_offset = _resolve_stable_identity(
        event, group_id, base_byte_offset, content_hash
    )
    if tool_calls:
        kind = RecordKind.ASSISTANT_TOOL_CALLS
    elif role is TrajectoryRole.TOOL:
        kind = RecordKind.TOOL_RESULT
    else:
        kind = RecordKind.MESSAGE
    order = record_index - 1
    if order < _INT64_MIN or order > _INT64_MAX:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_RECORD_ORDER_OOR) from None
    provenance = Provenance(
        stable_source_record_id=stable_id,
        source_identity_kind=identity_kind,
        source_order_id=_source_order_id(
            event.timestamp_ms, event.source_sequence, stable_id
        ),
        component_key=component_key,
        component_index=event.component_index,
        component_type_ordinal=ordinal,
        native_record_id=event.native_record_id,
        producer_version=event.producer_version,
        source_sequence=event.source_sequence,
        source_offset=provenance_offset,
        source_anchor_kind=event.source_anchor_kind,
    )
    return IrRecord(
        id=record_id(group_id, stable_id, component_key),
        kind=kind,
        role=role,
        order=order,
        provenance=provenance,
        hashes=RecordHashes(content_sha256="", record_sha256=""),
        source_timestamp_ms=event.timestamp_ms,
        source_timestamp_precise=event.timestamp_precise,
        timestamp_ms=None,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        is_error=is_error,
    )


def _event_diagnostic(
    code: str,
    message: str,
    event: DecodedEvent,
    record_index: int,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        message=message,
        input_line=event.input_line,
        record_index=record_index,
    )


def _is_harness_noise(content: str) -> bool:
    head = content.lstrip()
    return any(head.startswith(prefix) for prefix in _NOISE_PREFIXES)


def _normalize_event(
    event: DecodedEvent,
    event_index: int,
    record_index: int,
    group_id: str,
    config: AppliedConfig,
    partial: bool,
    plan: _Plan,
    diagnostics: list[Diagnostic],
) -> IrRecord | None:
    ordinal = plan.ordinals[event_index]
    base = config.base_byte_offset

    if event.kind in ("message", "reasoning"):
        content = event.content if event.content is not None else ""
        if content.strip() == "":
            return None
        role = (
            TrajectoryRole.REASONING
            if event.kind == "reasoning"
            else event.role
        )
        if role is TrajectoryRole.USER and _is_harness_noise(content):
            diagnostics.append(
                _event_diagnostic(
                    DIAG_NOISE_RECORD_DROPPED,
                    "Dropped a harness-noise user record.",
                    event,
                    record_index,
                )
            )
            return None
        bucket = "reasoning" if event.kind == "reasoning" else "message"
        component_key = f"{bucket}:{ordinal}"
        if event.kind == "reasoning" or role is TrajectoryRole.REASONING:
            record_type = "reasoning"
        elif role is TrajectoryRole.USER:
            record_type = "user"
        else:
            record_type = "assistant"
        content_hash = _content_hash_for_semantic(
            record_type, {"content": content}
        )
        return _create_record(
            event=event,
            record_index=record_index,
            group_id=group_id,
            ordinal=ordinal,
            component_key=component_key,
            role=role,
            content=content,
            tool_calls=(),
            tool_call_id=None,
            tool_name=None,
            is_error=None,
            base_byte_offset=base,
            content_hash=content_hash,
        )

    if event.kind == "tool-call":
        call = plan.calls[event_index]
        if call.synthesized:
            diagnostics.append(
                _event_diagnostic(
                    DIAG_TOOL_CALL_ID_SYNTHESIZED,
                    f"Synthesized tool-call ID {_quote(call.source_id)}.",
                    event,
                    record_index,
                )
            )
        if call.renamed:
            diagnostics.append(
                _event_diagnostic(
                    DIAG_DUPLICATE_TOOL_CALL_ID,
                    (
                        f"Renamed duplicate tool-call ID {_quote(call.source_id)} "
                        f"to {_quote(call.final_id)}."
                    ),
                    event,
                    record_index,
                )
            )
        name = event.tool_name if event.tool_name else "unknown_tool"
        if not event.tool_name:
            diagnostics.append(
                _event_diagnostic(
                    DIAG_UNKNOWN_TOOL_NAME,
                    f"Substituted {_quote(name)} for a missing tool name.",
                    event,
                    record_index,
                )
            )
        args, reshaped, truncated = shrink_arguments(
            event.arguments_json,
            config.bounds.tool_arguments_max_characters,
        )
        if reshaped:
            diagnostics.append(
                _event_diagnostic(
                    DIAG_TOOL_ARGUMENTS_RESHAPED,
                    (
                        f"Reshaped arguments for tool call {_quote(call.final_id)} "
                        "into a JSON object."
                    ),
                    event,
                    record_index,
                )
            )
        if truncated:
            max_c = config.bounds.tool_arguments_max_characters
            diagnostics.append(
                _event_diagnostic(
                    DIAG_TOOL_ARGUMENTS_TRUNCATED,
                    (
                        f"Truncated arguments for tool call {_quote(call.final_id)} "
                        f"to at most {max_c} Unicode code points."
                    ),
                    event,
                    record_index,
                )
            )
        tool_call = ToolCall(id=call.final_id, name=name, arguments_json=args)
        content_hash = _content_hash_for_semantic(
            "assistant-tool-call",
            {"name": name, "args": args},
        )
        return _create_record(
            event=event,
            record_index=record_index,
            group_id=group_id,
            ordinal=ordinal,
            component_key=f"tool-call:{call.final_id}",
            role=TrajectoryRole.ASSISTANT,
            content=None,
            tool_calls=(tool_call,),
            tool_call_id=None,
            tool_name=None,
            is_error=None,
            base_byte_offset=base,
            content_hash=content_hash,
        )

    # tool-result
    source_id = event.tool_call_id if event.tool_call_id is not None else ""
    entries = plan.open_calls.get(source_id)
    open_entry: _PlannedCall | None = None
    if entries is not None:
        for entry in entries:
            if not entry.consumed:
                open_entry = entry
                break
    cross_chunk = (
        open_entry is None
        and partial
        and source_id != ""
        and (entries is None or len(entries) == 0)
    )
    if open_entry is None and not cross_chunk:
        duplicate = entries is not None and len(entries) > 0
        if duplicate:
            diagnostics.append(
                _event_diagnostic(
                    DIAG_DUPLICATE_TOOL_RESULT,
                    f"Dropped a duplicate result for tool call {_quote(source_id)}.",
                    event,
                    record_index,
                )
            )
        else:
            diagnostics.append(
                _event_diagnostic(
                    DIAG_ORPHAN_TOOL_RESULT,
                    (
                        "Dropped a tool result without a preceding call for "
                        f"{_quote(source_id)}."
                    ),
                    event,
                    record_index,
                )
            )
        return None
    if open_entry is not None:
        open_entry.consumed = True
        final_id = open_entry.final_id
    else:
        final_id = source_id
    if config.filters.tool_results == "omit":
        return None
    original = event.content if event.content is not None else ""
    content = truncate_result(
        original,
        config.bounds.tool_results_max_characters,
        config.bounds.tool_results_strategy,
    )
    if content != original:
        max_c = config.bounds.tool_results_max_characters
        strategy = config.bounds.tool_results_strategy
        diagnostics.append(
            _event_diagnostic(
                DIAG_TOOL_RESULT_TRUNCATED,
                (
                    f"Truncated the result for tool call {_quote(final_id)} to at "
                    f"most {max_c} Unicode code points using the "
                    f"{_quote(strategy)} strategy."
                ),
                event,
                record_index,
            )
        )
    content_hash = _content_hash_for_semantic("tool", {"content": content})
    is_error = event.is_error if event.is_error is not None else False
    return _create_record(
        event=event,
        record_index=record_index,
        group_id=group_id,
        ordinal=ordinal,
        component_key=f"tool-result:{final_id}",
        role=TrajectoryRole.TOOL,
        content=content,
        tool_calls=(),
        tool_call_id=final_id,
        tool_name=event.tool_name,
        is_error=is_error,
        base_byte_offset=base,
        content_hash=content_hash,
    )


def _create_meta(
    group_id: str,
    source_name: str,
    cwd: str | None,
    git_branch: str | None,
    model: str | None,
    producer_version: str | None,
) -> IrRecord:
    provenance = Provenance(
        stable_source_record_id="meta",
        source_identity_kind=SourceIdentityKind.SYNTHETIC,
        source_order_id="0|0000-00-00T00:00:00.000Z|00000000000000000000|meta",
        component_key="meta",
        component_index=0,
        component_type_ordinal=0,
    )
    record = IrRecord(
        id=record_id(group_id, "meta", "meta"),
        kind=RecordKind.META,
        role=TrajectoryRole.META,
        order=-1,
        provenance=provenance,
        hashes=RecordHashes(content_sha256="", record_sha256=""),
        source_name=source_name,
        cwd=cwd,
        git_branch=git_branch,
        model=model,
        producer_version=producer_version,
    )
    return replace(record, hashes=hash_record(record))


def _resolve_model(
    session_model: str | None, model_counts: dict[str, int]
) -> str | None:
    if session_model is not None and session_model != "":
        return session_model
    if not model_counts:
        return None
    # Most frequent; UTF-16 code-unit name ascending on ties (tip pin).
    from hypabolic_trajectory.canonical import utf16_code_units

    items = list(model_counts.items())
    items.sort(key=lambda pair: (-pair[1], tuple(utf16_code_units(pair[0]))))
    return items[0][0]


def map_model_invocation(
    invocation: DecodedModelInvocation,
    group_id: str,
    base_byte_offset: int,
) -> ModelInvocation:
    """Map decoded model invocation → IR (absolute offset + id formula)."""
    absolute_offset: int | None = None
    if invocation.source_offset is not None:
        absolute_offset = _checked_add_i64(
            invocation.source_offset,
            base_byte_offset,
            _MSG_MODEL_BYTE_ANCHOR_OOR,
        )

    native = _non_empty(invocation.native_record_id)
    if native is not None:
        identity = native
    elif absolute_offset is not None:
        identity = location_identity(
            group_id, SourceAnchorKind.BYTE.value, absolute_offset
        )
    elif invocation.response_id is not None:
        identity = invocation.response_id
    else:
        identity = "model-invocation"

    usage: ModelTokenUsage | None = None
    tokens = (
        invocation.input_tokens,
        invocation.output_tokens,
        invocation.cache_read_tokens,
        invocation.cache_write_tokens,
        invocation.total_tokens,
    )
    if any(t is not None for t in tokens):
        usage = ModelTokenUsage(
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            cache_read_tokens=invocation.cache_read_tokens,
            cache_write_tokens=invocation.cache_write_tokens,
            total_tokens=invocation.total_tokens,
        )

    return ModelInvocation(
        id=model_invocation_id(group_id, identity),
        native_record_id=invocation.native_record_id,
        source_sequence=invocation.source_sequence,
        source_offset=absolute_offset,
        provider=invocation.provider,
        api_family=invocation.api_family,
        requested_model=invocation.requested_model,
        response_model=invocation.response_model,
        response_id=invocation.response_id,
        stop_reason=invocation.stop_reason,
        producer_version=invocation.producer_version,
        usage=usage,
        started_at_ms=invocation.started_at_ms,
        started_at_precise=invocation.started_at_precise,
        first_response_at_ms=invocation.first_response_at_ms,
        first_response_at_precise=invocation.first_response_at_precise,
        completed_at_ms=invocation.completed_at_ms,
        completed_at_precise=invocation.completed_at_precise,
    )


def normalize_decoded(
    decoded: DecodedSession,
    *,
    config: AppliedConfig,
) -> TrajectoryIR:
    """Normalize a decoded session into immutable ``TrajectoryIR``.

    Public to unit tests and adapter-backed ``normalize_to_ir``. Never raises
    ``source_group_required`` (projection-only).
    """
    group_id, source_group_resolved = resolve_group_id(
        decoded.group_id, config.group_id
    )
    # Non-zero base always implies partial mode (identity.md / behaviour pin).
    partial = config.partial or config.base_byte_offset != 0
    diagnostics: list[Diagnostic] = list(decoded.diagnostics)
    plan = plan_events(decoded.events)
    body: list[IrRecord] = []
    anchors: dict[int, int] = {}
    model_counts: dict[str, int] = {}

    for event_index, event in enumerate(decoded.events):
        if event.model:
            model_counts[event.model] = model_counts.get(event.model, 0) + 1
        record_index = len(body) + 1
        record = _normalize_event(
            event,
            event_index,
            record_index,
            group_id,
            config,
            partial,
            plan,
            diagnostics,
        )
        if record is None:
            continue
        if event.timestamp_ms is not None:
            anchors[len(body)] = event.timestamp_ms
        body.append(record)

    if not partial:
        if not any(r.role is TrajectoryRole.USER for r in body):
            raise TrajectoryError(
                FATAL_MISSING_USER_RECORDS, _MSG_MISSING_USER
            ) from None
        if not any(r.role is TrajectoryRole.ASSISTANT for r in body):
            raise TrajectoryError(
                FATAL_MISSING_ASSISTANT_RECORDS, _MSG_MISSING_ASSISTANT
            ) from None

    timestamps = fill_timestamps(
        len(body), anchors, decoded.created_at_ms, diagnostics
    )
    stamped: list[IrRecord] = []
    for record, ts in zip(body, timestamps, strict=True):
        filled = replace(record, timestamp_ms=ts)
        stamped.append(replace(filled, hashes=hash_record(filled)))

    model = _resolve_model(decoded.model, model_counts)
    meta = _create_meta(
        group_id,
        decoded.source_name,
        decoded.cwd,
        decoded.git_branch,
        model,
        decoded.producer_version,
    )
    model_invocations = tuple(
        map_model_invocation(inv, group_id, config.base_byte_offset)
        for inv in decoded.model_invocations
    )
    return TrajectoryIR(
        source=decoded.source,
        source_name=decoded.source_name,
        group_id=group_id,
        source_group_resolved=source_group_resolved,
        records=(meta, *stamped),
        diagnostics=tuple(diagnostics),
        config=config,
        execution=TrajectoryExecution(
            model_invocations=model_invocations,
            workflow_invocations=(),
        ),
        producer_version=decoded.producer_version,
    )


def normalize_to_ir(request: NormalizeRequest) -> TrajectoryIR:
    """Normalize a native transcript into immutable ``TrajectoryIR``.

    Validates free-function entry, resolves the built-in source adapter, decodes,
    then runs full group/linking/bounds/identity/timestamp behaviour (PY-04b).

    Free functions always use built-in adapters only (engine isolation pin).
    """
    source, transcript = validate_normalize_entry(request)
    adapter = get_source_adapter(source.value)
    if adapter is None:
        # Known wire source but no built-in adapter registered yet.
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_NORMALIZE_NOT_IMPLEMENTED) from None

    decoded = adapter.decode(transcript, source_context=request.source_context)
    config = _apply_config(request)
    return normalize_decoded(decoded, config=config)
