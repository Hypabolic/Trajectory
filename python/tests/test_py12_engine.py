"""PY-12: TrajectoryEngine ship surface — create_default / project / adapters."""

from __future__ import annotations

import hypabolic_trajectory as ht
from hypabolic_trajectory.engine import TrajectoryEngine
from hypabolic_trajectory.errors import FATAL_UNKNOWN_OUTPUT_SCHEMA


def _empty_ir() -> ht.TrajectoryIR:
    cfg = ht.AppliedConfig(
        bounds=ht.AppliedBounds(
            tool_arguments_max_characters=None,
            tool_results_max_characters=None,
            tool_results_strategy="head-tail",
        ),
        filters=ht.AppliedFilters(tool_results="include"),
        group_id="g",
        base_byte_offset=0,
        partial=False,
    )
    return ht.TrajectoryIR(
        source=ht.TrajectorySource.PI,
        source_name="Pi",
        group_id="g",
        source_group_resolved=True,
        records=(),
        diagnostics=(),
        config=cfg,
        execution=ht.TrajectoryExecution(model_invocations=()),
    )


def test_trajectory_engine_exported_from_root() -> None:
    assert ht.TrajectoryEngine is TrajectoryEngine
    assert "TrajectoryEngine" in ht.__all__
    eng = ht.TrajectoryEngine.create_default()
    assert isinstance(eng, TrajectoryEngine)


def test_create_default_registers_tip_matrix_including_otel() -> None:
    eng = TrajectoryEngine.create_default()
    ir = _empty_ir()

    letta = eng.project(ir, ht.LETTA_TRAJECTORY_V1)
    assert letta == ht.project_letta(ir)

    canonical = eng.project(ir, ht.LETTA_CANONICAL_V1)
    assert canonical == ht.project_canonical(ir)

    hypabolic = eng.project(ir, ht.HYPABOLIC_TRAJECTORY_V1)
    assert hypabolic == ht.project_hypabolic(ir)

    openai = eng.project(ir, ht.OPENAI_CHAT_MESSAGES)
    assert openai == ht.project_openai(ir)
    assert isinstance(openai, list)

    jsonl = eng.project(ir, ht.JSONL_MINIMAL)
    assert jsonl == ht.project_minimal_jsonl(ir)
    assert isinstance(jsonl, str)

    otel = eng.project(ir, ht.OTEL_GENAI_SPANS_V1)
    assert otel == ht.project_otel_genai(ir)
    assert type(otel) is dict
    assert "spans" in otel
    assert "trace_id" in otel
    assert "schema_url" in otel


def test_project_unknown_schema_raises_unknown_output_schema() -> None:
    eng = TrajectoryEngine.create_default()
    ir = _empty_ir()
    try:
        eng.project(ir, "not-a-registered-schema")
        raise AssertionError("expected TrajectoryError")
    except ht.TrajectoryError as err:
        assert err.code == FATAL_UNKNOWN_OUTPUT_SCHEMA
        assert "not-a-registered-schema" in err.message
        assert "No output adapter is registered" in err.message


def test_add_output_adapter_duplicate_raises_value_error() -> None:
    eng = TrajectoryEngine.create_default()

    def _noop(_ir: ht.TrajectoryIR) -> ht.JsonObject:
        return {"ok": True}

    # Built-in schema already registered by create_default.
    try:
        eng.add_output_adapter(ht.OTEL_GENAI_SPANS_V1, _noop)
        raise AssertionError("expected ValueError")
    except ValueError as err:
        assert "already registered" in str(err)
        assert ht.OTEL_GENAI_SPANS_V1 in str(err)

    eng.add_output_adapter("custom-schema-v1", _noop)
    try:
        eng.add_output_adapter("custom-schema-v1", _noop)
        raise AssertionError("expected ValueError")
    except ValueError as err:
        assert "already registered" in str(err)


def test_engine_instances_are_independent() -> None:
    eng_a = TrajectoryEngine.create_default()
    eng_b = TrajectoryEngine.create_default()
    ir = _empty_ir()

    def _a(_ir: ht.TrajectoryIR) -> ht.JsonObject:
        return {"engine": "a"}

    def _b(_ir: ht.TrajectoryIR) -> ht.JsonObject:
        return {"engine": "b"}

    eng_a.add_output_adapter("shared-custom-id", _a)
    eng_b.add_output_adapter("shared-custom-id", _b)

    assert eng_a.project(ir, "shared-custom-id") == {"engine": "a"}
    assert eng_b.project(ir, "shared-custom-id") == {"engine": "b"}


def test_free_functions_unaffected_by_engine_mutations() -> None:
    eng = TrajectoryEngine.create_default()
    ir = _empty_ir()

    def _custom(_ir: ht.TrajectoryIR) -> ht.JsonObject:
        return {"hijacked": True}

    eng.add_output_adapter("free-fn-isolation-v1", _custom)
    assert eng.project(ir, "free-fn-isolation-v1") == {"hijacked": True}

    # Free projectors always use built-ins regardless of engine state.
    assert ht.project_letta(ir) == {"records": [], "diagnostics": []}
    assert ht.project_canonical(ir) == eng.project(ir, ht.LETTA_CANONICAL_V1)
    assert ht.project_openai(ir) == eng.project(ir, ht.OPENAI_CHAT_MESSAGES)
    assert ht.project_minimal_jsonl(ir) == eng.project(ir, ht.JSONL_MINIMAL)
    assert ht.project_otel_genai(ir) == eng.project(ir, ht.OTEL_GENAI_SPANS_V1)


def test_engine_normalize_to_ir_matches_free_function() -> None:
    eng = TrajectoryEngine.create_default()
    # Empty object is invalid_input for pi — same domain path as free function.
    req = ht.NormalizeRequest(source="pi", transcript=b"{}")
    try:
        eng.normalize_to_ir(req)
        raise AssertionError("expected TrajectoryError")
    except ht.TrajectoryError as eng_err:
        try:
            ht.normalize_to_ir(req)
            raise AssertionError("expected free normalize TrajectoryError")
        except ht.TrajectoryError as free_err:
            assert eng_err.code == free_err.code
            assert eng_err.message == free_err.message


def test_project_type_boundaries() -> None:
    eng = TrajectoryEngine.create_default()
    ir = _empty_ir()
    try:
        eng.project(ir, 123)  # type: ignore[arg-type]
        raise AssertionError("expected TypeError")
    except TypeError:
        pass
    try:
        eng.project("not-ir", ht.LETTA_TRAJECTORY_V1)  # type: ignore[arg-type]
        raise AssertionError("expected TypeError")
    except TypeError:
        pass


def test_normalize_convenience_methods_match_free_functions() -> None:
    """normalize_to_* compose normalize_to_ir + registered projectors."""
    from pathlib import Path

    case = (
        Path(__file__).resolve().parents[2]
        / "conformance"
        / "cases"
        / "pi"
        / "tool-calls"
        / "input.jsonl"
    )
    req = ht.NormalizeRequest(source="pi", transcript=case.read_bytes())
    eng = TrajectoryEngine.create_default()

    assert eng.normalize_to_letta(req) == ht.normalize_to_letta(req)
    assert eng.normalize_to_canonical(req) == ht.normalize_to_canonical(req)
    assert eng.normalize_to_hypabolic(req) == ht.normalize_to_hypabolic(req)

    # Bare engine without letta registered: valid IR then unknown_output_schema.
    bare = TrajectoryEngine()
    try:
        bare.normalize_to_letta(req)
        raise AssertionError("expected TrajectoryError")
    except ht.TrajectoryError as err:
        assert err.code == FATAL_UNKNOWN_OUTPUT_SCHEMA
