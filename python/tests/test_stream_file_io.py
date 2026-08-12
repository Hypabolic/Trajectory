"""LS-09 optional file I/O package tests (Python)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from hypabolic_trajectory.io import (
    HOST_IO_PERMISSION,
    HOST_PATH_OUTSIDE_ROOT,
    HOST_PATH_REQUIRED,
    HOST_ROOT_REQUIRED,
    FileStreamHostError,
    FileStreamOptions,
    FileTrajectoryStream,
)
from hypabolic_trajectory.streaming.framing import split_complete_lines

SESSION_LINE = (
    b'{"type":"session","version":3,"id":"stream-file-io-py",'
    b'"timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}\n'
)
USER_LINE = (
    b'{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z",'
    b'"message":{"role":"user","content":[{"type":"text","text":"hello"}]},'
    b'"sessionId":"stream-file-io-py"}\n'
)


def test_split_complete_lines_holds_incomplete() -> None:
    complete, pending = split_complete_lines(b"abc\ndef")
    assert complete == b"abc\n"
    assert pending == b"def"


def test_growth_and_incomplete_line(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_bytes(b"")

    fs = FileTrajectoryStream.open(
        FileStreamOptions(
            root=root,
            path=path,
            source="pi",
            group_id="stream-file-io-py",
        )
    )
    u0 = fs.poll()
    assert u0 is not None
    assert u0.kind == "updated"
    assert u0.snapshot is not None
    assert len(u0.snapshot.records) == 0

    path.write_bytes(SESSION_LINE + USER_LINE[:40])  # incomplete second line
    u1 = fs.poll()
    assert u1 is not None
    assert u1.kind == "updated"
    assert u1.snapshot is not None
    # Session meta committed; incomplete user line held at host — not materialized.
    records_after_partial = len(u1.snapshot.records)
    assert records_after_partial >= 1  # session meta
    assert not any(r.record.get("role") == "user" for r in u1.snapshot.records)

    path.write_bytes(SESSION_LINE + USER_LINE)
    u2 = fs.poll()
    assert u2 is not None
    assert u2.kind == "updated"
    assert u2.snapshot is not None
    assert len(u2.snapshot.records) > records_after_partial
    assert any(r.record.get("role") == "user" for r in u2.snapshot.records)
    # Host errors are not stream diagnostics.
    for d in u2.diagnostics:
        assert "path" not in d.code.lower()
        assert str(path) not in d.message


def test_coalesced_growth(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_bytes(SESSION_LINE)

    fs = FileTrajectoryStream.open(
        FileStreamOptions(root=root, path=path, source="pi", group_id="stream-file-io-py")
    )
    assert fs.poll() is not None

    # Two lines appear before next poll → one append of the complete segment.
    path.write_bytes(SESSION_LINE + USER_LINE)
    update = fs.poll()
    assert update is not None
    assert update.kind == "updated"
    assert update.snapshot is not None
    assert len(update.snapshot.records) >= 1


def test_truncation_surfaces_core_reset(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_bytes(SESSION_LINE + USER_LINE)

    fs = FileTrajectoryStream.open(
        FileStreamOptions(root=root, path=path, source="pi", group_id="stream-file-io-py")
    )
    first = fs.poll()
    assert first is not None
    assert first.kind == "updated"
    prior = fs.cursor.position.next_byte_offset  # type: ignore[union-attr]
    assert prior > 0

    path.write_bytes(SESSION_LINE)  # shrink
    update = fs.poll()
    assert update is not None
    assert update.kind == "reset-required"
    assert update.reset is not None
    assert update.reset.reason in (
        "source-truncated",
        "source-replaced",
        "source-compacted",
    )


def test_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "other" / "x.jsonl"
    outside.parent.mkdir()
    outside.write_bytes(b"\n")
    with pytest.raises(FileStreamHostError) as ei:
        FileTrajectoryStream.open(
            FileStreamOptions(root=root, path=outside, source="pi")
        )
    assert ei.value.code == HOST_PATH_OUTSIDE_ROOT
    assert ei.value.message == "File stream path is outside the explicit root."


def test_root_and_path_required(tmp_path: Path) -> None:
    with pytest.raises(FileStreamHostError) as ei:
        FileTrajectoryStream.open(
            FileStreamOptions(root="", path=tmp_path / "a.jsonl", source="pi")
        )
    assert ei.value.code == HOST_ROOT_REQUIRED

    with pytest.raises(FileStreamHostError) as ei2:
        FileTrajectoryStream.open(
            FileStreamOptions(root=tmp_path, path="", source="pi")
        )
    assert ei2.value.code == HOST_PATH_REQUIRED


@pytest.mark.skipif(sys.platform == "win32", reason="chmod permission model differs")
def test_permission_denied_is_host_error(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_bytes(SESSION_LINE)
    os.chmod(path, 0)
    try:
        fs = FileTrajectoryStream.open(
            FileStreamOptions(root=root, path=path, source="pi", group_id="x")
        )
        with pytest.raises(FileStreamHostError) as ei:
            fs.poll()
        assert ei.value.code in (HOST_IO_PERMISSION, "io_error")
        assert str(path) not in (ei.value.message or "")
        # Message is the fixed content-safe host string (path only on .path attr).
        assert "permission" in ei.value.message.lower() or ei.value.code == "io_error"
    finally:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def test_finish_flushes_host_pending(tmp_path: Path) -> None:
    """Host-held incomplete line must reach core finish (final unterminated commit)."""
    root = tmp_path / "store"
    root.mkdir()
    path = root / "session.jsonl"
    # Complete session + incomplete user line (no trailing LF).
    incomplete_user = USER_LINE.rstrip(b"\n")
    path.write_bytes(SESSION_LINE + incomplete_user)

    fs = FileTrajectoryStream.open(
        FileStreamOptions(
            root=root,
            path=path,
            source="pi",
            group_id="stream-file-io-py",
        )
    )
    u0 = fs.poll()
    assert u0 is not None
    assert u0.kind == "updated"
    # Incomplete user held at host edge only (session meta may exist).
    assert u0.snapshot is not None
    assert not any(r.record.get("role") == "user" for r in u0.snapshot.records)
    records_before_finish = len(u0.snapshot.records)

    finished = fs.finish()
    assert finished.kind in ("updated", "unchanged")
    assert fs.stream.state.finished is True
    # Final unterminated user line committed via finish.
    assert finished.snapshot is not None
    assert len(finished.snapshot.records) > records_before_finish
    assert any(r.record.get("role") == "user" for r in finished.snapshot.records)


def test_core_streaming_has_no_io_package_import() -> None:
    """Core stream modules must not import the optional io package."""
    import hypabolic_trajectory.streaming.apply as apply_mod
    import hypabolic_trajectory.streaming.types as types_mod

    for mod in (apply_mod, types_mod):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        assert "hypabolic_trajectory.io" not in text
        assert "FileTrajectoryStream" not in text
