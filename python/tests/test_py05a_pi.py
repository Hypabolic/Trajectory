"""PY-05a unit tests: Pi decode-only adapter + Pi lister registry registration."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hypabolic_trajectory.diagnostics import (
    DIAG_INVALID_JSON_LINE,
    DIAG_NON_OBJECT_JSON_LINE,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.listing.common import decode_cursor
from hypabolic_trajectory.listing.protocol import get_lister, registered_lister_names
from hypabolic_trajectory.sources.protocol import (
    get_source_adapter,
    registered_source_names,
)
from hypabolic_trajectory.timestamps import format_ms

# Repo root: python/tests/ -> python/ -> repo
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PI_CASES = _REPO_ROOT / "conformance" / "cases" / "pi"
_PI_STORE = _REPO_ROOT / "conformance" / "stores" / "pi-pagination"
_SRC = _REPO_ROOT / "python" / "src"


def _read_case(name: str) -> bytes:
    return (_PI_CASES / name / "input.jsonl").read_bytes()


def _ctx() -> SourceContext:
    return SourceContext()


def _pi_adapter():
    """Lazy import so registration tests are not polluted by direct module load."""
    from hypabolic_trajectory.sources.pi import PI_SOURCE_ADAPTER

    return PI_SOURCE_ADAPTER


# ---------------------------------------------------------------------------
# Registry registration (import-time under export owner)
# ---------------------------------------------------------------------------


def test_pi_registered_on_clean_package_import() -> None:
    """Fresh interpreter: only ``import hypabolic_trajectory`` must register pi.

    Avoids false greens from ``from ...sources.pi import ...`` side effects.
    """
    script = (
        "import hypabolic_trajectory\n"
        "from hypabolic_trajectory.sources.protocol import registered_source_names\n"
        "from hypabolic_trajectory.listing.protocol import registered_lister_names\n"
        "from hypabolic_trajectory.sources.protocol import get_source_adapter\n"
        "from hypabolic_trajectory.listing.protocol import get_lister\n"
        "assert 'pi' in registered_source_names(), registered_source_names()\n"
        "assert 'pi' in registered_lister_names(), registered_lister_names()\n"
        "assert get_source_adapter('pi') is not None\n"
        "assert get_lister('pi') is not None\n"
        "assert get_source_adapter('pi').source.value == 'pi'\n"
        "assert get_lister('pi').source.value == 'pi'\n"
        "print('ok')\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_SRC)}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_pi_present_in_process_registries_after_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    assert "pi" in registered_source_names()
    assert "pi" in registered_lister_names()
    assert get_source_adapter("pi") is not None
    assert get_lister("pi") is not None


def test_runtime_capabilities_not_edited_by_py05a() -> None:
    """PY-05a must not claim sources — claim-writer issues only."""
    # Source of truth for CI: python/runtime-capabilities.json at package root.
    caps_path = _REPO_ROOT / "python" / "runtime-capabilities.json"
    text = caps_path.read_text(encoding="utf-8")
    # File may list empty claimed sources early; must remain claim-writer owned.
    # Smoke: capabilities file still parses and is not rewritten by this slice.
    import json

    caps = json.loads(text)
    assert caps["runtime"] == "python"
    assert caps["normalizer_contract_version"] == "0.2.0"


# ---------------------------------------------------------------------------
# Decode: fixture-driven vectors
# ---------------------------------------------------------------------------


def test_decode_tool_calls_session_and_events() -> None:
    transcript = _read_case("tool-calls")
    session = _pi_adapter().decode(transcript, source_context=_ctx())

    assert session.source.value == "pi"
    assert session.source_name == "pi"
    assert session.group_id == "019f92c8-d694-7ac3-9144-e3b22add01f3"
    assert session.group_resolved is True
    assert session.cwd == "/home/user/pi-demo"
    assert session.producer_version == "3"
    assert session.diagnostics == ()

    kinds = [e.kind for e in session.events]
    assert "message" in kinds
    assert "reasoning" in kinds
    assert "tool-call" in kinds
    assert "tool-result" in kinds

    user = next(e for e in session.events if e.role == TrajectoryRole.USER)
    assert user.content is not None
    assert "notes.txt" in user.content
    assert user.source_anchor_kind == SourceAnchorKind.BYTE
    assert user.native_record_id == "87b91a11"
    assert user.source_offset is not None
    assert user.source_offset >= 0
    assert user.input_line is not None and user.input_line >= 1

    tool_calls = [e for e in session.events if e.kind == "tool-call"]
    assert {c.tool_call_id for c in tool_calls} == {"toolu_pi_1", "toolu_pi_2"}
    assert all(c.arguments_json for c in tool_calls)

    results = [e for e in session.events if e.kind == "tool-result"]
    assert {r.tool_call_id for r in results} == {"toolu_pi_1", "toolu_pi_2"}
    assert all(r.is_error is False for r in results)

    assert len(session.model_invocations) >= 2
    inv = session.model_invocations[0]
    assert inv.provider == "anthropic"
    assert inv.api_family == "anthropic-messages"
    assert inv.response_model == "claude-sonnet-5"
    assert inv.requested_model == "claude-sonnet-5"  # from model_change
    assert inv.response_id is not None
    assert inv.input_tokens is not None
    assert inv.source_offset is not None


def test_decode_byte_offsets_match_utf8_line_starts() -> None:
    transcript = _read_case("tool-calls")
    session = _pi_adapter().decode(transcript, source_context=_ctx())

    expected: dict[str, int] = {}
    offset = 0
    for raw_line in transcript.split(b"\n"):
        if raw_line.strip():
            import json

            row = json.loads(raw_line.decode("utf-8"))
            if row.get("type") == "message" and "id" in row:
                expected[row["id"]] = offset
        offset += len(raw_line) + 1  # + newline (split drops it; last may overshoot)

    # Recompute carefully with newline accounting matching the adapter.
    expected = {}
    offset = 0
    data = transcript
    while offset <= len(data):
        nl = data.find(b"\n", offset)
        end = len(data) if nl < 0 else nl
        line_bytes = data[offset:end]
        if line_bytes.endswith(b"\r"):
            line_bytes = line_bytes[:-1]
        if line_bytes.strip():
            import json

            try:
                row = json.loads(line_bytes.decode("utf-8"))
            except Exception:
                row = None
            if (
                isinstance(row, dict)
                and row.get("type") == "message"
                and isinstance(row.get("id"), str)
            ):
                expected[row["id"]] = offset
        if end == len(data):
            break
        offset = end + 1

    for event in session.events:
        if event.native_record_id and event.native_record_id in expected:
            assert event.source_offset == expected[event.native_record_id]
            assert event.source_anchor_kind == SourceAnchorKind.BYTE


def test_decode_malformed_json_is_content_safe() -> None:
    transcript = (
        b'{"type":"session","id":"safe","timestamp":"2026-01-01T00:00:00Z"}\n'
        b'{"type":"message","id":"u1","timestamp":"2026-01-01T00:00:01Z",'
        b'"message":{"role":"user","content":"hello"}}\n'
        b'{"secret":"PRIVATE-MARKER"\n'
        b'{"type":"message","id":"a1","timestamp":"2026-01-01T00:00:02Z",'
        b'"message":{"role":"assistant","content":"done"}}\n'
    )
    session = _pi_adapter().decode(transcript, source_context=_ctx())
    assert len(session.diagnostics) == 1
    diag = session.diagnostics[0]
    assert diag.code == DIAG_INVALID_JSON_LINE
    assert "PRIVATE-MARKER" not in diag.message
    assert diag.input_line == 3


def test_decode_non_object_json_line() -> None:
    transcript = (
        b'{"type":"session","id":"s1"}\n'
        b"[1,2,3]\n"
        b'{"type":"message","id":"u1","message":{"role":"user","content":"hi"}}\n'
    )
    session = _pi_adapter().decode(transcript, source_context=_ctx())
    assert any(d.code == DIAG_NON_OBJECT_JSON_LINE for d in session.diagnostics)


def test_decode_rejects_nan_json_constants() -> None:
    """Python json.loads accepts NaN by default; peers reject non-JSON constants."""
    transcript = (
        b'{"type":"session","id":"s","extra":NaN}\n'
        b'{"type":"message","id":"u1","message":{"role":"user","content":"hi"}}\n'
    )
    session = _pi_adapter().decode(transcript, source_context=_ctx())
    assert any(d.code == DIAG_INVALID_JSON_LINE for d in session.diagnostics)
    # Session header line dropped; message still decoded.
    assert any(e.role == TrajectoryRole.USER for e in session.events)


def test_decode_empty_without_session_or_message_is_invalid() -> None:
    with pytest.raises(TrajectoryError) as ei:
        _pi_adapter().decode(b"\n\n", source_context=_ctx())
    assert ei.value.code == FATAL_INVALID_INPUT
    assert "session JSONL" in ei.value.message


def test_decode_session_header_only_is_valid() -> None:
    transcript = b'{"type":"session","id":"header-only","version":1}\n'
    session = _pi_adapter().decode(transcript, source_context=_ctx())
    assert session.group_id == "header-only"
    assert session.group_resolved is True
    assert session.events == ()
    assert session.model_invocations == ()


def test_decode_missing_assistant_fixture() -> None:
    session = _pi_adapter().decode(
        _read_case("missing-assistant"), source_context=_ctx()
    )
    assert session.group_id == "pi-missing-assistant"
    roles = [e.role for e in session.events]
    assert TrajectoryRole.USER in roles
    assert TrajectoryRole.ASSISTANT not in roles


def test_decode_unicode_boundaries_preserves_content() -> None:
    session = _pi_adapter().decode(
        _read_case("unicode-boundaries"), source_context=_ctx()
    )
    assert session.group_id == "unicode-session"
    assert session.group_resolved is True
    user = next(e for e in session.events if e.role == TrajectoryRole.USER)
    assert user.content is not None
    assert "😀" in user.content
    assert session.created_at_ms is not None
    # Dual timing present when source timestamp has fractional digits.
    assert session.created_at_precise is not None
    assert "+00:00" in session.created_at_precise


def test_decode_tool_result_error_prefix() -> None:
    transcript = (
        b'{"type":"session","id":"err"}\n'
        b'{"type":"message","id":"t1","message":{"role":"toolResult",'
        b'"toolCallId":"c1","toolName":"x","content":"boom","isError":true}}\n'
    )
    session = _pi_adapter().decode(transcript, source_context=_ctx())
    result = session.events[0]
    assert result.kind == "tool-result"
    assert result.is_error is True
    assert result.content is not None
    assert result.content.startswith("Error:")


def test_decode_rejects_non_bytes_transcript() -> None:
    with pytest.raises(TypeError):
        _pi_adapter().decode("not-bytes", source_context=_ctx())  # type: ignore[arg-type]


def test_decode_rejects_naive_timestamp_strings() -> None:
    """Peer RFC-3339 gate: naive / non-offset timestamps are ignored (not assumed UTC)."""
    transcript = (
        b'{"type":"session","id":"naive","timestamp":"2026-01-01T00:00:00"}\n'
        b'{"type":"message","id":"u1","timestamp":"2026-01-01T00:00:01",'
        b'"message":{"role":"user","content":"hi"}}\n'
    )
    session = _pi_adapter().decode(transcript, source_context=_ctx())
    assert session.created_at_ms is None
    user = session.events[0]
    assert user.timestamp_ms is None


def test_adapter_source_property() -> None:
    from hypabolic_trajectory.sources.pi import PiSourceAdapter

    assert PiSourceAdapter().source.value == "pi"


# ---------------------------------------------------------------------------
# Lister
# ---------------------------------------------------------------------------


def test_list_missing_store_empty_page(tmp_path: Path) -> None:
    lister = get_lister("pi")
    assert lister is not None
    missing = tmp_path / "no-such-root"
    page = lister.list_page(root=missing, cursor=None, limit=50)
    assert page.items == ()
    assert page.next_cursor is None


def test_list_pi_project_directories_and_cursor(tmp_path: Path) -> None:
    lister = get_lister("pi")
    assert lister is not None

    project = tmp_path / "sessions" / "-workspace-project"
    project.mkdir(parents=True)
    older = project / "2026-01-01_old.jsonl"
    newer = project / "2026-01-02_new.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    # Set mtimes deterministically (seconds resolution is enough for sort order).
    os.utime(older, (1_704_067_200, 1_704_067_200))  # 2024-ish; order matters
    os.utime(newer, (1_704_153_600, 1_704_153_600))

    first = lister.list_page(root=tmp_path, cursor=None, limit=1)
    assert len(first.items) == 1
    assert first.items[0].id == "2026-01-02_new"
    assert first.next_cursor is not None
    assert first.items[0].size_bytes == 3
    assert first.items[0].updated_at is not None
    # updated_at is ...fffZ
    assert first.items[0].updated_at.endswith("Z")

    second = lister.list_page(root=tmp_path, cursor=first.next_cursor, limit=1)
    assert len(second.items) == 1
    assert second.items[0].id == "2026-01-01_old"
    assert second.next_cursor is None


def test_list_ignores_non_jsonl_and_matches_conformance_layout(tmp_path: Path) -> None:
    """Mirror conformance/stores/pi-pagination layout."""
    lister = get_lister("pi")
    assert lister is not None

    (tmp_path / "sessions" / "project-a").mkdir(parents=True)
    (tmp_path / "sessions" / "project-b").mkdir(parents=True)
    older = tmp_path / "sessions" / "project-a" / "older.jsonl"
    newer = tmp_path / "sessions" / "project-b" / "newer.jsonl"
    ignored = tmp_path / "sessions" / "project-b" / "ignored.txt"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    ignored.write_text("not a trajectory", encoding="utf-8")

    # Match store fixture timestamps.
    import calendar
    from datetime import datetime, timezone

    def set_utc(path: Path, iso: str) -> None:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        epoch = calendar.timegm(dt.utctimetuple())
        os.utime(path, (epoch, epoch))

    set_utc(older, "2026-01-01T00:00:00.000Z")
    set_utc(newer, "2026-01-02T00:00:00.000Z")
    set_utc(ignored, "2026-01-03T00:00:00.000Z")

    page1 = lister.list_page(root=tmp_path, cursor=None, limit=1)
    assert len(page1.items) == 1
    assert page1.items[0].id == "newer"
    assert page1.items[0].updated_at == "2026-01-02T00:00:00.000Z"
    assert page1.items[0].size_bytes == 3
    assert page1.next_cursor is not None
    # Cursor payload is base64url of "1\n0\nnewer"
    item_id, index = decode_cursor(page1.next_cursor)
    assert item_id == "newer"
    assert index == 0

    page2 = lister.list_page(root=tmp_path, cursor=page1.next_cursor, limit=1)
    assert len(page2.items) == 1
    assert page2.items[0].id == "older"
    assert page2.items[0].updated_at == "2026-01-01T00:00:00.000Z"
    assert page2.next_cursor is None

    # Path is native locator under root (not slash-normalized for identity).
    assert page1.items[0].path.endswith(
        str(Path("sessions") / "project-b" / "newer.jsonl")
    )


def test_list_invalid_limit_raises() -> None:
    lister = get_lister("pi")
    assert lister is not None
    with pytest.raises(TrajectoryError) as ei:
        lister.list_page(root=Path("/tmp"), cursor=None, limit=0)
    assert ei.value.code == FATAL_INVALID_INPUT


def test_format_ms_used_for_listing_clock() -> None:
    # Sanity: listing clock matches public format_ms helper.
    assert format_ms(0) == "1970-01-01T00:00:00.000Z"
