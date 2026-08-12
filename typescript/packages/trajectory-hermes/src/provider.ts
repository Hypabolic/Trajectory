/**
 * Hermes provider: list / query / change-token → core applyHermesExport.
 * Uses node:sqlite when available; MemoryHermesStore for pure fixture CI.
 */
import { createHash } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

import {
  applyHermesExport,
  createStream,
  resetStream,
  type StreamCursor,
  type StreamOptions,
  type StreamState,
  type StreamUpdate,
} from "@hypabolic/trajectory";

export const HOST_STORE_REQUIRED = "store_required";
export const HOST_SESSION_NOT_FOUND = "session_not_found";
export const HOST_DB_ERROR = "db_error";

const MSG_STORE_REQUIRED = "Hermes provider store path is required.";
const MSG_SESSION_NOT_FOUND = "Hermes session was not found in the provider store.";
const MSG_DB_ERROR = "Hermes provider could not query the store.";

const SCHEMA_SQL = `
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
`;

export class HermesHostError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
    this.name = "HermesHostError";
  }
}

export interface HermesSessionInfo {
  readonly sessionId: string;
  readonly title?: string | null;
  readonly model?: string | null;
  readonly startedAt?: number | null;
  readonly source?: string | null;
}

export interface HermesStore {
  listSessions(): HermesSessionInfo[];
  exportSession(sessionId: string): Uint8Array;
  databaseGeneration(): string;
}

type JsonObject = Record<string, unknown>;

function isInactive(row: JsonObject): boolean {
  const a = row.active ?? 1;
  return a === 0 || a === false || a === "0";
}

function isNumberId(value: unknown): boolean {
  if (typeof value === "boolean") return false;
  return typeof value === "number" && Number.isInteger(value);
}

function orderActiveMessages(messages: JsonObject[]): JsonObject[] {
  let active = messages.filter((m) => !isInactive(m));
  if (active.length > 0 && active.every((m) => isNumberId(m.id))) {
    active = [...active].sort((a, b) => Number(a.id) - Number(b.id));
  }
  return active;
}

function sha256Hex(data: string | Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

export function computeChangeToken(messages: JsonObject[]): string {
  const active = orderActiveMessages(messages);
  const parts = active.map((row) =>
    sha256Hex(
      JSON.stringify({
        id: row.id,
        role: row.role,
        content: row.content,
        tool_call_id: row.tool_call_id,
        tool_name: row.tool_name,
        tool_calls: row.tool_calls,
        finish_reason: row.finish_reason,
        timestamp: row.timestamp,
        active: row.active ?? 1,
      }),
    ),
  );
  return sha256Hex(parts.length ? parts.join("|") : "");
}

export function exportSessionJson(
  session: JsonObject | null | undefined,
  messages: JsonObject[],
): Uint8Array {
  const active = orderActiveMessages(messages);
  const payload =
    session != null ? { session, messages: active } : { messages: active };
  return new TextEncoder().encode(JSON.stringify(payload));
}

export class MemoryHermesStore implements HermesStore {
  databaseGenerationValue = "mem-0";
  sessions = new Map<string, JsonObject>();
  messages = new Map<string, JsonObject[]>();
  #nextRowId = 1;

  listSessions(): HermesSessionInfo[] {
    return [...this.sessions.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([sessionId, sess]) => ({
        sessionId,
        title: typeof sess.title === "string" ? sess.title : null,
        model: typeof sess.model === "string" ? sess.model : null,
        startedAt: typeof sess.started_at === "number" ? sess.started_at : null,
        source: typeof sess.source === "string" ? sess.source : null,
      }));
  }

  exportSession(sessionId: string): Uint8Array {
    if (!this.sessions.has(sessionId) && !this.messages.has(sessionId)) {
      throw new HermesHostError(HOST_SESSION_NOT_FOUND, MSG_SESSION_NOT_FOUND);
    }
    return exportSessionJson(
      this.sessions.get(sessionId) ?? null,
      this.messages.get(sessionId) ?? [],
    );
  }

  databaseGeneration(): string {
    return this.databaseGenerationValue;
  }

  upsertSession(session: JsonObject): void {
    const sid = session.id;
    if (typeof sid !== "string" || !sid) {
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
    this.sessions.set(sid, { ...session });
  }

  appendMessage(sessionId: string, row: JsonObject): JsonObject {
    const msg = { ...row };
    if (msg.id === undefined) {
      msg.id = this.#nextRowId++;
    } else if (isNumberId(msg.id)) {
      this.#nextRowId = Math.max(this.#nextRowId, Number(msg.id) + 1);
    }
    if (msg.session_id === undefined) msg.session_id = sessionId;
    if (msg.active === undefined) msg.active = 1;
    const list = this.messages.get(sessionId) ?? [];
    list.push(msg);
    this.messages.set(sessionId, list);
    return msg;
  }

  softDeleteMessage(sessionId: string, messageId: unknown): void {
    const list = this.messages.get(sessionId) ?? [];
    for (const row of list) {
      if (row.id === messageId) {
        row.active = 0;
        return;
      }
    }
    throw new HermesHostError(HOST_SESSION_NOT_FOUND, MSG_SESSION_NOT_FOUND);
  }

  setDatabaseGeneration(value: string): void {
    this.databaseGenerationValue = value;
  }
}

export class SqliteHermesProvider implements HermesStore {
  readonly path: string;
  #generation: string;

  constructor(path: string, databaseGeneration?: string) {
    if (!path || !path.trim()) {
      throw new HermesHostError(HOST_STORE_REQUIRED, MSG_STORE_REQUIRED);
    }
    this.path = resolve(path);
    this.#generation = databaseGeneration ?? `sqlite:${this.path}`;
  }

  databaseGeneration(): string {
    return this.#generation;
  }

  setDatabaseGeneration(value: string): void {
    this.#generation = value;
  }

  initializeSchema(): void {
    mkdirSync(dirname(this.path), { recursive: true });
    try {
      const db = new DatabaseSync(this.path);
      db.exec(SCHEMA_SQL);
      db.close();
    } catch {
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
  }

  listSessions(): HermesSessionInfo[] {
    try {
      const db = new DatabaseSync(this.path, { readOnly: true });
      const rows = db
        .prepare("SELECT id, source, model, title, started_at FROM sessions ORDER BY id")
        .all() as Array<Record<string, unknown>>;
      db.close();
      return rows.map((r) => ({
        sessionId: String(r.id),
        title: r.title != null ? String(r.title) : null,
        model: r.model != null ? String(r.model) : null,
        startedAt: typeof r.started_at === "number" ? r.started_at : null,
        source: r.source != null ? String(r.source) : null,
      }));
    } catch {
      return [];
    }
  }

  exportSession(sessionId: string): Uint8Array {
    try {
      const db = new DatabaseSync(this.path, { readOnly: true });
      const sess = db
        .prepare("SELECT * FROM sessions WHERE id = ?")
        .get(sessionId) as Record<string, unknown> | undefined;
      const msgRows = db
        .prepare("SELECT * FROM messages WHERE session_id = ? ORDER BY id")
        .all(sessionId) as Array<Record<string, unknown>>;
      db.close();
      if (!sess && msgRows.length === 0) {
        throw new HermesHostError(HOST_SESSION_NOT_FOUND, MSG_SESSION_NOT_FOUND);
      }
      return exportSessionJson(
        sess ?? null,
        msgRows as JsonObject[],
      );
    } catch (err) {
      if (err instanceof HermesHostError) throw err;
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
  }

  insertSession(session: JsonObject): void {
    this.initializeSchema();
    const sid = session.id;
    if (typeof sid !== "string" || !sid) {
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
    try {
      const db = new DatabaseSync(this.path);
      const params: Array<null | number | string> = [
        sid,
        sqlText(session.source) ?? "tui",
        sqlText(session.model),
        sqlText(session.title),
        sqlText(session.cwd),
        sqlText(session.system_prompt),
        typeof session.started_at === "number" ? session.started_at : null,
      ];
      db.prepare(
        `INSERT OR REPLACE INTO sessions
         (id, source, model, title, cwd, system_prompt, started_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).run(...params);
      db.close();
    } catch {
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
  }

  insertMessage(sessionId: string, row: JsonObject): number {
    this.initializeSchema();
    let toolCalls: string | null = null;
    if (row.tool_calls != null) {
      toolCalls =
        typeof row.tool_calls === "string"
          ? row.tool_calls
          : JSON.stringify(row.tool_calls);
    }
    const idParam =
      typeof row.id === "number" || typeof row.id === "string" ? row.id : null;
    try {
      const db = new DatabaseSync(this.path);
      const params: Array<null | number | string> = [
        idParam,
        sessionId,
        sqlText(row.role) ?? "user",
        sqlText(row.content),
        sqlText(row.tool_call_id),
        toolCalls,
        sqlText(row.tool_name),
        typeof row.timestamp === "number" ? row.timestamp : 0,
        sqlText(row.finish_reason),
        sqlText(row.reasoning),
        sqlText(row.reasoning_content),
        typeof row.observed === "number" ? row.observed : 0,
        isInactive(row) ? 0 : 1,
      ];
      const result = db
        .prepare(
          `INSERT INTO messages
           (id, session_id, role, content, tool_call_id, tool_calls, tool_name,
            timestamp, finish_reason, reasoning, reasoning_content, observed, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run(...params);
      db.close();
      return Number(result.lastInsertRowid ?? 0);
    } catch {
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
  }

  softDeleteMessage(sessionId: string, messageId: number): void {
    try {
      const db = new DatabaseSync(this.path);
      const result = db
        .prepare("UPDATE messages SET active = 0 WHERE session_id = ? AND id = ?")
        .run(sessionId, messageId);
      db.close();
      if (result.changes === 0) {
        throw new HermesHostError(HOST_SESSION_NOT_FOUND, MSG_SESSION_NOT_FOUND);
      }
    } catch (err) {
      if (err instanceof HermesHostError) throw err;
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
  }
}

function sqlText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export interface HermesProviderOptions {
  readonly sessionId: string;
  readonly store: HermesStore;
  readonly stream?: StreamOptions;
  readonly groupId?: string;
}

export class HermesProviderStream {
  #store: HermesStore;
  #sessionId: string;
  #state: StreamState;
  #closed = false;

  private constructor(store: HermesStore, sessionId: string, state: StreamState) {
    this.#store = store;
    this.#sessionId = sessionId;
    this.#state = state;
  }

  static open(options: HermesProviderOptions): HermesProviderStream {
    const group = options.groupId ?? options.sessionId;
    const streamOpts = options.stream ?? { source: "hermes" as const, groupId: group };
    const state = createStream(streamOpts);
    return new HermesProviderStream(options.store, options.sessionId, state);
  }

  get cursor(): StreamCursor {
    return this.#state.cursor;
  }

  get state(): StreamState {
    return this.#state;
  }

  listSessions(): HermesSessionInfo[] {
    return this.#store.listSessions();
  }

  poll(): StreamUpdate | null {
    if (this.#closed) return null;
    const gen = this.#store.databaseGeneration();
    let exportBytes: Uint8Array;
    try {
      exportBytes = this.#store.exportSession(this.#sessionId);
    } catch (err) {
      if (err instanceof HermesHostError) throw err;
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(new TextDecoder().decode(exportBytes));
    } catch {
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
    let messages: JsonObject[];
    if (Array.isArray(parsed)) {
      messages = parsed as JsonObject[];
    } else if (
      parsed &&
      typeof parsed === "object" &&
      Array.isArray((parsed as { messages?: unknown }).messages)
    ) {
      messages = (parsed as { messages: JsonObject[] }).messages;
    } else {
      throw new HermesHostError(HOST_DB_ERROR, MSG_DB_ERROR);
    }
    const token = computeChangeToken(messages);

    if (
      this.#state.snapshot !== null &&
      this.#state.cursor.position.kind === "hermes-row" &&
      this.#state.cursor.position.databaseGeneration &&
      this.#state.cursor.position.databaseGeneration !== gen
    ) {
      const result = resetStream(this.#state, {
        reason: "source-replaced",
        sourceRevision: gen,
        material: exportBytes,
        changeToken: token,
      });
      this.#state = result.state;
      return result.update;
    }

    const result = applyHermesExport(
      this.#state,
      exportBytes,
      token,
      gen,
      gen,
      undefined,
    );
    this.#state = result.state;
    return result.update;
  }

  close(): void {
    this.#closed = true;
  }
}
