"""Hermes provider: list / query / change-token → core apply_hermes_export."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from hypabolic_trajectory.identity import sha256_hex
from hypabolic_trajectory.streaming.apply import (
    apply_hermes_export,
    create_stream,
    reset_stream,
)
from hypabolic_trajectory.streaming.types import (
    HermesRowPosition,
    StreamCursor,
    StreamOptions,
    StreamResetRequest,
    StreamState,
    StreamUpdate,
)

HOST_STORE_REQUIRED = "store_required"
HOST_SESSION_NOT_FOUND = "session_not_found"
HOST_DB_ERROR = "db_error"

_MSG_STORE_REQUIRED = "Hermes provider store path is required."
_MSG_SESSION_NOT_FOUND = "Hermes session was not found in the provider store."
_MSG_DB_ERROR = "Hermes provider could not query the store."

# Minimal schema for fixture / provider-owned temp DBs (not a full Hermes migrate).
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'tui',
    model TEXT,
    title TEXT,
    cwd TEXT,
    system_prompt TEXT,
    started_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL DEFAULT 0,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    observed INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id);
"""


class HermesHostError(Exception):
    """Host-side provider error (not a stream diagnostic)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True, kw_only=True)
class HermesSessionInfo:
    session_id: str
    title: str | None = None
    model: str | None = None
    started_at: float | None = None
    source: str | None = None


class HermesStore(Protocol):
    """Provider storage surface: list sessions, query export, change generation."""

    def list_sessions(self) -> list[HermesSessionInfo]: ...

    def export_session(self, session_id: str) -> bytes: ...

    def database_generation(self) -> str: ...


def _is_inactive(row: dict[str, Any]) -> bool:
    active = row.get("active", 1)
    return active in (0, False, "0")


def _order_active_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = [m for m in messages if not _is_inactive(m)]
    if active and all(_is_number_id(m.get("id")) for m in active):
        indexed = list(enumerate(active))
        indexed.sort(key=lambda item: (int(item[1]["id"]), item[0]))
        return [m for _, m in indexed]
    return active


def _is_number_id(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return False


def compute_change_token(messages: list[dict[str, Any]]) -> str:
    """Opaque change token over ordered active message fingerprints."""
    active = _order_active_messages(messages)
    parts: list[str] = []
    for row in active:
        subset = {
            "id": row.get("id"),
            "role": row.get("role"),
            "content": row.get("content"),
            "tool_call_id": row.get("tool_call_id"),
            "tool_name": row.get("tool_name"),
            "tool_calls": row.get("tool_calls"),
            "finish_reason": row.get("finish_reason"),
            "timestamp": row.get("timestamp"),
            "active": row.get("active", 1),
        }
        parts.append(
            sha256_hex(
                json.dumps(
                    subset, separators=(",", ":"), sort_keys=True, ensure_ascii=False
                ).encode("utf-8")
            )
        )
    return sha256_hex("|".join(parts).encode("utf-8") if parts else b"")


def export_session_json(
    *,
    session: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> bytes:
    """Build Hermes export envelope bytes (active rows only, ordered)."""
    active = _order_active_messages(messages)
    if session is not None:
        payload: dict[str, Any] = {"session": session, "messages": active}
    else:
        payload = {"messages": active}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass
class MemoryHermesStore:
    """In-memory Hermes store for CI fixtures (no SQLite)."""

    database_generation_value: str = "mem-0"
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _next_row_id: int = 1

    def list_sessions(self) -> list[HermesSessionInfo]:
        out: list[HermesSessionInfo] = []
        for sid, sess in sorted(self.sessions.items()):
            out.append(
                HermesSessionInfo(
                    session_id=sid,
                    title=sess.get("title") if isinstance(sess.get("title"), str) else None,
                    model=sess.get("model") if isinstance(sess.get("model"), str) else None,
                    started_at=(
                        float(sess["started_at"])
                        if isinstance(sess.get("started_at"), (int, float))
                        else None
                    ),
                    source=sess.get("source") if isinstance(sess.get("source"), str) else None,
                )
            )
        return out

    def export_session(self, session_id: str) -> bytes:
        if session_id not in self.sessions and session_id not in self.messages:
            raise HermesHostError(HOST_SESSION_NOT_FOUND, _MSG_SESSION_NOT_FOUND)
        session = self.sessions.get(session_id)
        messages = list(self.messages.get(session_id, []))
        return export_session_json(session=session, messages=messages)

    def database_generation(self) -> str:
        return self.database_generation_value

    def upsert_session(self, session: dict[str, Any]) -> None:
        sid = session.get("id")
        if not isinstance(sid, str) or not sid:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR)
        self.sessions[sid] = dict(session)

    def append_message(self, session_id: str, row: dict[str, Any]) -> dict[str, Any]:
        msg = dict(row)
        if "id" not in msg:
            msg["id"] = self._next_row_id
            self._next_row_id += 1
        elif _is_number_id(msg["id"]):
            self._next_row_id = max(self._next_row_id, int(msg["id"]) + 1)
        msg.setdefault("session_id", session_id)
        msg.setdefault("active", 1)
        self.messages.setdefault(session_id, []).append(msg)
        return msg

    def soft_delete_message(self, session_id: str, message_id: Any) -> None:
        rows = self.messages.get(session_id, [])
        for row in rows:
            if row.get("id") == message_id:
                row["active"] = 0
                return
        raise HermesHostError(HOST_SESSION_NOT_FOUND, _MSG_SESSION_NOT_FOUND)

    def set_database_generation(self, value: str) -> None:
        self.database_generation_value = value


class SqliteHermesProvider:
    """SQLite Hermes store: query rows in a read transaction (no byte-tail)."""

    def __init__(self, path: str | Path, *, database_generation: str | None = None) -> None:
        if path is None or (isinstance(path, str) and not str(path).strip()):
            raise HermesHostError(HOST_STORE_REQUIRED, _MSG_STORE_REQUIRED)
        self._path = Path(path)
        self._generation = database_generation or f"sqlite:{self._path.resolve()}"

    @property
    def path(self) -> Path:
        return self._path

    def database_generation(self) -> str:
        return self._generation

    def set_database_generation(self, value: str) -> None:
        self._generation = value

    def initialize_schema(self) -> None:
        """Create minimal sessions/messages tables for fixture DBs."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self._path) as conn:
                conn.executescript(_SCHEMA_SQL)
                conn.commit()
        except sqlite3.Error as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err

    def list_sessions(self) -> list[HermesSessionInfo]:
        if not self._path.is_file():
            return []
        try:
            with sqlite3.connect(self._path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, source, model, title, started_at FROM sessions ORDER BY id"
                ).fetchall()
        except sqlite3.Error as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err
        out: list[HermesSessionInfo] = []
        for r in rows:
            out.append(
                HermesSessionInfo(
                    session_id=str(r["id"]),
                    title=r["title"] if r["title"] is not None else None,
                    model=r["model"] if r["model"] is not None else None,
                    started_at=float(r["started_at"]) if r["started_at"] is not None else None,
                    source=r["source"] if r["source"] is not None else None,
                )
            )
        return out

    def export_session(self, session_id: str) -> bytes:
        if not self._path.is_file():
            raise HermesHostError(HOST_SESSION_NOT_FOUND, _MSG_SESSION_NOT_FOUND)
        try:
            with sqlite3.connect(self._path) as conn:
                conn.row_factory = sqlite3.Row
                # Read transaction for a consistent export snapshot.
                conn.execute("BEGIN")
                sess_row = conn.execute(
                    "SELECT * FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                msg_rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                    (session_id,),
                ).fetchall()
                conn.execute("COMMIT")
        except sqlite3.Error as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err

        if sess_row is None and not msg_rows:
            raise HermesHostError(HOST_SESSION_NOT_FOUND, _MSG_SESSION_NOT_FOUND)

        session = dict(sess_row) if sess_row is not None else None
        messages = [dict(r) for r in msg_rows]
        # tool_calls may be stored as JSON text — leave as-is for decoder.
        return export_session_json(session=session, messages=messages)

    def insert_session(self, session: dict[str, Any]) -> None:
        self.initialize_schema()
        sid = session.get("id")
        if not isinstance(sid, str) or not sid:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR)
        try:
            with sqlite3.connect(self._path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sessions
                    (id, source, model, title, cwd, system_prompt, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        session.get("source") or "tui",
                        session.get("model"),
                        session.get("title"),
                        session.get("cwd"),
                        session.get("system_prompt"),
                        session.get("started_at"),
                    ),
                )
                conn.commit()
        except sqlite3.Error as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err

    def insert_message(self, session_id: str, row: dict[str, Any]) -> int:
        self.initialize_schema()
        tool_calls = row.get("tool_calls")
        if tool_calls is not None and not isinstance(tool_calls, str):
            tool_calls = json.dumps(tool_calls, separators=(",", ":"), ensure_ascii=False)
        try:
            with sqlite3.connect(self._path) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO messages
                    (id, session_id, role, content, tool_call_id, tool_calls, tool_name,
                     timestamp, finish_reason, reasoning, reasoning_content, observed, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("id"),
                        session_id,
                        row.get("role") or "user",
                        row.get("content"),
                        row.get("tool_call_id"),
                        tool_calls,
                        row.get("tool_name"),
                        row.get("timestamp") or 0,
                        row.get("finish_reason"),
                        row.get("reasoning"),
                        row.get("reasoning_content"),
                        row.get("observed", 0),
                        1 if row.get("active", 1) not in (0, False, "0") else 0,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
        except sqlite3.Error as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err

    def soft_delete_message(self, session_id: str, message_id: int) -> None:
        try:
            with sqlite3.connect(self._path) as conn:
                cur = conn.execute(
                    "UPDATE messages SET active = 0 WHERE session_id = ? AND id = ?",
                    (session_id, message_id),
                )
                conn.commit()
                if cur.rowcount == 0:
                    raise HermesHostError(HOST_SESSION_NOT_FOUND, _MSG_SESSION_NOT_FOUND)
        except sqlite3.Error as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err


@dataclass(frozen=True, slots=True, kw_only=True)
class HermesProviderOptions:
    session_id: str
    store: HermesStore
    stream: StreamOptions | None = None
    group_id: str | None = None


class HermesProviderStream:
    """Poll a Hermes store for one session and feed core apply_hermes_export."""

    def __init__(
        self,
        *,
        store: HermesStore,
        session_id: str,
        stream_state: StreamState,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._state = stream_state
        self._closed = False

    @classmethod
    def open(cls, options: HermesProviderOptions) -> HermesProviderStream:
        stream_opts = options.stream
        group = options.group_id or options.session_id
        if stream_opts is None:
            stream_opts = StreamOptions(source="hermes", group_id=group)
        state = create_stream(stream_opts)
        return cls(store=options.store, session_id=options.session_id, stream_state=state)

    @property
    def cursor(self) -> StreamCursor:
        return self._state.cursor

    @property
    def state(self) -> StreamState:
        return self._state

    def list_sessions(self) -> list[HermesSessionInfo]:
        return self._store.list_sessions()

    def poll(self) -> StreamUpdate | None:
        """Query export once; apply or signal reset-required. None only when closed."""
        if self._closed:
            return None

        gen = self._store.database_generation()
        try:
            export = self._store.export_session(self._session_id)
        except HermesHostError:
            raise
        except Exception as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err

        # Parse messages for change token (active ordered).
        try:
            parsed = json.loads(export.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as err:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR) from err
        if isinstance(parsed, list):
            messages = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
            messages = list(parsed["messages"])
        else:
            raise HermesHostError(HOST_DB_ERROR, _MSG_DB_ERROR)
        token = compute_change_token(messages)

        # Generation change while live → install new generation via reset + export.
        if (
            self._state.snapshot is not None
            and isinstance(self._state.cursor.position, HermesRowPosition)
            and self._state.cursor.position.database_generation
            and self._state.cursor.position.database_generation != gen
        ):
            self._state, update = reset_stream(
                self._state,
                StreamResetRequest(
                    reason="source-replaced",
                    source_revision=gen,
                    material=export,
                    change_token=token,
                ),
            )
            return update

        self._state, update = apply_hermes_export(
            self._state,
            export,
            change_token=token,
            database_generation=gen,
            source_revision=gen,
            cursor=None,
        )
        return update

    def close(self) -> None:
        self._closed = True
