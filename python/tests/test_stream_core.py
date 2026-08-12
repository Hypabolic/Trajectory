"""LS-03 / LS-04: stream state, snapshot apply, delta-apply equivalence."""

from __future__ import annotations

from pathlib import Path

import pytest

from hypabolic_trajectory import (
    StreamOptions,
    TrajectoryStream,
    apply_delta_to_snapshot,
    apply_snapshot,
    create_stream,
)
from hypabolic_trajectory.streaming.framing import split_complete_lines
from hypabolic_trajectory.streaming.types import (
    BytePosition,
    StreamCursor,
    StreamResetRequest,
)

ROOT = Path(__file__).resolve().parents[2]
STREAM_CASES = ROOT / "conformance" / "cases" / "streaming"


def _read(case: str, name: str) -> bytes:
    return (STREAM_CASES / case / name).read_bytes()


def test_split_complete_lines_holds_unterminated() -> None:
    committed, pending = split_complete_lines(b'{"a":1}\n{"b":')
    assert committed == b'{"a":1}\n'
    assert pending == b'{"b":'
    committed, pending = split_complete_lines(b"")
    assert committed == b"" and pending == b""
    committed, pending = split_complete_lines(b"no-newline")
    assert committed == b"" and pending == b"no-newline"


def test_empty_prefix_snapshot() -> None:
    state = create_stream(StreamOptions(source="pi", group_id="stream-empty-prefix"))
    state, update = apply_snapshot(state, b"", source_revision="gen-0")
    assert update.kind == "updated"
    assert update.snapshot is not None
    assert update.delta is not None
    assert update.snapshot.records == ()
    assert update.snapshot.complete is False
    assert update.cursor.group_id == "stream-empty-prefix"
    assert isinstance(update.cursor.position, BytePosition)
    assert update.cursor.position.next_byte_offset == 0

    # Idempotent duplicate
    state2, update2 = apply_snapshot(state, b"", source_revision="gen-0")
    assert update2.kind == "unchanged"
    assert state2.cursor.prefix_sha256 == state.cursor.prefix_sha256


def test_snapshot_delta_equivalence() -> None:
    a = _read("snapshot-delta-equivalence", "step-a.jsonl")
    b = _read("snapshot-delta-equivalence", "step-b.jsonl")
    state = create_stream(
        StreamOptions(source="pi", group_id="stream-snapshot-delta-equivalence")
    )
    state, u1 = apply_snapshot(state, a, source_revision="gen-0")
    assert u1.kind == "updated"
    assert u1.snapshot is not None and u1.delta is not None
    recon0 = apply_delta_to_snapshot(None, u1.delta.to_dict())
    assert recon0["records"] == u1.snapshot.to_dict()["records"]

    state, u2 = apply_snapshot(state, b, source_revision="gen-0")
    assert u2.kind == "updated"
    assert u2.snapshot is not None and u2.delta is not None
    recon = apply_delta_to_snapshot(u1.snapshot.to_dict(), u2.delta.to_dict())
    assert recon["records"] == u2.snapshot.to_dict()["records"]
    assert recon["diagnostics"] == u2.snapshot.to_dict()["diagnostics"]
    assert recon["revision"] == u2.snapshot.to_dict()["revision"]


def test_record_replacement_upsert() -> None:
    v1 = _read("record-replacement", "step-v1.jsonl")
    v2 = _read("record-replacement", "step-v2.jsonl")
    state = create_stream(
        StreamOptions(source="pi", group_id="stream-record-replacement")
    )
    state, u1 = apply_snapshot(state, v1, source_revision="gen-0")
    state, u2 = apply_snapshot(state, v2, source_revision="gen-0")
    assert u2.kind == "updated"
    assert u2.delta is not None
    ops = [op.op for op in u2.delta.operations]
    assert "upsert" in ops
    recon = apply_delta_to_snapshot(u1.snapshot.to_dict(), u2.delta.to_dict())
    assert recon["records"] == u2.snapshot.to_dict()["records"]


def test_source_group_conflict() -> None:
    m1 = _read("source-group-conflict", "step-matching.jsonl")
    m2 = _read("source-group-conflict", "step-foreign-group.jsonl")
    state = create_stream(
        StreamOptions(source="pi", group_id="stream-expected-group")
    )
    state, u1 = apply_snapshot(state, m1, source_revision="gen-0")
    assert u1.kind == "updated"
    prior_offset = state.cursor.position.next_byte_offset  # type: ignore[union-attr]
    state2, u2 = apply_snapshot(state, m2, source_revision="gen-0")
    assert u2.kind == "reset-required"
    assert u2.reset is not None
    assert u2.reset.reason == "group-changed"
    # Atomic: cursor unchanged
    assert state2.cursor.position.next_byte_offset == prior_offset  # type: ignore[union-attr]


def test_file_truncate_reset() -> None:
    long = _read("file-truncate-reset", "step-long.jsonl")
    short = _read("file-truncate-reset", "step-truncated.jsonl")
    state = create_stream(
        StreamOptions(source="pi", group_id="stream-file-truncate-reset")
    )
    state, u1 = apply_snapshot(state, long, source_revision="gen-0")
    assert u1.kind == "updated"
    state2, u2 = apply_snapshot(state, short, source_revision="gen-0")
    assert u2.kind == "reset-required"
    assert u2.reset is not None
    assert u2.reset.reason == "source-truncated"
    assert state2.cursor.prefix_sha256 == state.cursor.prefix_sha256


def test_trajectory_stream_facade() -> None:
    stream = TrajectoryStream.create(
        StreamOptions(source="pi", group_id="stream-empty-prefix")
    )
    update = stream.apply_snapshot(b"", source_revision="gen-0")
    assert update.kind == "updated"
    assert stream.cursor.group_id == "stream-empty-prefix"


def test_cursor_conflict_atomic() -> None:
    state = create_stream(StreamOptions(source="pi", group_id="g"))
    state, _ = apply_snapshot(state, b"", source_revision="gen-0")
    bad = StreamCursor(
        source="pi",
        group_id="g",
        generation=0,
        position=BytePosition(next_byte_offset=99, pending_byte_length=0),
        source_revision="gen-0",
        prefix_sha256=state.cursor.prefix_sha256,
    )
    state2, update = apply_snapshot(
        state, b"", source_revision="gen-0", cursor=bad
    )
    assert update.kind == "reset-required"
    assert update.reset is not None
    assert update.reset.reason == "cursor-mismatch"
    assert state2.cursor.position.next_byte_offset == 0  # type: ignore[union-attr]


def test_reset_installs_new_generation() -> None:
    long = _read("file-truncate-reset", "step-long.jsonl")
    short = _read("file-truncate-reset", "step-truncated.jsonl")
    from hypabolic_trajectory.streaming import reset_stream

    state = create_stream(
        StreamOptions(source="pi", group_id="stream-file-truncate-reset")
    )
    state, _ = apply_snapshot(state, long, source_revision="gen-0")
    state, update = reset_stream(
        state,
        StreamResetRequest(
            reason="source-truncated",
            generation=1,
            source_revision="gen-1",
            material=short,
        ),
    )
    assert update.kind == "updated"
    assert state.generation == 1
    assert state.cursor.generation == 1


def test_diagnostic_key_null_input_line_uses_sentinel() -> None:
    from hypabolic_trajectory.streaming.delta import diagnostic_key

    assert diagnostic_key({"code": "x", "input_line": None, "record_index": None}) == "x|-|-"
    assert diagnostic_key({"code": "x"}) == "x|-|-"
    assert diagnostic_key({"code": "x", "input_line": 3}) == "x|3|-"


def test_max_line_bytes_returns_buffer_limit() -> None:
    state = create_stream(
        StreamOptions(source="pi", group_id="g", max_line_bytes=4)
    )
    state2, update = apply_snapshot(state, b'{"a":1}\n', source_revision="gen-0")
    assert update.kind == "error"
    assert update.error is not None
    assert update.error.code == "stream_buffer_limit"
    assert state2.cursor.position.next_byte_offset == 0  # type: ignore[union-attr]


def test_no_io_imports_in_streaming_modules() -> None:
    """Core stream modules must not import filesystem/network/sqlite."""
    import hypabolic_trajectory.streaming.apply as apply_mod
    import hypabolic_trajectory.streaming.delta as delta_mod
    import hypabolic_trajectory.streaming.framing as framing_mod
    import hypabolic_trajectory.streaming.types as types_mod

    for mod in (apply_mod, delta_mod, framing_mod, types_mod):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        for banned in (
            "import sqlite3",
            "from sqlite3",
            "import socket",
            "from pathlib import",
            "import pathlib",
            "watchdog",
            "asyncio",
        ):
            assert banned not in text, f"{mod.__name__} contains {banned!r}"
