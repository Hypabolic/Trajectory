"""Pure ``otel-genai-spans-v1`` projection (core; no OpenTelemetry SDK).

Authority:
  - docs/python-implementation-spec.md §4 OTEL GenAI projection pins
  - tip Rust ``opentelemetry_value`` / ``precise_record`` / ``precise_invocation``
  - conformance goldens (``expected.otel.json``)

Side-effect free: never contacts collectors or imports ``opentelemetry-*``.
"""

from __future__ import annotations

from typing import Any

from hypabolic_trajectory._json_types import JsonObject
from hypabolic_trajectory._version import WIRE_PACKAGE_VERSION
from hypabolic_trajectory.diagnostics import (
    DIAG_MODEL_SPAN_OMITTED,
    MSG_MODEL_SPAN_OMITTED,
)
from hypabolic_trajectory.identity import sha256_hex
from hypabolic_trajectory.ir.models import (
    IrRecord,
    ModelInvocation,
    RecordKind,
    TrajectoryIR,
    TrajectoryRole,
)
from hypabolic_trajectory.timestamps import otel_span_time

_SCHEMA_URL: str = "https://opentelemetry.io/schemas/gen-ai/1.42.0"
_INSTRUMENTATION_SCOPE: str = "Hypabolic.Trajectory.OpenTelemetry"
_CONTENT_POLICY: JsonObject = {
    "messages_included": False,
    "tool_arguments_included": False,
    "tool_results_included": False,
    "maximum_characters": 1024,
}


def project_otel_genai(trajectory: TrajectoryIR) -> JsonObject:
    """Project immutable IR into a deterministic ``otel-genai-spans-v1`` tree.

    Does not import or require the OpenTelemetry SDK. Does not include IR
    success-path diagnostics — only projection-local ``model_span_omitted``.
    """
    trace_id = _non_zero(sha256_hex(f"{trajectory.source_name}|{trajectory.group_id}")[:32])
    body = [record for record in trajectory.records if record.kind != RecordKind.META]
    spans: list[JsonObject] = []
    diagnostics: list[JsonObject] = []
    # (start_index, end_index, start_ms, end_ms, span_id)
    turns: list[tuple[int, int, int, int, str]] = []

    users = [
        (index, record)
        for index, record in enumerate(body)
        if record.role == TrajectoryRole.USER
    ]
    for position, (start_index, first) in enumerate(users):
        end_index = users[position + 1][0] if position + 1 < len(users) else len(body)
        segment = body[start_index:end_index]
        last = next(
            (record for record in reversed(segment) if record.source_timestamp_ms is not None),
            None,
        )
        if last is None:
            continue
        start_ms = first.source_timestamp_ms
        end_ms = last.source_timestamp_ms
        if start_ms is None or end_ms is None:
            continue
        span_id = _span_id_for(f"agent|{first.id}")
        if end_ms < start_ms:
            start_time = _precise_record(first)
            end_time = start_time
        else:
            start_time = _precise_record(first)
            end_time = _precise_record(last)
        spans.append(
            _span_value(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=None,
                name="invoke_agent",
                kind="INTERNAL",
                start_time=start_time,
                end_time=end_time,
                status="UNSET",
                attributes=_attributes(
                    [
                        ("gen_ai.operation.name", "invoke_agent"),
                        ("gen_ai.conversation.id", trajectory.group_id),
                        ("hypabolic.trajectory.id", trace_id),
                        ("hypabolic.trajectory.source", trajectory.source_name),
                        ("hypabolic.trajectory.record.id", first.id),
                    ]
                ),
            )
        )
        turns.append((start_index, end_index, start_ms, end_ms, span_id))

    for invocation in trajectory.execution.model_invocations:
        _emit_model_span(
            invocation=invocation,
            trace_id=trace_id,
            turns=turns,
            spans=spans,
            diagnostics=diagnostics,
        )

    results: dict[str, IrRecord] = {}
    for record in body:
        if record.kind == RecordKind.TOOL_RESULT and record.tool_call_id is not None:
            results[record.tool_call_id] = record

    for record_index, record in enumerate(body):
        if record.kind != RecordKind.ASSISTANT_TOOL_CALLS:
            continue
        for call in record.tool_calls:
            result = results.get(call.id)
            if result is None:
                continue
            start_ms = record.source_timestamp_ms
            end_ms = result.source_timestamp_ms
            if start_ms is None or end_ms is None:
                continue
            parent = next(
                (
                    turn_span_id
                    for first_i, last_i, _, _, turn_span_id in reversed(turns)
                    if first_i <= record_index < last_i
                ),
                None,
            )
            if end_ms < start_ms:
                start_time = _precise_record(record)
                end_time = start_time
            else:
                start_time = _precise_record(record)
                end_time = _precise_record(result)
            status = "ERROR" if result.is_error is True else "UNSET"
            spans.append(
                _span_value(
                    trace_id=trace_id,
                    span_id=_span_id_for(f"tool|{call.id}|{record.id}"),
                    parent_span_id=parent,
                    name=f"execute_tool {call.name}",
                    kind="INTERNAL",
                    start_time=start_time,
                    end_time=end_time,
                    status=status,
                    attributes=_attributes(
                        [
                            ("gen_ai.operation.name", "execute_tool"),
                            ("gen_ai.tool.name", call.name),
                            ("gen_ai.tool.call.id", call.id),
                            ("hypabolic.trajectory.call_record.id", record.id),
                            ("hypabolic.trajectory.result_record.id", result.id),
                        ]
                    ),
                )
            )

    spans.sort(
        key=lambda span: (
            str(span["start_time"]),
            str(span["name"]),
            str(span["span_id"]),
        )
    )

    # Fixed root field order (spec §4).
    return {
        "schema_url": _SCHEMA_URL,
        "trace_id": trace_id,
        "instrumentation_scope": _INSTRUMENTATION_SCOPE,
        "instrumentation_version": WIRE_PACKAGE_VERSION,
        "resource_attributes": [],
        "spans": spans,
        "diagnostics": diagnostics,
        "content_policy": dict(_CONTENT_POLICY),
    }


def _emit_model_span(
    *,
    invocation: ModelInvocation,
    trace_id: str,
    turns: list[tuple[int, int, int, int, str]],
    spans: list[JsonObject],
    diagnostics: list[JsonObject],
) -> None:
    start_ms = invocation.started_at_ms
    end_ms = invocation.completed_at_ms
    if start_ms is None or end_ms is None:
        diagnostics.append(_model_span_omitted(invocation.id))
        return
    if (
        invocation.provider is None
        and invocation.requested_model is None
        and invocation.response_model is None
    ):
        diagnostics.append(_model_span_omitted(invocation.id))
        return

    parent = next(
        (
            span_id
            for _, _, turn_start, turn_end, span_id in reversed(turns)
            if turn_start <= start_ms <= turn_end
        ),
        None,
    )
    model = invocation.requested_model or invocation.response_model
    name = "chat" if model is None else f"chat {model}"

    attrs: list[tuple[str, Any]] = [
        ("gen_ai.operation.name", "chat"),
        ("hypabolic.trajectory.invocation.id", invocation.id),
    ]
    _push_string(attrs, "gen_ai.provider.name", invocation.provider)
    _push_string(attrs, "gen_ai.request.model", invocation.requested_model)
    _push_string(attrs, "gen_ai.response.model", invocation.response_model)
    _push_string(attrs, "gen_ai.response.id", invocation.response_id)
    _push_string(attrs, "hypabolic.trajectory.api_family", invocation.api_family)
    if invocation.stop_reason is not None:
        attrs.append(("gen_ai.response.finish_reasons", [invocation.stop_reason]))
    if invocation.usage is not None:
        usage = invocation.usage
        _push_int(attrs, "gen_ai.usage.input_tokens", usage.input_tokens)
        _push_int(attrs, "gen_ai.usage.output_tokens", usage.output_tokens)
        _push_int(attrs, "gen_ai.usage.cache_read.input_tokens", usage.cache_read_tokens)
        _push_int(attrs, "gen_ai.usage.cache_creation.input_tokens", usage.cache_write_tokens)

    if end_ms < start_ms:
        start_time = _precise_invocation(start_ms, invocation.started_at_precise)
        end_time = start_time
    else:
        start_time = _precise_invocation(start_ms, invocation.started_at_precise)
        end_time = _precise_invocation(end_ms, invocation.completed_at_precise)

    spans.append(
        _span_value(
            trace_id=trace_id,
            span_id=_span_id_for(f"model|{invocation.id}"),
            parent_span_id=parent,
            name=name,
            kind="CLIENT",
            start_time=start_time,
            end_time=end_time,
            status="UNSET",
            attributes=_attributes(attrs),
        )
    )


def _precise_record(record: IrRecord) -> str:
    return otel_span_time(
        precise=record.source_timestamp_precise,
        ms=record.source_timestamp_ms,
    )


def _precise_invocation(milliseconds: int, precise: str | None) -> str:
    return otel_span_time(precise=precise, ms=milliseconds)


def _span_id_for(seed: str) -> str:
    return _non_zero(sha256_hex(seed)[:16])


def _non_zero(value: str) -> str:
    if value and all(ch == "0" for ch in value):
        return value[:-1] + "1"
    return value


def _model_span_omitted(invocation_id: str) -> JsonObject:
    return {
        "code": DIAG_MODEL_SPAN_OMITTED,
        "message": MSG_MODEL_SPAN_OMITTED,
        "record_id": invocation_id,
    }


def _push_string(attrs: list[tuple[str, Any]], key: str, value: str | None) -> None:
    if value is not None:
        attrs.append((key, value))


def _push_int(attrs: list[tuple[str, Any]], key: str, value: int | None) -> None:
    if value is not None:
        attrs.append((key, value))


def _attributes(items: list[tuple[str, Any]]) -> list[JsonObject]:
    """Sort by key ascending; emit string / integer / string_values shapes."""
    out: list[JsonObject] = []
    for key, value in sorted(items, key=lambda pair: pair[0]):
        if isinstance(value, list):
            out.append({"key": key, "string_values": list(value)})
        elif isinstance(value, int) and not isinstance(value, bool):
            out.append({"key": key, "integer_value": value})
        else:
            out.append({"key": key, "string_value": str(value)})
    return out


def _span_value(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
    name: str,
    kind: str,
    start_time: str,
    end_time: str,
    status: str,
    attributes: list[JsonObject],
) -> JsonObject:
    # Fixed field order (spec §4).
    span: JsonObject = {
        "trace_id": trace_id,
        "span_id": span_id,
    }
    if parent_span_id is not None:
        span["parent_span_id"] = parent_span_id
    span["name"] = name
    span["kind"] = kind
    span["start_time"] = start_time
    span["end_time"] = end_time
    span["status"] = status
    span["attributes"] = attributes
    span["links"] = []
    span["events"] = []
    return span


__all__ = ["project_otel_genai"]
