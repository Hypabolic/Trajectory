"""PY-05b unit tests: claude-code + codex adapters and listers (register only)."""

from __future__ import annotations

import calendar
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hypabolic_trajectory.diagnostics import (
    DIAG_INJECTED_CONTEXT_DROPPED,
    DIAG_INVALID_JSON_LINE,
    DIAG_SIDECHAIN_RECORD_DROPPED,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import FATAL_SOURCE_GROUP_CONFLICT, TrajectoryError
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.listing.common import decode_cursor
from hypabolic_trajectory.listing.protocol import get_lister, registered_lister_names
from hypabolic_trajectory.sources.protocol import (
    get_source_adapter,
    registered_source_names,
)


def _claude_adapter():
    from hypabolic_trajectory.sources.claude_code import CLAUDE_CODE_SOURCE_ADAPTER

    return CLAUDE_CODE_SOURCE_ADAPTER


def _codex_adapter():
    from hypabolic_trajectory.sources.codex import CODEX_SOURCE_ADAPTER

    return CODEX_SOURCE_ADAPTER

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLAUDE_CASES = _REPO_ROOT / "conformance" / "cases" / "claude-code"
_CODEX_CASES = _REPO_ROOT / "conformance" / "cases" / "codex"


def _read_claude(name: str) -> bytes:
    return (_CLAUDE_CASES / name / "input.jsonl").read_bytes()


def _read_codex(name: str) -> bytes:
    return (_CODEX_CASES / name / "input.jsonl").read_bytes()


def _ctx() -> SourceContext:
    return SourceContext()


def _set_utc(path: Path, iso: str) -> None:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    epoch = calendar.timegm(dt.utctimetuple())
    os.utime(path, (epoch, epoch))


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------


def test_claude_code_and_codex_register_via_root_package_subprocess() -> None:
    """Fresh interpreter: only ``import hypabolic_trajectory`` must register both."""
    import subprocess
    import sys

    script = (
        "import hypabolic_trajectory as ht\n"
        "from hypabolic_trajectory.sources.protocol import registered_source_names\n"
        "from hypabolic_trajectory.listing.protocol import registered_lister_names\n"
        "src = registered_source_names()\n"
        "lst = registered_lister_names()\n"
        "assert 'claude-code' in src and 'codex' in src, src\n"
        "assert 'claude-code' in lst and 'codex' in lst, lst\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_REPO_ROOT / "python"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_claude_code_adapter_registered_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    assert "claude-code" in registered_source_names()
    adapter = get_source_adapter("claude-code")
    assert adapter is not None
    assert adapter.source.value == "claude-code"


def test_codex_adapter_registered_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    assert "codex" in registered_source_names()
    adapter = get_source_adapter("codex")
    assert adapter is not None
    assert adapter.source.value == "codex"


def test_claude_code_lister_registered_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    assert "claude-code" in registered_lister_names()
    lister = get_lister("claude-code")
    assert lister is not None
    assert lister.source.value == "claude-code"


def test_codex_lister_registered_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    assert "codex" in registered_lister_names()
    lister = get_lister("codex")
    assert lister is not None
    assert lister.source.value == "codex"


def test_runtime_capabilities_file_integrity() -> None:
    """Capabilities file remains valid JSON with stable identity pins.

    Claim membership is owned by claim-writer issues (PY-10b-sources-claude-codex
    and later); this slice only ensures the file is still well-formed and was
    not rewritten as a non-capabilities document.
    """
    caps_path = _REPO_ROOT / "python" / "runtime-capabilities.json"
    caps = json.loads(caps_path.read_text(encoding="utf-8"))
    assert caps["runtime"] == "python"
    assert caps["normalizer_contract_version"] == "0.2.0"
    assert isinstance(caps.get("sources"), list)
    assert isinstance(caps.get("outputs"), list)
    assert isinstance(caps.get("capabilities"), list)


# ---------------------------------------------------------------------------
# Claude Code decode
# ---------------------------------------------------------------------------


def test_claude_decode_tool_call_fixture() -> None:
    session = _claude_adapter().decode(
        _read_claude("tool-call"), source_context=_ctx()
    )
    assert session.source.value == "claude-code"
    assert session.source_name == "claude-code"
    assert session.cwd == "/workspace/project"
    assert session.git_branch == "main"
    assert session.producer_version == "unknown"
    assert session.diagnostics == ()

    kinds = [e.kind for e in session.events]
    assert kinds.count("message") >= 2
    assert "reasoning" in kinds
    assert "tool-call" in kinds
    assert "tool-result" in kinds

    tool_call = next(e for e in session.events if e.kind == "tool-call")
    assert tool_call.tool_call_id == "toolu_01A"
    assert tool_call.tool_name == "Read"
    assert tool_call.arguments_json == '{"file_path":"retry.py"}'
    assert tool_call.source_anchor_kind == SourceAnchorKind.BYTE
    assert tool_call.native_record_id == "a1"

    tool_result = next(e for e in session.events if e.kind == "tool-result")
    assert tool_result.tool_call_id == "toolu_01A"
    assert tool_result.content == "1\tdef retry():"
    assert tool_result.is_error is False

    assert len(session.model_invocations) == 2
    inv = session.model_invocations[0]
    assert inv.response_model == "claude-opus-4-6"
    assert inv.completed_at_ms is not None


def test_claude_decode_cleanup_diagnostics() -> None:
    session = _claude_adapter().decode(
        _read_claude("cleanup"), source_context=_ctx()
    )
    codes = [d.code for d in session.diagnostics]
    assert DIAG_INVALID_JSON_LINE in codes
    assert DIAG_SIDECHAIN_RECORD_DROPPED in codes
    # isMeta user is still decoded (noise drop is normalizer-side).
    assert any(e.role == TrajectoryRole.USER for e in session.events)
    assert any(e.role == TrajectoryRole.ASSISTANT for e in session.events)
    # Sidechain assistant must not appear.
    assert not any(
        e.native_record_id == "a1" and e.role == TrajectoryRole.ASSISTANT
        for e in session.events
    )


def test_claude_decode_mixed_version_earliest_context() -> None:
    session = _claude_adapter().decode(
        _read_claude("mixed-version"), source_context=_ctx()
    )
    assert session.group_id == "session-mixed"
    assert session.group_resolved is True
    assert session.cwd == "/workspace/mixed"
    assert session.git_branch == "feature/mixed"
    # Earliest non-transport version wins (.NET Earlier).
    assert session.producer_version == "2.1.139"
    assert any(e.kind == "reasoning" for e in session.events)
    assert any(e.kind == "tool-call" for e in session.events)
    # Fallback block ignored; image marker in tool result + user text.
    tool_result = next(e for e in session.events if e.kind == "tool-result")
    assert "[image]" in (tool_result.content or "")
    user_follow = [
        e
        for e in session.events
        if e.role == TrajectoryRole.USER and e.content == "Use the existing value."
    ]
    assert len(user_follow) == 1
    invs = session.model_invocations
    assert invs[0].input_tokens == 100
    assert invs[0].cache_read_tokens == 10
    assert invs[0].response_id == "msg_legacy"
    assert invs[1].response_model == "claude-sonnet-4-5"


def test_claude_multiple_session_ids_conflict() -> None:
    transcript = (
        b'{"type":"user","sessionId":"s-b","uuid":"u1","message":'
        b'{"role":"user","content":"hi"}}\n'
        b'{"type":"user","sessionId":"s-a","uuid":"u2","message":'
        b'{"role":"user","content":"yo"}}\n'
    )
    with pytest.raises(TrajectoryError) as ei:
        _claude_adapter().decode(transcript, source_context=_ctx())
    assert ei.value.code == FATAL_SOURCE_GROUP_CONFLICT
    # Ordinal sort of quoted ids.
    assert '"s-a"' in ei.value.message
    assert '"s-b"' in ei.value.message


def test_claude_byte_offsets_match_line_starts() -> None:
    transcript = _read_claude("tool-call")
    session = _claude_adapter().decode(transcript, source_context=_ctx())
    expected: dict[str, int] = {}
    offset = 0
    data = transcript
    while offset <= len(data):
        nl = data.find(b"\n", offset)
        end = len(data) if nl < 0 else nl
        line_bytes = data[offset:end]
        if line_bytes.endswith(b"\r"):
            line_bytes = line_bytes[:-1]
        if line_bytes.strip():
            try:
                row = json.loads(line_bytes.decode("utf-8"))
            except Exception:
                row = None
            if isinstance(row, dict) and isinstance(row.get("uuid"), str):
                expected[row["uuid"]] = offset
        if end == len(data):
            break
        offset = end + 1

    for event in session.events:
        if event.native_record_id and event.native_record_id in expected:
            assert event.source_offset == expected[event.native_record_id]
            assert event.source_anchor_kind == SourceAnchorKind.BYTE


def test_claude_rejects_non_bytes() -> None:
    with pytest.raises(TypeError):
        _claude_adapter().decode("x", source_context=_ctx())  # type: ignore[arg-type]


def test_claude_rejects_non_finite_json_constants() -> None:
    transcript = b'{"type":"user","uuid":"u1","message":{"role":"user","content":NaN}}\n'
    session = _claude_adapter().decode(transcript, source_context=_ctx())
    assert any(d.code == DIAG_INVALID_JSON_LINE for d in session.diagnostics)
    assert session.events == ()


def test_claude_adapter_source_property() -> None:
    from hypabolic_trajectory.sources.claude_code import ClaudeCodeSourceAdapter
    assert ClaudeCodeSourceAdapter().source.value == "claude-code"


# ---------------------------------------------------------------------------
# Codex decode
# ---------------------------------------------------------------------------


def test_codex_decode_full_fixture() -> None:
    session = _codex_adapter().decode(_read_codex("full"), source_context=_ctx())
    assert session.source.value == "codex"
    assert session.group_id == "codex-session-1"
    assert session.group_resolved is True
    assert session.cwd == "/repo/codex"
    assert session.git_branch == "main"
    assert session.model == "gpt-5.2-codex"
    assert session.producer_version == "0.140.0"
    assert session.created_at_ms is not None
    assert session.model_invocations == ()

    codes = [d.code for d in session.diagnostics]
    assert codes == [DIAG_INJECTED_CONTEXT_DROPPED]
    assert session.diagnostics[0].input_line == 3

    kinds = [e.kind for e in session.events]
    assert "reasoning" in kinds
    assert kinds.count("tool-call") == 4
    assert kinds.count("tool-result") == 3

    user = next(e for e in session.events if e.role == TrajectoryRole.USER)
    assert user.content == "Inspect the repository.\n[image]"

    by_name = {
        e.tool_name: e for e in session.events if e.kind == "tool-call" and e.tool_name
    }
    assert by_name["exec_command"].arguments_json == '{"cmd":"rg --files"}'
    assert by_name["apply_patch"].arguments_json == (
        '{"input":"*** Begin Patch\\n*** End Patch"}'
    )
    assert "query" in (by_name["web_search"].arguments_json or "")
    assert "compiler diagnostics" in (by_name["tool_search"].arguments_json or "")

    search_result = next(
        e
        for e in session.events
        if e.kind == "tool-result" and e.tool_call_id == "call-search"
    )
    assert search_result.content is not None
    assert "build" in search_result.content

    assistant = next(
        e
        for e in session.events
        if e.role == TrajectoryRole.ASSISTANT and e.kind == "message"
    )
    assert assistant.content == "The repository is ready."


def test_codex_decode_missing_group() -> None:
    session = _codex_adapter().decode(
        _read_codex("missing-group"), source_context=_ctx()
    )
    assert session.group_id is None
    assert session.group_resolved is False
    assert any(e.role == TrajectoryRole.USER for e in session.events)
    assert any(e.role == TrajectoryRole.ASSISTANT for e in session.events)


def test_codex_decode_group_conflict_detected_id() -> None:
    session = _codex_adapter().decode(
        _read_codex("group-conflict"), source_context=_ctx()
    )
    # Adapter only reports detected group; normalizer raises conflict with provided.
    assert session.group_id == "detected-session"
    assert session.group_resolved is True


def test_codex_turn_context_does_not_override_session_meta_cwd() -> None:
    session = _codex_adapter().decode(_read_codex("full"), source_context=_ctx())
    assert session.cwd == "/repo/codex"  # not /repo/ignored from turn_context


def test_codex_rejects_non_bytes() -> None:
    with pytest.raises(TypeError):
        _codex_adapter().decode("x", source_context=_ctx())  # type: ignore[arg-type]


def test_codex_rejects_non_finite_json_constants() -> None:
    transcript = (
        b'{"type":"response_item","payload":{"type":"message","role":"user",'
        b'"content":Infinity}}\n'
    )
    session = _codex_adapter().decode(transcript, source_context=_ctx())
    assert any(d.code == DIAG_INVALID_JSON_LINE for d in session.diagnostics)
    assert session.events == ()


def test_codex_adapter_source_property() -> None:
    from hypabolic_trajectory.sources.codex import CodexSourceAdapter
    assert CodexSourceAdapter().source.value == "codex"


# ---------------------------------------------------------------------------
# Listers
# ---------------------------------------------------------------------------


def test_claude_list_missing_store_empty_page(tmp_path: Path) -> None:
    lister = get_lister("claude-code")
    assert lister is not None
    page = lister.list_page(root=tmp_path / "missing", cursor=None, limit=50)
    assert page.items == ()
    assert page.next_cursor is None


def test_claude_list_conformance_layout(tmp_path: Path) -> None:
    lister = get_lister("claude-code")
    assert lister is not None

    (tmp_path / "project-a").mkdir()
    (tmp_path / "project-b").mkdir()
    older = tmp_path / "project-a" / "older.jsonl"
    newer = tmp_path / "project-b" / "newer.jsonl"
    ignored = tmp_path / "project-b" / "ignored.txt"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    ignored.write_text("not a trajectory", encoding="utf-8")
    _set_utc(older, "2026-04-01T00:00:00.000Z")
    _set_utc(newer, "2026-04-02T00:00:00.000Z")
    _set_utc(ignored, "2026-04-03T00:00:00.000Z")

    page1 = lister.list_page(root=tmp_path, cursor=None, limit=1)
    assert len(page1.items) == 1
    assert page1.items[0].id == "newer"
    assert page1.items[0].updated_at == "2026-04-02T00:00:00.000Z"
    assert page1.items[0].size_bytes == 3
    assert page1.next_cursor is not None
    item_id, index = decode_cursor(page1.next_cursor)
    assert item_id == "newer"
    assert index == 0

    page2 = lister.list_page(root=tmp_path, cursor=page1.next_cursor, limit=1)
    assert len(page2.items) == 1
    assert page2.items[0].id == "older"
    assert page2.items[0].updated_at == "2026-04-01T00:00:00.000Z"
    assert page2.next_cursor is None


def test_codex_list_recursive_depth_layout(tmp_path: Path) -> None:
    lister = get_lister("codex")
    assert lister is not None

    older = tmp_path / "2026" / "04" / "01" / "older.jsonl"
    newer = tmp_path / "2026" / "04" / "02" / "newer.jsonl"
    ignored = tmp_path / "2026" / "04" / "02" / "ignored.txt"
    older.parent.mkdir(parents=True)
    newer.parent.mkdir(parents=True)
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    ignored.write_text("not a trajectory", encoding="utf-8")
    _set_utc(older, "2026-04-01T00:00:00.000Z")
    _set_utc(newer, "2026-04-02T00:00:00.000Z")

    page1 = lister.list_page(root=tmp_path, cursor=None, limit=1)
    assert page1.items[0].id == "newer"
    assert page1.items[0].updated_at == "2026-04-02T00:00:00.000Z"
    assert page1.next_cursor is not None

    page2 = lister.list_page(root=tmp_path, cursor=page1.next_cursor, limit=1)
    assert page2.items[0].id == "older"
    assert page2.next_cursor is None


def test_codex_list_depth_limit_four(tmp_path: Path) -> None:
    """Four directory levels under root are scanned; deeper jsonl is skipped."""
    lister = get_lister("codex")
    assert lister is not None
    # depth: root/a/b/c/d/file.jsonl → 4 levels of subdirs from root
    deep_ok = tmp_path / "a" / "b" / "c" / "d" / "ok.jsonl"
    deep_ok.parent.mkdir(parents=True)
    deep_ok.write_text("{}\n", encoding="utf-8")
    too_deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "skip.jsonl"
    too_deep.parent.mkdir(parents=True)
    too_deep.write_text("{}\n", encoding="utf-8")
    _set_utc(deep_ok, "2026-04-02T00:00:00.000Z")
    _set_utc(too_deep, "2026-04-03T00:00:00.000Z")

    page = lister.list_page(root=tmp_path, cursor=None, limit=50)
    ids = {item.id for item in page.items}
    assert "ok" in ids
    assert "skip" not in ids


def test_list_invalid_limit_raises() -> None:
    for name in ("claude-code", "codex"):
        lister = get_lister(name)
        assert lister is not None
        with pytest.raises(TrajectoryError) as ei:
            lister.list_page(root=".", cursor=None, limit=0)
        assert ei.value.code == "invalid_input"


def test_list_pre_epoch_mtime_keeps_item(tmp_path: Path) -> None:
    """Pre-epoch mtimes remain listable (optional updated_at still present)."""
    lister = get_lister("claude-code")
    assert lister is not None
    project = tmp_path / "proj"
    project.mkdir()
    path = project / "ancient.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    # 1969-12-31T23:59:59Z
    os.utime(path, (-1, -1))
    page = lister.list_page(root=tmp_path, cursor=None, limit=50)
    assert len(page.items) == 1
    assert page.items[0].id == "ancient"
    assert page.items[0].updated_at is not None
    assert page.items[0].updated_at.startswith("1969-")
