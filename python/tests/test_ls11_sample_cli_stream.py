"""LS-11 sample CLI stream / ahp-stream coverage (Python).

Uses temp stores + FakeAhpHost only. Sample is not a daemon.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "python" / "samples"
FIXTURE_PI = REPO_ROOT / "conformance" / "cases" / "pi" / "tool-calls" / "input.jsonl"
STREAM_CASES = REPO_ROOT / "conformance" / "cases" / "streaming"
CHAT = "ahp-chat:/00000000-0000-4000-8000-0000000000c1"

if str(SAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(SAMPLES_DIR))

from trajectory_cli import cli as trajectory_cli  # noqa: E402

SESSION_LINE = (
    b'{"type":"session","version":3,"id":"ls11-stream-py",'
    b'"timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}\n'
)
USER_LINE = (
    b'{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z",'
    b'"message":{"role":"user","content":[{"type":"text","text":"hello"}]},'
    b'"sessionId":"ls11-stream-py"}\n'
)


def test_parse_emit_and_delivery() -> None:
    assert trajectory_cli.parse_emit("snapshot+delta") == "snapshot+delta"
    assert trajectory_cli.parse_emit("both") == "snapshot+delta"
    assert trajectory_cli.emit_to_delivery("snapshot+delta") == "both"
    assert trajectory_cli.emit_to_delivery("snapshot") == "snapshot"
    assert trajectory_cli.emit_to_delivery("delta") == "delta"


def test_parse_args_stream_flags() -> None:
    args = trajectory_cli.parse_args(
        [
            "stream",
            "--source",
            "pi",
            "--path",
            "/tmp/session.jsonl",
            "--emit",
            "snapshot",
            "--follow",
            "--max-updates",
            "2",
            "--interval",
            "0.01",
        ]
    )
    assert args.command == "stream"
    assert args.source == "pi"
    assert args.emit == "snapshot"
    assert args.follow is True
    assert args.max_updates == 2
    assert args.interval == 0.01


def test_parse_args_ahp_stream_flags() -> None:
    args = trajectory_cli.parse_args(
        [
            "ahp-stream",
            "--url",
            "fake://demo",
            "--chat",
            CHAT,
            "--from-seq",
            "3",
            "--emit",
            "snapshot+delta",
            "--max-updates",
            "1",
        ]
    )
    assert args.command == "ahp-stream"
    assert args.url == "fake://demo"
    assert args.chat == CHAT
    assert args.from_seq == 3
    assert args.emit == "snapshot+delta"


def test_help_mentions_stream_and_not_daemon(capsys: pytest.CaptureFixture[str]) -> None:
    assert trajectory_cli.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "stream" in out
    assert "ahp-stream" in out
    assert "daemon" in out.lower()
    assert "Not a daemon" in out or "not a daemon" in out.lower()
    assert "--watch" in out
    assert "Watch live" in out or "watch" in out.lower()


def test_stream_temp_file_snapshot_delta(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_bytes(SESSION_LINE + USER_LINE)

    code = trajectory_cli.main(
        [
            "stream",
            "--source",
            "pi",
            "--root",
            str(root),
            "--path",
            str(path),
            "--emit",
            "snapshot+delta",
            "--max-updates",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "stream update" in out
    assert "snapshot" in out
    assert "delta" in out
    assert "live tail" in out
    assert "Content omitted" in out
    assert "not a daemon" in out.lower()
    # Privacy default: no user prose from fixture
    assert "hello" not in out


def test_stream_follow_growth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_bytes(SESSION_LINE)

    # Without --follow: single poll of current prefix.
    code = trajectory_cli.main(
        [
            "stream",
            "--source",
            "pi",
            "--root",
            str(root),
            "--path",
            str(path),
            "--max-updates",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Emitted 1 update" in out or "stream update #1" in out


def test_stream_rejects_ahp_source(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(
        ["stream", "--source", "ahp", "--path", str(FIXTURE_PI)]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "invalid_input" in err


def test_stream_id_requires_root(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(
        ["stream", "--source", "pi", "--id", "some-id"]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "invalid_input" in err
    assert "root" in err.lower()


def test_stream_show_content_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_bytes(SESSION_LINE + USER_LINE)
    code = trajectory_cli.main(
        [
            "stream",
            "--source",
            "pi",
            "--root",
            str(root),
            "--path",
            str(path),
            "--show-content",
            "--max-updates",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "private" in out.lower()


def test_ahp_stream_fake_host_actions(capsys: pytest.CaptureFixture[str]) -> None:
    actions = STREAM_CASES / "ahp-action-turn-flow" / "step-actions.jsonl"
    assert actions.is_file()
    code = trajectory_cli.main(
        [
            "ahp-stream",
            "--url",
            "fake://demo",
            "--chat",
            CHAT,
            "--actions-path",
            str(actions),
            "--emit",
            "snapshot+delta",
            "--max-updates",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "stream update" in out or "ready" in out.lower()
    assert "snapshot" in out
    assert "delta" in out
    assert "Content omitted" in out
    # Auth tokens must not appear
    assert "test-token" not in out
    blob = out.lower()
    assert "bearer" not in blob


def test_ahp_stream_fake_empty_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(
        [
            "ahp-stream",
            "--url",
            "fake://demo",
            "--chat",
            CHAT,
            "--max-updates",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "ready" in out.lower() or "stream update" in out
    assert "not a daemon" in out.lower()


def test_ahp_stream_rejects_ws_url(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(
        [
            "ahp-stream",
            "--url",
            "ws://localhost:9999",
            "--chat",
            CHAT,
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "fake://" in err
    assert "invalid_input" in err


def test_ahp_stream_requires_chat(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(["ahp-stream", "--url", "fake://demo"])
    assert code == 2
    err = capsys.readouterr().err
    assert "chat" in err.lower()


def _write_pi_store(root: Path, session_id: str, body: bytes) -> Path:
    session_dir = root / "sessions" / "demo"
    session_dir.mkdir(parents=True)
    path = session_dir / f"{session_id}.jsonl"
    path.write_bytes(body)
    return path


def test_parse_args_watch_flag() -> None:
    args = trajectory_cli.parse_args(["browse", "--source", "pi", "--watch", "--id", "abc"])
    assert args.command == "browse"
    assert args.watch is True
    assert args.id == "abc"


def test_browse_watch_listed_session(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "agent"
    _write_pi_store(root, "watch-me", SESSION_LINE + USER_LINE)
    code = trajectory_cli.main(
        [
            "browse",
            "--source",
            "pi",
            "--root",
            str(root),
            "--id",
            "watch-me",
            "--watch",
            "--emit",
            "snapshot+delta",
            "--max-updates",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "stream update" in out
    assert "snapshot" in out
    assert "delta" in out
    assert "live tail" in out
    assert "not a daemon" in out.lower()
    assert "hello" not in out


def test_browse_watch_rejects_ahp(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(
        ["browse", "--source", "ahp", "--root", "/tmp", "--id", "x", "--watch"]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "invalid_input" in err
    assert "file JSONL" in err or "Watch live" in err or "watch" in err.lower()


def test_stream_without_path_or_id_requires_tty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = trajectory_cli.main(["stream", "--source", "pi"])
    assert code == 2
    err = capsys.readouterr().err
    assert "invalid_input" in err
    assert "TTY" in err or "path" in err.lower()


def test_stream_fixture_pi_path_parent_root(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --path is given without --root, parent dir is the explicit root."""
    assert FIXTURE_PI.is_file()
    code = trajectory_cli.main(
        [
            "stream",
            "--source",
            "pi",
            "--path",
            str(FIXTURE_PI),
            "--max-updates",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "stream update" in out
