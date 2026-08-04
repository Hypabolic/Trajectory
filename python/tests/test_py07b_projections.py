"""PY-07b unit vectors: project_openai + project_minimal_jsonl.

Covers list-root openai (skip meta/reasoning), jsonl filled-ms +00:00 clock,
kind underscore stripping, final newline, shared escape (no second serializer),
and the unicode-boundaries oracle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypabolic_trajectory import (
    AppliedBounds,
    AppliedConfig,
    AppliedFilters,
    Bounds,
    Diagnostic,
    Filters,
    IrRecord,
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
    TrajectoryError,
    TrajectoryExecution,
    TrajectoryIR,
    TrajectoryRole,
    TrajectorySource,
    normalize_to_ir,
    project_minimal_jsonl,
    project_openai,
    serialize_projection,
)
from hypabolic_trajectory.project.core import _minimal_record
from hypabolic_trajectory.timestamps import format_ms_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
UNICODE_CASE = REPO_ROOT / "conformance" / "cases" / "pi" / "unicode-boundaries"
UNICODE_OPENAI = UNICODE_CASE / "expected.openai.json"
UNICODE_JSONL = UNICODE_CASE / "expected.minimal.jsonl"
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
    diagnostics: list[Diagnostic] | None = None,
) -> TrajectoryIR:
    return TrajectoryIR(
        source=TrajectorySource.PI,
        source_name="pi",
        group_id="unicode-session",
        source_group_resolved=True,
        records=tuple(records),
        diagnostics=tuple(diagnostics or ()),
        config=_cfg(),
        execution=TrajectoryExecution(model_invocations=()),
        producer_version="3",
    )


def _meta() -> IrRecord:
    return IrRecord(
        id="meta-id",
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


def _user(content: str = "hi", ts: int = 1_735_670_601_123) -> IrRecord:
    return IrRecord(
        id="user-id",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.USER,
        order=0,
        provenance=_prov(),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        timestamp_ms=ts,
        content=content,
    )


def _reasoning(content: str = "think", ts: int = 1_735_670_601_200) -> IrRecord:
    return IrRecord(
        id="reason-id",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.REASONING,
        order=1,
        provenance=_prov(stable="reason"),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        timestamp_ms=ts,
        content=content,
    )


def _tool_calls(ts: int = 1_735_670_601_300) -> IrRecord:
    return IrRecord(
        id="call-id",
        kind=RecordKind.ASSISTANT_TOOL_CALLS,
        role=TrajectoryRole.ASSISTANT,
        order=2,
        provenance=_prov(stable="call"),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        timestamp_ms=ts,
        tool_calls=(
            ToolCall(id="c1", name="probe", arguments_json='{"x":1}'),
        ),
    )


def _tool_result(
    *,
    content: str | None = "ok",
    tool_name: str | None = "probe",
    tool_call_id: str | None = "c1",
    is_error: bool | None = False,
    ts: int = 1_735_670_601_400,
) -> IrRecord:
    return IrRecord(
        id="res-id",
        kind=RecordKind.TOOL_RESULT,
        role=TrajectoryRole.TOOL,
        order=3,
        provenance=_prov(stable="res"),
        hashes=_hashes(),
        source_timestamp_ms=ts,
        timestamp_ms=ts,
        content=content,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        is_error=is_error,
    )


def _unicode_request() -> NormalizeRequest:
    return NormalizeRequest(
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


# ---------------------------------------------------------------------------
# Oracle: pi/unicode-boundaries
# ---------------------------------------------------------------------------


def test_project_openai_unicode_boundaries_byte_exact() -> None:
    ir = normalize_to_ir(_unicode_request())
    got = serialize_projection(project_openai(ir))
    expected = UNICODE_OPENAI.read_text(encoding="utf-8")
    assert got == expected


def test_project_minimal_jsonl_unicode_boundaries_byte_exact() -> None:
    ir = normalize_to_ir(_unicode_request())
    got = project_minimal_jsonl(ir)
    expected = UNICODE_JSONL.read_text(encoding="utf-8")
    assert got == expected
    assert got.endswith("\n")


# ---------------------------------------------------------------------------
# project_openai
# ---------------------------------------------------------------------------


def test_project_openai_skips_meta_and_reasoning() -> None:
    out = project_openai(
        _ir([_meta(), _user(), _reasoning(), _tool_calls(), _tool_result()])
    )
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "tool"]
    assert all("timestamp" not in m for m in out)


def test_project_openai_tool_calls_shape() -> None:
    out = project_openai(_ir([_tool_calls()]))
    assert out == [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "probe", "arguments": '{"x":1}'},
                }
            ],
        }
    ]


def test_project_openai_tool_result_optional_name_and_defaults() -> None:
    with_name = project_openai(_ir([_tool_result()]))[0]
    assert with_name == {
        "role": "tool",
        "content": "ok",
        "tool_call_id": "c1",
        "name": "probe",
    }
    bare = project_openai(
        _ir([_tool_result(content=None, tool_name=None, tool_call_id=None)])
    )[0]
    assert bare == {"role": "tool", "content": "", "tool_call_id": ""}
    assert "name" not in bare


def test_project_openai_message_empty_content_default() -> None:
    rec = IrRecord(
        id="a",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.ASSISTANT,
        order=0,
        provenance=_prov(),
        hashes=_hashes(),
        timestamp_ms=1,
        content=None,
    )
    assert project_openai(_ir([rec])) == [{"role": "assistant", "content": ""}]


def test_project_openai_returns_list_not_object() -> None:
    out = project_openai(_ir([_user()]))
    assert isinstance(out, list)
    # Root is a JSON array when serialized
    assert serialize_projection(out).startswith("[")


def test_project_openai_ignores_diagnostics_on_product_root() -> None:
    """openai-chat-messages has no diagnostics array (spec §3 casing matrix)."""
    diag = Diagnostic(code="tool_arguments_truncated", message="Truncated.")
    out = project_openai(_ir([_user(), _tool_calls()], diagnostics=[diag]))
    assert isinstance(out, list)
    assert all(isinstance(item, dict) for item in out)
    assert all("diagnostics" not in item for item in out)
    # Flat message list only — no envelope object wrapping diagnostics.
    assert [m["role"] for m in out] == ["user", "assistant"]


def test_project_openai_empty_tool_calls_array() -> None:
    """ASSISTANT_TOOL_CALLS with empty calls still emits tool_calls:[]."""
    rec = IrRecord(
        id="call-id",
        kind=RecordKind.ASSISTANT_TOOL_CALLS,
        role=TrajectoryRole.ASSISTANT,
        order=0,
        provenance=_prov(stable="call"),
        hashes=_hashes(),
        timestamp_ms=1,
        tool_calls=(),
    )
    assert project_openai(_ir([rec])) == [
        {"role": "assistant", "tool_calls": []}
    ]


def test_project_openai_does_not_mutate_ir() -> None:
    ir = _ir([_user(), _tool_calls()])
    before = tuple(ir.records)
    _ = project_openai(ir)
    assert ir.records is before
    assert ir.records[0].content == "hi"


# ---------------------------------------------------------------------------
# project_minimal_jsonl
# ---------------------------------------------------------------------------


def test_project_minimal_jsonl_includes_meta_order_minus_one_no_timestamp() -> None:
    doc = project_minimal_jsonl(_ir([_meta(), _user()]))
    lines = doc.splitlines()
    assert len(lines) == 2
    assert doc.endswith("\n")
    meta_line = lines[0]
    assert '"order":-1' in meta_line
    assert '"kind":"meta"' in meta_line
    assert "timestamp" not in meta_line


def test_project_minimal_jsonl_kind_strips_underscores() -> None:
    doc = project_minimal_jsonl(_ir([_tool_calls(), _tool_result()]))
    assert '"kind":"assistanttoolcalls"' in doc
    assert '"kind":"toolresult"' in doc
    assert "assistant_tool_calls" not in doc
    assert "tool_result" not in doc


def test_project_minimal_jsonl_timestamp_from_filled_ms_plus00() -> None:
    ts = 1_735_670_601_123
    # Deliberately different source clock — must not appear on wire.
    rec = IrRecord(
        id="user-id",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.USER,
        order=0,
        provenance=_prov(),
        hashes=_hashes(),
        source_timestamp_ms=ts - 999_999,
        timestamp_ms=ts,
        content="hi",
    )
    doc = project_minimal_jsonl(_ir([rec]))
    expected_ts = format_ms_jsonl(ts)
    assert expected_ts.endswith("+00:00")
    assert f'"timestamp":"{expected_ts}"' in doc
    # Whole document: jsonl clock uses +00:00 only; no Z form on the body clock.
    assert "Z" not in doc
    # Negative pre-1970 filled ms still emits three-digit +00:00 form.
    pre = IrRecord(
        id="pre",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.USER,
        order=0,
        provenance=_prov(stable="pre"),
        hashes=_hashes(),
        timestamp_ms=-1,
        content="x",
    )
    pre_doc = project_minimal_jsonl(_ir([pre]))
    assert format_ms_jsonl(-1) in pre_doc
    assert pre_doc.endswith("\n")
    assert "Z" not in pre_doc


def test_project_minimal_jsonl_out_of_range_timestamp_raises() -> None:
    """Invalid filled ms propagates TrajectoryError from format_ms (peer Result)."""
    # Far beyond datetime range that format_ms can represent.
    rec = IrRecord(
        id="bad",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.USER,
        order=0,
        provenance=_prov(stable="bad"),
        hashes=_hashes(),
        timestamp_ms=10**15,  # year ~33 million — out of range for UTC calendar
        content="x",
    )
    with pytest.raises(TrajectoryError) as exc_info:
        project_minimal_jsonl(_ir([rec]))
    assert exc_info.value.code == "invalid_input"


def test_project_minimal_jsonl_empty_tool_calls_omitted() -> None:
    """Rust omits empty tool_calls; TS would emit []. Pin Rust semantics."""
    rec = IrRecord(
        id="call-id",
        kind=RecordKind.ASSISTANT_TOOL_CALLS,
        role=TrajectoryRole.ASSISTANT,
        order=0,
        provenance=_prov(stable="call"),
        hashes=_hashes(),
        timestamp_ms=1,
        tool_calls=(),
    )
    line = project_minimal_jsonl(_ir([rec])).rstrip("\n")
    assert "tool_calls" not in line
    # Asymmetry with openai: same record yields tool_calls:[] on that projector.
    assert project_openai(_ir([rec]))[0]["tool_calls"] == []


def test_project_minimal_jsonl_field_order() -> None:
    rec = _tool_result()
    obj = _minimal_record(rec)
    assert list(obj.keys()) == [
        "id",
        "order",
        "kind",
        "role",
        "timestamp",
        "content",
        "tool_call_id",
        "tool_name",
        "is_error",
    ]
    call_obj = _minimal_record(_tool_calls())
    assert list(call_obj.keys())[-1] == "tool_calls"
    assert list(call_obj["tool_calls"][0].keys()) == [  # type: ignore[index]
        "id",
        "name",
        "arguments_json",
    ]


def test_project_minimal_jsonl_uses_shared_escape_not_second_serializer() -> None:
    rec = _user(content='say "hi"\tand\nbye \ue000')
    line = project_minimal_jsonl(_ir([rec])).rstrip("\n")
    # Literal pin of shared Trajectory escape (uppercase hex PUA; short \t \n).
    # stdlib json.dumps would emit lowercase \\ue000 or leave U+E000 unescaped.
    assert line == (
        '{"id":"user-id","order":0,"kind":"message","role":"user",'
        '"timestamp":"2024-12-31T18:43:21.123+00:00",'
        r'"content":"say \"hi\"\tand\nbye \uE000"}'
    )
    assert r"\uE000" in line
    assert r"\t" in line
    assert r"\n" in line


def test_project_minimal_jsonl_empty_trajectory() -> None:
    assert project_minimal_jsonl(_ir([])) == ""


def test_project_minimal_jsonl_no_diagnostics_document() -> None:
    diag = Diagnostic(code="tool_result_truncated", message="Truncated.")
    doc = project_minimal_jsonl(_ir([_user()], diagnostics=[diag]))
    assert "diagnostics" not in doc
    assert "tool_result_truncated" not in doc


def test_project_minimal_jsonl_does_not_mutate_ir() -> None:
    ir = _ir([_meta(), _user()])
    before_ids = [r.id for r in ir.records]
    _ = project_minimal_jsonl(ir)
    assert [r.id for r in ir.records] == before_ids


def test_public_root_exports_project_openai_and_jsonl() -> None:
    import hypabolic_trajectory as ht

    assert ht.project_openai is project_openai
    assert ht.project_minimal_jsonl is project_minimal_jsonl
    assert "project_openai" in ht.__all__
    assert "project_minimal_jsonl" in ht.__all__
