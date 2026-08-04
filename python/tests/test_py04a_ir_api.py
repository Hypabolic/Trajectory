"""PY-04a: IR models, DTO freezes, protocols, normalize_to_ir skeleton, exports."""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest

import hypabolic_trajectory as ht
from hypabolic_trajectory import ir as ir_mod
from hypabolic_trajectory.engine import TrajectoryEngine
from hypabolic_trajectory.listing.protocol import (
    TrajectoryLister,
    get_lister,
    register_lister,
    registered_lister_names,
)
from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedModelInvocation,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import (
    SourceAdapter,
    get_source_adapter,
    register_source_adapter,
    registered_source_names,
)

# Exhaustive root __all__ names landed under PY-04a export owner (TrajectoryEngine
# deferred to PY-12 per intermediate-build pin).
_EXPECTED_ROOT_ALL = frozenset(
    {
        "NORMALIZER_CONTRACT_VERSION",
        "PACKAGE_VERSION",
        "__version__",
        "WIRE_PACKAGE_VERSION",
        "LETTA_TRAJECTORY_V1",
        "LETTA_CANONICAL_V1",
        "HYPABOLIC_TRAJECTORY_V1",
        "OPENAI_CHAT_MESSAGES",
        "JSONL_MINIMAL",
        "OTEL_GENAI_SPANS_V1",
        "SCHEMA_IDS",
        "SchemaId",
        "IMPLEMENTED_SOURCES",
        "TrajectorySource",
        "JsonPrimitive",
        "JsonValue",
        "JsonObject",
        "SourceContext",
        "ToolArgumentBounds",
        "ToolResultBounds",
        "Bounds",
        "Filters",
        "NormalizeOptions",
        "NormalizeRequest",
        "TrajectoryListing",
        "TrajectoryListingPage",
        "Diagnostic",
        "TrajectoryError",
        "normalize_to_ir",
        "normalize_to_letta",
        "normalize_to_canonical",
        "normalize_to_hypabolic",
        "project_letta",
        "project_canonical",
        "project_hypabolic",
        "project_openai",
        "project_minimal_jsonl",
        "project_otel_genai",
        "list_trajectories",
        "serialize_projection",
        "canonical_json",
        "TrajectoryIR",
        "IrRecord",
        "RecordKind",
        "TrajectoryRole",
        "ToolCall",
        "Provenance",
        "SourceIdentityKind",
        "SourceAnchorKind",
        "RecordHashes",
        "AppliedConfig",
        "AppliedBounds",
        "AppliedFilters",
        "TrajectoryExecution",
        "ModelInvocation",
        "ModelTokenUsage",
        "WorkflowInvocation",
    }
)

_EXPECTED_IR_ALL = frozenset(
    {
        "TrajectoryIR",
        "IrRecord",
        "RecordKind",
        "TrajectoryRole",
        "ToolCall",
        "Provenance",
        "SourceIdentityKind",
        "SourceAnchorKind",
        "RecordHashes",
        "AppliedConfig",
        "AppliedBounds",
        "AppliedFilters",
        "TrajectoryExecution",
        "ModelInvocation",
        "ModelTokenUsage",
        "WorkflowInvocation",
        "Diagnostic",
    }
)


# ---------------------------------------------------------------------------
# SchemaId Literal-only (must not collapse to str)
# ---------------------------------------------------------------------------


def test_schema_id_is_literal_only() -> None:
    args = get_args(ht.SchemaId)
    assert set(args) == set(ht.SCHEMA_IDS)
    assert all(isinstance(a, str) for a in args)
    assert "letta-trajectory-v1" in args
    # Not a bare Union with str that would collapse typing.
    assert get_origin(ht.SchemaId) is not type(str)


# ---------------------------------------------------------------------------
# Root / ir exports
# ---------------------------------------------------------------------------


def test_root_all_exact_freeze() -> None:
    assert set(ht.__all__) == _EXPECTED_ROOT_ALL
    for n in _EXPECTED_ROOT_ALL:
        assert hasattr(ht, n), n


def test_ir_all_stable_subset() -> None:
    assert set(ir_mod.__all__) == _EXPECTED_IR_ALL
    for n in _EXPECTED_IR_ALL:
        assert hasattr(ir_mod, n)


def test_trajectory_engine_not_in_root_all_until_py12() -> None:
    assert "TrajectoryEngine" not in ht.__all__


def test_free_function_signatures_frozen() -> None:
    sig = inspect.signature(ht.normalize_to_ir)
    assert list(sig.parameters) == ["request"]
    hints = get_type_hints(ht.normalize_to_ir)
    assert hints["request"] is ht.NormalizeRequest
    assert hints["return"] is ht.TrajectoryIR

    list_sig = inspect.signature(ht.list_trajectories)
    assert list(list_sig.parameters) == ["source", "root", "cursor", "limit"]
    assert list_sig.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY

    eng_add = inspect.signature(TrajectoryEngine.add_output_adapter)
    eng_proj = inspect.signature(TrajectoryEngine.project)
    assert "schema_id" in eng_add.parameters
    assert "schema_id" in eng_proj.parameters
    # Annotations preserved as SchemaId | str (string form under postponed eval ok).
    add_ann = eng_add.parameters["schema_id"].annotation
    proj_ann = eng_proj.parameters["schema_id"].annotation
    assert "SchemaId" in str(add_ann) and "str" in str(add_ann)
    assert "SchemaId" in str(proj_ann) and "str" in str(proj_ann)


def test_dto_field_names_frozen() -> None:
    assert [f.name for f in dataclasses.fields(ht.NormalizeRequest)] == [
        "source",
        "transcript",
        "source_context",
        "options",
    ]
    assert [f.name for f in dataclasses.fields(ht.SourceContext)] == [
        "group_id",
        "base_byte_offset",
        "partial",
    ]
    assert [f.name for f in dataclasses.fields(ht.TrajectoryIR)] == [
        "source",
        "source_name",
        "group_id",
        "source_group_resolved",
        "records",
        "diagnostics",
        "config",
        "execution",
        "producer_version",
    ]
    assert [f.name for f in dataclasses.fields(DecodedSession)] == [
        "source",
        "source_name",
        "group_id",
        "group_resolved",
        "cwd",
        "git_branch",
        "model",
        "producer_version",
        "created_at_ms",
        "created_at_precise",
        "events",
        "model_invocations",
        "diagnostics",
    ]
    assert [f.name for f in dataclasses.fields(DecodedEvent)] == [
        "kind",
        "role",
        "content",
        "tool_call_id",
        "tool_name",
        "arguments_json",
        "is_error",
        "input_line",
        "timestamp_ms",
        "timestamp_precise",
        "model",
        "producer_version",
        "native_record_id",
        "source_sequence",
        "source_offset",
        "source_anchor_kind",
        "component_index",
    ]


# ---------------------------------------------------------------------------
# DTO construction (no domain validation at construct time)
# ---------------------------------------------------------------------------


def test_dto_defaults_and_frozen() -> None:
    req = ht.NormalizeRequest(source="pi", transcript=b"{}")
    assert req.source == "pi"
    assert req.source_context.base_byte_offset == 0
    assert req.options.bounds.tool_arguments.max_characters == 20_000
    assert req.options.bounds.tool_results.max_characters == 2_500
    assert req.options.bounds.tool_results.strategy == "head-tail"
    assert req.options.filters.tool_results == "include"
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.source = "codex"  # type: ignore[misc]


def test_dto_allows_out_of_range_ints_at_construction() -> None:
    bad = ht.NormalizeRequest(
        source="pi",
        transcript=b"x",
        source_context=ht.SourceContext(base_byte_offset=2**63),
        options=ht.NormalizeOptions(
            bounds=ht.Bounds(
                tool_arguments=ht.ToolArgumentBounds(max_characters=0),
                tool_results=ht.ToolResultBounds(max_characters=-1),
            )
        ),
    )
    assert bad.source_context.base_byte_offset == 2**63


def test_listing_dto() -> None:
    page = ht.TrajectoryListingPage(
        items=(ht.TrajectoryListing(id="a", path="/tmp/a"),),
        next_cursor=None,
    )
    assert page.next_cursor is None
    assert page.items[0].id == "a"


# ---------------------------------------------------------------------------
# IR models
# ---------------------------------------------------------------------------


def test_trajectory_ir_source_is_enum() -> None:
    cfg = ht.AppliedConfig(
        bounds=ht.AppliedBounds(
            tool_arguments_max_characters=20_000,
            tool_results_max_characters=2_500,
            tool_results_strategy="head-tail",
        ),
        filters=ht.AppliedFilters(tool_results="include"),
        group_id="g",
        base_byte_offset=0,
        partial=False,
    )
    ir = ht.TrajectoryIR(
        source=ht.TrajectorySource.PI,
        source_name="Pi",
        group_id="g",
        source_group_resolved=True,
        records=(),
        diagnostics=(),
        config=cfg,
        execution=ht.TrajectoryExecution(model_invocations=()),
    )
    assert ir.source is ht.TrajectorySource.PI
    assert ir.source.value == "pi"
    assert isinstance(ir.source, ht.TrajectorySource)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ir.group_id = "x"  # type: ignore[misc]


def test_ir_enums_wire_values() -> None:
    assert ht.RecordKind.ASSISTANT_TOOL_CALLS == "assistant_tool_calls"
    assert ht.TrajectoryRole.USER == "user"
    assert ht.SourceIdentityKind.NATIVE == "native"
    assert ht.SourceAnchorKind.BYTE == "byte"


# ---------------------------------------------------------------------------
# Decode seam + protocols
# ---------------------------------------------------------------------------


def test_decoded_session_and_source_adapter_protocol() -> None:
    class _Adapter:
        @property
        def source(self) -> ht.TrajectorySource:
            return ht.TrajectorySource.PI

        def decode(
            self,
            transcript: bytes,
            *,
            source_context: ht.SourceContext,
        ) -> DecodedSession:
            _ = (transcript, source_context)
            return DecodedSession(
                source=ht.TrajectorySource.PI,
                source_name="Pi",
                group_resolved=False,
                events=(
                    DecodedEvent(
                        kind="message",
                        role=ht.TrajectoryRole.USER,
                        content="hi",
                        component_index=0,
                    ),
                ),
                model_invocations=(),
                diagnostics=(),
            )

    adapter: SourceAdapter = _Adapter()
    from hypabolic_trajectory.sources import protocol as proto

    prior = proto._ADAPTERS.get("pi")
    register_source_adapter(adapter)
    try:
        assert "pi" in registered_source_names()
        got = get_source_adapter("pi")
        assert got is not None
        session = got.decode(b"{}", source_context=ht.SourceContext())
        assert session.source is ht.TrajectorySource.PI
        assert session.events[0].kind == "message"
        assert isinstance(session.model_invocations, tuple)
        # Protocol method signature freeze
        decode_sig = inspect.signature(SourceAdapter.decode)
        assert list(decode_sig.parameters) == ["self", "transcript", "source_context"]
        assert decode_sig.parameters["source_context"].kind is inspect.Parameter.KEYWORD_ONLY
    finally:
        if prior is None:
            proto._ADAPTERS.pop("pi", None)
        else:
            proto._ADAPTERS["pi"] = prior


def test_trajectory_lister_protocol() -> None:
    class _Lister:
        @property
        def source(self) -> ht.TrajectorySource:
            return ht.TrajectorySource.CODEX

        def list_page(
            self,
            *,
            root: str | Path,
            cursor: str | None,
            limit: int,
        ) -> ht.TrajectoryListingPage:
            _ = (root, cursor, limit)
            return ht.TrajectoryListingPage(items=(), next_cursor=None)

    lister: TrajectoryLister = _Lister()
    from hypabolic_trajectory.listing import protocol as lproto

    prior = lproto._LISTERS.get("codex")
    register_lister(lister)
    try:
        assert "codex" in registered_lister_names()
        got = get_lister("codex")
        assert got is not None
        assert got.list_page(root="/tmp", cursor=None, limit=10).next_cursor is None
        list_sig = inspect.signature(TrajectoryLister.list_page)
        assert list(list_sig.parameters) == ["self", "root", "cursor", "limit"]
        assert all(
            p.kind is inspect.Parameter.KEYWORD_ONLY
            for name, p in list_sig.parameters.items()
            if name != "self"
        )
    finally:
        if prior is None:
            lproto._LISTERS.pop("codex", None)
        else:
            lproto._LISTERS["codex"] = prior


def test_decoded_model_invocation_fields() -> None:
    m = DecodedModelInvocation(
        native_record_id="n1",
        source_offset=10,
        input_tokens=1,
        output_tokens=2,
    )
    assert m.source_offset == 10
    assert m.total_tokens is None


# ---------------------------------------------------------------------------
# normalize_to_ir skeleton — entry validation
# ---------------------------------------------------------------------------


def test_normalize_to_ir_callable_unknown_source() -> None:
    with pytest.raises(ht.TrajectoryError) as ei:
        ht.normalize_to_ir(ht.NormalizeRequest(source="nope", transcript=b""))
    assert ei.value.code == "unknown_source"
    assert ei.value.__cause__ is None
    assert ei.value.__context__ is None


def test_encode_failure_has_no_exception_context() -> None:
    # Lone surrogate cannot encode as UTF-8 strict; domain error must be content-safe.
    bad = "\ud800"
    with pytest.raises(ht.TrajectoryError) as ei:
        ht.normalize_to_ir(ht.NormalizeRequest(source="pi", transcript=bad))
    assert ei.value.code == "invalid_input"
    assert ei.value.__cause__ is None
    assert ei.value.__context__ is None
    assert bad not in ei.value.message
    assert bad not in repr(ei.value)


def test_normalize_to_ir_enum_source_skeleton() -> None:
    with pytest.raises(ht.TrajectoryError) as ei:
        ht.normalize_to_ir(
            ht.NormalizeRequest(source=ht.TrajectorySource.PI, transcript=b"{}")
        )
    assert ei.value.code == "invalid_input"


def test_normalize_to_ir_rejects_out_of_range_base_byte_offset() -> None:
    with pytest.raises(ht.TrajectoryError) as ei:
        ht.normalize_to_ir(
            ht.NormalizeRequest(
                source="pi",
                transcript=b"{}",
                source_context=ht.SourceContext(base_byte_offset=2**63),
            )
        )
    assert ei.value.code == "invalid_input"


def test_normalize_to_ir_rejects_bool_base_byte_offset() -> None:
    with pytest.raises(TypeError):
        ht.normalize_to_ir(
            ht.NormalizeRequest(
                source="pi",
                transcript=b"{}",
                source_context=ht.SourceContext(base_byte_offset=True),  # type: ignore[arg-type]
            )
        )


def test_normalize_to_ir_rejects_invalid_argument_bounds() -> None:
    for bad in (0, 1, -5):
        with pytest.raises(ht.TrajectoryError) as ei:
            ht.normalize_to_ir(
                ht.NormalizeRequest(
                    source="pi",
                    transcript=b"{}",
                    options=ht.NormalizeOptions(
                        bounds=ht.Bounds(
                            tool_arguments=ht.ToolArgumentBounds(max_characters=bad),
                        )
                    ),
                )
            )
        assert ei.value.code == "invalid_input"


def test_normalize_to_ir_rejects_invalid_result_bounds() -> None:
    with pytest.raises(ht.TrajectoryError) as ei:
        ht.normalize_to_ir(
            ht.NormalizeRequest(
                source="pi",
                transcript=b"{}",
                options=ht.NormalizeOptions(
                    bounds=ht.Bounds(
                        tool_results=ht.ToolResultBounds(max_characters=0),
                    )
                ),
            )
        )
    assert ei.value.code == "invalid_input"


def test_normalize_to_ir_accepts_none_bounds_then_skeleton() -> None:
    with pytest.raises(ht.TrajectoryError) as ei:
        ht.normalize_to_ir(
            ht.NormalizeRequest(
                source="pi",
                transcript="{}",
                options=ht.NormalizeOptions(
                    bounds=ht.Bounds(
                        tool_arguments=ht.ToolArgumentBounds(max_characters=None),
                        tool_results=ht.ToolResultBounds(max_characters=None),
                    )
                ),
            )
        )
    assert ei.value.code == "invalid_input"


def test_normalize_to_ir_typeerror_on_wrong_transcript_type() -> None:
    with pytest.raises(TypeError):
        ht.normalize_to_ir(
            ht.NormalizeRequest(source="pi", transcript=123)  # type: ignore[arg-type]
        )


def test_normalize_to_ir_typeerror_before_unknown_source_on_bad_transcript() -> None:
    # Mixed invalid: bad transcript type + unknown source → TypeError first.
    with pytest.raises(TypeError):
        ht.normalize_to_ir(
            ht.NormalizeRequest(source="nope", transcript=123)  # type: ignore[arg-type]
        )


def test_normalize_to_ir_typeerror_on_non_bool_partial() -> None:
    with pytest.raises(TypeError):
        ht.normalize_to_ir(
            ht.NormalizeRequest(
                source="pi",
                transcript=b"{}",
                source_context=ht.SourceContext(partial=1),  # type: ignore[arg-type]
            )
        )


# ---------------------------------------------------------------------------
# Free-function / engine binding isolation pin
# ---------------------------------------------------------------------------


def test_engine_add_output_adapter_type_boundary() -> None:
    eng = TrajectoryEngine.create_default()
    with pytest.raises(TypeError):
        eng.add_output_adapter(123, lambda _ir: {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        eng.add_output_adapter("ok", "not-callable")  # type: ignore[arg-type]


def test_engine_custom_projector_isolation_from_free_functions() -> None:
    eng_a = TrajectoryEngine.create_default()
    eng_b = TrajectoryEngine.create_default()

    def _fake(_ir: ht.TrajectoryIR) -> ht.JsonObject:
        return {"custom": True}

    # Register under the built-in letta schema id so free project_letta
    # would share the lookup key if isolation were broken.
    eng_a.add_output_adapter(ht.LETTA_TRAJECTORY_V1, _fake)
    with pytest.raises(ValueError):
        eng_a.add_output_adapter(ht.LETTA_TRAJECTORY_V1, _fake)

    # Minimal IR for projection dispatch (no normalize behaviour required).
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
    ir = ht.TrajectoryIR(
        source=ht.TrajectorySource.PI,
        source_name="Pi",
        group_id="g",
        source_group_resolved=True,
        records=(),
        diagnostics=(),
        config=cfg,
        execution=ht.TrajectoryExecution(model_invocations=()),
    )

    # Custom adapter works only on the mutated engine.
    assert eng_a.project(ir, ht.LETTA_TRAJECTORY_V1) == {"custom": True}
    # Second engine does not see the custom adapter.
    with pytest.raises(ht.TrajectoryError):
        eng_b.project(ir, ht.LETTA_TRAJECTORY_V1)
    # Free project_letta never observes engine mutations (isolation pin).
    with pytest.raises(ht.TrajectoryError) as ei:
        ht.project_letta(ir)
    assert ei.value.code == "invalid_input"

    # Engine normalize uses the same free-function built-in path.
    with pytest.raises(ht.TrajectoryError) as ei2:
        eng_a.normalize_to_ir(ht.NormalizeRequest(source="pi", transcript=b"{}"))
    assert ei2.value.code == "invalid_input"
