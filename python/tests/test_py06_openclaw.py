"""PY-06-openclaw: OpenClaw decode adapter + lister registry registration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hypabolic_trajectory import TrajectorySource, normalize_to_ir
from hypabolic_trajectory.dto import NormalizeRequest, SourceContext, TrajectoryListing
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import RecordKind, TrajectoryRole
from hypabolic_trajectory.listing.common import encode_cursor
from hypabolic_trajectory.listing.openclaw import OpenClawTrajectoryLister
from hypabolic_trajectory.listing.protocol import get_lister, registered_lister_names
from hypabolic_trajectory.sources.openclaw import OpenClawSourceAdapter
from hypabolic_trajectory.sources.protocol import get_source_adapter, registered_source_names

# Repo-root conformance fixtures (unit vectors; full runner is later issues).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPENCLAW_CASES = _REPO_ROOT / "conformance" / "cases" / "openclaw"
_OPENCLAW_STORE = (
    _REPO_ROOT / "conformance" / "stores" / "openclaw-pagination" / "store.json"
)
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Registration (package import)
# ---------------------------------------------------------------------------


def test_openclaw_adapter_registered_on_package_import() -> None:
    assert "openclaw" in registered_source_names()
    adapter = get_source_adapter("openclaw")
    assert adapter is not None
    assert adapter.source is TrajectorySource.OPENCLAW


def test_openclaw_lister_registered_on_package_import() -> None:
    assert "openclaw" in registered_lister_names()
    lister = get_lister("openclaw")
    assert lister is not None
    assert lister.source is TrajectorySource.OPENCLAW


def test_openclaw_registers_via_root_package_only_subprocess() -> None:
    """Fresh interpreter: only ``import hypabolic_trajectory`` wires openclaw."""
    code = (
        "import hypabolic_trajectory as ht\n"
        "from hypabolic_trajectory.sources.protocol import registered_source_names\n"
        "from hypabolic_trajectory.listing.protocol import registered_lister_names\n"
        "assert 'openclaw' in registered_source_names()\n"
        "assert 'openclaw' in registered_lister_names()\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


# ---------------------------------------------------------------------------
# Decode — tool-calls fixture
# ---------------------------------------------------------------------------


def test_decode_tool_calls_fixture_events_and_group() -> None:
    transcript = _read_bytes(_OPENCLAW_CASES / "tool-calls" / "input.jsonl")
    decoded = OpenClawSourceAdapter().decode(
        transcript, source_context=SourceContext()
    )
    assert decoded.source is TrajectorySource.OPENCLAW
    assert decoded.source_name == "openclaw"
    assert decoded.group_id == "openclaw-session-0001"
    assert decoded.group_resolved is True
    assert decoded.cwd == "/home/user/workspace"
    assert decoded.producer_version == "7"
    # Session dual clock: 2026-07-05T09:00:00.000Z
    assert decoded.created_at_ms == 1_783_242_000_000
    assert decoded.created_at_precise == "2026-07-05T09:00:00.0000000+00:00"

    kinds = [e.kind for e in decoded.events]
    roles = [e.role for e in decoded.events]
    assert kinds == [
        "message",
        "reasoning",
        "tool-call",
        "tool-result",
        "message",
    ]
    assert roles == [
        TrajectoryRole.USER,
        TrajectoryRole.REASONING,
        TrajectoryRole.ASSISTANT,
        TrajectoryRole.TOOL,
        TrajectoryRole.ASSISTANT,
    ]
    # component_index on multi-part assistant message (thinking + toolCall)
    entry2 = [e for e in decoded.events if e.native_record_id == "entry-2"]
    assert [e.component_index for e in entry2] == [0, 1]
    user = decoded.events[0]
    assert user.timestamp_ms == 1_783_242_001_000
    assert user.timestamp_precise == "2026-07-05T09:00:01.0000000+00:00"
    tool_call = next(e for e in decoded.events if e.kind == "tool-call")
    assert tool_call.tool_call_id == "call_oc_1"
    assert tool_call.tool_name == "bash"
    assert tool_call.arguments_json == '{"command":"ls"}'
    assert tool_call.source_anchor_kind is not None
    assert tool_call.source_anchor_kind.value == "byte"
    assert tool_call.source_offset is not None
    assert tool_call.source_offset >= 0

    assert len(decoded.model_invocations) == 2
    inv0 = decoded.model_invocations[0]
    assert inv0.provider == "anthropic"
    assert inv0.api_family == "anthropic-messages"
    assert inv0.response_model == "claude-opus-4-8"
    assert inv0.input_tokens == 120
    assert inv0.output_tokens == 45
    assert inv0.total_tokens == 165
    assert inv0.stop_reason == "toolUse"
    assert inv0.completed_at_ms == 1_783_242_004_000
    assert inv0.completed_at_precise == "2026-07-05T09:00:04.0000000+00:00"


def test_normalize_to_ir_dispatches_openclaw_tool_calls() -> None:
    transcript = _read_bytes(_OPENCLAW_CASES / "tool-calls" / "input.jsonl")
    ir = normalize_to_ir(
        NormalizeRequest(source="openclaw", transcript=transcript)
    )
    assert ir.source is TrajectorySource.OPENCLAW
    assert ir.group_id == "openclaw-session-0001"
    assert ir.source_group_resolved is True
    # meta + user + reasoning + tool-calls + tool-result + assistant
    kinds = [r.kind for r in ir.records]
    assert kinds[0] is RecordKind.META
    assert RecordKind.MESSAGE in kinds
    assert RecordKind.ASSISTANT_TOOL_CALLS in kinds
    assert RecordKind.TOOL_RESULT in kinds
    body = [r for r in ir.records if r.kind is not RecordKind.META]
    assert body[0].role is TrajectoryRole.USER
    assert body[0].content == "What files are in the workspace?"


# ---------------------------------------------------------------------------
# Decode — cleanup fixture (diagnostics, image, error tool, delivery-mirror)
# ---------------------------------------------------------------------------


def test_decode_cleanup_fixture_diagnostics_and_masks() -> None:
    transcript = _read_bytes(_OPENCLAW_CASES / "cleanup" / "input.jsonl")
    decoded = OpenClawSourceAdapter().decode(
        transcript, source_context=SourceContext()
    )
    assert decoded.group_id == "openclaw-session-0002"
    codes = [d.code for d in decoded.diagnostics]
    assert "invalid_json_line" in codes
    # custom type rows are ignored (not unknown_semantic for openclaw/pi-family)
    assert "unknown_semantic_record" not in codes

    # User content concatenates text + [image]
    user = next(e for e in decoded.events if e.role is TrajectoryRole.USER)
    assert "Summarize the meeting photo I sent." in (user.content or "")
    assert "[image]" in (user.content or "")

    # Error tool result is prefixed when content does not already start with error
    err_result = next(
        e
        for e in decoded.events
        if e.kind == "tool-result" and e.tool_call_id == "call_oc_err"
    )
    assert err_result.is_error is True
    assert (err_result.content or "").startswith("Error:")

    # delivery-mirror assistant prose kept; model metadata masked
    mirror_msgs = [
        e
        for e in decoded.events
        if e.kind == "message"
        and e.role is TrajectoryRole.ASSISTANT
        and e.content
        and "Reminder sent" in e.content
    ]
    assert len(mirror_msgs) == 1
    assert mirror_msgs[0].model is None
    mirror_inv = [
        inv
        for inv in decoded.model_invocations
        if inv.response_model is None
        and inv.provider == "openclaw"
    ]
    # The delivery-mirror invocation has provider openclaw and masked response model.
    assert any(inv.api_family == "openai-responses" for inv in decoded.model_invocations)
    assert any(
        inv.provider == "openclaw" and inv.response_model is None
        for inv in decoded.model_invocations
    )
    _ = mirror_inv


def test_empty_transcript_without_session_is_invalid_input() -> None:
    with pytest.raises(TrajectoryError) as ei:
        OpenClawSourceAdapter().decode(b"", source_context=SourceContext())
    assert ei.value.code == FATAL_INVALID_INPUT
    assert "OpenClaw" in ei.value.message


def test_session_only_transcript_ok() -> None:
    line = (
        b'{"type":"session","version":1,"id":"only-session",'
        b'"timestamp":"2026-01-01T00:00:00.000Z"}\n'
    )
    decoded = OpenClawSourceAdapter().decode(line, source_context=SourceContext())
    assert decoded.group_id == "only-session"
    assert decoded.events == ()
    assert decoded.model_invocations == ()


def test_non_object_json_line_diagnostic() -> None:
    transcript = b'{"type":"session","id":"s1"}\n[1,2,3]\n'
    decoded = OpenClawSourceAdapter().decode(
        transcript, source_context=SourceContext()
    )
    assert any(d.code == "non_object_json_line" for d in decoded.diagnostics)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_are_invalid_json_line(constant: str) -> None:
    transcript = (
        b'{"type":"session","id":"s-const"}\n'
        + f'{{"type":"message","id":"bad","message":{{"role":"user","content":{constant}}}}}\n'.encode()
    )
    decoded = OpenClawSourceAdapter().decode(
        transcript, source_context=SourceContext()
    )
    assert any(d.code == "invalid_json_line" for d in decoded.diagnostics)
    assert decoded.events == ()


def test_nan_inside_tool_arguments_is_invalid_json_line() -> None:
    transcript = (
        b'{"type":"session","id":"s-nan-args"}\n'
        b'{"type":"message","id":"a1","timestamp":"2026-01-01T00:00:01.000Z",'
        b'"message":{"role":"assistant","content":[{"type":"toolCall","id":"c1",'
        b'"name":"bash","arguments":{"x":NaN}}],"model":"m"}}\n'
        b'{"type":"message","id":"u1","timestamp":"2026-01-01T00:00:00.000Z",'
        b'"message":{"role":"user","content":[{"type":"text","text":"hi"}]}}\n'
    )
    decoded = OpenClawSourceAdapter().decode(
        transcript, source_context=SourceContext()
    )
    assert any(d.code == "invalid_json_line" for d in decoded.diagnostics)
    # Only the user line (valid) should produce an event.
    assert [e.kind for e in decoded.events] == ["message"]


@pytest.mark.parametrize(
    "bad_ts",
    [
        "2026-01-01",
        "2026-01-01 00:00:00Z",
        "20260101T000000Z",
        "2026-01-01T00:00:00",  # naive / no offset
    ],
)
def test_non_rfc3339_timestamps_rejected(bad_ts: str) -> None:
    line = (
        b'{"type":"session","id":"s-ts"}\n'
        + (
            '{"type":"message","id":"u1","timestamp":'
            + json.dumps(bad_ts)
            + ',"message":{"role":"user","content":[{"type":"text","text":"x"}]}}\n'
        ).encode()
    )
    decoded = OpenClawSourceAdapter().decode(line, source_context=SourceContext())
    user = decoded.events[0]
    assert user.timestamp_ms is None
    assert user.timestamp_precise is None


def test_integer_ms_timestamp_precise_pad() -> None:
    # delivery-mirror cleanup fixture uses integer ms on message timestamp.
    transcript = _read_bytes(_OPENCLAW_CASES / "cleanup" / "input.jsonl")
    decoded = OpenClawSourceAdapter().decode(
        transcript, source_context=SourceContext()
    )
    mirror = next(
        e
        for e in decoded.events
        if e.content and "Reminder sent" in e.content
    )
    assert mirror.timestamp_ms == 1_783_346_410_000
    assert mirror.timestamp_precise == "2026-07-06T14:00:10.0000000+00:00"


@pytest.mark.parametrize(
    ("token", "expect"),
    [
        (_INT64_MIN, _INT64_MIN),
        (_INT64_MAX, _INT64_MAX),
        (_INT64_MIN - 1, None),
        (_INT64_MAX + 1, None),
    ],
)
def test_token_usage_int64_bounds(token: int, expect: int | None) -> None:
    # JSON numbers outside int64 still parse in Python as int; adapter must drop them.
    msg = {
        "type": "message",
        "id": "a1",
        "timestamp": "2026-01-01T00:00:01.000Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "model": "m",
            "usage": {"input": token, "output": 1, "totalTokens": 1},
        },
    }
    transcript = (
        b'{"type":"session","id":"s-tok"}\n'
        + (json.dumps(msg) + "\n").encode()
        + b'{"type":"message","id":"u1","timestamp":"2026-01-01T00:00:00.000Z",'
        b'"message":{"role":"user","content":[{"type":"text","text":"hey"}]}}\n'
    )
    decoded = OpenClawSourceAdapter().decode(
        transcript, source_context=SourceContext()
    )
    inv = decoded.model_invocations[0]
    assert inv.input_tokens == expect


def test_model_change_feeds_requested_model() -> None:
    lines = b"\n".join(
        [
            b'{"type":"session","id":"s-mc"}',
            b'{"type":"model_change","provider":"anthropic","modelId":"claude-x"}',
            (
                b'{"type":"message","id":"a1","timestamp":"2026-01-01T00:00:01.000Z",'
                b'"message":{"role":"assistant","content":[{"type":"text","text":"hi"}],'
                b'"provider":"anthropic","model":"claude-x"}}'
            ),
            (
                b'{"type":"message","id":"u1","timestamp":"2026-01-01T00:00:00.000Z",'
                b'"message":{"role":"user","content":[{"type":"text","text":"hey"}]}}'
            ),
        ]
    )
    # Order in file is session, model_change, assistant, user — still decodes.
    decoded = OpenClawSourceAdapter().decode(lines, source_context=SourceContext())
    inv = decoded.model_invocations[0]
    assert inv.requested_model == "claude-x"
    assert inv.provider == "anthropic"


# ---------------------------------------------------------------------------
# Lister
# ---------------------------------------------------------------------------


def _write_store(root: Path, files: list[dict[str, object]]) -> None:
    for item in files:
        rel = str(item["path"])
        content = str(item["content"])
        updated_at = item.get("updated_at")
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if type(updated_at) is str:
            # Convert ISO ...Z to epoch seconds for os.utime.
            from datetime import datetime

            dt = datetime.fromisoformat(updated_at)
            ts = dt.timestamp()
            os.utime(target, (ts, ts))


def test_lister_openclaw_pagination_fixture_shape(tmp_path: Path) -> None:
    store = json.loads(_OPENCLAW_STORE.read_text(encoding="utf-8"))
    _write_store(tmp_path, store["files"])

    lister = OpenClawTrajectoryLister()
    page1 = lister.list_page(root=tmp_path, cursor=None, limit=1)
    assert len(page1.items) == 1
    assert page1.items[0].id == "newer"
    assert page1.items[0].path.endswith(
        str(Path("agents") / "agent-b" / "sessions" / "newer.jsonl")
    )
    assert page1.items[0].updated_at == "2026-01-02T00:00:00.000Z"
    assert page1.items[0].size_bytes == 3  # "{}\n"
    assert page1.next_cursor is not None

    page2 = lister.list_page(root=tmp_path, cursor=page1.next_cursor, limit=1)
    assert len(page2.items) == 1
    assert page2.items[0].id == "older"
    assert page2.items[0].updated_at == "2026-01-01T00:00:00.000Z"
    assert page2.next_cursor is None

    # Non-jsonl and sessions/ outside agents/ are ignored.
    all_page = lister.list_page(root=tmp_path, cursor=None, limit=50)
    ids = {item.id for item in all_page.items}
    assert ids == {"newer", "older"}


def test_lister_missing_agents_returns_empty_page(tmp_path: Path) -> None:
    page = OpenClawTrajectoryLister().list_page(
        root=tmp_path, cursor=None, limit=50
    )
    assert page.items == ()
    assert page.next_cursor is None


def test_lister_ignores_non_directory_agent_entries(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "not-a-dir.jsonl").write_text("{}\n", encoding="utf-8")
    sessions = agents / "agent-z" / "sessions"
    sessions.mkdir(parents=True)
    target = sessions / "only.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    page = OpenClawTrajectoryLister().list_page(
        root=tmp_path, cursor=None, limit=50
    )
    assert [i.id for i in page.items] == ["only"]


def test_registered_lister_list_page_matches_class() -> None:
    lister = get_lister("openclaw")
    assert lister is not None
    # Smoke: empty root path that does not exist → empty page.
    page = lister.list_page(root="/no/such/openclaw/root/for/tests", cursor=None, limit=10)
    assert page.items == ()
    assert isinstance(page.items, tuple)


def test_cursor_resume_after_first_item(tmp_path: Path) -> None:
    """Cursor encodes last id + index; resume yields subsequent page."""
    store = json.loads(_OPENCLAW_STORE.read_text(encoding="utf-8"))
    _write_store(tmp_path, store["files"])
    # Manually build cursor as if page1 ended at newer (index 0).
    cursor = encode_cursor("newer", 0)
    page = OpenClawTrajectoryLister().list_page(
        root=tmp_path, cursor=cursor, limit=1
    )
    assert [i.id for i in page.items] == ["older"]
    assert page.next_cursor is None


def test_listing_item_type() -> None:
    item = TrajectoryListing(
        id="x",
        path="/tmp/x.jsonl",
        updated_at="2026-01-01T00:00:00.000Z",
        size_bytes=1,
    )
    assert item.id == "x"
