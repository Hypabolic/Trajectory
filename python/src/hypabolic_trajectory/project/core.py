"""Core projections: letta / canonical / hypabolic + serialize_projection.

Authority:
  - docs/python-implementation-spec.md §3 (emit architecture, hypabolic pins,
    diagnostic casing matrix, serialize_projection, null policy)
  - tip Rust ``projection.rs`` / TS ``projections.ts`` field order + shapes
  - conformance goldens (expected.letta/canonical/hypabolic.json)

PY-07a exclusive owner of:
  ``project_letta``, ``project_canonical``, ``project_hypabolic``,
  ``normalize_to_*`` (via api convenience), and public ``serialize_projection``.
"""

from __future__ import annotations

from typing import Any

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory._json_types import JsonObject, JsonValue
from hypabolic_trajectory._schema import HYPABOLIC_TRAJECTORY_V1
from hypabolic_trajectory._version import (
    NORMALIZER_CONTRACT_VERSION,
    WIRE_PACKAGE_VERSION,
)
from hypabolic_trajectory.canonical import (
    INT64_MAX,
    INT64_MIN,
    canonical_json,
    compact_json,
    escape_json_string,
)
from hypabolic_trajectory.diagnostics import Diagnostic
from hypabolic_trajectory.errors import (
    FATAL_INVALID_INPUT,
    FATAL_SOURCE_GROUP_REQUIRED,
    TrajectoryError,
)
from hypabolic_trajectory.identity import trajectory_id as compute_trajectory_id
from hypabolic_trajectory.ir.models import (
    IrRecord,
    RecordKind,
    TrajectoryIR,
    TrajectoryRole,
)
from hypabolic_trajectory.timestamps import format_ms

# Exact message from conformance/cases/codex/missing-group/expected.error.json
MSG_SOURCE_GROUP_REQUIRED: str = (
    "Canonical Codex normalization requires a source group: include "
    "session_meta or pass sourceContext.groupId."
)

# Wire normalizer name for hypabolic envelope (peer pin).
_HYPABOLIC_NORMALIZER_NAME: str = "Hypabolic.Trajectory"


def serialize_projection(value: JsonValue, *, write_indented: bool = False) -> str:
    """Serialize a projection tree with the shared Trajectory escape.

    Compact mode is byte-equivalent to ``compact_json`` / tip ``relaxed_json``
    (insertion order retained; keys never sorted). Indented mode uses 2-space
    indent and ``\\n`` newlines with no trailing whitespace / no final newline.

    Raises:
        TypeError: non-JSON-serializable values, non-finite floats, integers
            outside signed int64, cyclic trees, non-str object keys.
    """
    if not write_indented:
        return compact_json(value)
    return _write_pretty(value, depth=0, active=set())


def project_letta(trajectory: TrajectoryIR) -> JsonObject:
    """Project IR to ``letta-trajectory-v1`` (camelCase diagnostics)."""
    return {
        "records": [to_letta_record(record) for record in trajectory.records],
        "diagnostics": _diagnostics_value(trajectory.diagnostics, snake_case=False),
    }


def project_canonical(trajectory: TrajectoryIR) -> JsonObject:
    """Project IR to ``letta-canonical-v1``.

    Raises ``source_group_required`` for codex when the source group is
    unresolved — ``normalize_to_ir`` never raises this; only canonical project.
    """
    if (
        trajectory.source == TrajectorySource.CODEX
        and not trajectory.source_group_resolved
    ):
        raise TrajectoryError(
            FATAL_SOURCE_GROUP_REQUIRED, MSG_SOURCE_GROUP_REQUIRED
        ) from None

    base = trajectory.config.base_byte_offset
    records = [
        _canonical_record(trajectory, record)
        for record in trajectory.records
        if base == 0 or record.kind != RecordKind.META
    ]
    bounds = trajectory.config.bounds
    filters = trajectory.config.filters
    return {
        "records": records,
        "diagnostics": _diagnostics_value(trajectory.diagnostics, snake_case=False),
        "normalizer_version": NORMALIZER_CONTRACT_VERSION,
        "canonical_schema_version": 1,
        "config": {
            "bounds": {
                "toolArguments": {
                    "maxCharacters": bounds.tool_arguments_max_characters,
                },
                "toolResults": {
                    "maxCharacters": bounds.tool_results_max_characters,
                    "strategy": bounds.tool_results_strategy,
                },
            },
            "filters": {
                "toolResults": filters.tool_results,
            },
        },
    }


def project_hypabolic(trajectory: TrajectoryIR) -> JsonObject:
    """Project IR to ``hypabolic-trajectory-v1`` (snake_case diagnostics)."""
    source_wire = str(trajectory.source)
    tid = compute_trajectory_id(source_wire, trajectory.group_id)
    source_obj: JsonObject = {
        "type": source_wire,
        "name": trajectory.source_name,
        "group_id": trajectory.group_id,
    }
    if trajectory.producer_version is not None:
        source_obj["producer_version"] = trajectory.producer_version

    cfg = trajectory.config
    bounds = cfg.bounds
    filters = cfg.filters
    partial = bool(cfg.partial or cfg.base_byte_offset != 0)

    return {
        "schema_id": HYPABOLIC_TRAJECTORY_V1,
        "schema_version": 1,
        "trajectory_id": tid,
        "source": source_obj,
        "segment": {
            "partial": partial,
            "base_byte_offset": cfg.base_byte_offset,
        },
        "normalizer": {
            "name": _HYPABOLIC_NORMALIZER_NAME,
            "version": WIRE_PACKAGE_VERSION,
        },
        "config": {
            "bounds": {
                "tool_arguments": {
                    "max_characters": bounds.tool_arguments_max_characters,
                },
                "tool_results": {
                    "max_characters": bounds.tool_results_max_characters,
                    "strategy": bounds.tool_results_strategy,
                },
            },
            "filters": {
                "tool_results": filters.tool_results,
            },
        },
        "records": [_hypabolic_record(record) for record in trajectory.records],
        "diagnostics": _diagnostics_value(trajectory.diagnostics, snake_case=True),
    }


# ---------------------------------------------------------------------------
# Letta record
# ---------------------------------------------------------------------------


def to_letta_record(record: IrRecord) -> JsonObject:
    """Map one IR record to the letta message-shaped object (fixed field order)."""
    if record.kind == RecordKind.META:
        value: JsonObject = {
            "role": "meta",
            "source": record.source_name if record.source_name is not None else "unknown",
        }
        if record.cwd is not None:
            value["cwd"] = record.cwd
        if record.git_branch is not None:
            value["git_branch"] = record.git_branch
        if record.model is not None:
            value["model"] = record.model
        return value

    if record.kind == RecordKind.ASSISTANT_TOOL_CALLS:
        call = record.tool_calls[0]
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
            "timestamp": _require_filled_ms(record.timestamp_ms),
        }

    if record.kind == RecordKind.TOOL_RESULT:
        return {
            "role": "tool",
            "tool_call_id": record.tool_call_id or "",
            "content": record.content if record.content is not None else "",
            "timestamp": _require_filled_ms(record.timestamp_ms),
        }

    # MESSAGE
    return {
        "role": str(record.role),
        "content": record.content if record.content is not None else "",
        "timestamp": _require_filled_ms(record.timestamp_ms),
    }


# ---------------------------------------------------------------------------
# Canonical record
# ---------------------------------------------------------------------------


def _canonical_record(trajectory: TrajectoryIR, record: IrRecord) -> JsonObject:
    call = record.tool_calls[0] if record.tool_calls else None
    return {
        "source_type": str(trajectory.source),
        "source_group_id": trajectory.group_id,
        "stable_source_record_id": record.provenance.stable_source_record_id,
        "source_identity_kind": str(record.provenance.source_identity_kind),
        "source_order_id": record.provenance.source_order_id,
        "component_index": record.provenance.component_index,
        "record_type": _record_type(record),
        "record_id": record.id,
        "record_hash": record.hashes.record_sha256,
        "content_hash": record.hashes.content_sha256,
        "source_timestamp": _optional_ms(record.source_timestamp_ms),
        "record_timestamp": _optional_ms(record.timestamp_ms),
        "content": (
            (record.content if record.content is not None else "")
            if record.kind == RecordKind.MESSAGE
            else None
        ),
        "tool_call_id": (
            call.id
            if call is not None
            else (record.tool_call_id if record.tool_call_id is not None else None)
        ),
        "tool_name": call.name if call is not None else None,
        "tool_arguments_json": call.arguments_json if call is not None else None,
        "tool_result_json": (
            (record.content if record.content is not None else "")
            if record.kind == RecordKind.TOOL_RESULT
            else None
        ),
        "record_json": canonical_json(to_letta_record(record)),
    }


def _record_type(record: IrRecord) -> str:
    if record.kind == RecordKind.META:
        return "meta"
    if record.kind == RecordKind.ASSISTANT_TOOL_CALLS:
        return "assistant-tool-call"
    if record.kind == RecordKind.TOOL_RESULT:
        return "tool"
    # MESSAGE
    if record.role == TrajectoryRole.USER:
        return "user"
    if record.role == TrajectoryRole.REASONING:
        return "reasoning"
    return "assistant"


# ---------------------------------------------------------------------------
# Hypabolic record
# ---------------------------------------------------------------------------


def _hypabolic_record(record: IrRecord) -> JsonObject:
    output: JsonObject = {
        "id": record.id,
        "kind": str(record.kind),
        "role": str(record.role),
        "order": record.order,
        "source_timestamp": _optional_ms(record.source_timestamp_ms),
        "timestamp": _optional_ms(record.timestamp_ms),
    }

    if record.kind == RecordKind.META:
        output["source_name"] = (
            record.source_name if record.source_name is not None else "unknown"
        )
        if record.cwd is not None:
            output["cwd"] = record.cwd
        if record.git_branch is not None:
            output["git_branch"] = record.git_branch
        if record.model is not None:
            output["model"] = record.model
        if record.producer_version is not None:
            output["producer_version"] = record.producer_version
    elif record.kind == RecordKind.ASSISTANT_TOOL_CALLS:
        output["content"] = None
        output["tool_calls"] = [
            {
                "id": call.id,
                "name": call.name,
                "arguments_json": call.arguments_json,
            }
            for call in record.tool_calls
        ]
    else:
        # MESSAGE | TOOL_RESULT — omit content key when absent
        if record.content is not None:
            output["content"] = record.content

    if record.tool_call_id is not None:
        output["tool_call_id"] = record.tool_call_id
    if record.tool_name is not None:
        output["tool_name"] = record.tool_name
    if record.is_error is not None:
        output["is_error"] = record.is_error

    prov = record.provenance
    provenance: JsonObject = {
        "stable_source_record_id": prov.stable_source_record_id,
        "source_identity_kind": str(prov.source_identity_kind),
        "source_order_id": prov.source_order_id,
        "component_key": prov.component_key,
        "component_index": prov.component_index,
        "component_type_ordinal": prov.component_type_ordinal,
    }
    if prov.producer_version is not None:
        provenance["producer_version"] = prov.producer_version
    if prov.native_record_id is not None:
        provenance["native_record_id"] = prov.native_record_id
    if prov.source_sequence is not None:
        provenance["source_sequence"] = prov.source_sequence
    if prov.source_offset is not None:
        provenance["source_offset"] = prov.source_offset
    if prov.source_anchor_kind is not None:
        provenance["source_anchor_kind"] = str(prov.source_anchor_kind)
    output["provenance"] = provenance
    output["hashes"] = {
        "content_sha256": record.hashes.content_sha256,
        "record_sha256": record.hashes.record_sha256,
    }
    return output


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _diagnostics_value(
    diagnostics: tuple[Diagnostic, ...] | list[Diagnostic],
    *,
    snake_case: bool,
) -> list[JsonObject]:
    """Copy diagnostics with schema-specific optional key casing.

    Always ``code`` + ``message``; optional location keys only when present.
    """
    line_key = "input_line" if snake_case else "inputLine"
    index_key = "record_index" if snake_case else "recordIndex"
    out: list[JsonObject] = []
    for diagnostic in diagnostics:
        item: JsonObject = {
            "code": diagnostic.code,
            "message": diagnostic.message,
        }
        if diagnostic.input_line is not None:
            item[line_key] = diagnostic.input_line
        if diagnostic.record_index is not None:
            item[index_key] = diagnostic.record_index
        if diagnostic.count is not None:
            item["count"] = diagnostic.count
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _optional_ms(milliseconds: int | None) -> str | None:
    if milliseconds is None:
        return None
    return format_ms(milliseconds)


def _require_filled_ms(milliseconds: int | None) -> str:
    if milliseconds is None:
        raise TrajectoryError(
            FATAL_INVALID_INPUT,
            "Body record timestamp is unavailable.",
        ) from None
    return format_ms(milliseconds)


# ---------------------------------------------------------------------------
# Pretty serialize (write_indented=True)
# ---------------------------------------------------------------------------


def _write_pretty(value: Any, *, depth: int, active: set[int]) -> str:
    pad = "  " * depth
    inner = "  " * (depth + 1)

    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        if value < INT64_MIN or value > INT64_MAX:
            raise TypeError(
                "serialize_projection integer outside signed int64 range "
                f"[{INT64_MIN}, {INT64_MAX}]"
            )
        return str(value)
    if type(value) is float:
        import math

        if not math.isfinite(value):
            raise TypeError(
                "serialize_projection does not accept non-finite floats "
                "(nan/inf/-inf)"
            )
        import json as _json

        return _json.dumps(value, allow_nan=False)
    if type(value) is str:
        return escape_json_string(value)
    if type(value) is dict:
        obj_id = id(value)
        if obj_id in active:
            raise TypeError("serialize_projection does not accept cyclic JSON trees")
        active.add(obj_id)
        try:
            if not value:
                return "{}"
            items: list[tuple[str, Any]] = []
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError(
                        f"JSON object keys must be str, got {type(key).__name__}"
                    )
                items.append((key, item))
            lines = [
                f"{inner}{escape_json_string(k)}: "
                f"{_write_pretty(v, depth=depth + 1, active=active)}"
                for k, v in items
            ]
            return "{\n" + ",\n".join(lines) + "\n" + pad + "}"
        finally:
            active.discard(obj_id)
    if type(value) is list:
        list_id = id(value)
        if list_id in active:
            raise TypeError("serialize_projection does not accept cyclic JSON trees")
        active.add(list_id)
        try:
            if not value:
                return "[]"
            lines = [
                f"{inner}{_write_pretty(item, depth=depth + 1, active=active)}"
                for item in value
            ]
            return "[\n" + ",\n".join(lines) + "\n" + pad + "]"
        finally:
            active.discard(list_id)
    raise TypeError(
        f"value is not JSON-serializable for Trajectory emit: {type(value).__name__}"
    )
