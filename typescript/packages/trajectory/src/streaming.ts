/**
 * Live session streaming core (LS-03 / LS-04).
 * Pure algorithm: no FS watchers, network, or SQLite.
 */
import { createHash } from "node:crypto";

import type { NormalizationOptions, TrajectorySource } from "./index.js";
import {
  normalizeAhp,
  normalizeClaudeCode,
  normalizeCodex,
  normalizeGrokBuild,
  normalizeHermes,
  normalizeOpenClaw,
  normalizePi,
  TrajectoryNormalizationError,
  type JsonObject,
  type JsonValue,
  type TrajectoryIR,
} from "./internal.js";
import {
  detectSequenceGap,
  emptyChatState,
  parseActionBatch,
  reduceAhpActions,
  shapeABytes,
  type ChatState,
} from "./ahp-reducer.js";
import { projectHypabolic } from "./projections.js";

function normalizeToIR(request: {
  source: TrajectorySource;
  transcriptBytes: Uint8Array;
  sourceContext?: {
    groupId?: string;
    baseByteOffset?: bigint;
    partial?: boolean;
  };
  options?: NormalizationOptions;
}): TrajectoryIR {
  const normalized = { ...request, transcriptBytes: request.transcriptBytes };
  if (request.source === "pi") return normalizePi(normalized);
  if (request.source === "claude-code") return normalizeClaudeCode(normalized);
  if (request.source === "codex") return normalizeCodex(normalized);
  if (request.source === "openclaw") return normalizeOpenClaw(normalized);
  if (request.source === "hermes") return normalizeHermes(normalized);
  if (request.source === "ahp") return normalizeAhp(normalized);
  if (request.source === "grok-build") return normalizeGrokBuild(normalized);
  throw new TrajectoryNormalizationError(
    "unknown_source",
    `No source adapter is registered for '${String(request.source)}'.`,
  );
}

export const STREAM_SCHEMA_ID = "trajectory-stream-v1" as const;

export type StreamDelivery = "both" | "snapshot" | "delta";
export type StreamUpdateKind = "updated" | "unchanged" | "reset-required" | "error";
export type StreamRecordStatus = "provisional" | "stable" | "final";
export type StreamResetReason =
  | "source-truncated"
  | "source-replaced"
  | "source-compacted"
  | "cursor-mismatch"
  | "group-changed"
  | "sequence-gap"
  | "prefix-hash-mismatch"
  | "manual";

export interface BytePosition {
  readonly kind: "byte";
  readonly nextByteOffset: bigint;
  readonly pendingByteLength: bigint;
}

export interface AhpServerSeqPosition {
  readonly kind: "ahp-server-seq";
  readonly nextServerSeq: bigint;
  readonly lastServerSeq: bigint;
  readonly nextByteOffset?: bigint;
}

export interface SnapshotRevisionPosition {
  readonly kind: "snapshot-revision";
  readonly revision: string;
  readonly contentSha256?: string | null;
}

export interface HermesRowPosition {
  readonly kind: "hermes-row";
  readonly databaseGeneration: string;
  readonly lastRowId?: number | null;
  readonly changeToken?: string | null;
}

export type StreamPosition =
  | BytePosition
  | AhpServerSeqPosition
  | SnapshotRevisionPosition
  | HermesRowPosition;

export interface StreamCursor {
  readonly cursorVersion: 1;
  readonly source: TrajectorySource;
  readonly groupId: string;
  readonly generation: bigint;
  readonly position: StreamPosition;
  readonly sourceRevision: string | null;
  readonly prefixSha256: string | null;
}

export interface StreamOptions {
  readonly source: TrajectorySource;
  readonly groupId?: string;
  readonly delivery?: StreamDelivery;
  readonly includeProvisional?: boolean;
  readonly requireCompleteLines?: boolean;
  readonly finalizeOnClose?: boolean;
  readonly resetPolicy?: "return-reset-required" | "auto-reset";
  readonly maxPendingBytes?: bigint;
  readonly maxLineBytes?: bigint;
  readonly normalize?: NormalizationOptions;
  /** Pinned AHP protocol version for Shape B → Shape A materialization (default 0.7.0). */
  readonly ahpProtocolVersion?: string;
}

export interface StreamRevision {
  readonly revision: bigint;
  readonly revisionId: string;
  readonly parentRevisionId: string | null;
  readonly complete: boolean;
  readonly generation: bigint;
}

export interface StreamDiagnostic {
  readonly code: string;
  readonly message: string;
  readonly inputLine?: number;
  readonly recordIndex?: number;
  readonly count?: number;
}

export interface StreamRecord {
  readonly status: StreamRecordStatus;
  readonly record: JsonObject;
  readonly provisionalId?: string;
  readonly replacesProvisionalId?: string;
  readonly finalizesProvisionalId?: string;
}

export interface StreamSnapshot {
  readonly schemaId: typeof STREAM_SCHEMA_ID;
  readonly source: string;
  readonly groupId: string;
  readonly revision: StreamRevision;
  readonly records: readonly StreamRecord[];
  readonly diagnostics: readonly StreamDiagnostic[];
  readonly complete: boolean;
}

export interface StreamDeltaOperation {
  readonly op: string;
  readonly [key: string]: JsonValue | undefined;
}

export interface StreamDelta {
  readonly schemaId: typeof STREAM_SCHEMA_ID;
  readonly baseRevisionId: string | null;
  readonly revision: StreamRevision;
  readonly operations: readonly StreamDeltaOperation[];
}

export interface StreamReset {
  readonly reason: StreamResetReason;
  readonly priorCursor: StreamCursor | null;
  readonly requiresSnapshot: boolean;
  readonly droppedRecordIds: readonly string[];
}

export interface StreamUpdate {
  readonly kind: StreamUpdateKind;
  readonly revision: StreamRevision;
  readonly cursor: StreamCursor;
  readonly snapshot: StreamSnapshot | null;
  readonly delta: StreamDelta | null;
  readonly diagnostics: readonly StreamDiagnostic[];
  readonly provisional: {
    readonly include: boolean;
    readonly provisionalIds: readonly string[];
    readonly finalizedIds: readonly string[];
  };
  readonly consumed: {
    readonly completeRecords: bigint;
    readonly bytes: bigint;
    readonly firstSourcePosition?: bigint;
    readonly lastSourcePosition?: bigint;
  };
  readonly reset?: StreamReset;
  readonly error?: { readonly code: string; readonly message: string };
}

export interface StreamState {
  options: StreamOptions;
  cursor: StreamCursor;
  pendingBytes: Uint8Array;
  committedPrefix: Uint8Array;
  snapshot: StreamSnapshot | null;
  generation: bigint;
  nextRevision: bigint;
  finished: boolean;
  groupLocked: boolean;
  /**
   * Last accepted append-bytes segment + pre-apply next_byte_offset.
   * True replay requires re-supply with that pre-apply cursor (not content alone).
   */
  lastAppendSegment: Uint8Array | null;
  lastAppendPreOffset: bigint | null;
  /** AHP stream state (LS-06 / LS-07). */
  ahpChatState: ChatState | null;
  ahpSession: ChatState | null;
  ahpProtocolVersion: string | null;
  ahpLastServerSeq: number | null;
  ahpTargetChannel: string | null;
  ahpLastSnapshotRevision: string | null;
  ahpLastContentSha256: string | null;
  lastAhpActionsSha256: string | null;
  lastAhpActionsPreSeq: number | null;
  /** Hermes export stream state (LS-07h). */
  hermesRowFingerprints: string[] | null;
  hermesLastExportSha: string | null;
}

export type StreamInputKind =
  | "append-bytes"
  | "snapshot-bytes"
  | "finish"
  | "reset"
  | "ahp-actions"
  | "ahp-snapshot"
  | "hermes-export";

export interface StreamInput {
  readonly kind: StreamInputKind;
  readonly data?: Uint8Array;
  readonly sourceRevision?: string;
  readonly cursor?: StreamCursor;
  readonly reset?: StreamResetRequest;
  readonly changeToken?: string;
  readonly databaseGeneration?: string;
}

export interface StreamResetRequest {
  readonly reason: StreamResetReason;
  readonly generation?: bigint;
  readonly sourceRevision?: string | null;
  readonly priorCursor?: StreamCursor | null;
  readonly material?: Uint8Array;
  readonly changeToken?: string | null;
}

const LF = 0x0a;
/** Signed non-negative int64 upper bound (streaming-cursor-v1 / buffer limits). */
const INT64_MAX = 0x7fffffffffffffffn;
const MSG_BUFFER_LIMIT_DOMAIN = "Stream buffer limits must be non-negative int64 values.";
const MSG_CURSOR_DOMAIN = "Stream cursor byte positions must be non-negative int64 values.";
const MSG_MATERIAL_DOMAIN = "Stream material length exceeds non-negative int64 domain.";
const MSG_AHP_SOURCE_REQUIRED = "AHP stream apply requires source ahp.";
const MSG_HERMES_SOURCE_REQUIRED = "Hermes export stream apply requires source hermes.";
const MSG_INVALID_HERMES_EXPORT = "Hermes export material is not valid session-export JSON.";
const MSG_UNSUPPORTED_INPUT = "Stream input kind is not supported for this source.";
const MSG_SEQUENCE_GAP = "AHP action-log serverSeq gap requires snapshot resync.";
const MSG_INVALID_AHP_ACTIONS = "AHP action batch could not be parsed.";
const MSG_INVALID_AHP_SNAPSHOT = "AHP snapshot material is not valid Shape A JSON.";

function isNonNegativeInt64(value: bigint): boolean {
  return value >= 0n && value <= INT64_MAX;
}

function sha256Hex(data: string | Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

export function splitCompleteLines(data: Uint8Array): { committed: Uint8Array; pending: Uint8Array } {
  if (data.length === 0) return { committed: data, pending: data };
  let lastLf = -1;
  for (let i = data.length - 1; i >= 0; i--) {
    if (data[i] === LF) {
      lastLf = i;
      break;
    }
  }
  if (lastLf < 0) return { committed: data.subarray(0, 0), pending: data };
  return {
    committed: data.subarray(0, lastLf + 1),
    pending: data.subarray(lastLf + 1),
  };
}

export function matchKey(streamRecord: StreamRecord | JsonObject): string {
  const rec = streamRecord as StreamRecord & JsonObject;
  const provisional =
    "provisionalId" in rec && typeof rec.provisionalId === "string" && rec.provisionalId
      ? rec.provisionalId
      : typeof rec.provisional_id === "string" && rec.provisional_id
        ? (rec.provisional_id as string)
        : undefined;
  if (provisional) return provisional;
  const body = ("record" in rec ? rec.record : undefined) as JsonObject | undefined;
  const id = body?.id;
  if (typeof id === "string" && id) return id;
  throw new Error("stream record missing match key");
}

export function diagnosticKey(d: StreamDiagnostic | JsonObject): string {
  const code = String((d as StreamDiagnostic).code ?? (d as JsonObject).code ?? "");
  // Treat missing and null the same: normative '-' sentinel.
  let line: string | number | null | undefined;
  if ("inputLine" in d && (d as StreamDiagnostic).inputLine !== undefined) {
    line = (d as StreamDiagnostic).inputLine;
  } else if ("input_line" in (d as JsonObject)) {
    line = (d as JsonObject).input_line as string | number | null | undefined;
  } else {
    line = undefined;
  }
  let index: string | number | null | undefined;
  if ("recordIndex" in d && (d as StreamDiagnostic).recordIndex !== undefined) {
    index = (d as StreamDiagnostic).recordIndex;
  } else if ("record_index" in (d as JsonObject)) {
    index = (d as JsonObject).record_index as string | number | null | undefined;
  } else {
    index = undefined;
  }
  const linePart = line === undefined || line === null ? "-" : String(line);
  const indexPart = index === undefined || index === null ? "-" : String(index);
  return `${code}|${linePart}|${indexPart}`;
}

/** Serialize int64/bigint for wire JSON without precision loss. */
export function int64ToJson(value: bigint): number | string {
  if (value >= BigInt(Number.MIN_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER)) {
    return Number(value);
  }
  return value.toString();
}

function stripBigInt(value: unknown): JsonValue {
  if (typeof value === "bigint") return int64ToJson(value);
  if (Array.isArray(value)) return value.map(stripBigInt);
  if (value && typeof value === "object") {
    const out: JsonObject = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = stripBigInt(v);
    }
    return out;
  }
  return value as JsonValue;
}

function recordToDict(r: StreamRecord): JsonObject {
  const out: JsonObject = {
    status: r.status,
    record: stripBigInt(r.record) as JsonObject,
  };
  if (r.provisionalId !== undefined) out.provisional_id = r.provisionalId;
  if (r.replacesProvisionalId !== undefined) out.replaces_provisional_id = r.replacesProvisionalId;
  if (r.finalizesProvisionalId !== undefined) out.finalizes_provisional_id = r.finalizesProvisionalId;
  return out;
}

function diagnosticToDict(d: StreamDiagnostic): JsonObject {
  const out: JsonObject = { code: d.code, message: d.message };
  if (d.inputLine !== undefined) out.input_line = d.inputLine;
  if (d.recordIndex !== undefined) out.record_index = d.recordIndex;
  if (d.count !== undefined) out.count = d.count;
  return out;
}

function revisionToDict(r: StreamRevision): JsonObject {
  return {
    revision: int64ToJson(r.revision),
    revision_id: r.revisionId,
    parent_revision_id: r.parentRevisionId,
    complete: r.complete,
    generation: int64ToJson(r.generation),
  };
}

export function snapshotToDict(s: StreamSnapshot): JsonObject {
  return {
    schema_id: s.schemaId,
    source: s.source,
    group_id: s.groupId,
    revision: revisionToDict(s.revision),
    records: s.records.map(recordToDict),
    diagnostics: s.diagnostics.map(diagnosticToDict),
    complete: s.complete,
  };
}

export function deltaToDict(d: StreamDelta): JsonObject {
  return {
    schema_id: d.schemaId,
    base_revision_id: d.baseRevisionId,
    revision: revisionToDict(d.revision),
    operations: d.operations.map((op) => {
      const { op: kind, ...rest } = op;
      return { op: kind, ...rest } as JsonObject;
    }),
  };
}

export function cursorToDict(c: StreamCursor): JsonObject {
  let position: JsonObject;
  if (c.position.kind === "byte") {
    position = {
      kind: "byte",
      next_byte_offset: int64ToJson(c.position.nextByteOffset),
      pending_byte_length: int64ToJson(c.position.pendingByteLength),
    };
  } else if (c.position.kind === "ahp-server-seq") {
    position = {
      kind: "ahp-server-seq",
      next_server_seq: int64ToJson(c.position.nextServerSeq),
      last_server_seq: int64ToJson(c.position.lastServerSeq),
    };
    if (c.position.nextByteOffset !== undefined) {
      position.next_byte_offset = int64ToJson(c.position.nextByteOffset);
    }
  } else if (c.position.kind === "hermes-row") {
    position = {
      kind: "hermes-row",
      database_generation: c.position.databaseGeneration,
    };
    if (c.position.lastRowId != null) {
      position.last_row_id = c.position.lastRowId;
    }
    if (c.position.changeToken != null) {
      position.change_token = c.position.changeToken;
    }
  } else {
    position = {
      kind: "snapshot-revision",
      revision: c.position.revision,
    };
    if (c.position.contentSha256 != null) {
      position.content_sha256 = c.position.contentSha256;
    }
  }
  return {
    cursor_version: 1,
    source: c.source,
    group_id: c.groupId,
    generation: int64ToJson(c.generation),
    position,
    source_revision: c.sourceRevision,
    prefix_sha256: c.prefixSha256,
  };
}

export function updateToDict(u: StreamUpdate): JsonObject {
  const out: JsonObject = {
    kind: u.kind,
    revision: revisionToDict(u.revision),
    cursor: cursorToDict(u.cursor),
    snapshot: u.snapshot ? snapshotToDict(u.snapshot) : null,
    delta: u.delta ? deltaToDict(u.delta) : null,
    diagnostics: u.diagnostics.map(diagnosticToDict),
    provisional: {
      include: u.provisional.include,
      provisional_ids: [...u.provisional.provisionalIds],
      finalized_ids: [...u.provisional.finalizedIds],
    },
    consumed: {
      complete_records: int64ToJson(u.consumed.completeRecords),
      bytes: int64ToJson(u.consumed.bytes),
      ...(u.consumed.firstSourcePosition !== undefined
        ? { first_source_position: int64ToJson(u.consumed.firstSourcePosition) }
        : {}),
      ...(u.consumed.lastSourcePosition !== undefined
        ? { last_source_position: int64ToJson(u.consumed.lastSourcePosition) }
        : {}),
    },
  };
  if (u.reset) {
    out.reset = {
      reason: u.reset.reason,
      prior_cursor: u.reset.priorCursor ? cursorToDict(u.reset.priorCursor) : null,
      requires_snapshot: u.reset.requiresSnapshot,
      dropped_record_ids: [...u.reset.droppedRecordIds],
    };
  }
  if (u.error) {
    out.error = { code: u.error.code, message: u.error.message };
  }
  return out;
}

function stableJson(value: unknown): string {
  return JSON.stringify(value, (_k, v) =>
    typeof v === "bigint" ? int64ToJson(v) : v,
  );
}

/** True when any complete LF-terminated line exceeds maxLineBytes. */
export function anyLineTooLong(data: Uint8Array, maxLineBytes: bigint): boolean {
  let start = 0;
  for (let i = 0; i < data.length; i++) {
    if (data[i] === LF) {
      if (BigInt(i - start + 1) > maxLineBytes) return true;
      start = i + 1;
    }
  }
  return false;
}

export function diffSnapshots(
  prior: StreamSnapshot | null,
  current: StreamSnapshot,
  revision: StreamRevision,
): StreamDelta {
  const priorRecords = prior?.records ?? [];
  const currRecords = current.records;
  const priorByKey = new Map(priorRecords.map((r) => [matchKey(r), r]));
  const currByKey = new Map(currRecords.map((r) => [matchKey(r), r]));
  const ops: StreamDeltaOperation[] = [];

  for (const key of [...priorByKey.keys()].filter((k) => !currByKey.has(k)).sort()) {
    ops.push({ op: "remove", record_id: key, reason: "source-rewrite" });
  }
  for (const rec of currRecords) {
    const key = matchKey(rec);
    const prev = priorByKey.get(key);
    if (!prev || stableJson(prev.record) !== stableJson(rec.record)) {
      ops.push({ op: "upsert", record: recordToDict(rec) });
    } else if (prev.status !== rec.status) {
      ops.push({ op: "state_change", record_id: key, status: rec.status });
    }
  }

  const priorDiags = new Map((prior?.diagnostics ?? []).map((d) => [diagnosticKey(d), d]));
  const currDiags = new Map(current.diagnostics.map((d) => [diagnosticKey(d), d]));
  for (const key of [...priorDiags.keys()].filter((k) => !currDiags.has(k)).sort()) {
    ops.push({ op: "diagnostic_remove", diagnostic_key: key });
  }
  for (const key of [...currDiags.keys()].sort()) {
    const d = currDiags.get(key)!;
    const prev = priorDiags.get(key);
    if (!prev || stableJson(diagnosticToDict(prev)) !== stableJson(diagnosticToDict(d))) {
      ops.push({ op: "diagnostic_add", diagnostic: diagnosticToDict(d) });
    }
  }

  return {
    schemaId: STREAM_SCHEMA_ID,
    baseRevisionId: prior?.revision.revisionId ?? null,
    revision,
    operations: ops,
  };
}

export function applyDeltaToSnapshot(
  priorSnapshot: JsonObject | null,
  delta: JsonObject,
): JsonObject {
  const base: JsonObject = priorSnapshot
    ? (JSON.parse(stableJson(priorSnapshot)) as JsonObject)
    : { schema_id: STREAM_SCHEMA_ID, records: [], diagnostics: [] };
  let records = [...((base.records as JsonObject[]) ?? [])];
  let diagnostics = [...((base.diagnostics as JsonObject[]) ?? [])];
  const ops = (delta.operations as JsonObject[]) ?? [];

  for (const op of ops) {
    const kind = op.op as string;
    if (kind === "upsert") {
      const entry = op.record as JsonObject;
      const key = matchKey(entry);
      const idx = records.findIndex((r) => matchKey(r) === key);
      if (idx >= 0) records[idx] = entry;
      else records.push(entry);
    } else if (kind === "remove") {
      const rid = op.record_id as string;
      records = records.filter((r) => matchKey(r) !== rid);
    } else if (kind === "finalize") {
      const pid = op.provisional_id as string;
      records = records.filter((r) => r.provisional_id !== pid && matchKey(r) !== pid);
      const entry = op.record as JsonObject;
      const key = matchKey(entry);
      const idx = records.findIndex((r) => matchKey(r) === key);
      if (idx >= 0) records[idx] = entry;
      else records.push(entry);
    } else if (kind === "state_change") {
      const rid = op.record_id as string;
      const status = op.status as string;
      const idx = records.findIndex((r) => matchKey(r) === rid);
      if (idx >= 0) records[idx] = { ...records[idx], status };
    } else if (kind === "diagnostic_add") {
      const d = op.diagnostic as JsonObject;
      const key = diagnosticKey(d);
      diagnostics = diagnostics.filter((x) => diagnosticKey(x) !== key);
      diagnostics.push(d);
    } else if (kind === "diagnostic_remove") {
      const key = op.diagnostic_key as string;
      diagnostics = diagnostics.filter((x) => diagnosticKey(x) !== key);
    } else if (kind === "reset") {
      records = [];
      diagnostics = [];
    }
  }

  const revision = delta.revision as JsonObject | undefined;
  base.records = records;
  base.diagnostics = diagnostics;
  if (revision) {
    base.revision = revision;
    if ("complete" in revision) base.complete = revision.complete;
  }
  return base;
}

function revisionId(
  generation: bigint,
  revision: bigint,
  source: string,
  groupId: string,
  prefixSha: string,
  recordIds: string[],
): string {
  return sha256Hex(`${generation}|${revision}|${source}|${groupId}|${prefixSha}|${recordIds.join(",")}`);
}

function cloneState(state: StreamState): StreamState {
  return {
    options: state.options,
    cursor: state.cursor,
    pendingBytes: state.pendingBytes.slice(),
    committedPrefix: state.committedPrefix.slice(),
    snapshot: state.snapshot,
    generation: state.generation,
    nextRevision: state.nextRevision,
    finished: state.finished,
    groupLocked: state.groupLocked,
    lastAppendSegment: state.lastAppendSegment === null
      ? null
      : state.lastAppendSegment.slice(),
    lastAppendPreOffset: state.lastAppendPreOffset,
    ahpChatState: state.ahpChatState
      ? (JSON.parse(JSON.stringify(state.ahpChatState)) as ChatState)
      : null,
    ahpSession: state.ahpSession
      ? (JSON.parse(JSON.stringify(state.ahpSession)) as ChatState)
      : null,
    ahpProtocolVersion: state.ahpProtocolVersion,
    ahpLastServerSeq: state.ahpLastServerSeq,
    ahpTargetChannel: state.ahpTargetChannel,
    ahpLastSnapshotRevision: state.ahpLastSnapshotRevision,
    ahpLastContentSha256: state.ahpLastContentSha256,
    lastAhpActionsSha256: state.lastAhpActionsSha256,
    lastAhpActionsPreSeq: state.lastAhpActionsPreSeq,
    hermesRowFingerprints: state.hermesRowFingerprints,
    hermesLastExportSha: state.hermesLastExportSha,
  };
}

function applyDelivery(
  snapshot: StreamSnapshot,
  delta: StreamDelta,
  delivery: StreamDelivery,
): { snapshot: StreamSnapshot | null; delta: StreamDelta | null } {
  if (delivery === "snapshot") return { snapshot, delta: null };
  if (delivery === "delta") return { snapshot: null, delta };
  return { snapshot, delta };
}

function unchangedUpdate(state: StreamState): StreamUpdate {
  const rev = state.snapshot?.revision ?? {
    revision: 0n,
    revisionId: "unchanged",
    parentRevisionId: null,
    complete: state.finished,
    generation: state.generation,
  };
  return {
    kind: "unchanged",
    revision: rev,
    cursor: state.cursor,
    snapshot: null,
    delta: null,
    diagnostics: [],
    provisional: { include: state.options.includeProvisional !== false, provisionalIds: [], finalizedIds: [] },
    consumed: { completeRecords: 0n, bytes: 0n },
  };
}

function errorUpdate(state: StreamState, code: string, message: string): StreamUpdate {
  const rev = state.snapshot?.revision ?? {
    revision: 0n,
    revisionId: "error",
    parentRevisionId: null,
    complete: false,
    generation: state.generation,
  };
  return {
    kind: "error",
    revision: rev,
    cursor: state.cursor,
    snapshot: null,
    delta: null,
    diagnostics: [],
    provisional: { include: state.options.includeProvisional !== false, provisionalIds: [], finalizedIds: [] },
    consumed: { completeRecords: 0n, bytes: 0n },
    error: { code, message },
  };
}

function resetRequired(
  state: StreamState,
  reason: StreamResetReason,
  code: string,
  message: string,
): StreamUpdate {
  const rev = state.snapshot?.revision ?? {
    revision: 0n,
    revisionId: "reset-required",
    parentRevisionId: null,
    complete: false,
    generation: state.generation,
  };
  const dropped = (state.snapshot?.records ?? []).map((r) => String(r.record.id));
  return {
    kind: "reset-required",
    revision: rev,
    cursor: state.cursor,
    snapshot: null,
    delta: null,
    diagnostics: [{ code, message }],
    provisional: { include: state.options.includeProvisional !== false, provisionalIds: [], finalizedIds: [] },
    consumed: { completeRecords: 0n, bytes: 0n },
    reset: {
      reason,
      priorCursor: state.cursor,
      requiresSnapshot: true,
      droppedRecordIds: dropped,
    },
  };
}

export function createStream(options: StreamOptions): StreamState {
  const groupId = options.groupId ?? "default";
  let position: StreamPosition;
  if (options.source === "ahp") {
    position = { kind: "snapshot-revision", revision: "", contentSha256: null };
  } else if (options.source === "hermes") {
    position = { kind: "hermes-row", databaseGeneration: "", lastRowId: null, changeToken: null };
  } else {
    position = { kind: "byte", nextByteOffset: 0n, pendingByteLength: 0n };
  }
  return {
    options,
    cursor: {
      cursorVersion: 1,
      source: options.source,
      groupId,
      generation: 0n,
      position,
      sourceRevision: null,
      prefixSha256: null,
    },
    pendingBytes: new Uint8Array(0),
    committedPrefix: new Uint8Array(0),
    snapshot: null,
    generation: 0n,
    nextRevision: 0n,
    finished: false,
    groupLocked: false,
    lastAppendSegment: null,
    lastAppendPreOffset: null,
    ahpChatState: null,
    ahpSession: null,
    ahpProtocolVersion: null,
    ahpLastServerSeq: null,
    ahpTargetChannel: null,
    ahpLastSnapshotRevision: null,
    ahpLastContentSha256: null,
    lastAhpActionsSha256: null,
    lastAhpActionsPreSeq: null,
    hermesRowFingerprints: null,
    hermesLastExportSha: null,
  };
}

function buildRecords(
  state: StreamState,
  committed: Uint8Array,
):
  | { records: StreamRecord[]; diagnostics: StreamDiagnostic[]; groupId: string }
  | StreamUpdate {
  const groupHint = state.groupLocked ? state.cursor.groupId : state.options.groupId;
  if (committed.length === 0) {
    return { records: [], diagnostics: [], groupId: groupHint ?? state.cursor.groupId };
  }
  try {
    const ir = normalizeToIR({
      source: state.options.source,
      transcriptBytes: committed,
      sourceContext: {
        ...(groupHint !== undefined ? { groupId: groupHint } : {}),
        baseByteOffset: 0n,
        partial: true,
      },
      ...(state.options.normalize !== undefined
        ? { options: state.options.normalize }
        : {}),
    });
    const hyp = projectHypabolic(ir);
    const rawRecords = (hyp.records as JsonObject[]) ?? [];
    const hasBackendSynth = ir.diagnostics.some(
      (d) => d.code === "backend_tool_result_synthesized",
    );
    const markProvisional =
      hasBackendSynth && state.options.source === "grok-build";
    const records: StreamRecord[] = rawRecords.map((r) => {
      if (markProvisional && isSyntheticBackendToolResult(r)) {
        const id = typeof r.id === "string" ? r.id : undefined;
        return {
          status: "provisional" as const,
          record: r,
          ...(id !== undefined ? { provisionalId: id } : {}),
        };
      }
      return {
        status: "stable" as const,
        record: r,
      };
    });
    const diagnostics: StreamDiagnostic[] = ir.diagnostics.map((d) => ({
      code: d.code,
      message: d.message,
      ...(d.inputLine === undefined ? {} : { inputLine: d.inputLine }),
      ...(d.recordIndex === undefined ? {} : { recordIndex: d.recordIndex }),
      ...(d.count === undefined ? {} : { count: d.count }),
    }));
    return { records, diagnostics, groupId: ir.groupId };
  } catch (err) {
    if (err instanceof TrajectoryNormalizationError) {
      if (err.code === "source_group_conflict") {
        return resetRequired(
          state,
          "group-changed",
          "stream_source_reset",
          "Source group changed relative to the active stream.",
        );
      }
      return errorUpdate(state, err.code, err.message);
    }
    throw err;
  }
}

/**
 * Cursor validation shared by applySnapshot / apply append-bytes.
 * Domain (non-negative int64) is checked before position equality so out-of-domain
 * offsets yield invalid_input, not cursor-mismatch.
 */
function cursorConflict(state: StreamState, cursor: StreamCursor | undefined): StreamUpdate | null {
  if (!cursor) return null;
  if (cursor.source !== state.cursor.source || cursor.generation !== state.cursor.generation) {
    return resetRequired(
      state,
      "cursor-mismatch",
      "stream_cursor_conflict",
      "Supplied stream cursor does not match stream state.",
    );
  }
  if (state.groupLocked && cursor.groupId !== state.cursor.groupId) {
    return resetRequired(
      state,
      "group-changed",
      "stream_cursor_conflict",
      "Supplied stream cursor does not match stream state.",
    );
  }
  if (cursor.position.kind === "byte" && state.cursor.position.kind === "byte") {
    if (
      !isNonNegativeInt64(cursor.position.nextByteOffset) ||
      !isNonNegativeInt64(cursor.position.pendingByteLength)
    ) {
      return errorUpdate(state, "invalid_input", MSG_CURSOR_DOMAIN);
    }
    if (cursor.position.nextByteOffset !== state.cursor.position.nextByteOffset) {
      return resetRequired(
        state,
        "cursor-mismatch",
        "stream_cursor_conflict",
        "Supplied stream cursor does not match stream state.",
      );
    }
  }
  if (
    cursor.position.kind === "ahp-server-seq" &&
    state.cursor.position.kind === "ahp-server-seq"
  ) {
    if (
      !isNonNegativeInt64(cursor.position.nextServerSeq) ||
      !isNonNegativeInt64(cursor.position.lastServerSeq)
    ) {
      return errorUpdate(
        state,
        "invalid_input",
        "Stream cursor serverSeq positions must be non-negative int64 values.",
      );
    }
    if (
      cursor.position.lastServerSeq !== state.cursor.position.lastServerSeq ||
      cursor.position.nextServerSeq !== state.cursor.position.nextServerSeq
    ) {
      return resetRequired(
        state,
        "cursor-mismatch",
        "stream_cursor_conflict",
        "Supplied stream cursor does not match stream state.",
      );
    }
  }
  if (
    cursor.position.kind === "snapshot-revision" &&
    state.cursor.position.kind === "snapshot-revision" &&
    cursor.position.revision !== state.cursor.position.revision
  ) {
    return resetRequired(
      state,
      "cursor-mismatch",
      "stream_cursor_conflict",
      "Supplied stream cursor does not match stream state.",
    );
  }
  if (
    cursor.position.kind === "hermes-row" &&
    state.cursor.position.kind === "hermes-row"
  ) {
    if (
      cursor.position.databaseGeneration !== state.cursor.position.databaseGeneration ||
      cursor.position.lastRowId !== state.cursor.position.lastRowId ||
      cursor.position.changeToken !== state.cursor.position.changeToken
    ) {
      return resetRequired(
        state,
        "cursor-mismatch",
        "stream_cursor_conflict",
        "Supplied stream cursor does not match stream state.",
      );
    }
  }
  return null;
}

function byteNextOffset(pos: StreamPosition): bigint {
  return pos.kind === "byte" ? pos.nextByteOffset : 0n;
}

export function applySnapshot(
  state: StreamState,
  material: Uint8Array,
  sourceRevision: string,
  cursor?: StreamCursor,
): { state: StreamState; update: StreamUpdate } {
  if (state.finished) {
    return { state, update: errorUpdate(state, "invalid_input", "Stream is already finished.") };
  }
  const conflict = cursorConflict(state, cursor);
  if (conflict) {
    return { state, update: conflict };
  }

  if (state.options.maxPendingBytes !== undefined && !isNonNegativeInt64(state.options.maxPendingBytes)) {
    return {
      state,
      update: errorUpdate(state, "invalid_input", MSG_BUFFER_LIMIT_DOMAIN),
    };
  }
  if (state.options.maxLineBytes !== undefined && !isNonNegativeInt64(state.options.maxLineBytes)) {
    return {
      state,
      update: errorUpdate(state, "invalid_input", MSG_BUFFER_LIMIT_DOMAIN),
    };
  }

  const requireComplete = state.options.requireCompleteLines !== false;
  const { committed, pending } = requireComplete
    ? splitCompleteLines(material)
    : { committed: material, pending: new Uint8Array(0) };

  if (BigInt(committed.length) > INT64_MAX || BigInt(pending.length) > INT64_MAX) {
    return {
      state,
      update: errorUpdate(state, "invalid_input", MSG_MATERIAL_DOMAIN),
    };
  }

  if (state.options.maxPendingBytes !== undefined) {
    if (BigInt(pending.length) > state.options.maxPendingBytes) {
      return {
        state,
        update: errorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
      };
    }
  }

  if (state.options.maxLineBytes !== undefined) {
    if (
      anyLineTooLong(committed, state.options.maxLineBytes) ||
      BigInt(pending.length) > state.options.maxLineBytes
    ) {
      return {
        state,
        update: errorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
      };
    }
  }

  const built = buildRecords(state, committed);
  if ("kind" in built) {
    return { state, update: built };
  }
  let { records, diagnostics, groupId } = built;
  if (state.options.includeProvisional === false) {
    records = records.filter((r) => r.status !== "provisional");
  }

  if (
    state.snapshot !== null &&
    BigInt(committed.length) < byteNextOffset(state.cursor.position)
  ) {
    const { reason, message } = shrinkResetReason(state, committed);
    return {
      state,
      update: resetRequired(state, reason, "stream_source_reset", message),
    };
  }

  const emptySha = sha256Hex(new Uint8Array(0));
  const effectivePrefixSha = committed.length === 0 ? emptySha : sha256Hex(committed);

  if (
    state.snapshot !== null &&
    state.cursor.sourceRevision === sourceRevision &&
    state.cursor.prefixSha256 === effectivePrefixSha &&
    equalBytes(state.pendingBytes, pending)
  ) {
    return { state, update: unchangedUpdate(state) };
  }

  const newState = cloneState(state);
  newState.groupLocked = true;
  const generation = newState.generation;
  const parentRevisionId = newState.snapshot?.revision.revisionId ?? null;
  const revisionNum = newState.nextRevision;
  const revId = revisionId(
    generation,
    revisionNum,
    newState.cursor.source,
    groupId,
    effectivePrefixSha,
    records.map((r) => String(r.record.id)),
  );
  const revision: StreamRevision = {
    revision: revisionNum,
    revisionId: revId,
    parentRevisionId,
    complete: false,
    generation,
  };
  const snapshot: StreamSnapshot = {
    schemaId: STREAM_SCHEMA_ID,
    source: newState.cursor.source,
    groupId,
    revision,
    records,
    diagnostics,
    complete: false,
  };
  const delta = diffSnapshots(newState.snapshot, snapshot, revision);
  const delivery = state.options.delivery ?? "both";
  const delivered = applyDelivery(snapshot, delta, delivery);
  const newCursor: StreamCursor = {
    cursorVersion: 1,
    source: newState.cursor.source,
    groupId,
    generation,
    position: {
      kind: "byte",
      nextByteOffset: BigInt(committed.length),
      pendingByteLength: BigInt(pending.length),
    },
    sourceRevision,
    prefixSha256: effectivePrefixSha,
  };
  const update: StreamUpdate = {
    kind: "updated",
    revision,
    cursor: newCursor,
    snapshot: delivered.snapshot,
    delta: delivered.delta,
    diagnostics,
    provisional: {
      include: state.options.includeProvisional !== false,
      provisionalIds: records.filter((r) => r.provisionalId).map((r) => r.provisionalId!),
      finalizedIds: [],
    },
    consumed: {
      completeRecords: BigInt(records.length),
      bytes: BigInt(committed.length),
      ...(committed.length > 0
        ? { firstSourcePosition: 0n, lastSourcePosition: BigInt(committed.length - 1) }
        : {}),
    },
  };
  newState.cursor = newCursor;
  newState.snapshot = snapshot;
  newState.pendingBytes = pending.slice();
  newState.committedPrefix = committed.slice();
  newState.nextRevision = revisionNum + 1n;
  // Snapshot replaces committed material; clear append-replay fingerprint.
  newState.lastAppendSegment = null;
  newState.lastAppendPreOffset = null;
  return { state: newState, update };
}

/**
 * Append complete-line segment for file JSONL sources.
 * Frames against the pending buffer, extends the committed prefix, then
 * re-normalizes the full committed prefix (oracle path). Append equals
 * full-prefix snapshot on every shared fixture. The oracle path is the
 * steady-state implementation (O(committed_prefix)); no separate incremental
 * decoder requires a performance fallback in this slice.
 * First-class pure function matching Python/Rust/.NET apply_append / ApplyAppend.
 */
export function applyAppend(
  state: StreamState,
  segment: Uint8Array,
  cursor?: StreamCursor,
  sourceRevision?: string,
): { state: StreamState; update: StreamUpdate } {
  if (state.finished) {
    return { state, update: errorUpdate(state, "invalid_input", "Stream is already finished.") };
  }

  if (state.options.maxPendingBytes !== undefined && !isNonNegativeInt64(state.options.maxPendingBytes)) {
    return {
      state,
      update: errorUpdate(state, "invalid_input", MSG_BUFFER_LIMIT_DOMAIN),
    };
  }
  if (state.options.maxLineBytes !== undefined && !isNonNegativeInt64(state.options.maxLineBytes)) {
    return {
      state,
      update: errorUpdate(state, "invalid_input", MSG_BUFFER_LIMIT_DOMAIN),
    };
  }

  if (segment.length === 0 && state.pendingBytes.length === 0) {
    return { state, update: unchangedUpdate(state) };
  }

  // True append replay: same segment re-supplied with the pre-apply cursor.
  // Content equality alone is not enough — successive identical growth segments
  // must both commit after the cursor advances.
  const preOffset = byteNextOffset(state.cursor.position);
  if (
    state.lastAppendSegment !== null &&
    state.lastAppendPreOffset !== null &&
    equalBytes(segment, state.lastAppendSegment) &&
    cursor !== undefined &&
    cursor.position.kind === "byte" &&
    cursor.position.nextByteOffset === state.lastAppendPreOffset &&
    cursor.source === state.cursor.source &&
    cursor.generation === state.cursor.generation &&
    cursor.groupId === state.cursor.groupId
  ) {
    return { state, update: unchangedUpdate(state) };
  }

  const conflict = cursorConflict(state, cursor);
  if (conflict) {
    return { state, update: conflict };
  }

  const pending = state.pendingBytes;
  if (
    BigInt(pending.length) > INT64_MAX ||
    BigInt(segment.length) > INT64_MAX ||
    BigInt(pending.length) + BigInt(segment.length) > INT64_MAX
  ) {
    return {
      state,
      update: errorUpdate(state, "invalid_input", MSG_MATERIAL_DOMAIN),
    };
  }

  const combined = new Uint8Array(pending.length + segment.length);
  combined.set(pending, 0);
  combined.set(segment, pending.length);
  const { committed: complete, pending: newPending } = splitCompleteLines(combined);

  if (state.options.maxPendingBytes !== undefined) {
    if (BigInt(newPending.length) > state.options.maxPendingBytes) {
      return {
        state,
        update: errorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
      };
    }
  }
  if (state.options.maxLineBytes !== undefined) {
    if (
      anyLineTooLong(complete, state.options.maxLineBytes) ||
      BigInt(newPending.length) > state.options.maxLineBytes
    ) {
      return {
        state,
        update: errorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
      };
    }
  }

  // No complete lines: only pending advanced (incomplete line / mid-UTF-8).
  // Visible records unchanged → kind=unchanged with patched pending cursor.
  if (complete.length === 0) {
    if (equalBytes(newPending, state.pendingBytes)) {
      return { state, update: unchangedUpdate(state) };
    }
    const newState = cloneState(state);
    newState.pendingBytes = newPending.slice();
    newState.lastAppendSegment = segment.slice();
    newState.lastAppendPreOffset = preOffset;
    newState.cursor = {
      ...newState.cursor,
      position: {
        kind: "byte",
        nextByteOffset: byteNextOffset(newState.cursor.position),
        pendingByteLength: BigInt(newPending.length),
      },
    };
    return { state: newState, update: unchangedUpdate(newState) };
  }

  const newPrefix = new Uint8Array(state.committedPrefix.length + complete.length);
  newPrefix.set(state.committedPrefix, 0);
  newPrefix.set(complete, state.committedPrefix.length);
  const tmp = cloneState(state);
  tmp.pendingBytes = new Uint8Array(0);
  const result = applySnapshot(
    tmp,
    newPrefix,
    sourceRevision ?? state.cursor.sourceRevision ?? "",
    undefined,
  );
  // Failure-atomic: failed/reset snapshot leaves prior state and pending intact.
  if (result.update.kind !== "updated" && result.update.kind !== "unchanged") {
    return { state, update: result.update };
  }

  result.state.pendingBytes = newPending.slice();
  result.state.lastAppendSegment = segment.slice();
  result.state.lastAppendPreOffset = preOffset;
  result.state.cursor = {
    ...result.state.cursor,
    position: {
      kind: "byte",
      nextByteOffset: byteNextOffset(result.state.cursor.position),
      pendingByteLength: BigInt(newPending.length),
    },
  };
  // Always copy patched cursor onto StreamUpdate (updated and unchanged).
  let update = { ...result.update, cursor: result.state.cursor };
  if (result.update.kind === "updated") {
    const priorLen = BigInt(state.committedPrefix.length);
    const completeLen = BigInt(complete.length);
    update = {
      ...update,
      consumed: {
        completeRecords: result.update.consumed.completeRecords,
        bytes: completeLen,
        ...(completeLen > 0n
          ? {
              firstSourcePosition: priorLen,
              lastSourcePosition: priorLen + completeLen - 1n,
            }
          : {}),
      },
    };
  }
  return { state: result.state, update };
}

function shrinkResetReason(
  state: StreamState,
  committed: Uint8Array,
): { reason: StreamResetReason; message: string } {
  if (bytesStartWith(state.committedPrefix, committed)) {
    return {
      reason: "source-truncated",
      message: "Source material is shorter than the committed cursor.",
    };
  }
  if (state.options.source === "grok-build") {
    return {
      reason: "source-compacted",
      message: "Source material was compacted relative to the committed cursor.",
    };
  }
  return {
    reason: "source-replaced",
    message: "Source material was replaced relative to the committed cursor.",
  };
}

function bytesStartWith(haystack: Uint8Array, prefix: Uint8Array): boolean {
  if (prefix.length > haystack.length) return false;
  for (let i = 0; i < prefix.length; i++) {
    if (haystack[i] !== prefix[i]) return false;
  }
  return true;
}

function isSyntheticBackendToolResult(record: JsonObject): boolean {
  return (
    record.role === "tool" &&
    typeof record.content === "string" &&
    record.content.startsWith("[backend ")
  );
}

function activeTurnNativeIds(active: unknown): Set<string> {
  const ids = new Set<string>();
  if (!active || typeof active !== "object" || Array.isArray(active)) return ids;
  const a = active as Record<string, unknown>;
  if (typeof a.id === "string" && a.id) ids.add(a.id);
  if (Array.isArray(a.responseParts)) {
    for (const part of a.responseParts) {
      if (!part || typeof part !== "object" || Array.isArray(part)) continue;
      const p = part as Record<string, unknown>;
      if (typeof p.id === "string" && p.id) ids.add(p.id);
      const tc = p.toolCall;
      if (tc && typeof tc === "object" && !Array.isArray(tc)) {
        const id = (tc as Record<string, unknown>).toolCallId;
        if (typeof id === "string" && id) ids.add(id);
      }
    }
  }
  return ids;
}

function recordFromActiveTurn(record: JsonObject, activeIds: Set<string>): boolean {
  if (activeIds.size === 0) return false;
  const prov = record.provenance;
  if (!prov || typeof prov !== "object" || Array.isArray(prov)) return false;
  const p = prov as Record<string, unknown>;
  for (const key of ["native_record_id", "stable_source_record_id"]) {
    const val = p[key];
    if (typeof val === "string" && activeIds.has(val)) return true;
  }
  return false;
}

function buildAhpRecords(
  state: StreamState,
  material: Uint8Array,
):
  | {
      records: StreamRecord[];
      diagnostics: StreamDiagnostic[];
      groupId: string;
      chat: ChatState;
      session: ChatState | null;
      protocolVersion: string | null;
    }
  | StreamUpdate {
  let root: Record<string, unknown>;
  try {
    root = JSON.parse(new TextDecoder().decode(material)) as Record<string, unknown>;
  } catch {
    return errorUpdate(state, "invalid_input", MSG_INVALID_AHP_SNAPSHOT);
  }
  if (!root || typeof root !== "object" || !root.chat || typeof root.chat !== "object") {
    return errorUpdate(state, "invalid_input", MSG_INVALID_AHP_SNAPSHOT);
  }
  const chat = root.chat as ChatState;
  const session =
    root.session && typeof root.session === "object" && !Array.isArray(root.session)
      ? (root.session as ChatState)
      : null;
  const protocolVersion =
    typeof root.ahpProtocolVersion === "string" ? root.ahpProtocolVersion : null;
  const activeIds = activeTurnNativeIds(chat.activeTurn);

  let groupHint: string | undefined;
  if (state.groupLocked && state.cursor.groupId.startsWith("ahp-chat:")) {
    groupHint = state.cursor.groupId;
  }

  try {
    const ir = normalizeToIR({
      source: "ahp",
      transcriptBytes: material,
      sourceContext: {
        ...(groupHint !== undefined ? { groupId: groupHint } : {}),
        baseByteOffset: 0n,
        partial: true,
      },
      ...(state.options.normalize !== undefined ? { options: state.options.normalize } : {}),
    });
    const hyp = projectHypabolic(ir);
    const rawRecords = (hyp.records as JsonObject[]) ?? [];
    let provN = 0;
    const records: StreamRecord[] = [];
    for (const r of rawRecords) {
      const isProv = r.role !== "meta" && recordFromActiveTurn(r, activeIds);
      if (isProv) {
        provN += 1;
        records.push({
          status: "provisional",
          record: r,
          provisionalId: `prov-active-turn-${provN}`,
        });
      } else {
        records.push({ status: "stable", record: r });
      }
    }
    const diagnostics: StreamDiagnostic[] = ir.diagnostics
      .filter((d) => d.code !== "ahp_active_turn_omitted")
      .map((d) => ({
        code: d.code,
        message: d.message,
        ...(d.inputLine === undefined ? {} : { inputLine: d.inputLine }),
        ...(d.recordIndex === undefined ? {} : { recordIndex: d.recordIndex }),
        ...(d.count === undefined ? {} : { count: d.count }),
      }));
    return {
      records,
      diagnostics,
      groupId: ir.groupId,
      chat,
      session,
      protocolVersion,
    };
  } catch (err) {
    if (err instanceof TrajectoryNormalizationError) {
      if (err.code === "source_group_conflict") {
        return resetRequired(
          state,
          "group-changed",
          "stream_source_reset",
          "Source group changed relative to the active stream.",
        );
      }
      return errorUpdate(state, err.code, err.message);
    }
    throw err;
  }
}

/** LS-06: successive AHP Shape A snapshots with provisional activeTurn. */
export function applyAhpSnapshot(
  state: StreamState,
  material: Uint8Array,
  sourceRevision: string,
  cursor?: StreamCursor,
): { state: StreamState; update: StreamUpdate } {
  if (state.finished) {
    return { state, update: errorUpdate(state, "invalid_input", "Stream is already finished.") };
  }
  if (state.options.source !== "ahp") {
    return { state, update: errorUpdate(state, "invalid_input", MSG_AHP_SOURCE_REQUIRED) };
  }
  const conflict = cursorConflict(state, cursor);
  if (conflict) return { state, update: conflict };

  const contentSha = sha256Hex(material);
  if (
    state.snapshot !== null &&
    state.ahpLastSnapshotRevision === sourceRevision &&
    state.ahpLastContentSha256 === contentSha
  ) {
    return { state, update: unchangedUpdate(state) };
  }

  const built = buildAhpRecords(state, material);
  if ("kind" in built) return { state, update: built };
  let { records, diagnostics, groupId, chat, session, protocolVersion } = built;
  if (state.options.includeProvisional === false) {
    records = records.filter((r) => r.status !== "provisional");
  }

  const newState = cloneState(state);
  newState.groupLocked = true;
  const generation = newState.generation;
  const parentRevisionId = newState.snapshot?.revision.revisionId ?? null;
  const revisionNum = newState.nextRevision;
  const revId = revisionId(
    generation,
    revisionNum,
    newState.cursor.source,
    groupId,
    contentSha,
    records.map((r) => String(r.record.id)),
  );
  const revision: StreamRevision = {
    revision: revisionNum,
    revisionId: revId,
    parentRevisionId,
    complete: false,
    generation,
  };
  const snapshot: StreamSnapshot = {
    schemaId: STREAM_SCHEMA_ID,
    source: newState.cursor.source,
    groupId,
    revision,
    records,
    diagnostics,
    complete: false,
  };
  const delta = diffSnapshots(newState.snapshot, snapshot, revision);
  const delivery = state.options.delivery ?? "both";
  const delivered = applyDelivery(snapshot, delta, delivery);

  const position: StreamPosition =
    newState.ahpLastServerSeq !== null || newState.cursor.position.kind === "ahp-server-seq"
      ? {
          kind: "ahp-server-seq",
          nextServerSeq: BigInt((newState.ahpLastServerSeq ?? -1) + 1),
          lastServerSeq: BigInt(newState.ahpLastServerSeq ?? -1),
        }
      : {
          kind: "snapshot-revision",
          revision: sourceRevision,
          contentSha256: contentSha,
        };

  const cursorOut: StreamCursor = {
    cursorVersion: 1,
    source: newState.cursor.source,
    groupId,
    generation,
    position,
    sourceRevision,
    prefixSha256: contentSha,
  };
  const provisionalIds = records
    .map((r) => r.provisionalId)
    .filter((x): x is string => typeof x === "string");
  const priorProv = new Set(
    (state.snapshot?.records ?? [])
      .map((r) => r.provisionalId)
      .filter((x): x is string => typeof x === "string"),
  );
  const finalizedIds = [...priorProv].filter((p) => !provisionalIds.includes(p)).sort();

  const update: StreamUpdate = {
    kind: "updated",
    revision,
    cursor: cursorOut,
    snapshot: delivered.snapshot,
    delta: delivered.delta,
    diagnostics,
    provisional: {
      include: state.options.includeProvisional !== false,
      provisionalIds,
      finalizedIds,
    },
    consumed: {
      completeRecords: BigInt(records.length),
      bytes: BigInt(material.length),
      ...(material.length > 0
        ? {
            firstSourcePosition: 0n,
            lastSourcePosition: BigInt(material.length - 1),
          }
        : {}),
    },
  };

  newState.cursor = cursorOut;
  newState.snapshot = snapshot;
  newState.nextRevision = revisionNum + 1n;
  newState.ahpChatState = chat;
  newState.ahpSession = session;
  newState.ahpProtocolVersion = protocolVersion;
  newState.ahpTargetChannel = groupId.startsWith("ahp-chat:")
    ? groupId
    : newState.ahpTargetChannel;
  newState.ahpLastSnapshotRevision = sourceRevision;
  newState.ahpLastContentSha256 = contentSha;
  newState.committedPrefix = new Uint8Array(0);
  newState.pendingBytes = new Uint8Array(0);
  newState.lastAppendSegment = null;
  newState.lastAppendPreOffset = null;
  return { state: newState, update };
}

/** LS-07: AHP Shape B action-log apply with serverSeq cursor. */
export function applyAhpActions(
  state: StreamState,
  data: Uint8Array,
  cursor?: StreamCursor,
): { state: StreamState; update: StreamUpdate } {
  if (state.finished) {
    return { state, update: errorUpdate(state, "invalid_input", "Stream is already finished.") };
  }
  if (state.options.source !== "ahp") {
    return { state, update: errorUpdate(state, "invalid_input", MSG_AHP_SOURCE_REQUIRED) };
  }

  const actionsSha = sha256Hex(data);
  const preSeq = state.ahpLastServerSeq;

  if (state.lastAhpActionsSha256 !== null && state.lastAhpActionsSha256 === actionsSha) {
    if (cursor === undefined) return { state, update: unchangedUpdate(state) };
    if (cursor.position.kind === "ahp-server-seq") {
      if (
        state.lastAhpActionsPreSeq !== null &&
        Number(cursor.position.lastServerSeq) === state.lastAhpActionsPreSeq
      ) {
        return { state, update: unchangedUpdate(state) };
      }
      if (
        state.cursor.position.kind === "ahp-server-seq" &&
        cursor.position.lastServerSeq === state.cursor.position.lastServerSeq
      ) {
        return { state, update: unchangedUpdate(state) };
      }
    } else if (cursor.position.kind === "snapshot-revision") {
      if (state.lastAhpActionsPreSeq === null || state.lastAhpActionsPreSeq === -1) {
        return { state, update: unchangedUpdate(state) };
      }
    }
  }

  if (cursor !== undefined && cursor.position.kind === "ahp-server-seq") {
    const conflict = cursorConflict(state, cursor);
    if (conflict) return { state, update: conflict };
  }

  let envelopes: Record<string, import("./ahp-reducer.js").Json>[];
  try {
    envelopes = parseActionBatch(data);
  } catch {
    return { state, update: errorUpdate(state, "invalid_input", MSG_INVALID_AHP_ACTIONS) };
  }
  if (envelopes.length === 0) return { state, update: unchangedUpdate(state) };

  let target = state.ahpTargetChannel;
  if (target === null && state.groupLocked && state.cursor.groupId.startsWith("ahp-chat:")) {
    target = state.cursor.groupId;
  }

  const gap = detectSequenceGap(envelopes, state.ahpLastServerSeq, target);
  if (gap !== null) {
    return {
      state,
      update: resetRequired(state, "sequence-gap", "stream_sequence_gap", MSG_SEQUENCE_GAP),
    };
  }

  const chatIn = state.ahpChatState ?? emptyChatState(target);
  const reduced = reduceAhpActions(chatIn, envelopes, target, state.ahpLastServerSeq);
  const protocol =
    state.ahpProtocolVersion ?? state.options.ahpProtocolVersion ?? "0.7.0";
  const material = shapeABytes(reduced.chat, protocol, state.ahpSession);
  const rev =
    reduced.lastServerSeq !== null ? `seq:${reduced.lastServerSeq}` : state.cursor.sourceRevision ?? "seq:0";

  const snapState = cloneState(state);
  snapState.ahpChatState = reduced.chat;
  snapState.ahpLastServerSeq = reduced.lastServerSeq;
  snapState.ahpLastSnapshotRevision = null;
  snapState.ahpLastContentSha256 = null;

  const { state: newState, update } = applyAhpSnapshot(snapState, material, rev, undefined);
  if (update.kind !== "updated" && update.kind !== "unchanged") {
    return { state, update };
  }

  const extra = reduced.diagnostics.map((d) => ({ code: d.code, message: d.message }));
  const seen = new Set<string>();
  const diagnostics: StreamDiagnostic[] = [];
  for (const d of [...update.diagnostics, ...extra]) {
    const key = `${d.code}|${d.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    diagnostics.push(d);
  }
  // Sort by diagnostic_key so snapshot order matches key-sorted
  // diagnostic_add ops under the delta-apply law (streaming.md §7).
  diagnostics.sort((a, b) => {
    const ka = diagnosticKey(a);
    const kb = diagnosticKey(b);
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  });

  const lastSeq = reduced.lastServerSeq ?? -1;
  const seqCursor: StreamCursor = {
    cursorVersion: 1,
    source: newState.cursor.source,
    groupId: newState.cursor.groupId,
    generation: newState.cursor.generation,
    position: {
      kind: "ahp-server-seq",
      nextServerSeq: BigInt(lastSeq + 1),
      lastServerSeq: BigInt(lastSeq),
      nextByteOffset: BigInt(data.length),
    },
    sourceRevision: rev,
    prefixSha256: newState.cursor.prefixSha256,
  };
  newState.cursor = seqCursor;
  newState.ahpLastServerSeq = reduced.lastServerSeq;
  newState.ahpChatState = reduced.chat;
  newState.ahpTargetChannel = newState.cursor.groupId.startsWith("ahp-chat:")
    ? newState.cursor.groupId
    : target;
  newState.lastAhpActionsSha256 = actionsSha;
  newState.lastAhpActionsPreSeq = preSeq ?? -1;

  if (update.kind === "unchanged" && extra.length === 0) {
    return { state: newState, update: unchangedUpdate(newState) };
  }

  let outSnapshot = update.snapshot;
  let outDelta = update.delta;
  if (outSnapshot && diagnostics !== outSnapshot.diagnostics) {
    const snap: StreamSnapshot = {
      ...outSnapshot,
      diagnostics,
    };
    const delta = diffSnapshots(state.snapshot, snap, snap.revision);
    const delivery = state.options.delivery ?? "both";
    const delivered = applyDelivery(snap, delta, delivery);
    outSnapshot = delivered.snapshot;
    outDelta = delivered.delta;
    newState.snapshot = snap;
  }

  return {
    state: newState,
    update: {
      kind: "updated",
      revision: update.revision,
      cursor: seqCursor,
      snapshot: outSnapshot,
      delta: outDelta,
      diagnostics,
      provisional: update.provisional,
      consumed: {
        completeRecords: update.consumed.completeRecords,
        bytes: BigInt(data.length),
        ...(data.length > 0
          ? { firstSourcePosition: 0n, lastSourcePosition: BigInt(data.length - 1) }
          : {}),
      },
    },
  };
}

export function applyStream(
  state: StreamState,
  input: StreamInput,
): { state: StreamState; update: StreamUpdate } {
  if (input.kind === "snapshot-bytes") {
    return applySnapshot(state, input.data ?? new Uint8Array(0), input.sourceRevision ?? "", input.cursor);
  }
  if (input.kind === "append-bytes") {
    return applyAppend(state, input.data ?? new Uint8Array(0), input.cursor, input.sourceRevision);
  }
  if (input.kind === "finish") {
    return finishStream(state);
  }
  if (input.kind === "reset") {
    if (!input.reset) {
      return {
        state,
        update: errorUpdate(state, "invalid_input", "reset input requires a StreamResetRequest."),
      };
    }
    return resetStream(state, input.reset);
  }
  if (input.kind === "ahp-snapshot") {
    return applyAhpSnapshot(state, input.data ?? new Uint8Array(0), input.sourceRevision ?? "", input.cursor);
  }
  if (input.kind === "ahp-actions") {
    return applyAhpActions(state, input.data ?? new Uint8Array(0), input.cursor);
  }
  if (input.kind === "hermes-export") {
    return applyHermesExport(
      state,
      input.data ?? new Uint8Array(0),
      input.changeToken,
      input.databaseGeneration,
      input.sourceRevision,
      input.cursor,
    );
  }
  return { state, update: errorUpdate(state, "invalid_input", MSG_UNSUPPORTED_INPUT) };
}

/** LS-07h: apply Hermes session export (array or {session, messages}). */
export function applyHermesExport(
  state: StreamState,
  material: Uint8Array,
  changeToken?: string | null,
  databaseGeneration?: string | null,
  sourceRevision?: string | null,
  cursor?: StreamCursor,
): { state: StreamState; update: StreamUpdate } {
  if (state.finished) {
    return { state, update: errorUpdate(state, "invalid_input", "Stream is already finished.") };
  }
  if (state.options.source !== "hermes") {
    return { state, update: errorUpdate(state, "invalid_input", MSG_HERMES_SOURCE_REQUIRED) };
  }
  const conflict = cursorConflict(state, cursor);
  if (conflict) return { state, update: conflict };

  const contentSha = sha256Hex(material);
  const meta = hermesExportMeta(material);
  if (!meta) {
    return { state, update: errorUpdate(state, "invalid_input", MSG_INVALID_HERMES_EXPORT) };
  }
  const { rowFingerprints, lastRowId } = meta;
  const gen =
    databaseGeneration && databaseGeneration.length > 0
      ? databaseGeneration
      : sourceRevision && sourceRevision.length > 0
        ? sourceRevision
        : "0";
  const token =
    changeToken && changeToken.length > 0 ? changeToken : contentSha;

  if (
    state.snapshot !== null &&
    state.hermesLastExportSha === contentSha &&
    state.cursor.position.kind === "hermes-row" &&
    state.cursor.position.databaseGeneration === gen &&
    state.cursor.position.changeToken === token
  ) {
    return { state, update: unchangedUpdate(state) };
  }

  if (
    state.snapshot !== null &&
    state.cursor.position.kind === "hermes-row" &&
    state.cursor.position.databaseGeneration &&
    state.cursor.position.databaseGeneration !== gen
  ) {
    return {
      state,
      update: resetRequired(
        state,
        "source-replaced",
        "stream_source_reset",
        "Source material was replaced relative to the committed cursor.",
      ),
    };
  }

  const priorFps = state.hermesRowFingerprints;
  if (state.snapshot !== null && priorFps !== null) {
    const n = priorFps.length;
    if (
      rowFingerprints.length < n ||
      !priorFps.every((fp, i) => fp === rowFingerprints[i])
    ) {
      return {
        state,
        update: resetRequired(
          state,
          "source-replaced",
          "stream_source_reset",
          "Source material was replaced relative to the committed cursor.",
        ),
      };
    }
  }

  const built = buildRecords(state, material);
  if ("kind" in built) return { state, update: built };
  let { records, diagnostics, groupId } = built;
  if (state.options.includeProvisional === false) {
    records = records.filter((r) => r.status !== "provisional");
  }

  const newState = cloneState(state);
  newState.groupLocked = true;
  const generation = newState.generation;
  const parentRevisionId = newState.snapshot?.revision.revisionId ?? null;
  const revisionNum = newState.nextRevision;
  const revId = revisionId(
    generation,
    revisionNum,
    newState.cursor.source,
    groupId,
    contentSha,
    records.map((r) => String(r.record.id ?? "")),
  );
  const revision: StreamRevision = {
    revision: revisionNum,
    revisionId: revId,
    parentRevisionId,
    complete: false,
    generation,
  };
  const snapshot: StreamSnapshot = {
    schemaId: STREAM_SCHEMA_ID,
    source: newState.cursor.source,
    groupId,
    revision,
    records,
    diagnostics,
    complete: false,
  };
  const delta = diffSnapshots(newState.snapshot, snapshot, revision);
  const delivery = state.options.delivery ?? "both";
  const delivered = applyDelivery(snapshot, delta, delivery);
  const cursorOut: StreamCursor = {
    cursorVersion: 1,
    source: newState.cursor.source,
    groupId,
    generation,
    position: {
      kind: "hermes-row",
      databaseGeneration: gen,
      lastRowId,
      changeToken: token,
    },
    sourceRevision: sourceRevision ?? gen,
    prefixSha256: contentSha,
  };
  const provisionalIds = records
    .map((r) => r.provisionalId)
    .filter((id): id is string => typeof id === "string");
  const update: StreamUpdate = {
    kind: "updated",
    revision,
    cursor: cursorOut,
    snapshot: delivered.snapshot,
    delta: delivered.delta,
    diagnostics,
    provisional: {
      include: state.options.includeProvisional !== false,
      provisionalIds,
      finalizedIds: [],
    },
    consumed: {
      completeRecords: BigInt(records.length),
      bytes: BigInt(material.length),
      ...(material.length > 0
        ? {
            firstSourcePosition: 0n,
            lastSourcePosition: BigInt(material.length - 1),
          }
        : {}),
    },
  };
  newState.cursor = cursorOut;
  newState.snapshot = snapshot;
  newState.nextRevision = revisionNum + 1n;
  newState.committedPrefix = new Uint8Array(0);
  newState.pendingBytes = new Uint8Array(0);
  newState.lastAppendSegment = null;
  newState.lastAppendPreOffset = null;
  newState.hermesRowFingerprints = rowFingerprints;
  newState.hermesLastExportSha = contentSha;
  return { state: newState, update };
}

function hermesExportMeta(
  material: Uint8Array,
): { rowFingerprints: string[]; lastRowId: number | null } | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(material));
  } catch {
    return null;
  }
  let messages: unknown[];
  if (Array.isArray(parsed)) {
    messages = parsed;
  } else if (
    parsed &&
    typeof parsed === "object" &&
    Array.isArray((parsed as { messages?: unknown }).messages)
  ) {
    messages = (parsed as { messages: unknown[] }).messages;
  } else {
    return null;
  }
  if (!messages.every((m) => m && typeof m === "object" && !Array.isArray(m))) {
    return null;
  }
  let active = (messages as JsonObject[]).filter((row) => {
    const a = row.active ?? 1;
    return a !== 0 && a !== false && a !== "0";
  });
  let lastRowId: number | null = null;
  if (active.length > 0 && active.every((r) => hermesIsNumberId(r.id))) {
    active = [...active].sort((a, b) => Number(a.id) - Number(b.id));
    lastRowId = Number(active[active.length - 1]!.id);
  }
  const rowFingerprints = active.map((row) =>
    sha256Hex(
      new TextEncoder().encode(
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
    ),
  );
  return { rowFingerprints, lastRowId };
}

function hermesIsNumberId(value: unknown): boolean {
  if (typeof value === "boolean") return false;
  if (typeof value === "number") return Number.isInteger(value);
  return false;
}



/** End-of-stream: optionally commit final unterminated line; finalize records. */
export function finishStream(state: StreamState): { state: StreamState; update: StreamUpdate } {
  if (state.finished) {
    return { state, update: unchangedUpdate(state) };
  }

  const opts = state.options;
  let material = state.committedPrefix.slice();
  let pending = state.pendingBytes.slice();
  // Commit one final non-empty unterminated line once (parity with Python).
  if (pending.length > 0 && !pending.every((b) => b === 0x20 || b === 0x09 || b === 0x0d || b === 0x0a)) {
    const withNl = new Uint8Array(material.length + pending.length + 1);
    withNl.set(material, 0);
    withNl.set(pending, material.length);
    withNl[withNl.length - 1] = LF;
    material = withNl;
    pending = new Uint8Array(0);
  }

  const { state: midState, update: midUpdate } = applySnapshot(
    state,
    material,
    state.cursor.sourceRevision ?? "finish",
    undefined,
  );
  if (midUpdate.kind !== "updated" && midUpdate.kind !== "unchanged") {
    return { state: midState, update: midUpdate };
  }

  const baseSnapshot = midState.snapshot;
  if (baseSnapshot === null) {
    const finished = cloneState(midState);
    finished.finished = true;
    return { state: finished, update: midUpdate };
  }

  const finalizeOnClose = opts.finalizeOnClose !== false;
  let finalized: StreamRecord[];
  if (finalizeOnClose) {
    finalized = baseSnapshot.records.map((rec): StreamRecord => {
      if (rec.status === "final") return rec;
      const finalizes = rec.finalizesProvisionalId ?? rec.provisionalId;
      return {
        status: "final",
        record: rec.record,
        ...(rec.provisionalId !== undefined ? { provisionalId: rec.provisionalId } : {}),
        ...(rec.replacesProvisionalId !== undefined
          ? { replacesProvisionalId: rec.replacesProvisionalId }
          : {}),
        ...(finalizes !== undefined ? { finalizesProvisionalId: finalizes } : {}),
      };
    });
  } else {
    finalized = [...baseSnapshot.records];
  }

  const generation = midState.generation;
  const parentRevisionId = baseSnapshot.revision.revisionId;
  const revisionNum = midState.nextRevision;
  const prefixSha = midState.cursor.prefixSha256 ?? sha256Hex(new Uint8Array(0));
  const revId = revisionId(
    generation,
    revisionNum,
    midState.cursor.source,
    baseSnapshot.groupId,
    prefixSha,
    finalized.map((r) => String(r.record.id)),
  );
  const revision: StreamRevision = {
    revision: revisionNum,
    revisionId: revId,
    parentRevisionId,
    complete: true,
    generation,
  };
  const snapshot: StreamSnapshot = {
    schemaId: STREAM_SCHEMA_ID,
    source: baseSnapshot.source,
    groupId: baseSnapshot.groupId,
    revision,
    records: finalized,
    diagnostics: baseSnapshot.diagnostics,
    complete: true,
  };
  const delta = diffSnapshots(baseSnapshot, snapshot, revision);
  const delivery = opts.delivery ?? "both";
  const delivered = applyDelivery(snapshot, delta, delivery);

  const newState = cloneState(midState);
  newState.finished = true;
  newState.pendingBytes = new Uint8Array(0);
  newState.committedPrefix = material.slice();
  newState.snapshot = snapshot;
  newState.cursor = {
    cursorVersion: 1,
    source: midState.cursor.source,
    groupId: snapshot.groupId,
    generation,
    position: {
      kind: "byte",
      nextByteOffset: BigInt(newState.committedPrefix.length),
      pendingByteLength: 0n,
    },
    sourceRevision: midState.cursor.sourceRevision,
    prefixSha256:
      newState.committedPrefix.length === 0
        ? sha256Hex(new Uint8Array(0))
        : sha256Hex(newState.committedPrefix),
  };
  newState.nextRevision = revisionNum + 1n;

  const update: StreamUpdate = {
    kind: "updated",
    revision,
    cursor: newState.cursor,
    snapshot: delivered.snapshot,
    delta: delivered.delta,
    diagnostics: snapshot.diagnostics,
    provisional: {
      include: opts.includeProvisional !== false,
      provisionalIds: [],
      finalizedIds: finalized
        .map((r) => r.finalizesProvisionalId)
        .filter((id): id is string => typeof id === "string" && id.length > 0),
    },
    consumed: {
      completeRecords: BigInt(finalized.length),
      bytes: BigInt(newState.committedPrefix.length),
    },
  };
  return { state: newState, update };
}

export function resetStream(
  state: StreamState,
  request: StreamResetRequest,
): { state: StreamState; update: StreamUpdate } {
  const generation = request.generation ?? state.generation + 1n;
  const groupId = state.options.groupId ?? state.cursor.groupId;
  let newState = cloneState(state);
  newState.generation = generation;
  newState.nextRevision = 0n;
  newState.finished = false;
  newState.pendingBytes = new Uint8Array(0);
  newState.committedPrefix = new Uint8Array(0);
  newState.snapshot = null;
  newState.groupLocked = false;
  newState.lastAppendSegment = null;
  newState.lastAppendPreOffset = null;
  newState.ahpChatState = null;
  newState.ahpSession = null;
  newState.ahpProtocolVersion = null;
  newState.ahpLastServerSeq = null;
  newState.ahpTargetChannel = null;
  newState.ahpLastSnapshotRevision = null;
  newState.ahpLastContentSha256 = null;
  newState.lastAhpActionsSha256 = null;
  newState.lastAhpActionsPreSeq = null;
  newState.hermesRowFingerprints = null;
  newState.hermesLastExportSha = null;
  let resetPosition: StreamPosition;
  if (state.options.source === "ahp") {
    resetPosition = {
      kind: "snapshot-revision",
      revision: request.sourceRevision ?? "",
      contentSha256: null,
    };
  } else if (state.options.source === "hermes") {
    resetPosition = {
      kind: "hermes-row",
      databaseGeneration: request.sourceRevision ?? "",
      lastRowId: null,
      changeToken: request.changeToken ?? null,
    };
  } else {
    resetPosition = { kind: "byte", nextByteOffset: 0n, pendingByteLength: 0n };
  }
  newState.cursor = {
    cursorVersion: 1,
    source: state.cursor.source,
    groupId,
    generation,
    position: resetPosition,
    sourceRevision: request.sourceRevision ?? null,
    prefixSha256: null,
  };
  const dropped = (state.snapshot?.records ?? []).map((r) => String(r.record.id));
  const resetMeta: StreamReset = {
    reason: request.reason,
    priorCursor: request.priorCursor ?? state.cursor,
    requiresSnapshot: request.material === undefined,
    droppedRecordIds: dropped,
  };
  if (request.material) {
    const result =
      state.options.source === "hermes"
        ? applyHermesExport(
            newState,
            request.material,
            request.changeToken,
            request.sourceRevision,
            request.sourceRevision,
            undefined,
          )
        : applySnapshot(
      newState,
      request.material,
      request.sourceRevision ?? "",
      undefined,
    );
    if (result.update.kind !== "updated" && result.update.kind !== "unchanged") {
      return result;
    }
    // Merge reset envelope onto successful post-reset snapshot update.
    let delta = result.update.delta;
    if (delta) {
      delta = {
        ...delta,
        operations: [
          { op: "reset", reset: cursorToDictCompatibleReset(resetMeta) },
          ...delta.operations,
        ],
      };
    }
    return {
      state: result.state,
      update: {
        ...result.update,
        delta,
        reset: resetMeta,
      },
    };
  }
  // Empty reset with no material → updated empty snapshot of new generation
  // (parity with Python reset_stream shell semantics).
  const emptySha = sha256Hex(new Uint8Array(0));
  const revisionNum = 0n;
  const revId = revisionId(
    generation,
    revisionNum,
    newState.cursor.source,
    groupId,
    emptySha,
    [],
  );
  const revision: StreamRevision = {
    revision: revisionNum,
    revisionId: revId,
    parentRevisionId: null,
    complete: false,
    generation,
  };
  const snapshot: StreamSnapshot = {
    schemaId: STREAM_SCHEMA_ID,
    source: newState.cursor.source,
    groupId,
    revision,
    records: [],
    diagnostics: [],
    complete: false,
  };
  let delta = diffSnapshots(null, snapshot, revision);
  delta = {
    ...delta,
    operations: [
      { op: "reset", reset: cursorToDictCompatibleReset(resetMeta) },
      ...delta.operations,
    ],
  };
  const delivery = state.options.delivery ?? "both";
  const delivered = applyDelivery(snapshot, delta, delivery);
  newState.snapshot = snapshot;
  newState.nextRevision = 1n;
  newState.cursor = {
    cursorVersion: 1,
    source: newState.cursor.source,
    groupId,
    generation,
    position: resetPosition,
    sourceRevision: request.sourceRevision ?? null,
    prefixSha256: emptySha,
  };
  const update: StreamUpdate = {
    kind: "updated",
    revision,
    cursor: newState.cursor,
    snapshot: delivered.snapshot,
    delta: delivered.delta,
    diagnostics: [],
    provisional: {
      include: state.options.includeProvisional !== false,
      provisionalIds: [],
      finalizedIds: [],
    },
    consumed: { completeRecords: 0n, bytes: 0n },
    reset: resetMeta,
  };
  return { state: newState, update };
}

function cursorToDictCompatibleReset(reset: StreamReset): JsonObject {
  return {
    reason: reset.reason,
    prior_cursor: reset.priorCursor ? cursorToDict(reset.priorCursor) : null,
    requires_snapshot: reset.requiresSnapshot,
    dropped_record_ids: [...reset.droppedRecordIds],
  };
}

function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

export class TrajectoryStream {
  #state: StreamState;

  private constructor(state: StreamState) {
    this.#state = state;
  }

  static create(options: StreamOptions): TrajectoryStream {
    return new TrajectoryStream(createStream(options));
  }

  get cursor(): StreamCursor {
    return this.#state.cursor;
  }

  get state(): StreamState {
    return this.#state;
  }

  applySnapshot(data: Uint8Array, sourceRevision: string, cursor?: StreamCursor): StreamUpdate {
    const result = applySnapshot(this.#state, data, sourceRevision, cursor);
    this.#state = result.state;
    return result.update;
  }

  applyAppend(data: Uint8Array, cursor?: StreamCursor, sourceRevision?: string): StreamUpdate {
    const result = applyAppend(this.#state, data, cursor, sourceRevision);
    this.#state = result.state;
    return result.update;
  }

  applyHermesExport(
    data: Uint8Array,
    opts?: {
      changeToken?: string | null;
      databaseGeneration?: string | null;
      sourceRevision?: string | null;
      cursor?: StreamCursor;
    },
  ): StreamUpdate {
    const result = applyHermesExport(
      this.#state,
      data,
      opts?.changeToken,
      opts?.databaseGeneration,
      opts?.sourceRevision,
      opts?.cursor,
    );
    this.#state = result.state;
    return result.update;
  }

  finish(): StreamUpdate {
    const result = finishStream(this.#state);
    this.#state = result.state;
    return result.update;
  }

  reset(request: StreamResetRequest): StreamUpdate {
    const result = resetStream(this.#state, request);
    this.#state = result.state;
    return result.update;
  }

  apply(input: StreamInput): StreamUpdate {
    const result = applyStream(this.#state, input);
    this.#state = result.state;
    return result.update;
  }
}
