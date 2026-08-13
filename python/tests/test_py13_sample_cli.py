"""PY-13 sample CLI (unpublished) unit coverage.

The CLI lives under ``python/samples/trajectory_cli`` and is never a console
script. Tests import it by adding samples/ to sys.path (same as runtime).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "python" / "samples"
FIXTURE_PI = REPO_ROOT / "conformance" / "cases" / "pi" / "tool-calls" / "input.jsonl"
FIXTURE_AHP = REPO_ROOT / "conformance" / "cases" / "ahp" / "tool-calls" / "input.json"

# Import sample package the same way users run it.
if str(SAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(SAMPLES_DIR))

from trajectory_cli import cli as trajectory_cli  # noqa: E402


def test_parse_source_aliases() -> None:
    assert trajectory_cli.parse_source("pi") == "pi"
    assert trajectory_cli.parse_source("Claude") == "claude-code"
    assert trajectory_cli.parse_source("claudecode") == "claude-code"
    with pytest.raises(Exception) as exc_info:
        trajectory_cli.parse_source("nope")
    assert getattr(exc_info.value, "code", None) == "unknown_source"


def test_parse_args_defaults_and_show_flags() -> None:
    args = trajectory_cli.parse_args([])
    assert args.command == "browse"
    assert args.limit == 50
    assert args.show_content is False
    assert args.format == "both"

    args = trajectory_cli.parse_args(
        [
            "show",
            "--source",
            "pi",
            "--path",
            str(FIXTURE_PI),
            "--format",
            "letta",
            "--show-content",
            "--limit",
            "10",
        ]
    )
    assert args.command == "show"
    assert args.source == "pi"
    assert args.format == "messages"  # letta alias
    assert args.show_content is True
    assert args.limit == 10
    assert args.path == str(FIXTURE_PI)


def test_parse_args_help() -> None:
    assert trajectory_cli.parse_args(["help"]).command == "help"
    assert trajectory_cli.parse_args(["--help"]).command == "help"
    assert trajectory_cli.parse_args(["-h"]).command == "help"


def test_expand_home_and_default_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("TRAJECTORY_PI_ROOT", raising=False)
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.delenv("CLAWDBOT_STATE_DIR", raising=False)
    monkeypatch.delenv("TRAJECTORY_OPENCLAW_ROOT", raising=False)

    assert trajectory_cli.expand_home("~") == str(tmp_path)
    assert trajectory_cli.expand_home("~/agent") == str(tmp_path / "agent")
    assert trajectory_cli.default_root("pi") == str(tmp_path / ".pi" / "agent")
    assert trajectory_cli.default_root("claude-code") == str(
        tmp_path / ".claude" / "projects"
    )
    assert trajectory_cli.default_root("codex") == str(tmp_path / ".codex" / "sessions")
    assert trajectory_cli.default_root("hermes") == str(tmp_path / ".hermes")
    # Prefer ~/.openclaw when present.
    (tmp_path / ".openclaw").mkdir()
    assert trajectory_cli.default_root("openclaw") == str(tmp_path / ".openclaw")

    monkeypatch.setenv("TRAJECTORY_PI_ROOT", "~/custom-pi")
    assert trajectory_cli.default_root("pi") == str(tmp_path / "custom-pi")


def test_openclaw_falls_back_to_clawdbot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("TRAJECTORY_OPENCLAW_ROOT", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.delenv("CLAWDBOT_STATE_DIR", raising=False)
    assert trajectory_cli.default_root("openclaw") == str(tmp_path / ".clawdbot")


def test_format_relative_time_and_sort() -> None:
    now = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert (
        trajectory_cli.format_relative_time("2026-08-13T12:00:00.000Z", now=now)
        == "just now"
    )
    assert (
        trajectory_cli.format_relative_time("2026-08-13T11:59:48.000Z", now=now)
        == "12s ago"
    )
    assert (
        trajectory_cli.format_relative_time("2026-08-13T11:55:00.000Z", now=now)
        == "5m ago"
    )
    assert (
        trajectory_cli.format_relative_time("2026-08-13T09:00:00.000Z", now=now)
        == "3h ago"
    )
    assert (
        trajectory_cli.format_relative_time("2026-08-11T12:00:00.000Z", now=now)
        == "2d ago"
    )
    assert (
        trajectory_cli.format_relative_time("2026-07-01T00:00:00.000Z", now=now)
        == "2026-07-01"
    )
    assert trajectory_cli.format_relative_time(None, now=now) == "—"

    from hypabolic_trajectory import TrajectoryListing

    older = TrajectoryListing(
        id="older", path="/o", updated_at="2026-08-13T11:00:00.000Z", title="Old"
    )
    newer = TrajectoryListing(
        id="newer", path="/n", updated_at="2026-08-13T11:59:00.000Z", title="New"
    )
    missing = TrajectoryListing(id="missing", path="/m", updated_at=None)
    ordered = trajectory_cli.sort_sessions_by_active([older, missing, newer])
    assert [item.id for item in ordered] == ["newer", "older", "missing"]
    label = trajectory_cli.format_session_choice(newer, now=now)
    assert label.startswith("1m ago")
    assert "New" in label
    assert "newer" in label


def test_format_bytes_and_truncate() -> None:
    assert trajectory_cli.format_bytes(512) == "512 B"
    assert trajectory_cli.format_bytes(2048).endswith("KB")
    assert trajectory_cli.truncate("hello", 10) == "hello"
    assert trajectory_cli.truncate("abcdefghij", 5) == "abcd…"


def test_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert trajectory_cli.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "browse" in out
    assert "list" in out
    assert "show" in out
    assert "pi" in out


def test_main_list_empty_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    empty = tmp_path / "empty-store"
    empty.mkdir()
    code = trajectory_cli.main(
        ["list", "--source", "pi", "--root", str(empty), "--limit", "5"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "No sessions found" in out


def test_main_list_hermes_empty_hint(capsys: pytest.CaptureFixture[str]) -> None:
    empty = Path(os.environ.get("TMPDIR", "/tmp")) / "trajectory-cli-hermes-empty"
    empty.mkdir(parents=True, exist_ok=True)
    code = trajectory_cli.main(
        ["list", "--source", "hermes", "--root", str(empty), "--limit", "5"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "SQLite-free" in out or "No sessions found" in out


def test_main_show_pi_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    assert FIXTURE_PI.is_file(), f"missing fixture {FIXTURE_PI}"
    code = trajectory_cli.main(
        [
            "show",
            "--source",
            "pi",
            "--path",
            str(FIXTURE_PI),
            "--format",
            "both",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "records" in out.lower() or "Roles" in out
    assert "Content omitted" in out
    assert "Hypabolic" in out or "Messages" in out


def test_main_show_ahp_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    assert FIXTURE_AHP.is_file(), f"missing fixture {FIXTURE_AHP}"
    code = trajectory_cli.main(
        [
            "show",
            "--source",
            "ahp",
            "--path",
            str(FIXTURE_AHP),
            "--format",
            "messages",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Messages" in out or "records" in out.lower()


def test_main_show_missing_path(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(
        ["show", "--source", "pi", "--path", "/no/such/file.jsonl"]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "invalid_input" in err


def test_main_show_requires_path_or_id(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(["show", "--source", "pi"])
    assert code == 2
    err = capsys.readouterr().err
    assert "invalid_input" in err
    assert "path" in err.lower() or "id" in err.lower()


def test_main_unknown_source(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(["list", "--source", "not-a-source"])
    assert code == 2
    err = capsys.readouterr().err
    assert "unknown_source" in err


def test_main_show_with_content(capsys: pytest.CaptureFixture[str]) -> None:
    code = trajectory_cli.main(
        [
            "show",
            "--source",
            "pi",
            "--path",
            str(FIXTURE_PI),
            "--show-content",
            "--format",
            "hypabolic",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "private" in out.lower()


def test_resolve_path_by_id(tmp_path: Path) -> None:
    """--id resolution lists under root and matches listing id."""
    # Empty root → id not found
    with pytest.raises(Exception) as exc_info:
        trajectory_cli.resolve_path("pi", str(tmp_path), None, "missing-id", 50)
    assert getattr(exc_info.value, "code", None) == "invalid_input"


def test_not_a_console_script_in_pyproject() -> None:
    """Hard packaging rule: sample CLI must not be a project script."""
    pyproject = (REPO_ROOT / "python" / "pyproject.toml").read_text(encoding="utf-8")
    # Comment may mention the rule; no real table may declare scripts.
    assert not any(
        line.strip().startswith("[project.scripts]") for line in pyproject.splitlines()
    )
    assert not any(
        line.strip().startswith("[project.gui-scripts]")
        for line in pyproject.splitlines()
    )
    # sdist exclude must keep samples out of the published payload
    # (root-anchored "/samples" or legacy "samples/**").
    assert (
        '"/samples"' in pyproject
        or "'/samples'" in pyproject
        or '"samples/**"' in pyproject
        or "'samples/**'" in pyproject
    )


def test_browse_empty_declines_path_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    empty = tmp_path / "empty"
    empty.mkdir()

    def fake_input(prompt: str = "") -> str:
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        code = trajectory_cli.main(
            ["browse", "--source", "pi", "--root", str(empty)]
        )
    assert code == 0
    out = capsys.readouterr().out
    assert "No sessions found" in out


def test_list_with_page_items(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """list prints a table when the lister returns items (pi under a fixture tree)."""
    # Point at a non-store directory still exercises empty path; for items we
    # patch list_trajectories to return synthetic rows.
    from hypabolic_trajectory import TrajectoryListing, TrajectoryListingPage

    fake_page = TrajectoryListingPage(
        items=(
            TrajectoryListing(
                id="sess-1",
                path=str(tmp_path / "a.jsonl"),
                updated_at="2026-01-01T00:00:00Z",
                size_bytes=128,
            ),
            TrajectoryListing(
                id="sess-2",
                path=str(tmp_path / "b.jsonl"),
                updated_at="2026-01-02T00:00:00Z",
                size_bytes=2048,
            ),
        ),
        next_cursor="cursor-more",
    )

    with patch.object(trajectory_cli, "list_trajectories", return_value=fake_page):
        code = trajectory_cli.main(
            ["list", "--source", "pi", "--root", str(tmp_path)]
        )
    assert code == 0
    out = capsys.readouterr().out
    assert "sess-1" in out
    assert "sess-2" in out
    assert "More sessions available" in out
