"""PY-07a unit vectors: core projections + serialize_projection.

Covers hypabolic trajectory_id/segment pins, diagnostic casing, null policy,
integer emit, source_group_required, convenience wrappers, and public serializer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from hypabolic_trajectory import (
    AppliedBounds,
    AppliedConfig,
    AppliedFilters,
    Diagnostic,
    IrRecord,
    Provenance,
    RecordHashes,
    RecordKind,
    SourceAnchorKind,
    SourceIdentityKind,
    ToolCall,
    TrajectoryError,
    TrajectoryExecution,
    TrajectoryIR,
    TrajectoryRole,
    TrajectorySource,
    project_canonical,
    project_hypabolic,
    project_letta,
    serialize_projection,
)
from hypabolic_trajectory.canonical import compact_json
from hypabolic_trajectory.identity import trajectory_id
from hypabolic_trajectory.project.core import MSG_SOURCE_GROUP_REQUIRED, to_letta_record

REPO_ROOT = Path(__file__).resolve().parents[2]
UNICODE_HYPA = (
    REPO_ROOT
    / "conformance"
    / "cases"
    / "pi"
    / "unicode-boundaries"
    / "expected.hypabolic.json"
)
PARTIAL_HYPA = (
    REPO_ROOT
    / "conformance"
    / "cases"
    / "pi"
    / "partial-chunk"
    / "expected.hypabolic.json"
)
MISSING_GROUP_ERR = (
    REPO_ROOT
    / "conformance"
    / "cases"
    / "codex"
    / "missing-group"
    / "expected.error.json"
)


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


def _cfg(
    *,
    base: int = 0,
    partial: bool = False,
    arg_max: int | None = 120,
    res_max: int | None = 10,
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
        group_id=None,
        base_byte_offset=base,
        partial=partial,
    )


def _ir(
    records: list[IrRecord],
    *,
    source: TrajectorySource = TrajectorySource.PI,
    group_id: str = "unicode-session",
    group_resolved: bool = True,
    source_name: str = "pi",
    producer_version: str | None = "3",
    config: AppliedConfig | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> TrajectoryIR:
    return TrajectoryIR(
        source=source,
        source_name=source_name,
        group_id=group_id,
        source_group_resolved=group_resolved,
        records=tuple(records),
        diagnostics=tuple(diagnostics or ()),
        config=config or _cfg(),
        execution=TrajectoryExecution(model_invocations=()),
        producer_version=producer_version,
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
        cwd="/tmp",
        model="test-model",
        producer_version="3",
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


# ---------------------------------------------------------------------------
# trajectory_id unit vectors (required by PY-07a)
# ---------------------------------------------------------------------------


def test_trajectory_id_unicode_session_matches_golden() -> None:
    golden = json.loads(UNICODE_HYPA.read_text(encoding="utf-8"))
    assert trajectory_id("pi", "unicode-session") == golden["trajectory_id"]
    # project_hypabolic must use the same formula
    ir = _ir([_meta(), _user()], group_id="unicode-session")
    hypa = project_hypabolic(ir)
    assert hypa["trajectory_id"] == golden["trajectory_id"]


def test_trajectory_id_partial_chunk_matches_golden() -> None:
    golden = json.loads(PARTIAL_HYPA.read_text(encoding="utf-8"))
    assert trajectory_id("pi", "pi-chunk-session") == golden["trajectory_id"]
    ir = _ir(
        [_meta(), _user()],
        group_id="pi-chunk-session",
        config=_cfg(base=4096, partial=True),
        producer_version=None,
    )
    hypa = project_hypabolic(ir)
    assert hypa["trajectory_id"] == golden["trajectory_id"]
    assert hypa["segment"] == golden["segment"]
    assert hypa["segment"]["partial"] is True
    assert hypa["segment"]["base_byte_offset"] == 4096


def test_segment_partial_when_base_nonzero_even_if_flag_false() -> None:
    ir = _ir([_user()], config=_cfg(base=100, partial=False))
    hypa = project_hypabolic(ir)
    assert hypa["segment"]["partial"] is True
    assert hypa["segment"]["base_byte_offset"] == 100


def test_segment_partial_when_flag_true_and_base_zero() -> None:
    ir = _ir([_user()], config=_cfg(base=0, partial=True))
    hypa = project_hypabolic(ir)
    assert hypa["segment"]["partial"] is True


# ---------------------------------------------------------------------------
# serialize_projection
# ---------------------------------------------------------------------------


def test_serialize_projection_compact_matches_compact_json() -> None:
    tree: dict = {
        "records": [{"role": "user", "content": "a\nb\t\"c", "n": 42, "x": None}],
        "diagnostics": [],
    }
    assert serialize_projection(tree) == compact_json(tree)
    assert serialize_projection(tree, write_indented=False) == compact_json(tree)


def test_serialize_projection_preserves_nulls() -> None:
    text = serialize_projection({"a": None, "b": 1})
    assert text == '{"a":null,"b":1}'


def test_serialize_projection_integer_no_exponent() -> None:
    text = serialize_projection({"n": 9007199254740993})  # > 2^53
    assert text == '{"n":9007199254740993}'
    assert "e" not in text.lower()


def test_serialize_projection_rejects_nonfinite_float() -> None:
    with pytest.raises(TypeError):
        serialize_projection({"n": math.nan})
    with pytest.raises(TypeError):
        serialize_projection({"n": math.inf})


def test_serialize_projection_indented_two_space() -> None:
    text = serialize_projection({"a": 1, "b": [None, True]}, write_indented=True)
    assert text == '{\n  "a": 1,\n  "b": [\n    null,\n    true\n  ]\n}'
    assert not text.endswith("\n")


def test_serialize_projection_shared_escape() -> None:
    text = serialize_projection({"\ue000": "\u2028"})
    assert text == r'{"\uE000":"\u2028"}'


# ---------------------------------------------------------------------------
# project_letta
# ---------------------------------------------------------------------------


def test_project_letta_field_order_and_null_content() -> None:
    call = ToolCall(id="c1", name="probe", arguments_json="{}")
    ts = 1_735_670_601_123
    records = [
        _meta(),
        _user(),
        IrRecord(
            id="tool-id",
            kind=RecordKind.ASSISTANT_TOOL_CALLS,
            role=TrajectoryRole.ASSISTANT,
            order=1,
            provenance=_prov(stable="asst"),
            hashes=_hashes(),
            timestamp_ms=ts,
            source_timestamp_ms=ts,
            tool_calls=(call,),
        ),
        IrRecord(
            id="res-id",
            kind=RecordKind.TOOL_RESULT,
            role=TrajectoryRole.TOOL,
            order=2,
            provenance=_prov(stable="res"),
            hashes=_hashes(),
            timestamp_ms=ts,
            source_timestamp_ms=ts,
            content="ok",
            tool_call_id="c1",
        ),
    ]
    diag = Diagnostic(
        code="tool_arguments_truncated",
        message="Truncated.",
        input_line=4,
        record_index=3,
    )
    out = project_letta(_ir(records, diagnostics=[diag]))
    assert list(out.keys()) == ["records", "diagnostics"]
    assert out["records"][0] == {
        "role": "meta",
        "source": "pi",
        "cwd": "/tmp",
        "model": "test-model",
    }
    assert out["records"][2]["content"] is None
    assert out["records"][2]["tool_calls"][0]["args"] == "{}"
    # camelCase diagnostics for letta
    assert out["diagnostics"] == [
        {
            "code": "tool_arguments_truncated",
            "message": "Truncated.",
            "inputLine": 4,
            "recordIndex": 3,
        }
    ]


def test_to_letta_record_message_empty_content() -> None:
    rec = _user(content="")
    # content was set; empty string is kept
    assert to_letta_record(rec)["content"] == ""
    rec2 = IrRecord(
        id="x",
        kind=RecordKind.MESSAGE,
        role=TrajectoryRole.ASSISTANT,
        order=0,
        provenance=_prov(),
        hashes=_hashes(),
        timestamp_ms=1,
        content=None,
    )
    assert to_letta_record(rec2)["content"] == ""


# ---------------------------------------------------------------------------
# project_canonical
# ---------------------------------------------------------------------------


def test_project_canonical_source_group_required_exact_message() -> None:
    expected = json.loads(MISSING_GROUP_ERR.read_text(encoding="utf-8"))
    ir = _ir(
        [_user()],
        source=TrajectorySource.CODEX,
        group_id="codex-fallback",
        group_resolved=False,
        source_name="codex",
    )
    with pytest.raises(TrajectoryError) as ei:
        project_canonical(ir)
    assert ei.value.code == expected["code"] == "source_group_required"
    assert ei.value.message == expected["message"] == MSG_SOURCE_GROUP_REQUIRED
    # Letta and hypabolic must NOT raise
    project_letta(ir)
    project_hypabolic(ir)


def test_project_canonical_null_fields_and_camel_diagnostics() -> None:
    diag = Diagnostic(code="timestamps_synthesized", message="Synthesized.", count=2)
    out = project_canonical(_ir([_meta(), _user()], diagnostics=[diag]))
    assert list(out.keys()) == [
        "records",
        "diagnostics",
        "normalizer_version",
        "canonical_schema_version",
        "config",
    ]
    assert out["normalizer_version"] == "0.2.0"
    assert out["canonical_schema_version"] == 1
    meta = out["records"][0]
    assert meta["source_timestamp"] is None
    assert meta["record_timestamp"] is None
    assert meta["content"] is None
    assert meta["tool_call_id"] is None
    assert meta["record_type"] == "meta"
    user = out["records"][1]
    assert user["record_type"] == "user"
    assert user["content"] == "hi"
    assert out["diagnostics"][0] == {
        "code": "timestamps_synthesized",
        "message": "Synthesized.",
        "count": 2,
    }
    assert "inputLine" not in out["diagnostics"][0]
    assert out["config"]["bounds"]["toolArguments"]["maxCharacters"] == 120
    assert out["config"]["bounds"]["toolResults"]["strategy"] == "head-tail"
    assert out["config"]["filters"]["toolResults"] == "include"


def test_project_canonical_omits_meta_when_base_nonzero() -> None:
    out = project_canonical(
        _ir([_meta(), _user()], config=_cfg(base=100, partial=True))
    )
    assert all(r["record_type"] != "meta" for r in out["records"])
    assert len(out["records"]) == 1


def test_project_canonical_null_max_characters() -> None:
    out = project_canonical(
        _ir([_user()], config=_cfg(arg_max=None, res_max=None, strategy="head"))
    )
    assert out["config"]["bounds"]["toolArguments"]["maxCharacters"] is None
    assert out["config"]["bounds"]["toolResults"]["maxCharacters"] is None


# ---------------------------------------------------------------------------
# project_hypabolic
# ---------------------------------------------------------------------------


def test_project_hypabolic_snake_diagnostics_and_root_order() -> None:
    diag = Diagnostic(
        code="tool_result_truncated",
        message="Truncated result.",
        input_line=3,
        record_index=2,
    )
    out = project_hypabolic(_ir([_meta(), _user()], diagnostics=[diag]))
    assert list(out.keys()) == [
        "schema_id",
        "schema_version",
        "trajectory_id",
        "source",
        "segment",
        "normalizer",
        "config",
        "records",
        "diagnostics",
    ]
    assert out["schema_id"] == "hypabolic-trajectory-v1"
    assert out["schema_version"] == 1
    assert out["normalizer"] == {
        "name": "Hypabolic.Trajectory",
        "version": "0.1.0",
    }
    assert out["source"]["type"] == "pi"
    assert out["source"]["group_id"] == "unicode-session"
    assert out["source"]["producer_version"] == "3"
    assert out["diagnostics"] == [
        {
            "code": "tool_result_truncated",
            "message": "Truncated result.",
            "input_line": 3,
            "record_index": 2,
        }
    ]
    meta = out["records"][0]
    assert meta["kind"] == "meta"
    assert meta["order"] == -1
    assert meta["source_timestamp"] is None
    assert meta["timestamp"] is None
    assert meta["source_name"] == "pi"
    user = out["records"][1]
    assert user["kind"] == "message"
    assert "tool_calls" not in user
    assert user["provenance"]["source_anchor_kind"] == "byte"
    assert out["config"]["bounds"]["tool_arguments"]["max_characters"] == 120
    assert out["config"]["filters"]["tool_results"] == "include"


def test_project_hypabolic_omits_producer_version_when_absent() -> None:
    out = project_hypabolic(_ir([_user()], producer_version=None))
    assert "producer_version" not in out["source"]


# ---------------------------------------------------------------------------
# Mutability + no IR mutation
# ---------------------------------------------------------------------------


def test_projectors_do_not_mutate_ir() -> None:
    records = [_meta(), _user()]
    ir = _ir(records)
    before = (ir.group_id, len(ir.records), ir.records[0].id)
    project_letta(ir)
    project_canonical(ir)
    project_hypabolic(ir)
    assert (ir.group_id, len(ir.records), ir.records[0].id) == before


def test_public_exports_callable() -> None:
    from hypabolic_trajectory import (
        normalize_to_canonical,
        normalize_to_hypabolic,
        normalize_to_letta,
        serialize_projection as sp,
    )

    assert callable(project_letta)
    assert callable(project_canonical)
    assert callable(project_hypabolic)
    assert callable(sp)
    assert callable(normalize_to_letta)
    assert callable(normalize_to_canonical)
    assert callable(normalize_to_hypabolic)
