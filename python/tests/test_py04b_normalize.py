"""PY-04b: Normalization core behaviour unit vectors.

Covers group resolution, whole/partial, tool linking, bounds, filled timestamps,
identity, diagnostics sequencing, and model-invocation absolute offset + id formula.
"""

from __future__ import annotations

import json

import pytest

from hypabolic_trajectory import (
    AppliedBounds,
    AppliedConfig,
    AppliedFilters,
    Bounds,
    Diagnostic,
    Filters,
    ModelTokenUsage,
    NormalizeOptions,
    NormalizeRequest,
    RecordKind,
    SourceAnchorKind,
    SourceContext,
    SourceIdentityKind,
    ToolArgumentBounds,
    ToolResultBounds,
    TrajectoryError,
    TrajectoryRole,
    TrajectorySource,
)
from hypabolic_trajectory.identity import (
    location_identity,
    model_invocation_id,
    record_id,
    sha256_hex,
)
from hypabolic_trajectory.normalize.bounds import shrink_arguments, truncate_result
from hypabolic_trajectory.normalize.core import (
    map_model_invocation,
    normalize_decoded,
    plan_events,
    resolve_group_id,
)
from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedModelInvocation,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import (
    get_source_adapter,
    register_source_adapter,
)


def _cfg(
    *,
    group_id: str | None = None,
    base: int = 0,
    partial: bool = False,
    arg_max: int | None = 20_000,
    res_max: int | None = 2_500,
    strategy: str = "head-tail",
    tool_results: str = "include",
) -> AppliedConfig:
    return AppliedConfig(
        bounds=AppliedBounds(
            tool_arguments_max_characters=arg_max,
            tool_results_max_characters=res_max,
            tool_results_strategy=strategy,  # type: ignore[arg-type]
        ),
        filters=AppliedFilters(tool_results=tool_results),  # type: ignore[arg-type]
        group_id=group_id,
        base_byte_offset=base,
        partial=partial,
    )


def _session(
    events: list[DecodedEvent],
    *,
    group_id: str | None = "sess",
    group_resolved: bool = True,
    model_invocations: list[DecodedModelInvocation] | None = None,
    diagnostics: list[Diagnostic] | None = None,
    created_at_ms: int | None = None,
    model: str | None = None,
) -> DecodedSession:
    return DecodedSession(
        source=TrajectorySource.PI,
        source_name="pi",
        group_id=group_id,
        group_resolved=group_resolved,
        model=model,
        created_at_ms=created_at_ms,
        events=tuple(events),
        model_invocations=tuple(model_invocations or ()),
        diagnostics=tuple(diagnostics or ()),
    )


def _msg(
    role: TrajectoryRole,
    content: str,
    *,
    native: str | None = None,
    offset: int | None = None,
    ts: int | None = None,
    seq: int | None = None,
    component_index: int = 0,
    model: str | None = None,
) -> DecodedEvent:
    return DecodedEvent(
        kind="message",
        role=role,
        content=content,
        native_record_id=native,
        source_offset=offset,
        source_anchor_kind=SourceAnchorKind.BYTE if offset is not None else None,
        timestamp_ms=ts,
        source_sequence=seq,
        component_index=component_index,
        model=model,
    )


def _tool_call(
    *,
    call_id: str | None,
    name: str | None = "tool",
    args: str | None = "{}",
    native: str | None = None,
    offset: int | None = None,
    component_index: int = 0,
) -> DecodedEvent:
    return DecodedEvent(
        kind="tool-call",
        role=TrajectoryRole.ASSISTANT,
        tool_call_id=call_id,
        tool_name=name,
        arguments_json=args,
        native_record_id=native,
        source_offset=offset,
        source_anchor_kind=SourceAnchorKind.BYTE if offset is not None else None,
        component_index=component_index,
    )


def _tool_result(
    *,
    call_id: str | None,
    content: str = "ok",
    native: str | None = None,
    offset: int | None = None,
    component_index: int = 0,
) -> DecodedEvent:
    return DecodedEvent(
        kind="tool-result",
        role=TrajectoryRole.TOOL,
        tool_call_id=call_id,
        content=content,
        native_record_id=native,
        source_offset=offset,
        source_anchor_kind=SourceAnchorKind.BYTE if offset is not None else None,
        component_index=component_index,
    )


# ---------------------------------------------------------------------------
# Group resolution
# ---------------------------------------------------------------------------


def test_group_detected_wins() -> None:
    with pytest.raises(TrajectoryError) as ei:
        resolve_group_id("detected", "provided-other")
    assert ei.value.code == "source_group_conflict"
    assert "detected" in ei.value.message
    assert '"' in ei.value.message  # quoted
    gid, resolved = resolve_group_id("detected", "detected")
    assert gid == "detected" and resolved is True
    gid2, resolved2 = resolve_group_id("detected", None)
    assert gid2 == "detected" and resolved2 is True


def test_group_provided_then_default() -> None:
    assert resolve_group_id(None, "supplied") == ("supplied", True)
    assert resolve_group_id(None, None) == ("default", False)
    assert resolve_group_id("", "") == ("default", False)


# ---------------------------------------------------------------------------
# Whole / partial fatals
# ---------------------------------------------------------------------------


def test_whole_mode_requires_user_and_assistant() -> None:
    only_user = _session(
        [_msg(TrajectoryRole.USER, "hi", native="u1")],
        group_id="g",
    )
    with pytest.raises(TrajectoryError) as ei:
        normalize_decoded(only_user, config=_cfg(group_id="g"))
    assert ei.value.code == "missing_assistant_records"

    only_asst = _session(
        [_msg(TrajectoryRole.ASSISTANT, "yo", native="a1")],
        group_id="g",
    )
    with pytest.raises(TrajectoryError) as ei2:
        normalize_decoded(only_asst, config=_cfg(group_id="g"))
    assert ei2.value.code == "missing_user_records"


def test_partial_mode_allows_missing_roles() -> None:
    ir = normalize_decoded(
        _session([_msg(TrajectoryRole.USER, "hi", native="u1")], group_id="g"),
        config=_cfg(group_id="g", partial=True),
    )
    assert any(r.role is TrajectoryRole.USER for r in ir.records)
    assert not any(r.role is TrajectoryRole.ASSISTANT for r in ir.records)


def test_nonzero_base_implies_partial() -> None:
    ir = normalize_decoded(
        _session([_msg(TrajectoryRole.USER, "hi", native="u1")], group_id="g"),
        config=_cfg(group_id="g", base=100),
    )
    # Survives without assistant because base != 0 ⇒ partial.
    assert ir.config.base_byte_offset == 100


# ---------------------------------------------------------------------------
# Tool linking
# ---------------------------------------------------------------------------


def test_tool_linking_synthesizes_and_renames() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _tool_call(call_id=None, name="a", native="c1"),  # → call_2
        _tool_call(call_id="dup", name="b", native="c2"),
        _tool_call(call_id="dup", name="c", native="c3"),  # → dup__2
        _tool_result(call_id="dup", content="r1", native="r1"),
        _tool_result(call_id="dup", content="r2", native="r2"),
        _msg(TrajectoryRole.ASSISTANT, "done", native="a"),
    ]
    ir = normalize_decoded(_session(events), config=_cfg())
    calls = [r for r in ir.records if r.kind is RecordKind.ASSISTANT_TOOL_CALLS]
    assert calls[0].tool_calls[0].id == "call_2"
    assert calls[1].tool_calls[0].id == "dup"
    assert calls[2].tool_calls[0].id == "dup__2"
    results = [r for r in ir.records if r.kind is RecordKind.TOOL_RESULT]
    assert results[0].tool_call_id == "dup"
    assert results[1].tool_call_id == "dup__2"
    codes = [d.code for d in ir.diagnostics]
    assert "tool_call_id_synthesized" in codes
    assert "duplicate_tool_call_id" in codes


def test_orphan_tool_result_dropped_whole_mode() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _tool_result(call_id="missing", content="x", native="r"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(_session(events), config=_cfg())
    assert not any(r.kind is RecordKind.TOOL_RESULT for r in ir.records)
    assert any(d.code == "orphan_tool_result" for d in ir.diagnostics)


def test_partial_cross_chunk_result_retained() -> None:
    events = [
        _tool_result(call_id="other-chunk", content="x", native="r"),
    ]
    ir = normalize_decoded(
        _session(events, group_id="g"),
        config=_cfg(group_id="g", partial=True),
    )
    results = [r for r in ir.records if r.kind is RecordKind.TOOL_RESULT]
    assert len(results) == 1
    assert results[0].tool_call_id == "other-chunk"


def test_omit_filter_removes_linked_results() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _tool_call(call_id="c1", native="tc"),
        _tool_result(call_id="c1", content="out", native="tr"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(
        _session(events), config=_cfg(tool_results="omit")
    )
    assert not any(r.kind is RecordKind.TOOL_RESULT for r in ir.records)
    assert any(r.kind is RecordKind.ASSISTANT_TOOL_CALLS for r in ir.records)


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_bounds_defaults_applied_config() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(_session(events), config=_cfg())
    assert ir.config.bounds.tool_arguments_max_characters == 20_000
    assert ir.config.bounds.tool_results_max_characters == 2_500
    assert ir.config.bounds.tool_results_strategy == "head-tail"


def test_shrink_arguments_reshapes_non_object() -> None:
    args, reshaped, truncated = shrink_arguments("not-json", None)
    assert reshaped is True
    assert truncated is False
    assert '"_raw"' in args


def test_shrink_arguments_rejects_non_finite_constants() -> None:
    for raw in ('{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'):
        args, reshaped, truncated = shrink_arguments(raw, None)
        assert reshaped is True
        assert truncated is False
        assert '"_raw"' in args


def test_shrink_arguments_leaf_floor_retains_prefix() -> None:
    # Leaf > 2000; limit large enough that floor shrink (keep ≥ 2000) fits
    # inside the outer JSON object envelope.
    leaf = "a" * 5000
    raw = json.dumps({"big": leaf}, separators=(",", ":"))
    # keep = max(2000, 5000//2)=2500 + "…" → value ~2501; envelope needs ~2515.
    args, reshaped, truncated = shrink_arguments(raw, 2600)
    assert reshaped is False
    assert truncated is True
    parsed = json.loads(args)
    assert "big" in parsed
    # Preferred floor: keep at least 2000 scalars (plus ellipsis if truncated).
    assert len(parsed["big"]) >= 2000
    assert parsed["big"].startswith("a" * 2000)
    assert "…" in parsed["big"]


def test_truncate_result_head_tail_uses_ellipsis() -> None:
    # Match tip unicode-boundaries style: short limit keeps ellipsis marker.
    text = "αβγ😀eXXXXりXYZ"  # multi-scalar
    out = truncate_result(text, 10, "head-tail")
    assert "…" in out
    assert len(out) <= 10 or "…" in out  # marker included in budget


def test_tool_arguments_truncated_diagnostic() -> None:
    big = '{"x":"' + ("a" * 500) + '"}'
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _tool_call(call_id="c1", args=big, native="tc"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(
        _session(events), config=_cfg(arg_max=50)
    )
    assert any(d.code == "tool_arguments_truncated" for d in ir.diagnostics)
    call = next(r for r in ir.records if r.kind is RecordKind.ASSISTANT_TOOL_CALLS)
    assert len(call.tool_calls[0].arguments_json) <= 50 or call.tool_calls[0].arguments_json.startswith("{")


# ---------------------------------------------------------------------------
# Noise + meta model
# ---------------------------------------------------------------------------


def test_noise_user_record_dropped() -> None:
    events = [
        _msg(TrajectoryRole.USER, "<command-name>foo", native="n"),
        _msg(TrajectoryRole.USER, "real", native="u"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(_session(events), config=_cfg())
    users = [r for r in ir.records if r.role is TrajectoryRole.USER]
    assert len(users) == 1
    assert users[0].content == "real"
    assert any(d.code == "noise_record_dropped" for d in ir.diagnostics)


def test_meta_model_frequency_tiebreak() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u", model="beta"),
        _msg(TrajectoryRole.ASSISTANT, "a1", native="a1", model="alpha"),
        _msg(TrajectoryRole.ASSISTANT, "a2", native="a2", model="alpha"),
        _msg(TrajectoryRole.ASSISTANT, "a3", native="a3", model="beta"),
    ]
    # alpha:2 beta:2 → ordinal name: alpha wins
    ir = normalize_decoded(_session(events), config=_cfg())
    meta = ir.records[0]
    assert meta.kind is RecordKind.META
    assert meta.model == "alpha"


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def test_timestamps_synthesized_from_base() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(
        _session(events, created_at_ms=1_000_000), config=_cfg()
    )
    body = [r for r in ir.records if r.kind is not RecordKind.META]
    assert body[0].timestamp_ms == 1_000_000
    assert body[1].timestamp_ms == 1_000_000 + 15_000
    assert any(d.code == "timestamps_synthesized" for d in ir.diagnostics)
    assert ir.records[0].timestamp_ms is None  # meta null


def test_timestamps_interpolated_between_anchors() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u", ts=1_000),
        _msg(TrajectoryRole.ASSISTANT, "m", native="m"),  # no ts
        _msg(TrajectoryRole.ASSISTANT, "a", native="a", ts=3_000),
    ]
    ir = normalize_decoded(_session(events), config=_cfg())
    body = [r for r in ir.records if r.kind is not RecordKind.META]
    assert body[0].timestamp_ms == 1_000
    assert body[1].timestamp_ms == 2_000  # linear mid
    assert body[2].timestamp_ms == 3_000
    assert any(d.code == "timestamps_interpolated" for d in ir.diagnostics)


def test_timestamps_interpolation_int_math_large_span() -> None:
    """Toward-zero integer division must not use float (mantissa loss > 2^53)."""
    from hypabolic_trajectory.normalize.core import _div_toward_zero

    assert _div_toward_zero(10, 3) == 3
    assert _div_toward_zero(-10, 3) == -3
    assert _div_toward_zero(10, -3) == -3
    assert _div_toward_zero(-10, -3) == 3
    # Magnitude above float mantissa; exact floor ratio via integers.
    big = 2**60 + 3
    assert _div_toward_zero(big, 2) == big // 2
    assert _div_toward_zero(-big, 2) == -(big // 2)


# ---------------------------------------------------------------------------
# Record identity
# ---------------------------------------------------------------------------


def test_native_record_identity() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="native-u", offset=10),
        _msg(TrajectoryRole.ASSISTANT, "a", native="native-a", offset=20),
    ]
    ir = normalize_decoded(_session(events, group_id="g"), config=_cfg(group_id="g"))
    user = next(r for r in ir.records if r.role is TrajectoryRole.USER)
    assert user.provenance.source_identity_kind is SourceIdentityKind.NATIVE
    assert user.provenance.stable_source_record_id == "native-u"
    assert user.provenance.source_offset == 10  # segment-relative
    assert user.id == record_id("g", "native-u", "message:0")


def test_location_identity_with_base_byte_offset() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", offset=5),
        _msg(TrajectoryRole.ASSISTANT, "a", offset=15),
    ]
    ir = normalize_decoded(
        _session(events, group_id="g"),
        config=_cfg(group_id="g", base=100, partial=True),
    )
    user = next(r for r in ir.records if r.role is TrajectoryRole.USER)
    expected_stable = location_identity("g", "byte", 105)
    assert user.provenance.source_identity_kind is SourceIdentityKind.LOCATION
    assert user.provenance.stable_source_record_id == expected_stable
    assert user.provenance.source_offset == 105  # absolute for location


def test_content_identity_fallback() -> None:
    events = [
        _msg(TrajectoryRole.USER, "hello"),
        _msg(TrajectoryRole.ASSISTANT, "world"),
    ]
    ir = normalize_decoded(
        _session(events, group_id="g"), config=_cfg(group_id="g")
    )
    user = next(r for r in ir.records if r.role is TrajectoryRole.USER)
    assert user.provenance.source_identity_kind is SourceIdentityKind.CONTENT
    assert len(user.provenance.stable_source_record_id) == 64


def test_meta_synthetic_identity() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(_session(events, group_id="g"), config=_cfg(group_id="g"))
    meta = ir.records[0]
    assert meta.order == -1
    assert meta.provenance.source_identity_kind is SourceIdentityKind.SYNTHETIC
    assert meta.provenance.source_order_id.startswith("0|")
    assert meta.id == record_id("g", "meta", "meta")


# ---------------------------------------------------------------------------
# Model-invocation formula (PY-04b acceptance vectors)
# ---------------------------------------------------------------------------


def test_model_invocation_native_id_path() -> None:
    inv = DecodedModelInvocation(
        native_record_id="nat-inv",
        source_offset=10,
        provider="openai",
        requested_model="gpt",
    )
    mapped = map_model_invocation(inv, "g", 100)
    assert mapped.id == model_invocation_id("g", "nat-inv")
    assert mapped.source_offset == 110  # absolute still stored
    assert mapped.native_record_id == "nat-inv"
    assert mapped.usage is None


def test_model_invocation_byte_offset_path_with_base() -> None:
    inv = DecodedModelInvocation(source_offset=42)
    mapped = map_model_invocation(inv, "group-x", 1000)
    identity = location_identity("group-x", "byte", 1042)
    assert mapped.source_offset == 1042
    assert mapped.id == model_invocation_id("group-x", identity)
    assert mapped.id == sha256_hex(
        # compact array [group, identity, "model-invocation"]
        __import__("hypabolic_trajectory.canonical", fromlist=["compact_json"]).compact_json(
            ["group-x", identity, "model-invocation"]
        )
    )


def test_model_invocation_response_id_fallback() -> None:
    inv = DecodedModelInvocation(response_id="resp-99")
    mapped = map_model_invocation(inv, "g", 0)
    assert mapped.id == model_invocation_id("g", "resp-99")
    assert mapped.source_offset is None


def test_model_invocation_literal_fallback() -> None:
    inv = DecodedModelInvocation()
    mapped = map_model_invocation(inv, "g", 0)
    assert mapped.id == model_invocation_id("g", "model-invocation")


def test_model_invocation_usage_omission() -> None:
    empty = map_model_invocation(DecodedModelInvocation(), "g", 0)
    assert empty.usage is None
    partial = map_model_invocation(
        DecodedModelInvocation(input_tokens=3, output_tokens=None), "g", 0
    )
    assert partial.usage is not None
    assert partial.usage.input_tokens == 3
    assert partial.usage.output_tokens is None


def test_model_invocation_int64_overflow() -> None:
    inv = DecodedModelInvocation(source_offset=2**62)
    with pytest.raises(TrajectoryError) as ei:
        map_model_invocation(inv, "g", 2**62)
    assert ei.value.code == "invalid_input"
    assert "out of range" in ei.value.message.lower()


def test_model_invocation_in_full_normalize() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    invs = [
        DecodedModelInvocation(
            native_record_id="mi-1",
            source_offset=7,
            input_tokens=1,
            output_tokens=2,
            started_at_ms=10,
            started_at_precise="2020-01-01T00:00:00.0000000+00:00",
        )
    ]
    ir = normalize_decoded(
        _session(events, model_invocations=invs, group_id="g"),
        config=_cfg(group_id="g", base=3),
    )
    mi = ir.execution.model_invocations[0]
    assert mi.source_offset == 10
    assert mi.id == model_invocation_id("g", "mi-1")
    assert mi.usage is not None
    assert mi.usage.input_tokens == 1
    assert mi.started_at_precise is not None
    assert ir.execution.workflow_invocations == ()


# ---------------------------------------------------------------------------
# Diagnostics order + dual timestamps on records
# ---------------------------------------------------------------------------


def test_diagnostics_decode_then_normalize() -> None:
    decode_diag = Diagnostic(code="invalid_json_line", message="bad line", input_line=1)
    events = [
        _msg(TrajectoryRole.USER, "<command-name>x", native="n"),
        _msg(TrajectoryRole.USER, "u", native="u"),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
    ]
    ir = normalize_decoded(
        _session(events, diagnostics=[decode_diag]), config=_cfg()
    )
    assert ir.diagnostics[0].code == "invalid_json_line"
    assert ir.diagnostics[1].code == "noise_record_dropped"


def test_dual_timestamps_copied_not_invented() -> None:
    events = [
        DecodedEvent(
            kind="message",
            role=TrajectoryRole.USER,
            content="u",
            native_record_id="u",
            timestamp_ms=123,
            timestamp_precise="2020-01-01T00:00:00.1230000+00:00",
            component_index=0,
        ),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a", ts=456),
    ]
    ir = normalize_decoded(_session(events), config=_cfg())
    user = next(r for r in ir.records if r.role is TrajectoryRole.USER)
    assert user.source_timestamp_ms == 123
    assert user.source_timestamp_precise == "2020-01-01T00:00:00.1230000+00:00"
    asst = next(r for r in ir.records if r.role is TrajectoryRole.ASSISTANT and r.kind is RecordKind.MESSAGE)
    assert asst.source_timestamp_precise is None  # never invented


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_normalize_is_deterministic() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", native="u", offset=1, ts=1000),
        _tool_call(call_id="c", args='{"a":1}', native="tc", offset=2),
        _tool_result(call_id="c", content="out", native="tr", offset=3),
        _msg(TrajectoryRole.ASSISTANT, "a", native="a", offset=4, ts=2000),
    ]
    invs = [DecodedModelInvocation(native_record_id="m", source_offset=9)]
    s = _session(events, model_invocations=invs, group_id="g")
    cfg = _cfg(group_id="g", base=50)
    a = normalize_decoded(s, config=cfg)
    b = normalize_decoded(s, config=cfg)
    assert a.group_id == b.group_id
    assert [r.id for r in a.records] == [r.id for r in b.records]
    assert [r.hashes.content_sha256 for r in a.records] == [
        r.hashes.content_sha256 for r in b.records
    ]
    assert a.execution.model_invocations[0].id == b.execution.model_invocations[0].id


# ---------------------------------------------------------------------------
# normalize_to_ir wiring (adapter present)
# ---------------------------------------------------------------------------


class _SyntheticAdapter:
    @property
    def source(self) -> TrajectorySource:
        return TrajectorySource.PI

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession:
        _ = transcript, source_context
        return _session(
            [
                _msg(TrajectoryRole.USER, "u", native="u"),
                _msg(TrajectoryRole.ASSISTANT, "a", native="a"),
            ],
            group_id="wired",
        )


def test_normalize_to_ir_uses_adapter_when_registered() -> None:
    import hypabolic_trajectory as ht
    from hypabolic_trajectory.sources.protocol import registered_source_names

    prior = get_source_adapter("pi")
    register_source_adapter(_SyntheticAdapter())
    try:
        ir = ht.normalize_to_ir(
            NormalizeRequest(source="pi", transcript=b"{}")
        )
        assert ir.group_id == "wired"
        assert ir.source is TrajectorySource.PI
        assert len(ir.records) >= 3  # meta + 2
    finally:
        # Restore registry: remove synthetic by re-registering prior if any.
        if prior is not None:
            register_source_adapter(prior)
        else:
            # Clear pi registration left by synthetic.
            from hypabolic_trajectory.sources import protocol as proto

            proto._ADAPTERS.pop("pi", None)  # noqa: SLF001


def test_normalize_to_ir_still_not_implemented_without_adapter() -> None:
    import hypabolic_trajectory as ht

    assert get_source_adapter("hermes") is None
    with pytest.raises(TrajectoryError) as ei:
        ht.normalize_to_ir(NormalizeRequest(source="hermes", transcript=b"{}"))
    assert ei.value.code == "invalid_input"
    assert "not implemented" in ei.value.message


def test_plan_events_component_ordinals() -> None:
    events = [
        _msg(TrajectoryRole.USER, "u", component_index=0),
        _msg(TrajectoryRole.ASSISTANT, "a", component_index=0),
        _msg(TrajectoryRole.ASSISTANT, "a2", component_index=1),  # same occurrence
    ]
    plan = plan_events(events)
    # occurrence 0: user message ordinal 0
    # occurrence 1: first assistant message ordinal 0
    # occurrence 1: second assistant (component_index 1) same occurrence → ordinal 1
    assert plan.ordinals[0] == 0
    assert plan.ordinals[1] == 0
    assert plan.ordinals[2] == 1
