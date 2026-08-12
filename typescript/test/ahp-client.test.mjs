/**
 * LS-10: optional @hypabolic/trajectory-ahp fake-host tests.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import {
  AhpStreamClient,
  FakeAhpHost,
  InMemoryAhpTransportPair,
} from "@hypabolic/trajectory-ahp";

const __dirname = dirname(fileURLToPath(import.meta.url));
const STREAM_CASES = join(__dirname, "../../conformance/cases/streaming");
const CHAT = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";

function loadActions(caseId, name) {
  const text = readFileSync(join(STREAM_CASES, caseId, name), "utf8");
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

function emptySnapshot() {
  return {
    ahpProtocolVersion: "0.7.0",
    chat: { id: CHAT, turns: [], activeTurn: null },
  };
}

test("subscribe actions feed core", () => {
  const pair = new InMemoryAhpTransportPair();
  const actions = loadActions("ahp-action-turn-flow", "step-actions.jsonl");
  const host = new FakeAhpHost(
    pair.host,
    { initialActions: actions },
    CHAT,
  );
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    { chatChannel: CHAT },
    (e) => events.push(e),
  );
  client.start();
  assert.ok(events.some((e) => e.kind === "ready"));
  const updates = events.filter((e) => e.kind === "stream-update");
  assert.ok(updates.length > 0);
  assert.equal(updates.at(-1).update.kind, "updated");
  assert.equal(client.cursor.position.kind, "ahp-server-seq");
  assert.equal(client.cursor.position.lastServerSeq, 5n);
  host.close();
  client.cancel();
});

test("auth failure", () => {
  const pair = new InMemoryAhpTransportPair();
  const host = new FakeAhpHost(
    pair.host,
    { requireAuth: true, acceptToken: "good" },
    CHAT,
  );
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    { chatChannel: CHAT, auth: () => ({ token: "bad" }) },
    (e) => events.push(e),
  );
  client.start();
  assert.ok(events.some((e) => e.kind === "auth-required"));
  assert.ok(events.some((e) => e.kind === "auth-failed"));
  assert.equal(host.authAttempts, 1);
  assert.ok(!events.some((e) => e.kind === "ready"));
  client.cancel();
});

test("auth success then subscribe", async () => {
  const pair = new InMemoryAhpTransportPair();
  const host = new FakeAhpHost(
    pair.host,
    {
      requireAuth: true,
      acceptToken: "secret-token-xyz",
      initialSnapshot: emptySnapshot(),
    },
    CHAT,
  );
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    {
      chatChannel: CHAT,
      auth: async () => ({ token: "secret-token-xyz" }),
    },
    (e) => events.push(e),
  );
  client.start();
  // auth callback may be async; allow microtasks
  await Promise.resolve();
  await Promise.resolve();
  assert.ok(events.some((e) => e.kind === "ready"));
  const token = "secret-token-xyz";
  for (const e of events.filter((x) => x.kind === "stream-update" && x.update)) {
    const diagnostics = e.update.diagnostics ?? [];
    for (const d of diagnostics) {
      assert.ok(!String(d.message ?? "").includes(token));
      assert.ok(!String(d.code ?? "").includes(token));
    }
    if (e.update.error) {
      assert.ok(!String(e.update.error.message ?? "").includes(token));
    }
  }
  client.cancel();
  void host;
});

test("sequence gap triggers resync", () => {
  const pair = new InMemoryAhpTransportPair();
  const actions = loadActions("ahp-action-turn-flow", "step-actions.jsonl");
  const host = new FakeAhpHost(
    pair.host,
    { initialActions: actions, initialSnapshot: emptySnapshot() },
    CHAT,
  );
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    { chatChannel: CHAT, autoResync: true },
    (e) => events.push(e),
  );
  client.start();
  const genBefore = client.cursor.generation;
  const updatesBefore = events.filter((e) => e.kind === "stream-update").length;
  const gap = loadActions("ahp-action-sequence-gap", "step-gap.jsonl");
  host.pushActions(gap);
  assert.ok(events.some((e) => e.kind === "resync-required"));
  assert.ok(host.resyncCount >= 1);
  assert.ok(client.cursor.generation > genBefore);
  const updatesAfter = events.filter((e) => e.kind === "stream-update");
  assert.ok(updatesAfter.length > updatesBefore);
  assert.ok(["updated", "unchanged"].includes(updatesAfter.at(-1).update.kind));
  client.cancel();
  assert.ok(client.isCancelled);
});

test("cancel while auth pending does not finish auth", async () => {
  let resolveAuth;
  const authPromise = new Promise((resolve) => {
    resolveAuth = resolve;
  });
  const pair = new InMemoryAhpTransportPair();
  const host = new FakeAhpHost(
    pair.host,
    { requireAuth: true, acceptToken: "secret-late" },
    CHAT,
  );
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    { chatChannel: CHAT, auth: () => authPromise },
    (e) => events.push(e),
  );
  client.start();
  assert.ok(events.some((e) => e.kind === "auth-required"));
  client.cancel();
  assert.ok(client.isCancelled);
  resolveAuth({ token: "secret-late" });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(host.authAttempts, 0);
  assert.ok(!events.some((e) => e.kind === "ready"));
  assert.ok(!events.some((e) => e.kind === "error"));
  assert.ok(!events.some((e) => e.kind === "auth-failed"));
});

test("duplicate action replay does not crash", () => {
  const pair = new InMemoryAhpTransportPair();
  const actions = loadActions("ahp-action-turn-flow", "step-actions.jsonl");
  const host = new FakeAhpHost(pair.host, { initialActions: actions }, CHAT);
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    { chatChannel: CHAT },
    (e) => events.push(e),
  );
  client.start();
  host.pushActions(actions);
  const updates = events.filter((e) => e.kind === "stream-update");
  assert.ok(updates.every((e) =>
    ["updated", "unchanged", "reset-required", "error"].includes(e.update.kind),
  ));
  client.cancel();
});

test("backpressure", () => {
  const pair = new InMemoryAhpTransportPair();
  const host = new FakeAhpHost(
    pair.host,
    { initialSnapshot: emptySnapshot() },
    CHAT,
  );
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    { chatChannel: CHAT, maxBufferedActions: 2 },
    (e) => events.push(e),
  );
  client.start();
  client.setPausedForTest(true);
  for (let i = 0; i < 5; i++) {
    host.pushAction({
      channel: CHAT,
      serverSeq: 100 + i,
      origin: { kind: "server" },
      action: { type: "chat/activityChanged", activity: "thinking" },
    });
  }
  assert.ok(events.some((e) => e.kind === "backpressure"));
  client.cancel();
});

test("cancel keeps cursor", () => {
  const pair = new InMemoryAhpTransportPair();
  const actions = loadActions("ahp-action-turn-flow", "step-actions.jsonl");
  const host = new FakeAhpHost(pair.host, { initialActions: actions }, CHAT);
  const events = [];
  const client = new AhpStreamClient(
    pair.client,
    { chatChannel: CHAT },
    (e) => events.push(e),
  );
  client.start();
  const cur = client.cursor;
  assert.equal(cur.position.kind, "ahp-server-seq");
  client.cancel();
  assert.ok(client.isCancelled);
  assert.equal(client.cursor.generation, cur.generation);
  assert.equal(client.cursor.position.lastServerSeq, cur.position.lastServerSeq);
  host.close();
});
