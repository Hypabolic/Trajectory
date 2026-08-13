#!/usr/bin/env node

import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  utimes,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";

import {
  applyAhpActions,
  applyAhpSnapshot,
  applyAppend,
  applyHermesExport,
  applySnapshot,
  createStream,
  finishStream,
  normalizeToIR,
  projectCanonical,
  projectHypabolic,
  projectLetta,
  projectMinimalJsonl,
  projectOpenAI,
  resetStream,
  serializeProjection,
  TrajectoryNormalizationError,
  updateToDict,
  type StreamCursor,
  type StreamOptions,
  type StreamResetRequest,
  type StreamSnapshot,
  type StreamState,
  type StreamUpdate,
  type TrajectorySource,
} from "@hypabolic/trajectory";
import {
  listAhpTrajectories,
  listClaudeCodeTrajectories,
  listCodexTrajectories,
  listCursorTrajectories,
  listGrokBuildTrajectories,
  listHermesTrajectories,
  listOpenClawTrajectories,
  listPiTrajectories,
} from "@hypabolic/trajectory-node";
import { projectOpenTelemetry } from "@hypabolic/trajectory-otel";

interface Request {
  protocol_version: string;
  case: string;
  operation: string;
  repository_root: string;
}

interface Manifest {
  id: string;
  source: string;
  group_id?: string;
  transcript?: string;
  operation?: Record<string, unknown>;
  steps?: StreamStep[];
  options?: {
    delivery?: "both" | "snapshot" | "delta";
    include_provisional?: boolean;
    require_complete_lines?: boolean;
    finalize_on_close?: boolean;
    reset_policy?: "return-reset-required" | "auto-reset";
    max_pending_bytes?: number;
    max_line_bytes?: number;
    ahp_protocol_version?: string;
  };
  oracle?: {
    prefix_re_normalize?: boolean;
    append_equals_prefix?: boolean;
    action_equals_snapshot?: boolean;
    snapshot_material?: string;
    snapshot_source_revision?: string;
  };
  store?: string;
  listing?: { limit?: number; all_pages?: boolean };
  source_context?: {
    group_id?: string;
    base_byte_offset?: number;
    partial?: boolean;
    include_encrypted_reasoning?: boolean | string;
  };
  bounds?: {
    tool_arguments?: { max_characters?: number | null };
    tool_results?: { max_characters?: number | null; strategy?: "head" | "head-tail" };
  };
  filters?: { tool_results?: "include" | "omit" };
}

interface StreamStep {
  id?: string;
  input: {
    kind: string;
    material?: string;
    inline_utf8?: string;
    source_revision?: string | null;
    cursor?: Record<string, unknown>;
    change_token?: string;
    database_generation?: string;
    reset?: {
      reason: string;
      generation?: number;
      source_revision?: string | null;
      material?: string;
      inline_utf8?: string;
      change_token?: string;
    };
  };
  expected?: Record<string, unknown>;
  double_invoke?: boolean;
}

class StreamEngineUnsupported extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamEngineUnsupported";
  }
}

const STREAM_OPERATIONS = new Set([
  "stream-sequence",
  "stream-replay",
  "stream-apply-append",
  "stream-apply-snapshot",
  "stream-apply-ahp-actions",
  "stream-apply-ahp-snapshot",
  "stream-finish",
  "stream-reset",
]);

try {
  const request = await readRequest();
  const response = await execute(request);
  process.stdout.write(JSON.stringify(response));
} catch (error) {
  process.stdout.write(JSON.stringify({
    case: "",
    operation: "",
    status: "protocol-error",
    output_text: null,
    diagnostics: [],
    fatal_error: {
      code: "invalid_request",
      message: error instanceof Error ? error.message : String(error),
    },
  }));
  process.exitCode = 2;
}

async function readRequest(): Promise<Request> {
  const text = process.argv.length === 3
    ? await readFile(process.argv[2]!, "utf8")
    : await new Promise<string>((resolveInput, reject) => {
      let value = "";
      process.stdin.setEncoding("utf8");
      process.stdin.on("data", (chunk: string) => { value += chunk; });
      process.stdin.on("end", () => { resolveInput(value); });
      process.stdin.on("error", reject);
    });
  const parsed: unknown = JSON.parse(text);
  if (!isObject(parsed)) throw new Error("The request must be a JSON object.");
  for (const key of ["protocol_version", "case", "operation", "repository_root"]) {
    if (typeof parsed[key] !== "string") throw new Error(`Request property '${key}' must be a string.`);
  }
  if (parsed.protocol_version !== "1") {
    throw new Error(`Unsupported protocol version '${String(parsed.protocol_version)}'.`);
  }
  return parsed as unknown as Request;
}

async function execute(request: Request): Promise<unknown> {
  const repositoryRoot = resolve(request.repository_root);
  const casesRoot = join(repositoryRoot, "conformance", "cases");
  const caseDirectory = safeResolve(casesRoot, request.case);
  const manifest = JSON.parse(await readFile(join(caseDirectory, "case.json"), "utf8")) as Manifest;
  if (manifest.id !== request.case) throw new Error("The requested case does not match its manifest ID.");
  if (!["pi", "claude-code", "codex", "openclaw", "hermes", "ahp", "grok-build", "cursor"].includes(manifest.source)) {
    throw new Error(`TypeScript does not support source '${manifest.source}'.`);
  }

  // LS-05: multi-step stream sequence via core apply_append / apply_snapshot.
  if (STREAM_OPERATIONS.has(request.operation)) {
    if (!Array.isArray(manifest.steps) || manifest.steps.length === 0) {
      throw new Error(
        `Stream operation '${request.operation}' requires a streaming case with steps[].`,
      );
    }
    if (request.operation === "stream-sequence" || request.operation === "stream-replay") {
      try {
        const outputText = await executeStreamSequence(caseDirectory, manifest);
        return {
          protocol_version: "1",
          case: request.case,
          operation: request.operation,
          status: "success",
          output_text: outputText,
          diagnostics: [],
          fatal_error: null,
        };
      } catch (error) {
        if (error instanceof StreamEngineUnsupported) {
          return {
            protocol_version: "1",
            case: request.case,
            operation: request.operation,
            status: "unsupported",
            output_text: null,
            diagnostics: [],
            fatal_error: {
              code: "capability_unsupported",
              message: error.message,
            },
          };
        }
        throw error;
      }
    }
    return {
      protocol_version: "1",
      case: request.case,
      operation: request.operation,
      status: "unsupported",
      output_text: null,
      diagnostics: [],
      fatal_error: {
        code: "capability_unsupported",
        message: "Per-step stream apply ops are not implemented yet.",
      },
    };
  }

  if (!manifest.operation || !(request.operation in manifest.operation)) {
    throw new Error(`Case '${request.case}' does not declare operation '${request.operation}'.`);
  }
  try {
    let outputText: string;
    let diagnostics: TrajectoryIRDiagnostics = [];
    if (request.operation === "list-trajectories") {
      outputText = await executeListing(repositoryRoot, manifest);
    } else {
      if (!manifest.transcript) throw new Error("Case field 'transcript' must be a non-empty string.");
      const sourceContext = manifest.source_context ?? {};
      const bounds = manifest.bounds ?? {};
      const filters = manifest.filters ?? {};
      const transcript = await readFile(safeResolve(caseDirectory, manifest.transcript));
      const includeEncrypted = sourceContext.include_encrypted_reasoning === true
        || sourceContext.include_encrypted_reasoning === "true";
      const trajectory = normalizeToIR({
        source: manifest.source as TrajectorySource,
        transcriptBytes: transcript,
        sourceContext: {
          ...(sourceContext.group_id === undefined ? {} : { groupId: sourceContext.group_id }),
          ...(sourceContext.base_byte_offset === undefined ? {} : { baseByteOffset: BigInt(sourceContext.base_byte_offset) }),
          partial: sourceContext.partial ?? false,
          ...(includeEncrypted ? { includeEncryptedReasoning: true } : {}),
        },
        options: {
          bounds: {
            ...(bounds.tool_arguments === undefined ? {} : {
              toolArguments: {
                ...(bounds.tool_arguments.max_characters === undefined
                  ? {}
                  : { maxCharacters: bounds.tool_arguments.max_characters }),
              },
            }),
            ...(bounds.tool_results === undefined ? {} : {
              toolResults: {
                ...(bounds.tool_results.max_characters === undefined
                  ? {}
                  : { maxCharacters: bounds.tool_results.max_characters }),
                ...(bounds.tool_results.strategy === undefined
                  ? {}
                  : { strategy: bounds.tool_results.strategy }),
              },
            }),
          },
          filters: {
            ...(filters.tool_results === undefined
              ? {}
              : { toolResults: filters.tool_results }),
          },
        },
      });
      diagnostics = trajectory.diagnostics;
      outputText = request.operation === "normalize-letta"
        ? serializeProjection(projectLetta(trajectory))
        : request.operation === "normalize-canonical"
          ? serializeProjection(projectCanonical(trajectory))
          : request.operation === "normalize-hypabolic"
            ? serializeProjection(projectHypabolic(trajectory))
            : request.operation === "project-openai"
              ? serializeProjection(projectOpenAI(trajectory))
              : request.operation === "project-minimal-jsonl"
                ? projectMinimalJsonl(trajectory)
                : request.operation === "project-otel"
                  ? serializeProjection(projectOpenTelemetry(trajectory) as never)
                  : unsupported(request.operation);
    }
    return {
      case: request.case,
      operation: request.operation,
      status: "success",
      output_text: outputText,
      diagnostics,
      fatal_error: null,
    };
  } catch (error) {
    if (!(error instanceof TrajectoryNormalizationError)) throw error;
    return {
      case: request.case,
      operation: request.operation,
      status: "fatal-error",
      output_text: null,
      diagnostics: [],
      fatal_error: { code: error.code, message: error.message },
    };
  }
}

type TrajectoryIRDiagnostics = readonly {
  readonly code: string;
  readonly message: string;
  readonly inputLine?: number;
  readonly recordIndex?: number;
  readonly count?: number;
}[];

function streamOptionsFromManifest(manifest: Manifest): StreamOptions {
  const opts = manifest.options ?? {};
  return {
    source: manifest.source as TrajectorySource,
    ...(manifest.group_id === undefined ? {} : { groupId: manifest.group_id }),
    delivery: opts.delivery ?? "both",
    includeProvisional: opts.include_provisional ?? true,
    requireCompleteLines: opts.require_complete_lines ?? true,
    finalizeOnClose: opts.finalize_on_close ?? true,
    resetPolicy: opts.reset_policy ?? "return-reset-required",
    ...(opts.max_pending_bytes === undefined
      ? {}
      : { maxPendingBytes: BigInt(opts.max_pending_bytes) }),
    ...(opts.max_line_bytes === undefined
      ? {}
      : { maxLineBytes: BigInt(opts.max_line_bytes) }),
    ...(opts.ahp_protocol_version === undefined
      ? {}
      : { ahpProtocolVersion: opts.ahp_protocol_version }),
  };
}

async function loadStepBytes(
  caseDirectory: string,
  input: { material?: string; inline_utf8?: string },
): Promise<Uint8Array> {
  if (input.inline_utf8 !== undefined) {
    return new TextEncoder().encode(input.inline_utf8);
  }
  if (!input.material) throw new Error("Step input requires material or inline_utf8.");
  // Binary read: never decode as text (Windows would inject CRLF into JSONL /
  // utf8-byte-boundary .bin tails and shift apply_append cursor / hashes).
  return new Uint8Array(await readFile(safeResolve(caseDirectory, input.material)));
}

function parseStreamCursor(raw: Record<string, unknown> | undefined): StreamCursor | undefined {
  if (!raw) return undefined;
  const source = raw.source;
  const groupId = raw.group_id;
  if (typeof source !== "string" || typeof groupId !== "string") {
    throw new Error("Step cursor requires source and group_id strings.");
  }
  const position = raw.position as Record<string, unknown> | undefined;
  if (!position || typeof position.kind !== "string") {
    throw new Error("Step cursor position must be an object with kind.");
  }
  let pos: StreamCursor["position"];
  if (position.kind === "byte") {
    pos = {
      kind: "byte",
      nextByteOffset: BigInt(Number(position.next_byte_offset ?? 0)),
      pendingByteLength: BigInt(Number(position.pending_byte_length ?? 0)),
    };
  } else if (position.kind === "ahp-server-seq") {
    pos = {
      kind: "ahp-server-seq",
      nextServerSeq: BigInt(Number(position.next_server_seq ?? 0)),
      lastServerSeq: BigInt(Number(position.last_server_seq ?? -1)),
      ...(position.next_byte_offset === undefined
        ? {}
        : { nextByteOffset: BigInt(Number(position.next_byte_offset)) }),
    };
  } else if (position.kind === "snapshot-revision") {
    if (typeof position.revision !== "string") {
      throw new Error("snapshot-revision.revision must be a string.");
    }
    pos = {
      kind: "snapshot-revision",
      revision: position.revision,
      contentSha256:
        typeof position.content_sha256 === "string" ? position.content_sha256 : null,
    };
  } else if (position.kind === "hermes-row") {
    if (typeof position.database_generation !== "string") {
      throw new Error("hermes-row.database_generation must be a string.");
    }
    pos = {
      kind: "hermes-row",
      databaseGeneration: position.database_generation,
      lastRowId:
        typeof position.last_row_id === "number" ? position.last_row_id : null,
      changeToken:
        typeof position.change_token === "string" ? position.change_token : null,
    };
  } else {
    throw new Error(
      "Stream cursor position.kind must be byte, ahp-server-seq, snapshot-revision, or hermes-row.",
    );
  }
  return {
    cursorVersion: 1,
    source: source as TrajectorySource,
    groupId,
    generation: BigInt(Number(raw.generation ?? 0)),
    position: pos,
    sourceRevision: (raw.source_revision as string | null | undefined) ?? null,
    prefixSha256: (raw.prefix_sha256 as string | null | undefined) ?? null,
  };
}

async function applyStreamStep(
  state: StreamState,
  caseDirectory: string,
  stepInput: StreamStep["input"],
): Promise<{ state: StreamState; update: StreamUpdate }> {
  const kind = stepInput.kind;
  const sourceRevision = stepInput.source_revision ?? undefined;
  const cursor = parseStreamCursor(stepInput.cursor);
  if (kind === "append-bytes") {
    const data = await loadStepBytes(caseDirectory, stepInput);
    return applyAppend(state, data, cursor, sourceRevision ?? undefined);
  }
  if (kind === "snapshot-bytes") {
    const data = await loadStepBytes(caseDirectory, stepInput);
    return applySnapshot(state, data, sourceRevision ?? "", cursor);
  }
  if (kind === "finish") {
    return finishStream(state);
  }
  if (kind === "reset") {
    if (!stepInput.reset) throw new Error("reset step requires reset object.");
    let material: Uint8Array | undefined;
    if (stepInput.reset.material !== undefined || stepInput.reset.inline_utf8 !== undefined) {
      material = await loadStepBytes(caseDirectory, stepInput.reset);
    }
    const request: StreamResetRequest = {
      reason: stepInput.reset.reason as StreamResetRequest["reason"],
      ...(stepInput.reset.generation === undefined
        ? {}
        : { generation: BigInt(stepInput.reset.generation) }),
      ...(stepInput.reset.source_revision === undefined || stepInput.reset.source_revision === null
        ? {}
        : { sourceRevision: stepInput.reset.source_revision }),
      ...(material === undefined ? {} : { material }),
    };
    return resetStream(state, request);
  }
  if (kind === "ahp-snapshot") {
    const data = await loadStepBytes(caseDirectory, stepInput);
    return applyAhpSnapshot(state, data, sourceRevision ?? "", cursor);
  }
  if (kind === "ahp-actions") {
    const data = await loadStepBytes(caseDirectory, stepInput);
    return applyAhpActions(state, data, cursor);
  }
  if (kind === "hermes-export") {
    const data = await loadStepBytes(caseDirectory, stepInput);
    const changeToken =
      typeof stepInput.change_token === "string" ? stepInput.change_token : undefined;
    const databaseGeneration =
      typeof stepInput.database_generation === "string"
        ? stepInput.database_generation
        : sourceRevision ?? undefined;
    return applyHermesExport(
      state,
      data,
      changeToken,
      databaseGeneration,
      sourceRevision ?? undefined,
      cursor,
    );
  }
  throw new Error(`Unsupported stream input kind '${kind}'.`);
}

function streamStateEquivalent(a: StreamState, b: StreamState): boolean {
  if (a.finished !== b.finished || a.generation !== b.generation) return false;
  if (a.committedPrefix.length !== b.committedPrefix.length) return false;
  for (let i = 0; i < a.committedPrefix.length; i++) {
    if (a.committedPrefix[i] !== b.committedPrefix[i]) return false;
  }
  if (a.pendingBytes.length !== b.pendingBytes.length) return false;
  for (let i = 0; i < a.pendingBytes.length; i++) {
    if (a.pendingBytes[i] !== b.pendingBytes[i]) return false;
  }
  const ca = a.cursor;
  const cb = b.cursor;
  if (
    ca.source !== cb.source ||
    ca.groupId !== cb.groupId ||
    ca.generation !== cb.generation ||
    ca.sourceRevision !== cb.sourceRevision ||
    ca.prefixSha256 !== cb.prefixSha256 ||
    ca.position.kind !== cb.position.kind
  ) {
    return false;
  }
  if (ca.position.kind === "byte" && cb.position.kind === "byte") {
    return (
      ca.position.nextByteOffset === cb.position.nextByteOffset &&
      ca.position.pendingByteLength === cb.position.pendingByteLength
    );
  }
  if (ca.position.kind === "ahp-server-seq" && cb.position.kind === "ahp-server-seq") {
    return (
      ca.position.nextServerSeq === cb.position.nextServerSeq &&
      ca.position.lastServerSeq === cb.position.lastServerSeq
    );
  }
  if (ca.position.kind === "snapshot-revision" && cb.position.kind === "snapshot-revision") {
    return (
      ca.position.revision === cb.position.revision &&
      ca.position.contentSha256 === cb.position.contentSha256
    );
  }
  if (ca.position.kind === "hermes-row" && cb.position.kind === "hermes-row") {
    return (
      ca.position.databaseGeneration === cb.position.databaseGeneration &&
      ca.position.lastRowId === cb.position.lastRowId &&
      ca.position.changeToken === cb.position.changeToken
    );
  }
  return false;
}

async function executeStreamSequence(
  caseDirectory: string,
  manifest: Manifest,
): Promise<string> {
  const steps = manifest.steps ?? [];
  let state = createStream(streamOptionsFromManifest(manifest));
  const stepResults: { id: string; update: ReturnType<typeof updateToDict>; idempotent: boolean }[] = [];

  for (const step of steps) {
    const stepId = typeof step.id === "string" ? step.id : "step";
    const double = step.double_invoke !== false;
    const preCursor = state.cursor;
    let result = await applyStreamStep(state, caseDirectory, step.input);
    let update = result.update;
    state = result.state;
    let idempotent = true;
    if (double) {
      let second: { state: StreamState; update: StreamUpdate };
      // Append true-replay: re-supply with the cursor that governed the first apply.
      if (
        step.input.kind === "append-bytes" &&
        (update.kind === "updated" || update.kind === "unchanged")
      ) {
        const replayCursor = parseStreamCursor(step.input.cursor) ?? preCursor;
        const data = await loadStepBytes(caseDirectory, step.input);
        second = applyAppend(
          state,
          data,
          replayCursor,
          step.input.source_revision ?? undefined,
        );
      } else {
        second = await applyStreamStep(state, caseDirectory, step.input);
      }
      if (update.kind === "updated" || update.kind === "unchanged") {
        idempotent =
          second.update.kind === "unchanged" ||
          (second.update.kind === "updated" && streamStateEquivalent(state, second.state));
      } else {
        idempotent =
          second.update.kind === update.kind && streamStateEquivalent(state, second.state);
      }
      state = second.state;
    }
    stepResults.push({
      id: stepId,
      update: updateToDict(update),
      idempotent,
    });
  }

  const payload: Record<string, unknown> = { steps: stepResults };
  const oracle = manifest.oracle;
  if (
    oracle?.append_equals_prefix ||
    oracle?.prefix_re_normalize ||
    oracle?.action_equals_snapshot
  ) {
    const section: Record<string, boolean> = {};
    if (oracle.append_equals_prefix || oracle.prefix_re_normalize) {
      let oracleState = createStream(streamOptionsFromManifest(manifest));
      const rev = state.cursor.sourceRevision ?? "oracle";
      let snap = applySnapshot(oracleState, state.committedPrefix, rev);
      oracleState = snap.state;
      // When the append path finished (stable→final), mirror finish so oracle
      // finality matches (LS-08 stable-to-final).
      if (
        (snap.update.kind === "updated" || snap.update.kind === "unchanged") &&
        state.finished
      ) {
        snap = finishStream(oracleState);
        oracleState = snap.state;
      }
      const ok =
        snap.update.kind === "updated" || snap.update.kind === "unchanged"
          ? oracleSnapshotsMatch(
              state.snapshot,
              snap.update.snapshot,
              state.cursor,
              snap.update.cursor,
            )
          : false;
      if (oracle.append_equals_prefix) section.append_equals_prefix = ok;
      if (oracle.prefix_re_normalize) section.prefix_re_normalize = ok;
    }
    if (oracle.action_equals_snapshot) {
      const materialName = oracle.snapshot_material ?? "step-snapshot.json";
      const rev = oracle.snapshot_source_revision ?? "ahp-equiv-1";
      try {
        const material = await loadStepBytes(caseDirectory, {
          material: materialName,
        });
        const snap = applyAhpSnapshot(
          createStream(streamOptionsFromManifest(manifest)),
          material,
          rev,
        );
        section.action_equals_snapshot =
          (snap.update.kind === "updated" || snap.update.kind === "unchanged") &&
          actionSnapshotParity(state.snapshot, snap.update.snapshot);
      } catch {
        section.action_equals_snapshot = false;
      }
    }
    payload.oracle = section;
  }
  return JSON.stringify(payload);
}

function actionSnapshotParity(
  actionSnap: StreamSnapshot | null | undefined,
  snapshotSnap: StreamSnapshot | null | undefined,
): boolean {
  if (!actionSnap || !snapshotSnap) return !actionSnap && !snapshotSnap;
  const actIds = actionSnap.records.map((r) => [r.record.id ?? null, r.status]);
  const snapIds = snapshotSnap.records.map((r) => [r.record.id ?? null, r.status]);
  if (JSON.stringify(actIds) !== JSON.stringify(snapIds)) return false;
  const nonMeta = (records: StreamSnapshot["records"]) =>
    records
      .filter((r) => r.record.role !== "meta")
      .map((r) => [r.record.id ?? null, r.status, r.record.role ?? null, r.record.content ?? null]);
  return JSON.stringify(nonMeta(actionSnap.records)) === JSON.stringify(nonMeta(snapshotSnap.records));
}

function recordParityKey(r: {
  status: string;
  record: { id?: string };
  provisionalId?: string | null;
  replacesProvisionalId?: string | null;
  finalizesProvisionalId?: string | null;
}): string {
  return JSON.stringify([
    r.record.id ?? "",
    r.status,
    r.provisionalId ?? null,
    r.replacesProvisionalId ?? null,
    r.finalizesProvisionalId ?? null,
  ]);
}

function diagnosticParityKey(d: {
  code: string;
  message: string;
  inputLine?: number | null;
  recordIndex?: number | null;
  count?: number | null;
}): string {
  return JSON.stringify([
    d.code,
    d.message,
    d.inputLine ?? null,
    d.recordIndex ?? null,
    d.count ?? null,
  ]);
}

function oracleSnapshotsMatch(
  appendSnap: StreamSnapshot | null | undefined,
  oracleSnap: StreamSnapshot | null | undefined,
  appendCursor: StreamCursor,
  oracleCursor: StreamCursor,
): boolean {
  // Missing snapshot (never updated — pure pending) ≡ empty incomplete snapshot.
  const aRecords = appendSnap?.records ?? [];
  const oRecords = oracleSnap?.records ?? [];
  const aKeys = aRecords.map(recordParityKey);
  const oKeys = oRecords.map(recordParityKey);
  if (JSON.stringify(aKeys) !== JSON.stringify(oKeys)) return false;
  const aDiags = (appendSnap?.diagnostics ?? []).map(diagnosticParityKey);
  const oDiags = (oracleSnap?.diagnostics ?? []).map(diagnosticParityKey);
  if (JSON.stringify(aDiags) !== JSON.stringify(oDiags)) return false;
  if ((appendSnap?.complete ?? false) !== (oracleSnap?.complete ?? false)) return false;
  if (appendCursor.prefixSha256 !== oracleCursor.prefixSha256) return false;
  if (
    appendCursor.position.kind === "byte" &&
    oracleCursor.position.kind === "byte"
  ) {
    return (
      appendCursor.position.nextByteOffset === oracleCursor.position.nextByteOffset
    );
  }
  return JSON.stringify(appendCursor.position) === JSON.stringify(oracleCursor.position);
}

async function executeListing(repositoryRoot: string, manifest: Manifest): Promise<string> {
  if (!manifest.store) throw new Error("Listing case requires a declarative store.");
  const storePath = safeResolve(join(repositoryRoot, "conformance", "stores"), join(manifest.store, "store.json"));
  const store = JSON.parse(await readFile(storePath, "utf8")) as {
    files: { path: string; content: string; updated_at?: string }[];
  };
  const root = await mkdtemp(join(tmpdir(), "trajectory-conformance-"));
  try {
    for (const fixture of store.files) {
      const destination = safeResolve(root, fixture.path);
      await mkdir(dirname(destination), { recursive: true });
      await writeFile(destination, fixture.content);
      if (fixture.updated_at) {
        const timestamp = new Date(fixture.updated_at);
        await utimes(destination, timestamp, timestamp);
      }
    }
    const listingRoot = manifest.source === "pi" || manifest.source === "openclaw"
      ? root
      : join(root, "store");
    const pages: unknown[] = [];
    let cursor: string | undefined;
    do {
      const list = manifest.source === "claude-code"
        ? listClaudeCodeTrajectories
        : manifest.source === "codex"
          ? listCodexTrajectories
          : manifest.source === "openclaw"
            ? listOpenClawTrajectories
            : manifest.source === "hermes"
              ? listHermesTrajectories
              : manifest.source === "ahp"
                ? listAhpTrajectories
              : manifest.source === "grok-build"
                ? listGrokBuildTrajectories
                : manifest.source === "cursor"
                  ? listCursorTrajectories
                : listPiTrajectories;
      const page = await list({
        root: listingRoot,
        limit: manifest.listing?.limit ?? 50,
        ...(cursor === undefined ? {} : { cursor }),
      });
      pages.push({
        items: page.items.map((item) => ({
          id: item.id,
          path: item.path.replace(root, "$ROOT").replaceAll("\\", "/"),
          updated_at: item.updatedAt,
          ...(item.title === undefined ? {} : { title: item.title }),
          size_bytes: item.sizeBytes,
        })),
        next_cursor: page.nextCursor,
      });
      cursor = page.nextCursor ?? undefined;
    } while (manifest.listing?.all_pages && cursor !== undefined);
    return JSON.stringify(manifest.listing?.all_pages ? pages : pages[0]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function safeResolve(root: string, path: string): string {
  if (isAbsolute(path)) throw new Error("Fixture path must be relative.");
  const output = resolve(root, path);
  const rel = relative(root, output);
  if (rel === ".." || rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) || isAbsolute(rel)) {
    throw new Error("Fixture path escapes its declared root.");
  }
  return output;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function unsupported(operation: string): never {
  throw new Error(`Unsupported operation '${operation}'.`);
}
