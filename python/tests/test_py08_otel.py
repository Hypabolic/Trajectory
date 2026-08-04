"""PY-08 unit vectors: pure project_otel_genai + otel SpanSetSink/emit_to.

Covers agent eligibility (incl. single-message), tool spans, model attribute
inventory, model_span_omitted, span time pad formula, span sort, non_zero ids,
unicode-boundaries golden parity, emit_to without SDK, and import matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypabolic_trajectory import (
    AppliedBounds,
    AppliedConfig,
    AppliedFilters,
    Bounds,
    Filters,
    IrRecord,
    ModelInvocation,
    ModelTokenUsage,
    NormalizeOptions,
    NormalizeRequest,
    Provenance,
    RecordHashes,
    RecordKind,
    SourceAnchorKind,
    SourceIdentityKind,
    ToolArgumentBounds,
    ToolCall,
    ToolResultBounds,
    TrajectoryExecution,
    TrajectoryIR,
    TrajectoryRole,
    TrajectorySource,
    WIRE_PACKAGE_VERSION,
    normalize_to_ir,
    project_otel_genai,
    serialize_projection,
)
from hypabolic_trajectory.identity import sha256_hex
from hypabolic_trajectory.project.otel_genai import _non_zero, _span_id_for
from hypabolic_trajectory.timestamps import format_ms_otel_pad

REPO_ROOT = Path(__file__).resolve().parents[2]
UNICODE_CASE = REPO_ROOT / "conformance" / "cases" / "pi" / "unicode-boundaries"
UNICODE_OTEL = UNICODE_CASE / "expected.otel.json"
UNICODE_INPUT = UNICODE_CASE / "input.jsonl"


def _prov(
    *,
    stable: str = "sid",
    kind: SourceIdentityKind = SourceIdentityKind.NATIVE,
    order: str = "1|x",
    component_key: str = "message:0",
    component_index: int = 0,
    component_type_ordinal: int = 0,
    native: str | None = "sid",
    sequence: int | None = 1,
    offset: int | None = 0,
    anchor: SourceAnchorKind | None = SourceAnchorKind.BYTE,
) -> Provenance:
    return Provenance(
        stable_source_record_id=stable,
        source_identity_kind=kind,
        source_order_id=order,
        component_key=component_key,
        component_index=component_index,
        component_type_ordinal=component_type_ordinal,
        native_record_id=native,
        source_sequence=sequence,
        source_offset=offset,
        source_anchor_kind=anchor,
    )


def _hashes(content: str = "c" * 64, record: str = "r" * 64) -> RecordHashes:
    return RecordHashes(content_sha256=content, record_sha256=record)


def _cfg() -> AppliedConfig:
    return AppliedConfig(
        bounds=AppliedBounds(
            tool_arguments_max_characters=120,
            tool_results_max_characters=10,
            tool_results_strategy="head-tail",
        ),
        filters=AppliedFilters(tool_results="include"),
        group_id=None,
        base_byte_offset=0,
        partial=False,
    )


def _ir(
    records: list[IrRecord],
    *,
    group_id: str = "g1",
    source_name: str = "pi",
    model_invocations: tuple[ModelInvocation, ...] = (),
) -> TrajectoryIR:
    return TrajectoryIR(
        source=TrajectorySource.PI,
        source_name=source_name,
        group_id=group_id,
        source_group_resolved=True,
        records=tuple(records),
        diagnostics=(),
        config=_cfg(),
        execution=TrajectoryExecution(model_invocations=model_invocations),
        producer_version="3",
    )


def _user(
    *,
    rid: str = "user-1",
    ts: int = 1_000,
    precise: str | None = None,
) -> IrRecord:
    return IrRecord(
        id=rid,
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.USER,
        order=0,
        provenance=_prov(stable=rid),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        source_timestamp_precise=precise,
        timestamp_ms=ts,
        content="hi",
    )


def _assistant(
    *,
    rid: str = "asst-1",
    ts: int = 1_100,
    precise: str | None = None,
    order: int = 1,
) -> IrRecord:
    return IrRecord(
        id=rid,
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.ASSISTANT,
        order=order,
        provenance=_prov(stable=rid),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        source_timestamp_precise=precise,
        timestamp_ms=ts,
        content="ok",
    )


def _tool_calls(
    *,
    rid: str = "calls-1",
    ts: int = 1_200,
    call_id: str = "c1",
    name: str = "probe",
    order: int = 2,
) -> IrRecord:
    return IrRecord(
        id=rid,
        kind=RecordKind.ASSISTANT_TOOL_CALLS,
        role=TrajectoryRole.ASSISTANT,
        order=order,
        provenance=_prov(stable=rid),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        timestamp_ms=ts,
        tool_calls=(ToolCall(id=call_id, name=name, arguments_json="{}"),),
    )


def _tool_result(
    *,
    rid: str = "res-1",
    ts: int = 1_300,
    call_id: str = "c1",
    is_error: bool | None = False,
    order: int = 3,
) -> IrRecord:
    return IrRecord(
        id=rid,
        kind=RecordKind.TOOL_RESULT,
        role=TrajectoryRole.TOOL,
        order=order,
        provenance=_prov(stable=rid),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        timestamp_ms=ts,
        content="out",
        tool_call_id=call_id,
        tool_name="probe",
        is_error=is_error,
    )


def _meta() -> IrRecord:
    return IrRecord(
        id="meta-1",
        kind=RecordKind.META,
        role=TrajectoryRole.META,
        order=-1,
        provenance=_prov(
            stable="meta",
            kind=SourceIdentityKind.SYNTHETIC,
            order="0|meta",
            component_key="meta",
            native=None,
            sequence=None,
            offset=None,
            anchor=None,
        ),
        hashes=_hashes(),
        source_name="pi",
    )


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def test_non_zero_replaces_all_zero_hex() -> None:
    assert _non_zero("0000") == "0001"
    assert _non_zero("00a0") == "00a0"
    assert _non_zero("a") == "a"


def test_trace_and_span_id_seeds() -> None:
    tree = project_otel_genai(_ir([_user(rid="u")]))
    expected_trace = _non_zero(sha256_hex("pi|g1")[:32])
    assert tree["trace_id"] == expected_trace
    agent = tree["spans"][0]
    assert agent["span_id"] == _span_id_for("agent|u")
    assert agent["trace_id"] == expected_trace


# ---------------------------------------------------------------------------
# Agent turns
# ---------------------------------------------------------------------------


def test_agent_single_message_turn_start_equals_end() -> None:
    """Single-user turn with no later timed body still emits invoke_agent."""
    tree = project_otel_genai(_ir([_user(rid="solo", ts=5_000)]))
    assert len(tree["spans"]) == 1
    span = tree["spans"][0]
    assert span["name"] == "invoke_agent"
    assert span["kind"] == "INTERNAL"
    assert span["status"] == "UNSET"
    assert span["start_time"] == span["end_time"] == format_ms_otel_pad(5_000)
    keys = [a["key"] for a in span["attributes"]]
    assert keys == sorted(keys)
    assert {
        a["key"]: a["string_value"] for a in span["attributes"]
    } == {
        "gen_ai.conversation.id": "g1",
        "gen_ai.operation.name": "invoke_agent",
        "hypabolic.trajectory.id": tree["trace_id"],
        "hypabolic.trajectory.record.id": "solo",
        "hypabolic.trajectory.source": "pi",
    }


def test_agent_skips_user_without_source_timestamp_ms() -> None:
    bare = IrRecord(
        id="no-ts",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.USER,
        order=0,
        provenance=_prov(stable="no-ts"),
        hashes=_hashes(),
        content="x",
    )
    tree = project_otel_genai(_ir([bare, _assistant(ts=2_000)]))
    assert tree["spans"] == []


def test_agent_clamps_end_before_start() -> None:
    tree = project_otel_genai(
        _ir(
            [
                _user(rid="u", ts=2_000),
                _assistant(rid="a", ts=1_000, order=1),
            ]
        )
    )
    span = tree["spans"][0]
    assert span["start_time"] == span["end_time"] == format_ms_otel_pad(2_000)


def test_agent_prefers_precise_unchanged() -> None:
    precise = "2025-01-01T00:00:00.1234567+00:00"
    tree = project_otel_genai(
        _ir([_user(rid="u", ts=1, precise=precise)])
    )
    assert tree["spans"][0]["start_time"] == precise
    assert tree["spans"][0]["end_time"] == precise


def test_meta_excluded_from_body() -> None:
    tree = project_otel_genai(_ir([_meta(), _user(rid="u", ts=10)]))
    assert len(tree["spans"]) == 1
    assert tree["spans"][0]["attributes"][-1]["string_value"] == "u" or any(
        a.get("string_value") == "u" and a["key"] == "hypabolic.trajectory.record.id"
        for a in tree["spans"][0]["attributes"]
    )


# ---------------------------------------------------------------------------
# Model spans + diagnostics
# ---------------------------------------------------------------------------


def test_model_span_full_attribute_inventory() -> None:
    inv = ModelInvocation(
        id="inv-1",
        provider="openai",
        api_family="chat.completions",
        requested_model="gpt-4o",
        response_model="gpt-4o-2024",
        response_id="resp-9",
        stop_reason="stop",
        usage=ModelTokenUsage(
            input_tokens=11,
            output_tokens=22,
            cache_read_tokens=3,
            cache_write_tokens=4,
        ),
        started_at_ms=1_050,
        completed_at_ms=1_090,
    )
    tree = project_otel_genai(
        _ir(
            [_user(rid="u", ts=1_000), _assistant(ts=1_100)],
            model_invocations=(inv,),
        )
    )
    model_spans = [s for s in tree["spans"] if s["name"].startswith("chat")]
    assert len(model_spans) == 1
    span = model_spans[0]
    assert span["name"] == "chat gpt-4o"
    assert span["kind"] == "CLIENT"
    assert span["status"] == "UNSET"
    assert span["span_id"] == _span_id_for("model|inv-1")
    assert span["parent_span_id"] == _span_id_for("agent|u")
    by_key = {a["key"]: a for a in span["attributes"]}
    assert list(by_key) == sorted(by_key)
    assert by_key["gen_ai.operation.name"]["string_value"] == "chat"
    assert by_key["hypabolic.trajectory.invocation.id"]["string_value"] == "inv-1"
    assert by_key["gen_ai.provider.name"]["string_value"] == "openai"
    assert by_key["gen_ai.request.model"]["string_value"] == "gpt-4o"
    assert by_key["gen_ai.response.model"]["string_value"] == "gpt-4o-2024"
    assert by_key["gen_ai.response.id"]["string_value"] == "resp-9"
    assert by_key["hypabolic.trajectory.api_family"]["string_value"] == "chat.completions"
    assert by_key["gen_ai.response.finish_reasons"]["string_values"] == ["stop"]
    assert by_key["gen_ai.usage.input_tokens"]["integer_value"] == 11
    assert by_key["gen_ai.usage.output_tokens"]["integer_value"] == 22
    assert by_key["gen_ai.usage.cache_read.input_tokens"]["integer_value"] == 3
    assert by_key["gen_ai.usage.cache_creation.input_tokens"]["integer_value"] == 4
    assert tree["diagnostics"] == []


def test_model_span_omitted_missing_timing() -> None:
    inv = ModelInvocation(
        id="inv-miss",
        provider="openai",
        requested_model="m",
        started_at_ms=None,
        completed_at_ms=1,
    )
    tree = project_otel_genai(_ir([_user()], model_invocations=(inv,)))
    assert tree["diagnostics"] == [
        {
            "code": "model_span_omitted",
            "message": (
                "Model span omitted because source-native timing or "
                "provider/model metadata is incomplete."
            ),
            "record_id": "inv-miss",
        }
    ]
    assert all(s["name"] != "chat m" for s in tree["spans"])


def test_model_span_omitted_missing_provider_and_models() -> None:
    inv = ModelInvocation(
        id="inv-bare",
        started_at_ms=10,
        completed_at_ms=20,
    )
    tree = project_otel_genai(_ir([_user()], model_invocations=(inv,)))
    assert len(tree["diagnostics"]) == 1
    assert tree["diagnostics"][0]["record_id"] == "inv-bare"
    assert not any(s["name"].startswith("chat") for s in tree["spans"])


def test_model_span_name_falls_back_to_response_model_then_chat() -> None:
    inv_resp = ModelInvocation(
        id="i1",
        response_model="rm",
        started_at_ms=10,
        completed_at_ms=20,
    )
    # provider alone is enough for eligibility; name becomes "chat"
    inv_provider = ModelInvocation(
        id="i2",
        provider="p",
        started_at_ms=30,
        completed_at_ms=40,
    )
    tree = project_otel_genai(
        _ir(
            [_user(ts=5), _assistant(ts=50)],
            model_invocations=(inv_resp, inv_provider),
        )
    )
    names = {s["name"] for s in tree["spans"] if s["kind"] == "CLIENT"}
    assert names == {"chat rm", "chat"}


def test_model_span_uses_precise_invocation_clocks() -> None:
    start_p = "2024-06-01T12:00:00.1111111+00:00"
    end_p = "2024-06-01T12:00:01.2222222+00:00"
    inv = ModelInvocation(
        id="i-p",
        provider="p",
        requested_model="m",
        started_at_ms=1,
        started_at_precise=start_p,
        completed_at_ms=2,
        completed_at_precise=end_p,
    )
    tree = project_otel_genai(_ir([_user(ts=0)], model_invocations=(inv,)))
    model = next(s for s in tree["spans"] if s["name"] == "chat m")
    assert model["start_time"] == start_p
    assert model["end_time"] == end_p


def test_model_usage_none_omits_token_attrs() -> None:
    inv = ModelInvocation(
        id="i-u",
        provider="p",
        requested_model="m",
        started_at_ms=10,
        completed_at_ms=20,
        usage=None,
    )
    tree = project_otel_genai(_ir([_user()], model_invocations=(inv,)))
    model = next(s for s in tree["spans"] if s["name"] == "chat m")
    keys = {a["key"] for a in model["attributes"]}
    assert "gen_ai.usage.input_tokens" not in keys
    assert "gen_ai.usage.output_tokens" not in keys


# ---------------------------------------------------------------------------
# Tool spans
# ---------------------------------------------------------------------------


def test_tool_span_success_and_error() -> None:
    tree = project_otel_genai(
        _ir(
            [
                _user(rid="u", ts=100),
                _tool_calls(rid="c", ts=200, call_id="cid", name="run"),
                _tool_result(rid="r", ts=300, call_id="cid", is_error=True),
            ]
        )
    )
    tool = next(s for s in tree["spans"] if s["name"] == "execute_tool run")
    assert tool["kind"] == "INTERNAL"
    assert tool["status"] == "ERROR"
    assert tool["parent_span_id"] == _span_id_for("agent|u")
    assert tool["span_id"] == _span_id_for("tool|cid|c")
    by_key = {a["key"]: a["string_value"] for a in tool["attributes"]}
    assert by_key == {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "run",
        "gen_ai.tool.call.id": "cid",
        "hypabolic.trajectory.call_record.id": "c",
        "hypabolic.trajectory.result_record.id": "r",
    }


def test_tool_span_skipped_without_timestamps() -> None:
    calls = IrRecord(
        id="c",
        kind=RecordKind.ASSISTANT_TOOL_CALLS,
        role=TrajectoryRole.ASSISTANT,
        order=1,
        provenance=_prov(stable="c"),
        hashes=_hashes(),
        tool_calls=(ToolCall(id="cid", name="run", arguments_json="{}"),),
    )
    result = _tool_result(rid="r", ts=300, call_id="cid")
    tree = project_otel_genai(_ir([_user(ts=100), calls, result]))
    assert not any(s["name"].startswith("execute_tool") for s in tree["spans"])


def test_tool_span_unset_when_is_error_false_or_none() -> None:
    for is_error in (False, None):
        tree = project_otel_genai(
            _ir(
                [
                    _user(ts=100),
                    _tool_calls(ts=200),
                    _tool_result(ts=300, is_error=is_error),
                ]
            )
        )
        tool = next(s for s in tree["spans"] if s["name"].startswith("execute_tool"))
        assert tool["status"] == "UNSET"


# ---------------------------------------------------------------------------
# Root shape / sort / determinism
# ---------------------------------------------------------------------------


def test_root_field_order_and_content_policy() -> None:
    tree = project_otel_genai(_ir([_user()]))
    assert list(tree.keys()) == [
        "schema_url",
        "trace_id",
        "instrumentation_scope",
        "instrumentation_version",
        "resource_attributes",
        "spans",
        "diagnostics",
        "content_policy",
    ]
    assert tree["schema_url"] == "https://opentelemetry.io/schemas/gen-ai/1.42.0"
    assert tree["instrumentation_scope"] == "Hypabolic.Trajectory.OpenTelemetry"
    assert tree["instrumentation_version"] == WIRE_PACKAGE_VERSION
    assert tree["resource_attributes"] == []
    assert tree["content_policy"] == {
        "messages_included": False,
        "tool_arguments_included": False,
        "tool_results_included": False,
        "maximum_characters": 1024,
    }
    span = tree["spans"][0]
    assert list(span.keys()) == [
        "trace_id",
        "span_id",
        "name",
        "kind",
        "start_time",
        "end_time",
        "status",
        "attributes",
        "links",
        "events",
    ]
    assert span["links"] == []
    assert span["events"] == []


def test_spans_sorted_by_start_name_span_id() -> None:
    # Two agent turns + tool in first turn — sort by start_time primarily.
    tree = project_otel_genai(
        _ir(
            [
                _user(rid="u1", ts=100),
                _tool_calls(rid="c", ts=150, call_id="cid"),
                _tool_result(rid="r", ts=160, call_id="cid"),
                _user_at("u2", 200, 4),
            ]
        )
    )
    starts = [s["start_time"] for s in tree["spans"]]
    assert starts == sorted(starts)
    names = [s["name"] for s in tree["spans"]]
    assert names == ["invoke_agent", "execute_tool probe", "invoke_agent"]


def _user_at(rid: str, ts: int, order: int) -> IrRecord:
    return IrRecord(
        id=rid,
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.USER,
        order=order,
        provenance=_prov(stable=rid),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        timestamp_ms=ts,
        content="hi",
    )


def test_double_run_deterministic() -> None:
    ir = _ir(
        [
            _user(rid="u", ts=100),
            _tool_calls(rid="c", ts=200),
            _tool_result(rid="r", ts=300),
        ],
        model_invocations=(
            ModelInvocation(
                id="inv",
                provider="p",
                requested_model="m",
                started_at_ms=150,
                completed_at_ms=180,
            ),
        ),
    )
    a = serialize_projection(project_otel_genai(ir))
    b = serialize_projection(project_otel_genai(ir))
    assert a == b


# ---------------------------------------------------------------------------
# Golden parity (unicode-boundaries)
# ---------------------------------------------------------------------------


def test_unicode_boundaries_otel_golden() -> None:
    request = NormalizeRequest(
        source="pi",
        transcript=UNICODE_INPUT.read_bytes(),
        options=NormalizeOptions(
            bounds=Bounds(
                tool_arguments=ToolArgumentBounds(max_characters=120),
                tool_results=ToolResultBounds(
                    max_characters=10, strategy="head-tail"
                ),
            ),
            filters=Filters(tool_results="include"),
        ),
    )
    ir = normalize_to_ir(request)
    tree = project_otel_genai(ir)
    expected = json.loads(UNICODE_OTEL.read_text(encoding="utf-8"))
    # Compare compact serialize for byte-level tree equality of structure.
    assert json.loads(serialize_projection(tree)) == expected
    assert serialize_projection(tree) == serialize_projection(expected)


# ---------------------------------------------------------------------------
# otel submodule: emit_to + import matrix (no SDK)
# ---------------------------------------------------------------------------


def test_otel_public_exports() -> None:
    import hypabolic_trajectory.otel as otel

    assert otel.__all__ == ("SpanSetSink", "emit_to")
    assert hasattr(otel, "SpanSetSink")
    assert hasattr(otel, "emit_to")


def test_emit_to_without_sdk() -> None:
    from hypabolic_trajectory.otel import SpanSetSink, emit_to

    captured: list[dict] = []

    class Capture:
        def emit(self, span_set: dict) -> None:
            captured.append(span_set)

    sink: SpanSetSink = Capture()  # type: ignore[assignment]
    ir = _ir([_user(rid="u", ts=1)])
    emit_to(sink, ir)
    assert len(captured) == 1
    assert captured[0]["instrumentation_scope"] == "Hypabolic.Trajectory.OpenTelemetry"
    assert captured[0] == project_otel_genai(ir)


def test_emit_to_propagates_sink_errors() -> None:
    from hypabolic_trajectory.otel import emit_to

    class Boom:
        def emit(self, span_set: dict) -> None:
            raise RuntimeError("sink failed")

    with pytest.raises(RuntimeError, match="sink failed"):
        emit_to(Boom(), _ir([_user()]))


def test_project_otel_genai_root_export() -> None:
    import hypabolic_trajectory as ht

    assert ht.project_otel_genai is project_otel_genai
    assert "project_otel_genai" in ht.__all__


def test_core_import_does_not_load_opentelemetry() -> None:
    """Import matrix: pure path must not pull opentelemetry packages."""
    import sys

    # May already be absent; ensure our import path doesn't require them.
    for name in list(sys.modules):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            pytest.skip("opentelemetry already imported in this process")
    from hypabolic_trajectory import project_otel_genai as fn
    from hypabolic_trajectory.otel import emit_to as emit

    tree = fn(_ir([_user()]))
    assert tree["spans"]

    class S:
        def emit(self, span_set: dict) -> None:
            pass

    emit(S(), _ir([_user()]))
    for name in sys.modules:
        assert not (
            name == "opentelemetry" or name.startswith("opentelemetry.")
        ), f"unexpected import: {name}"
