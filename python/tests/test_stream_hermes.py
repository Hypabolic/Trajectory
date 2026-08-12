"""LS-07h: Hermes export stream apply + optional provider fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from hypabolic_trajectory import (
    HermesRowPosition,
    StreamOptions,
    apply_delta_to_snapshot,
    apply_hermes_export,
    create_stream,
    reset_stream,
)
from hypabolic_trajectory.hermes_provider import (
    HermesProviderOptions,
    HermesProviderStream,
    MemoryHermesStore,
    SqliteHermesProvider,
    compute_change_token,
)
from hypabolic_trajectory.streaming.types import StreamResetRequest

ROOT = Path(__file__).resolve().parents[2]
HERMES_FIXTURE = ROOT / "conformance" / "cases" / "hermes" / "tool-calls" / "input.json"


def _tool_calls_export() -> bytes:
    return HERMES_FIXTURE.read_bytes()


def test_hermes_export_snapshot_and_idempotent() -> None:
    material = _tool_calls_export()
    state = create_stream(
        StreamOptions(source="hermes", group_id="hermes-session-0001")
    )
    assert isinstance(state.cursor.position, HermesRowPosition)

    state, u1 = apply_hermes_export(
        state,
        material,
        change_token="tok-1",
        database_generation="db-1",
        source_revision="db-1",
    )
    assert u1.kind == "updated"
    assert u1.snapshot is not None
    assert u1.delta is not None
    assert len(u1.snapshot.records) >= 2
    assert isinstance(u1.cursor.position, HermesRowPosition)
    assert u1.cursor.position.database_generation == "db-1"
    assert u1.cursor.position.change_token == "tok-1"
    assert u1.cursor.position.last_row_id == 104

    recon = apply_delta_to_snapshot(None, u1.delta.to_dict())
    assert recon["records"] == u1.snapshot.to_dict()["records"]

    _, u_dup = apply_hermes_export(
        state,
        material,
        change_token="tok-1",
        database_generation="db-1",
        source_revision="db-1",
    )
    assert u_dup.kind == "unchanged"


def test_hermes_export_insert_growth() -> None:
    base = json.loads(_tool_calls_export().decode("utf-8"))
    messages = list(base["messages"])
    partial = {
        "session": base["session"],
        "messages": messages[:1],
    }
    full = base

    state = create_stream(
        StreamOptions(source="hermes", group_id="hermes-session-0001")
    )
    state, u1 = apply_hermes_export(
        state,
        json.dumps(partial).encode("utf-8"),
        change_token="t-partial",
        database_generation="db-1",
    )
    assert u1.kind == "updated"
    n1 = len(u1.snapshot.records) if u1.snapshot else 0

    state, u2 = apply_hermes_export(
        state,
        json.dumps(full).encode("utf-8"),
        change_token="t-full",
        database_generation="db-1",
    )
    assert u2.kind == "updated"
    assert u2.snapshot is not None
    assert len(u2.snapshot.records) > n1
    assert isinstance(u2.cursor.position, HermesRowPosition)
    assert u2.cursor.position.last_row_id == 104


def test_hermes_export_soft_delete_reset() -> None:
    base = json.loads(_tool_calls_export().decode("utf-8"))
    state = create_stream(
        StreamOptions(source="hermes", group_id="hermes-session-0001")
    )
    state, u1 = apply_hermes_export(
        state,
        json.dumps(base).encode("utf-8"),
        change_token="t1",
        database_generation="db-1",
    )
    assert u1.kind == "updated"
    prior = state.cursor

    # Soft-delete the first user message (active=0) — prior fingerprint prefix breaks.
    mutated = json.loads(json.dumps(base))
    mutated["messages"][0]["active"] = 0
    state2, u2 = apply_hermes_export(
        state,
        json.dumps(mutated).encode("utf-8"),
        change_token="t2",
        database_generation="db-1",
    )
    assert u2.kind == "reset-required"
    assert u2.reset is not None
    assert u2.reset.reason == "source-replaced"
    assert state2.cursor.position == prior.position

    # After explicit reset, soft-deleted export applies.
    state3, u3 = reset_stream(
        state,
        StreamResetRequest(
            reason="source-replaced",
            source_revision="db-1",
            material=json.dumps(mutated).encode("utf-8"),
            change_token="t2",
        ),
    )
    assert u3.kind == "updated"
    assert u3.reset is not None
    assert state3.generation >= 1


def test_hermes_export_nonnumeric_ids() -> None:
    export = {
        "session": {"id": "s-nonnum", "source": "tui", "started_at": 1.0},
        "messages": [
            {
                "id": "msg-a",
                "session_id": "s-nonnum",
                "role": "user",
                "content": "hello",
                "timestamp": 1.0,
                "active": 1,
            },
            {
                "id": "msg-b",
                "session_id": "s-nonnum",
                "role": "assistant",
                "content": "world",
                "timestamp": 2.0,
                "active": 1,
                "finish_reason": "stop",
            },
        ],
    }
    material = json.dumps(export).encode("utf-8")
    state = create_stream(StreamOptions(source="hermes", group_id="s-nonnum"))
    state, u = apply_hermes_export(
        state, material, change_token="nn-1", database_generation="db-nn"
    )
    assert u.kind == "updated"
    assert isinstance(u.cursor.position, HermesRowPosition)
    assert u.cursor.position.last_row_id is None
    assert u.snapshot is not None
    assert any(r.record.get("role") == "user" for r in u.snapshot.records)


def test_hermes_export_wrong_source() -> None:
    state = create_stream(StreamOptions(source="pi", group_id="g"))
    _, u = apply_hermes_export(state, b"[]", change_token="x", database_generation="g")
    assert u.kind == "error"
    assert u.error is not None
    assert "hermes" in u.error.message.lower()


def test_memory_provider_snapshot_insert_soft_delete() -> None:
    store = MemoryHermesStore(database_generation_value="mem-1")
    store.upsert_session(
        {
            "id": "sess-mem",
            "source": "tui",
            "model": "gpt-test",
            "started_at": 100.0,
            "title": "mem",
        }
    )
    store.append_message(
        "sess-mem",
        {
            "id": 1,
            "role": "user",
            "content": "hi",
            "timestamp": 101.0,
            "active": 1,
        },
    )

    stream = HermesProviderStream.open(
        HermesProviderOptions(session_id="sess-mem", store=store, group_id="sess-mem")
    )
    sessions = stream.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == "sess-mem"

    u0 = stream.poll()
    assert u0 is not None
    assert u0.kind == "updated"
    assert u0.snapshot is not None
    n0 = len(u0.snapshot.records)

    store.append_message(
        "sess-mem",
        {
            "id": 2,
            "role": "assistant",
            "content": "hello",
            "timestamp": 102.0,
            "active": 1,
            "finish_reason": "stop",
        },
    )
    u1 = stream.poll()
    assert u1 is not None
    assert u1.kind == "updated"
    assert u1.snapshot is not None
    assert len(u1.snapshot.records) > n0

    store.soft_delete_message("sess-mem", 1)
    u2 = stream.poll()
    assert u2 is not None
    assert u2.kind == "reset-required"

    # Generation change forces reset install path.
    store.append_message(
        "sess-mem",
        {
            "id": 3,
            "role": "user",
            "content": "again",
            "timestamp": 103.0,
            "active": 1,
        },
    )
    store.set_database_generation("mem-2")
    # New stream after soft-delete reset.
    stream2 = HermesProviderStream.open(
        HermesProviderOptions(session_id="sess-mem", store=store, group_id="sess-mem")
    )
    u3 = stream2.poll()
    assert u3 is not None
    assert u3.kind == "updated"


def test_sqlite_provider_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    provider = SqliteHermesProvider(db, database_generation="sql-1")
    provider.initialize_schema()
    provider.insert_session(
        {
            "id": "sess-sql",
            "source": "tui",
            "model": "gpt-test",
            "title": "sql",
            "started_at": 200.0,
        }
    )
    provider.insert_message(
        "sess-sql",
        {
            "id": 10,
            "role": "user",
            "content": "from sqlite",
            "timestamp": 201.0,
            "active": 1,
        },
    )
    provider.insert_message(
        "sess-sql",
        {
            "id": 11,
            "role": "assistant",
            "content": "ok",
            "timestamp": 202.0,
            "active": 1,
            "finish_reason": "stop",
        },
    )

    assert len(provider.list_sessions()) == 1
    export = provider.export_session("sess-sql")
    token = compute_change_token(json.loads(export.decode("utf-8"))["messages"])
    assert len(token) == 64

    stream = HermesProviderStream.open(
        HermesProviderOptions(
            session_id="sess-sql", store=provider, group_id="sess-sql"
        )
    )
    u = stream.poll()
    assert u is not None
    assert u.kind == "updated"
    assert u.snapshot is not None
    assert any(r.record.get("role") == "user" for r in u.snapshot.records)
    assert isinstance(u.cursor.position, HermesRowPosition)
    assert u.cursor.position.last_row_id == 11

    provider.soft_delete_message("sess-sql", 10)
    u2 = stream.poll()
    assert u2 is not None
    assert u2.kind == "reset-required"
