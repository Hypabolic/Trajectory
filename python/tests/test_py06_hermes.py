"""PY-06-hermes unit tests: Hermes decode adapter + empty listing stub."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypabolic_trajectory.diagnostics import DIAG_INVALID_JSON_LINE
from hypabolic_trajectory.dto import NormalizeRequest, SourceContext
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import RecordKind, TrajectoryRole
from hypabolic_trajectory.listing.hermes import HermesTrajectoryLister, _resolve_store_path
from hypabolic_trajectory.listing.protocol import get_lister, registered_lister_names
from hypabolic_trajectory.ir.models import AppliedBounds, AppliedConfig, AppliedFilters
from hypabolic_trajectory.normalize.core import normalize_decoded
from hypabolic_trajectory.project.core import project_canonical, project_letta
from hypabolic_trajectory.sources.hermes import (
    HERMES_SOURCE_ADAPTER,
    HermesSourceAdapter,
    decode_hermes,
)
from hypabolic_trajectory.sources.protocol import get_source_adapter, registered_source_names

# Repo root: python/tests/ -> python/ -> repo
_REPO_ROOT = Path(__file__).resolve().parents[2]
_HERMES_CASES = _REPO_ROOT / "conformance" / "cases" / "hermes"


def _read_case(name: str) -> bytes:
    return (_HERMES_CASES / name / "input.json").read_bytes()


def _read_expected_letta(name: str) -> dict:
    return json.loads((_HERMES_CASES / name / "expected.letta.json").read_text(encoding="utf-8"))


def _read_expected_canonical(name: str) -> dict:
    return json.loads(
        (_HERMES_CASES / name / "expected.canonical.json").read_text(encoding="utf-8")
    )


def _ctx() -> SourceContext:
    return SourceContext()


def _cfg() -> AppliedConfig:
    return AppliedConfig(
        bounds=AppliedBounds(
            tool_arguments_max_characters=20_000,
            tool_results_max_characters=2_500,
            tool_results_strategy="head-tail",
        ),
        filters=AppliedFilters(tool_results="include"),
        group_id=None,
        base_byte_offset=0,
        partial=False,
    )


# ---------------------------------------------------------------------------
# Registry registration (import-time under export owner)
# ---------------------------------------------------------------------------


def test_hermes_adapter_registered_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    assert "hermes" in registered_source_names()
    adapter = get_source_adapter("hermes")
    assert adapter is not None
    assert adapter.source.value == "hermes"
    assert isinstance(adapter, HermesSourceAdapter)
    assert adapter is HERMES_SOURCE_ADAPTER


def test_hermes_lister_registered_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    assert "hermes" in registered_lister_names()
    lister = get_lister("hermes")
    assert lister is not None
    assert lister.source.value == "hermes"


def test_runtime_capabilities_claim_writer_owns_hermes_source() -> None:
    """Hermes source claim is owned by PY-10b-sources-hermes (verify-green).

    PY-06-hermes only registered the adapter; claim-writer issues own membership
    in runtime-capabilities.json. After PY-10b-sources-hermes, hermes is claimed.
    """
    caps_path = _REPO_ROOT / "python" / "runtime-capabilities.json"
    caps = json.loads(caps_path.read_text(encoding="utf-8"))
    assert caps["runtime"] == "python"
    claimed = caps.get("sources") or []
    assert "hermes" in claimed


# ---------------------------------------------------------------------------
# Decode: fixture-driven vectors (normalize → letta vs golden)
# ---------------------------------------------------------------------------


def test_tool_calls_decode_and_normalize_letta() -> None:
    import hypabolic_trajectory as ht

    transcript = _read_case("tool-calls")
    decoded = HERMES_SOURCE_ADAPTER.decode(transcript, source_context=_ctx())
    assert decoded.source.value == "hermes"
    assert decoded.source_name == "hermes"
    assert decoded.group_id == "hermes-session-0001"
    assert decoded.group_resolved is True
    assert decoded.cwd == "/workspace/demo"
    assert decoded.model == "gpt-5.2"
    assert decoded.created_at_ms == 1_783_000_000_000
    # user + reasoning + tool-call + tool-result + assistant
    assert len(decoded.events) == 5
    assert decoded.events[0].kind == "message"
    assert decoded.events[0].role is TrajectoryRole.USER
    assert decoded.events[1].kind == "reasoning"
    assert decoded.events[2].kind == "tool-call"
    assert decoded.events[2].tool_call_id == "call_hermes_1"
    assert decoded.events[2].tool_name == "terminal"
    assert decoded.events[2].arguments_json == '{"command":"pwd"}'
    assert decoded.events[3].kind == "tool-result"
    assert decoded.events[4].kind == "message"
    assert decoded.events[4].role is TrajectoryRole.ASSISTANT

    ir = ht.normalize_to_ir(NormalizeRequest(source="hermes", transcript=transcript))
    assert project_letta(ir) == _read_expected_letta("tool-calls")
    assert project_canonical(ir) == _read_expected_canonical("tool-calls")


def test_array_envelope_parity_matches_session_envelope() -> None:
    """Bare message-row array is observationally equivalent to envelope form."""
    import hypabolic_trajectory as ht

    array_bytes = _read_case("array-envelope-parity")
    envelope_bytes = _read_case("tool-calls")

    array_ir = ht.normalize_to_ir(
        NormalizeRequest(source="hermes", transcript=array_bytes)
    )
    envelope_ir = ht.normalize_to_ir(
        NormalizeRequest(source="hermes", transcript=envelope_bytes)
    )
    # Array form resolves group from session_id on rows; no cwd/model meta.
    assert array_ir.group_id == "hermes-session-0001"
    assert array_ir.source_group_resolved is True
    # Body timestamps + roles match envelope body (meta may differ on cwd/model).
    array_body = [r for r in array_ir.records if r.kind is not RecordKind.META]
    envelope_body = [r for r in envelope_ir.records if r.kind is not RecordKind.META]
    assert len(array_body) == len(envelope_body)
    for a, e in zip(array_body, envelope_body, strict=True):
        assert a.role == e.role
        assert a.content == e.content
        assert a.timestamp_ms == e.timestamp_ms
        if a.kind is RecordKind.ASSISTANT_TOOL_CALLS:
            assert a.tool_calls[0].id == e.tool_calls[0].id
            assert a.tool_calls[0].name == e.tool_calls[0].name
            assert a.tool_calls[0].arguments_json == e.tool_calls[0].arguments_json

    assert project_letta(array_ir) == _read_expected_letta("array-envelope-parity")
    assert project_canonical(array_ir) == _read_expected_canonical("array-envelope-parity")


def test_cleanup_soft_delete_id_order_adopted_tool_ids_orphan() -> None:
    import hypabolic_trajectory as ht

    transcript = _read_case("cleanup")
    decoded = decode_hermes(transcript)
    # Soft-deleted active=0 user row dropped; remaining ordered by id.
    roles = [e.role for e in decoded.events]
    assert TrajectoryRole.USER in roles
    # Adopted tool-call id from following tool row when tool_calls lacked id.
    tool_calls = [e for e in decoded.events if e.kind == "tool-call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_call_id == "call_adopted_1"
    assert tool_calls[0].tool_name == "screenshot_inspect"
    # \0json: content prefix expands to text blocks (image_url ignored).
    user_events = [e for e in decoded.events if e.role is TrajectoryRole.USER]
    assert user_events[0].content == "Describe this screenshot."
    # Whitespace-only reasoning dropped.
    assert all(
        e.content is None or e.content.strip() for e in decoded.events if e.kind == "reasoning"
    )

    ir = ht.normalize_to_ir(NormalizeRequest(source="hermes", transcript=transcript))
    projected = project_letta(ir)
    assert projected == _read_expected_letta("cleanup")
    assert project_canonical(ir) == _read_expected_canonical("cleanup")
    codes = [d["code"] for d in projected["diagnostics"]]
    assert "orphan_tool_result" in codes


def test_missing_assistant_fatal() -> None:
    import hypabolic_trajectory as ht

    transcript = _read_case("missing-assistant")
    with pytest.raises(TrajectoryError) as ei:
        ht.normalize_to_ir(NormalizeRequest(source="hermes", transcript=transcript))
    assert ei.value.code == "missing_assistant_records"
    assert "assistant" in ei.value.message


def test_invalid_transcript_shapes() -> None:
    with pytest.raises(TrajectoryError) as ei:
        decode_hermes(b"not-json")
    assert ei.value.code == FATAL_INVALID_INPUT
    assert "Hermes transcript" in ei.value.message

    with pytest.raises(TrajectoryError) as ei2:
        decode_hermes(b'{"no_messages": true}')
    assert ei2.value.code == FATAL_INVALID_INPUT

    with pytest.raises(TrajectoryError) as ei3:
        decode_hermes(b'[1, 2, 3]')
    assert ei3.value.code == FATAL_INVALID_INPUT

    # Content-safety: no transcript fragment in public message.
    with pytest.raises(TrajectoryError) as ei4:
        decode_hermes(b'{"messages": "secret-payload"}')
    assert "secret-payload" not in ei4.value.message
    assert ei4.value.__cause__ is None
    assert ei4.value.__context__ is None

    # Strict JSON: reject non-standard NaN/Infinity (peer parsers).
    with pytest.raises(TrajectoryError) as ei5:
        decode_hermes(b'[{"role":"user","content":"x","timestamp":NaN}]')
    assert ei5.value.code == FATAL_INVALID_INPUT


def test_undecodable_tool_calls_nan_constant_diagnostic() -> None:
    payload = json.dumps(
        [
            {
                "id": 1,
                "session_id": "s",
                "role": "user",
                "content": "hi",
                "timestamp": 1783000001.0,
                "active": 1,
            },
            {
                "id": 2,
                "session_id": "s",
                "role": "assistant",
                "content": "ok",
                "tool_calls": "[{\"name\": \"t\", \"arguments\": NaN}]",
                "timestamp": 1783000002.0,
                "active": 1,
            },
        ]
    ).encode("utf-8")
    # The outer transcript is standard JSON (tool_calls is a string). The inner
    # string payload contains a non-standard constant and must be rejected as
    # undecodable tool_calls, not raise TypeError later.
    decoded = decode_hermes(payload)
    assert any(d.code == DIAG_INVALID_JSON_LINE for d in decoded.diagnostics)
    assert not any(e.kind == "tool-call" for e in decoded.events)


def test_undecodable_tool_calls_diagnostic() -> None:
    payload = json.dumps(
        [
            {
                "id": 1,
                "session_id": "s",
                "role": "user",
                "content": "hi",
                "timestamp": 1783000001.0,
                "active": 1,
            },
            {
                "id": 2,
                "session_id": "s",
                "role": "assistant",
                "content": "ok",
                "tool_calls": "not-json{",
                "timestamp": 1783000002.0,
                "active": 1,
            },
        ]
    ).encode("utf-8")
    decoded = decode_hermes(payload)
    assert any(d.code == DIAG_INVALID_JSON_LINE for d in decoded.diagnostics)
    assert any("tool_calls" in d.message for d in decoded.diagnostics)
    # Assistant message content still emitted; bad tool_calls skipped.
    assert any(e.kind == "message" and e.role is TrajectoryRole.ASSISTANT for e in decoded.events)
    assert not any(e.kind == "tool-call" for e in decoded.events)


def test_native_ids_preferred_for_identity() -> None:
    transcript = _read_case("tool-calls")
    decoded = decode_hermes(transcript)
    for event in decoded.events:
        assert event.native_record_id is not None
        assert event.source_anchor_kind is None
    ir = normalize_decoded(decoded, config=_cfg())
    body = [r for r in ir.records if r.kind is not RecordKind.META]
    assert body[0].provenance.native_record_id == "101"
    assert body[0].provenance.source_identity_kind.value == "native"


def test_inactive_false_and_zero_dropped() -> None:
    payload = json.dumps(
        [
            {
                "id": 1,
                "session_id": "s",
                "role": "user",
                "content": "keep",
                "timestamp": 1783000001.0,
                "active": 1,
            },
            {
                "id": 2,
                "session_id": "s",
                "role": "user",
                "content": "drop-zero",
                "timestamp": 1783000002.0,
                "active": 0,
            },
            {
                "id": 3,
                "session_id": "s",
                "role": "user",
                "content": "drop-false",
                "timestamp": 1783000003.0,
                "active": False,
            },
            {
                "id": 4,
                "session_id": "s",
                "role": "assistant",
                "content": "reply",
                "timestamp": 1783000004.0,
                "active": 1,
            },
        ]
    ).encode("utf-8")
    decoded = decode_hermes(payload)
    contents = [e.content for e in decoded.events if e.kind == "message"]
    assert "drop-zero" not in contents
    assert "drop-false" not in contents
    assert "keep" in contents
    assert "reply" in contents


def test_decode_requires_bytes() -> None:
    with pytest.raises(TypeError):
        HERMES_SOURCE_ADAPTER.decode("{}", source_context=_ctx())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Empty listing policy
# ---------------------------------------------------------------------------


def test_list_always_empty_for_any_root(tmp_path: Path) -> None:
    lister = get_lister("hermes")
    assert lister is not None

    # Missing store
    page = lister.list_page(root=tmp_path / "missing", cursor=None, limit=50)
    assert page.items == ()
    assert page.next_cursor is None

    # Directory root (would contain state.db)
    (tmp_path / "state.db").write_bytes(b"not-a-real-sqlite")
    page2 = lister.list_page(root=tmp_path, cursor=None, limit=50)
    assert page2.items == ()
    assert page2.next_cursor is None

    # Explicit .db path still empty (no SQLite reader in core)
    page3 = lister.list_page(root=tmp_path / "state.db", cursor=None, limit=10)
    assert page3.items == ()
    assert page3.next_cursor is None


def test_list_invalid_limit_raises(tmp_path: Path) -> None:
    lister = get_lister("hermes")
    assert lister is not None
    with pytest.raises(TrajectoryError) as ei:
        lister.list_page(root=tmp_path, cursor=None, limit=0)
    assert ei.value.code == FATAL_INVALID_INPUT


def test_list_invalid_cursor_raises(tmp_path: Path) -> None:
    lister = get_lister("hermes")
    assert lister is not None
    with pytest.raises(TrajectoryError) as ei:
        lister.list_page(root=tmp_path, cursor="not-a-cursor", limit=50)
    assert ei.value.code == FATAL_INVALID_INPUT


def test_list_valid_cursor_on_empty_still_empty(tmp_path: Path) -> None:
    from hypabolic_trajectory.listing.common import encode_cursor

    lister = get_lister("hermes")
    assert lister is not None
    cursor = encode_cursor("any", 0)
    page = lister.list_page(root=tmp_path, cursor=cursor, limit=50)
    assert page.items == ()
    assert page.next_cursor is None


def test_resolve_store_path_rules(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    assert _resolve_store_path(db) == db
    assert _resolve_store_path(tmp_path) == tmp_path / "state.db"
    assert _resolve_store_path(str(tmp_path / "other.DB")) == tmp_path / "other.DB"
    with pytest.raises(TypeError):
        _resolve_store_path(123)  # type: ignore[arg-type]


def test_lister_class_source_property() -> None:
    assert HermesTrajectoryLister().source.value == "hermes"
