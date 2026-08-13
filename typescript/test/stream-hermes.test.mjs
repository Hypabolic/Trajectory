import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import {
  applyDeltaToSnapshot,
  applyHermesExport,
  createStream,
  resetStream,
} from "@hypabolic/trajectory";
import {
  HermesProviderStream,
  MemoryHermesStore,
  SqliteHermesProvider,
} from "@hypabolic/trajectory-hermes";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const fixture = readFileSync(
  join(root, "conformance/cases/hermes/tool-calls/input.json"),
);

test("hermes export snapshot + idempotent", () => {
  let state = createStream({ source: "hermes", groupId: "hermes-session-0001" });
  assert.equal(state.cursor.position.kind, "hermes-row");
  let r = applyHermesExport(state, fixture, "tok-1", "db-1", "db-1");
  assert.equal(r.update.kind, "updated");
  assert.ok(r.update.snapshot);
  assert.ok(r.update.delta);
  assert.ok(r.update.snapshot.records.length >= 2);
  assert.equal(r.update.cursor.position.kind, "hermes-row");
  assert.equal(r.update.cursor.position.databaseGeneration, "db-1");
  assert.equal(r.update.cursor.position.lastRowId, 104);
  const recon = applyDeltaToSnapshot(null, r.update.delta);
  assert.equal(recon.records.length, r.update.snapshot.records.length);
  assert.deepEqual(
    recon.records.map((x) => x.record.id),
    r.update.snapshot.records.map((x) => x.record.id),
  );
  state = r.state;
  r = applyHermesExport(state, fixture, "tok-1", "db-1", "db-1");
  assert.equal(r.update.kind, "unchanged");
});

test("hermes soft-delete requires reset", () => {
  const base = JSON.parse(fixture.toString("utf8"));
  let state = createStream({ source: "hermes", groupId: "hermes-session-0001" });
  let r = applyHermesExport(
    state,
    new TextEncoder().encode(JSON.stringify(base)),
    "t1",
    "db-1",
  );
  assert.equal(r.update.kind, "updated");
  state = r.state;
  const prior = state.cursor;
  const mutated = JSON.parse(JSON.stringify(base));
  mutated.messages[0].active = 0;
  r = applyHermesExport(
    state,
    new TextEncoder().encode(JSON.stringify(mutated)),
    "t2",
    "db-1",
  );
  assert.equal(r.update.kind, "reset-required");
  assert.equal(r.update.reset?.reason, "source-replaced");
  assert.deepEqual(r.state.cursor.position, prior.position);

  r = resetStream(state, {
    reason: "source-replaced",
    sourceRevision: "db-1",
    material: new TextEncoder().encode(JSON.stringify(mutated)),
    changeToken: "t2",
  });
  assert.equal(r.update.kind, "updated");
  assert.ok(r.update.reset);
});

test("hermes nonnumeric ids", () => {
  const exportDoc = {
    session: { id: "s-nonnum", source: "tui", started_at: 1.0 },
    messages: [
      {
        id: "msg-a",
        session_id: "s-nonnum",
        role: "user",
        content: "hello",
        timestamp: 1.0,
        active: 1,
      },
      {
        id: "msg-b",
        session_id: "s-nonnum",
        role: "assistant",
        content: "world",
        timestamp: 2.0,
        active: 1,
        finish_reason: "stop",
      },
    ],
  };
  const material = new TextEncoder().encode(JSON.stringify(exportDoc));
  const state = createStream({ source: "hermes", groupId: "s-nonnum" });
  const r = applyHermesExport(state, material, "nn-1", "db-nn");
  assert.equal(r.update.kind, "updated");
  assert.equal(r.update.cursor.position.kind, "hermes-row");
  assert.equal(r.update.cursor.position.lastRowId, null);
  assert.ok(r.update.snapshot.records.some((rec) => rec.record.role === "user"));
});

test("memory provider snapshot insert soft-delete", () => {
  const store = new MemoryHermesStore();
  store.databaseGenerationValue = "mem-1";
  store.upsertSession({
    id: "sess-mem",
    source: "tui",
    model: "gpt-test",
    started_at: 100.0,
    title: "mem",
  });
  store.appendMessage("sess-mem", {
    id: 1,
    role: "user",
    content: "hi",
    timestamp: 101.0,
    active: 1,
  });
  const stream = HermesProviderStream.open({
    sessionId: "sess-mem",
    store,
    groupId: "sess-mem",
  });
  assert.equal(stream.listSessions().length, 1);
  let u = stream.poll();
  assert.equal(u?.kind, "updated");
  const n0 = u.snapshot.records.length;
  store.appendMessage("sess-mem", {
    id: 2,
    role: "assistant",
    content: "hello",
    timestamp: 102.0,
    active: 1,
    finish_reason: "stop",
  });
  u = stream.poll();
  assert.equal(u?.kind, "updated");
  assert.ok(u.snapshot.records.length > n0);
  store.softDeleteMessage("sess-mem", 1);
  u = stream.poll();
  assert.equal(u?.kind, "reset-required");
});

test("sqlite provider roundtrip", () => {
  const dir = mkdtempSync(join(tmpdir(), "traj-hermes-"));
  const db = join(dir, "state.db");
  const provider = new SqliteHermesProvider(db, "sql-1");
  provider.initializeSchema();
  provider.insertSession({
    id: "sess-sql",
    source: "tui",
    model: "gpt-test",
    title: "sql",
    started_at: 200.0,
  });
  provider.insertMessage("sess-sql", {
    id: 10,
    role: "user",
    content: "from sqlite",
    timestamp: 201.0,
    active: 1,
  });
  provider.insertMessage("sess-sql", {
    id: 11,
    role: "assistant",
    content: "ok",
    timestamp: 202.0,
    active: 1,
    finish_reason: "stop",
  });
  assert.equal(provider.listSessions().length, 1);
  const stream = HermesProviderStream.open({
    sessionId: "sess-sql",
    store: provider,
    groupId: "sess-sql",
  });
  const u = stream.poll();
  assert.equal(u?.kind, "updated");
  assert.ok(u.snapshot.records.some((r) => r.record.role === "user"));
  assert.equal(u.cursor.position.kind, "hermes-row");
  assert.equal(u.cursor.position.lastRowId, 11);
  provider.softDeleteMessage("sess-sql", 10);
  const u2 = stream.poll();
  assert.equal(u2?.kind, "reset-required");
});
