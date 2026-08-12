/**
 * AHP Shape B action-log reducer (LS-07). Pure, no network.
 * Protocol pin: 0.7.x. Peer: python streaming/ahp_reducer.py
 */

export const MSG_UNKNOWN_ACTION = "Ignored an unknown AHP action type.";
export const MSG_FOREIGN_CHANNEL = "Ignored an AHP action for a non-target channel.";
export const MSG_INVALID_ACTIONS = "AHP action batch must be JSONL envelopes or a JSON array.";

const KNOWN_CHAT = new Set([
  "chat/turnStarted",
  "chat/responsePart",
  "chat/delta",
  "chat/reasoning",
  "chat/toolCallStart",
  "chat/toolCallDelta",
  "chat/toolCallReady",
  "chat/toolCallConfirmed",
  "chat/toolCallComplete",
  "chat/toolCallResultConfirmed",
  "chat/toolCallContentChanged",
  "chat/toolCallAuthRequired",
  "chat/toolCallAuthResolved",
  "chat/usage",
  "chat/turnComplete",
  "chat/turnCancelled",
  "chat/error",
  "chat/truncated",
  "chat/activityChanged",
  "chat/workingDirectorySet",
  "chat/workingDirectoryRemoved",
  "chat/inputRequested",
  "chat/inputAnswerChanged",
  "chat/inputCompleted",
]);

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type ChatState = { [k: string]: Json };

export function emptyChatState(resource: string | null = null): ChatState {
  return {
    resource,
    title: null,
    status: 1,
    activity: "",
    modifiedAt: null,
    origin: { kind: "user" },
    workingDirectories: [],
    turns: [],
    activeTurn: null,
  };
}

export function parseActionBatch(data: Uint8Array): Record<string, Json>[] {
  const text = new TextDecoder("utf-8", { fatal: true }).decode(data);
  const stripped = text.trim();
  if (!stripped) return [];
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length > 1) {
    return lines.map((line) => {
      const obj = JSON.parse(line) as unknown;
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
        throw new Error(MSG_INVALID_ACTIONS);
      }
      return obj as Record<string, Json>;
    });
  }
  const parsed = JSON.parse(stripped) as unknown;
  if (Array.isArray(parsed)) {
    return parsed.filter(
      (x): x is Record<string, Json> => !!x && typeof x === "object" && !Array.isArray(x),
    );
  }
  if (parsed && typeof parsed === "object") return [parsed as Record<string, Json>];
  throw new Error(MSG_INVALID_ACTIONS);
}

function normalizeEnvelope(raw: Record<string, Json>): {
  channel: string | null;
  serverSeq: number | null;
  action: Record<string, Json>;
} | null {
  if (raw.action && typeof raw.action === "object" && !Array.isArray(raw.action)) {
    const seq = raw.serverSeq;
    return {
      channel: typeof raw.channel === "string" ? raw.channel : null,
      serverSeq: typeof seq === "number" && Number.isFinite(seq) ? seq : null,
      action: raw.action as Record<string, Json>,
    };
  }
  if (typeof raw.type === "string") {
    const seq = raw.serverSeq;
    const action: Record<string, Json> = {};
    for (const [k, v] of Object.entries(raw)) {
      if (k === "channel" || k === "serverSeq" || k === "origin") continue;
      action[k] = v;
    }
    return {
      channel: typeof raw.channel === "string" ? raw.channel : null,
      serverSeq: typeof seq === "number" && Number.isFinite(seq) ? seq : null,
      action,
    };
  }
  return null;
}

export function detectSequenceGap(
  envelopes: Record<string, Json>[],
  lastServerSeq: number | null,
  targetChannel: string | null,
): number | null {
  const expected = lastServerSeq === null ? 1 : lastServerSeq + 1;
  const seqs: number[] = [];
  for (const raw of envelopes) {
    const env = normalizeEnvelope(raw);
    if (!env || env.serverSeq === null) continue;
    const ch = env.channel;
    if (typeof ch === "string" && targetChannel !== null && ch !== targetChannel) continue;
    if (typeof ch === "string" && !ch.startsWith("ahp-chat:")) continue;
    if (lastServerSeq !== null && env.serverSeq <= lastServerSeq) continue;
    seqs.push(env.serverSeq);
  }
  if (seqs.length === 0) return null;
  seqs.sort((a, b) => a - b);
  if (lastServerSeq !== null && seqs[0]! > expected) return seqs[0]!;
  let prev = seqs[0]!;
  for (let i = 1; i < seqs.length; i++) {
    const s = seqs[i]!;
    if (s > prev + 1) return prev + 1;
    prev = s;
  }
  return null;
}

export function reduceAhpActions(
  chat: ChatState | null,
  envelopes: Record<string, Json>[],
  targetChannel: string | null,
  lastServerSeq: number | null,
): {
  chat: ChatState;
  lastServerSeq: number | null;
  diagnostics: { code: string; message: string }[];
  applied: number[];
} {
  let state: ChatState = chat
    ? (JSON.parse(JSON.stringify(chat)) as ChatState)
    : emptyChatState(targetChannel);
  const diagnostics: { code: string; message: string }[] = [];
  const applied: number[] = [];
  let last = lastServerSeq;
  let channel = targetChannel ?? (typeof state.resource === "string" ? state.resource : null);
  if (channel && state.resource == null) state.resource = channel;

  const normalized = envelopes
    .map(normalizeEnvelope)
    .filter((e): e is NonNullable<typeof e> => e !== null)
    .sort((a, b) => {
      if (a.serverSeq === null && b.serverSeq === null) return 0;
      if (a.serverSeq === null) return 1;
      if (b.serverSeq === null) return -1;
      return a.serverSeq - b.serverSeq;
    });

  for (const env of normalized) {
    const action = env.action;
    const actionType = action.type;
    if (typeof actionType !== "string") {
      diagnostics.push({ code: "ahp_unknown_action", message: MSG_UNKNOWN_ACTION });
      continue;
    }
    const envChannel = env.channel;
    if (channel === null && typeof envChannel === "string" && envChannel.startsWith("ahp-chat:")) {
      channel = envChannel;
      state.resource = channel;
    }
    if (typeof envChannel === "string" && channel !== null && envChannel !== channel) {
      diagnostics.push({ code: "ahp_foreign_channel", message: MSG_FOREIGN_CHANNEL });
      continue;
    }
    if (typeof envChannel === "string" && !envChannel.startsWith("ahp-chat:")) {
      diagnostics.push({ code: "ahp_foreign_channel", message: MSG_FOREIGN_CHANNEL });
      continue;
    }
    if (env.serverSeq === null) {
      if (!KNOWN_CHAT.has(actionType)) {
        diagnostics.push({ code: "ahp_unknown_action", message: MSG_UNKNOWN_ACTION });
        continue;
      }
      state = applyChatAction(state, action);
      continue;
    }
    const seq = env.serverSeq;
    if (last !== null && seq <= last) continue;
    if (!KNOWN_CHAT.has(actionType)) {
      diagnostics.push({ code: "ahp_unknown_action", message: MSG_UNKNOWN_ACTION });
      last = seq;
      applied.push(seq);
      continue;
    }
    state = applyChatAction(state, action);
    last = seq;
    applied.push(seq);
  }
  if (channel !== null) state.resource = channel;
  return { chat: state, lastServerSeq: last, diagnostics, applied };
}

export function shapeABytes(
  chat: ChatState,
  protocolVersion = "0.7.0",
  session: ChatState | null = null,
): Uint8Array {
  const envelope: ChatState = { ahpProtocolVersion: protocolVersion, chat };
  if (session) envelope.session = session;
  return new TextEncoder().encode(JSON.stringify(envelope));
}

function activeTurn(state: ChatState): ChatState | null {
  const a = state.activeTurn;
  return a && typeof a === "object" && !Array.isArray(a) ? (a as ChatState) : null;
}

function applyChatAction(state: ChatState, action: Record<string, Json>): ChatState {
  const t = action.type;
  if (t === "chat/turnStarted") return turnStarted(state, action);
  if (t === "chat/responsePart") return responsePart(state, action);
  if (t === "chat/delta") return delta(state, action, ["markdown"]);
  if (t === "chat/reasoning") return delta(state, action, ["reasoning"]);
  if (t === "chat/toolCallStart") return toolCallStart(state, action);
  if (t === "chat/toolCallDelta") return updateTool(state, action, (tc) => {
    if (tc.status !== "streaming") return tc;
    if (typeof action.content === "string") {
      const prev = typeof tc.partialInput === "string" ? tc.partialInput : "";
      tc.partialInput = prev + action.content;
    }
    if (action.invocationMessage !== undefined) tc.invocationMessage = action.invocationMessage;
    return tc;
  });
  if (t === "chat/toolCallReady") return updateTool(state, action, (tc) => {
    if (!["streaming", "running", "pending-confirmation"].includes(String(tc.status))) return tc;
    if (action.intention !== undefined) tc.intention = action.intention;
    if (action.invocationMessage !== undefined) tc.invocationMessage = action.invocationMessage;
    if (action.toolInput !== undefined) tc.toolInput = action.toolInput;
    if (action.contributor !== undefined) tc.contributor = action.contributor;
    if (action.confirmed) {
      tc.status = "running";
      tc.confirmed = action.confirmed;
    } else tc.status = "pending-confirmation";
    return tc;
  });
  if (t === "chat/toolCallConfirmed") return updateTool(state, action, (tc) => {
    if (tc.status !== "pending-confirmation") return tc;
    if (action.approved) {
      tc.status = "running";
      tc.confirmed = action.confirmed ?? "user-action";
    } else {
      tc.status = "cancelled";
      tc.success = false;
      tc.reason = action.reason ?? "denied";
    }
    return tc;
  });
  if (t === "chat/toolCallComplete") return updateTool(state, action, (tc) => {
    const st = String(tc.status);
    if (!["running", "pending-confirmation", "auth-required"].includes(st)) return tc;
    const result =
      action.result && typeof action.result === "object" && !Array.isArray(action.result)
        ? (action.result as ChatState)
        : {};
    if (st === "auth-required" && result.success === true) return tc;
    for (const key of ["success", "pastTenseMessage", "content", "structuredContent", "error", "reasonMessage"]) {
      if (key in result) tc[key] = result[key] as Json;
    }
    const needsConfirm = Boolean(action.requiresResultConfirmation) && st !== "auth-required";
    tc.status = needsConfirm ? "pending-result-confirmation" : "completed";
    return tc;
  });
  if (t === "chat/toolCallResultConfirmed") return updateTool(state, action, (tc) => {
    if (tc.status !== "pending-result-confirmation") return tc;
    if (action.approved) tc.status = "completed";
    else {
      tc.status = "cancelled";
      tc.success = false;
      tc.reason = "result-denied";
    }
    return tc;
  });
  if (t === "chat/toolCallContentChanged") return updateTool(state, action, (tc) => {
    if (tc.status !== "running") return tc;
    if ("content" in action) tc.content = action.content;
    return tc;
  });
  if (t === "chat/toolCallAuthRequired") return updateTool(state, action, (tc) => {
    if (tc.status !== "running") return tc;
    const c = tc.contributor;
    if (!c || typeof c !== "object" || Array.isArray(c) || (c as ChatState).kind !== "mcp") return tc;
    tc.status = "auth-required";
    if ("auth" in action) tc.auth = action.auth;
    return tc;
  });
  if (t === "chat/toolCallAuthResolved") return updateTool(state, action, (tc) => {
    if (tc.status !== "auth-required") return tc;
    tc.status = "running";
    delete tc.auth;
    return tc;
  });
  if (t === "chat/usage") {
    const next = clone(state);
    const active = activeTurn(next);
    if (!active || active.id !== action.turnId || typeof action.usage !== "object") return state;
    active.usage = action.usage;
    next.activeTurn = active;
    return next;
  }
  if (t === "chat/turnComplete") return endTurn(state, action, "complete");
  if (t === "chat/turnCancelled") return endTurn(state, action, "cancelled");
  if (t === "chat/error") return endTurn(state, action, "error");
  if (t === "chat/truncated") return truncated(state, action);
  if (t === "chat/activityChanged") {
    const next = clone(state);
    next.activity = (action.activity as string) || "";
    return next;
  }
  if (t === "chat/workingDirectorySet") {
    const next = clone(state);
    const d = action.directory;
    if (typeof d !== "string") return state;
    const dirs = Array.isArray(next.workingDirectories) ? [...(next.workingDirectories as Json[])] : [];
    if (!dirs.includes(d)) dirs.push(d);
    next.workingDirectories = dirs;
    return next;
  }
  if (t === "chat/workingDirectoryRemoved") {
    const next = clone(state);
    const d = action.directory;
    if (typeof d !== "string") return state;
    const dirs = Array.isArray(next.workingDirectories) ? (next.workingDirectories as Json[]) : [];
    next.workingDirectories = dirs.filter((x) => x !== d);
    return next;
  }
  return state;
}

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

function turnStarted(state: ChatState, action: Record<string, Json>): ChatState {
  const next = clone(state);
  if (typeof action.turnId !== "string") return state;
  const message =
    action.message && typeof action.message === "object" && !Array.isArray(action.message)
      ? action.message
      : { text: "", origin: { kind: "user" } };
  next.activeTurn = {
    id: action.turnId,
    startedAt: typeof action.startedAt === "string" ? action.startedAt : null,
    duration: null,
    message,
    responseParts: [],
    usage: null,
    state: "in-progress",
    error: null,
  };
  next.activity = (next.activity as string) || "generating";
  return next;
}

function responsePart(state: ChatState, action: Record<string, Json>): ChatState {
  const next = clone(state);
  const active = activeTurn(next);
  if (!active || active.id !== action.turnId || typeof action.part !== "object" || !action.part) return state;
  const parts = Array.isArray(active.responseParts) ? [...(active.responseParts as Json[])] : [];
  parts.push(clone(action.part));
  active.responseParts = parts;
  next.activeTurn = active;
  return next;
}

function delta(state: ChatState, action: Record<string, Json>, kinds: string[]): ChatState {
  const next = clone(state);
  const active = activeTurn(next);
  if (
    !active ||
    active.id !== action.turnId ||
    typeof action.partId !== "string" ||
    typeof action.content !== "string"
  ) {
    return state;
  }
  const parts = Array.isArray(active.responseParts) ? (active.responseParts as ChatState[]) : [];
  let found = false;
  active.responseParts = parts.map((part) => {
    if (!found && kinds.includes(String(part.kind)) && part.id === action.partId) {
      found = true;
      const p = clone(part);
      const prev = typeof p.content === "string" ? p.content : "";
      p.content = prev + action.content;
      return p;
    }
    return part;
  });
  if (!found) return state;
  next.activeTurn = active;
  return next;
}

function toolCallStart(state: ChatState, action: Record<string, Json>): ChatState {
  const next = clone(state);
  const active = activeTurn(next);
  if (!active || active.id !== action.turnId || typeof action.toolCallId !== "string") return state;
  const parts = Array.isArray(active.responseParts) ? [...(active.responseParts as Json[])] : [];
  parts.push({
    kind: "toolCall",
    toolCall: {
      toolCallId: action.toolCallId,
      toolName: (action.toolName as string) || "unknown",
      displayName: action.displayName ?? null,
      intention: action.intention ?? null,
      contributor: action.contributor ?? null,
      status: "streaming",
      success: null,
      confirmed: null,
      content: null,
      toolInput: null,
      invocationMessage: null,
      pastTenseMessage: null,
    },
  });
  active.responseParts = parts;
  next.activeTurn = active;
  return next;
}

function updateTool(
  state: ChatState,
  action: Record<string, Json>,
  updater: (tc: ChatState) => ChatState,
): ChatState {
  const next = clone(state);
  const active = activeTurn(next);
  if (!active || active.id !== action.turnId || typeof action.toolCallId !== "string") return state;
  const parts = Array.isArray(active.responseParts) ? (active.responseParts as ChatState[]) : [];
  let found = false;
  active.responseParts = parts.map((part) => {
    if (
      !found &&
      part.kind === "toolCall" &&
      part.toolCall &&
      typeof part.toolCall === "object" &&
      !Array.isArray(part.toolCall) &&
      (part.toolCall as ChatState).toolCallId === action.toolCallId
    ) {
      found = true;
      const tc = updater(clone(part.toolCall as ChatState));
      return { kind: "toolCall", toolCall: tc };
    }
    return part;
  });
  if (!found) return state;
  next.activeTurn = active;
  return next;
}

function endTurn(state: ChatState, action: Record<string, Json>, turnState: string): ChatState {
  const next = clone(state);
  const active = activeTurn(next);
  if (!active || active.id !== action.turnId) return state;
  const duration =
    typeof action.duration === "number" ? Math.max(0, action.duration) : 0;
  const parts = Array.isArray(active.responseParts) ? (active.responseParts as ChatState[]) : [];
  const newParts = parts.map((part) => {
    if (part.kind === "toolCall" && part.toolCall && typeof part.toolCall === "object") {
      const tc = clone(part.toolCall as ChatState);
      if (tc.status !== "completed" && tc.status !== "cancelled") {
        tc.status = "cancelled";
        tc.success = false;
        tc.reason = "skipped";
      }
      return { kind: "toolCall", toolCall: tc };
    }
    return part;
  });
  const turn: ChatState = {
    id: active.id ?? null,
    startedAt: active.startedAt ?? null,
    duration,
    message: active.message ?? null,
    responseParts: newParts,
    usage: active.usage ?? null,
    state: turnState,
    error: turnState === "error" ? (action.error ?? null) : null,
  };
  const turns = Array.isArray(next.turns) ? [...(next.turns as Json[])] : [];
  turns.push(turn);
  next.turns = turns;
  next.activeTurn = null;
  next.activity = "";
  return next;
}

function truncated(state: ChatState, action: Record<string, Json>): ChatState {
  const next = clone(state);
  const turnId = action.turnId;
  const turns = Array.isArray(next.turns) ? (next.turns as ChatState[]) : [];
  if (turnId === undefined || turnId === null) {
    next.turns = [];
  } else {
    if (typeof turnId !== "string") return state;
    const idx = turns.findIndex((t) => t.id === turnId);
    if (idx < 0) return state;
    next.turns = turns.slice(0, idx + 1);
  }
  next.activeTurn = null;
  next.activity = "";
  return next;
}
