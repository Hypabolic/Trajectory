"""PY-03 unit: Diagnostic shape, fixed codes, TrajectoryError content-safety."""

from __future__ import annotations

import traceback

import pytest

import hypabolic_trajectory as ht
from hypabolic_trajectory import Diagnostic, TrajectoryError
from hypabolic_trajectory.diagnostics import (
    DIAG_TIMESTAMPS_INTERPOLATED,
    DIAG_TIMESTAMPS_SYNTHESIZED,
    DIAGNOSTIC_CODES,
    MSG_MODEL_SPAN_OMITTED,
)
from hypabolic_trajectory.errors import (
    FATAL_ERROR_CODES,
    FATAL_INVALID_INPUT,
    FATAL_UNKNOWN_SOURCE,
)


# ---------------------------------------------------------------------------
# Fixed codes (contracts/spec/diagnostics.md)
# ---------------------------------------------------------------------------

_CORE_DIAGNOSTIC_CODES = frozenset(
    {
        "invalid_json_line",
        "non_object_json_line",
        "injected_context_dropped",
        "noise_record_dropped",
        "sidechain_record_dropped",
        "unknown_semantic_record",
        "unknown_content_block",
        "tool_call_id_synthesized",
        "duplicate_tool_call_id",
        "orphan_tool_result",
        "duplicate_tool_result",
        "unknown_tool_name",
        "tool_arguments_reshaped",
        "tool_arguments_truncated",
        "tool_result_truncated",
        "timestamps_synthesized",
        "timestamps_interpolated",
    }
)

_CORE_FATAL_CODES = frozenset(
    {
        "invalid_input",
        "unknown_source",
        "unknown_output_schema",
        "missing_user_records",
        "missing_assistant_records",
        "invalid_normalized_transcript",
        "listing_unavailable",
        "source_group_conflict",
        "source_group_required",
    }
)


def test_diagnostic_codes_include_contract_set() -> None:
    assert _CORE_DIAGNOSTIC_CODES <= DIAGNOSTIC_CODES
    assert DIAG_TIMESTAMPS_SYNTHESIZED == "timestamps_synthesized"
    assert DIAG_TIMESTAMPS_INTERPOLATED == "timestamps_interpolated"


def test_fatal_error_codes_match_contract() -> None:
    assert FATAL_ERROR_CODES == _CORE_FATAL_CODES
    assert FATAL_INVALID_INPUT == "invalid_input"
    assert FATAL_UNKNOWN_SOURCE == "unknown_source"


def test_diagnostic_dataclass_shape() -> None:
    d = Diagnostic(code="noise_record_dropped", message="Dropped noise record.")
    assert d.code == "noise_record_dropped"
    assert d.message == "Dropped noise record."
    assert d.input_line is None
    assert d.record_index is None
    assert d.count is None

    d2 = Diagnostic(
        code="tool_arguments_truncated",
        message="Tool arguments truncated.",
        input_line=3,
        record_index=7,
        count=2,
    )
    assert d2.input_line == 3
    assert d2.record_index == 7
    assert d2.count == 2


def test_diagnostic_is_frozen_kw_only() -> None:
    d = Diagnostic(code="x", message="y")
    with pytest.raises(AttributeError):
        d.code = "z"  # type: ignore[misc]
    with pytest.raises(TypeError):
        Diagnostic("x", "y")  # type: ignore[misc]


def test_diagnostic_equality() -> None:
    a = Diagnostic(code="a", message="m", input_line=1)
    b = Diagnostic(code="a", message="m", input_line=1)
    c = Diagnostic(code="a", message="m", input_line=2)
    assert a == b
    assert a != c


def test_trajectory_error_str_repr_eq() -> None:
    err = TrajectoryError("invalid_input", "Input is invalid.")
    assert err.code == "invalid_input"
    assert err.message == "Input is invalid."
    assert str(err) == "invalid_input: Input is invalid."
    assert repr(err) == "TrajectoryError(code='invalid_input', message='Input is invalid.')"
    assert err == TrajectoryError("invalid_input", "Input is invalid.")
    assert err != TrajectoryError("unknown_source", "Input is invalid.")
    assert err != TrajectoryError("invalid_input", "other")
    assert err != object()


def test_trajectory_error_public_export() -> None:
    assert ht.TrajectoryError is TrajectoryError
    assert ht.Diagnostic is Diagnostic
    assert "TrajectoryError" in ht.__all__
    assert "Diagnostic" in ht.__all__
    # Optional alias must not appear in root __all__
    assert "TrajectoryDiagnostic" not in ht.__all__


def test_model_span_omitted_message_pin() -> None:
    assert MSG_MODEL_SPAN_OMITTED == (
        "Model span omitted because source-native timing or provider/model "
        "metadata is incomplete."
    )


def test_traceback_content_safety_from_none() -> None:
    """Translating low-level errors must clear cause/context so secrets never
    appear in public exception traceback or exception attributes.
    """
    from hypabolic_trajectory.errors import raise_trajectory_error

    secret = "SUPER_SECRET_TRANSCRIPT_PAYLOAD_xyzzy_do_not_leak"
    secret_path = "/Users/secret/agent/sessions/leaky.jsonl"

    def _translate() -> None:
        domain: TrajectoryError | None = None
        try:
            # Simulate parse/OS failure carrying transcript fragments.
            raise ValueError(f"parse failed near {secret} at {secret_path}")
        except ValueError:
            domain = TrajectoryError("invalid_input", "Input is invalid.")
        if domain is not None:
            # Raise outside except so __context__ is not retained.
            raise_trajectory_error(domain.code, domain.message)

    with pytest.raises(TrajectoryError) as excinfo:
        _translate()

    err = excinfo.value
    assert err.code == "invalid_input"
    assert err.message == "Input is invalid."
    assert err.__cause__ is None
    assert err.__context__ is None

    tb_text = traceback.format_exception(type(err), err, err.__traceback__)
    joined = "".join(tb_text)
    assert secret not in joined
    assert secret_path not in joined
    assert "ValueError" not in joined
    assert "leaky.jsonl" not in joined

    # Public str/repr remain content-safe (no secrets).
    assert secret not in str(err)
    assert secret not in repr(err)
    assert secret_path not in str(err)
    assert secret_path not in repr(err)


def test_format_ms_production_path_content_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production ``format_ms`` must not chain low-level exceptions into public TB.

    Patch the module-level ``_utc_from_unix_ms`` wrapper so a sentinel secret is
    raised, then assert the domain ``TrajectoryError`` has no cause/context and
    TB excludes the secret.
    """
    from hypabolic_trajectory import timestamps as ts

    secret = "SUPER_SECRET_TRANSCRIPT_PAYLOAD_xyzzy_fromtimestamp"

    def _boom(_milliseconds: int) -> object:
        raise OSError(f"platform failure carrying {secret}")

    monkeypatch.setattr(ts, "_utc_from_unix_ms", _boom)

    with pytest.raises(TrajectoryError) as excinfo:
        ts.format_ms(0)

    err = excinfo.value
    assert err.code == "invalid_input"
    assert err.message == "Timestamp is out of range."
    assert err.__cause__ is None
    assert err.__context__ is None
    joined = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    assert secret not in joined
    assert "OSError" not in joined
    assert secret not in str(err)
    assert secret not in repr(err)


def test_diagnostic_message_must_not_hold_transcript() -> None:
    """Unit guard: constructing a diagnostic with a secret in message is possible
    (callers must not do it); this documents the content-safety contract for
    producers — messages should be fixed policy text only.
    """
    d = Diagnostic(code="noise_record_dropped", message="Dropped noise record.")
    assert "transcript" not in d.message.lower()
    assert len(d.message) < 200
