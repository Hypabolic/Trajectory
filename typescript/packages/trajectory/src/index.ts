/**
 * Adapters start from exact transcript bytes. String input is a convenience only;
 * adapters derive byte anchors from this UTF-8 encoding, never UTF-16 indices.
 */
export type TranscriptInput = Uint8Array | string;
export type TrajectorySource = "pi" | "claude-code" | "codex" | "openclaw" | "hermes" | "ahp" | "grok-build";

export interface SourceContext {
  readonly groupId?: string;
  readonly baseByteOffset?: bigint;
  readonly partial?: boolean;
  /** When true, project Grok Build encrypted_content into reasoning text. */
  readonly includeEncryptedReasoning?: boolean;
}

export interface NormalizeRequest {
  readonly source: TrajectorySource;
  readonly transcript?: TranscriptInput;
  readonly transcriptBytes?: Uint8Array;
  readonly sourceContext?: SourceContext;
  readonly options?: NormalizationOptions;
}

export interface NormalizationOptions {
  readonly bounds?: {
    readonly toolArguments?: { readonly maxCharacters?: number | null };
    readonly toolResults?: {
      readonly maxCharacters?: number | null;
      readonly strategy?: "head" | "head-tail";
    };
  };
  readonly filters?: { readonly toolResults?: "include" | "omit" };
}

export interface TrajectoryDiagnostic {
  readonly code: string;
  readonly message: string;
  readonly inputLine?: number;
  readonly recordIndex?: number;
  readonly count?: number;
}

export const NORMALIZER_CONTRACT_VERSION = "0.2.0";
export const ImplementedSources = ["pi", "claude-code", "codex", "openclaw", "hermes", "ahp", "grok-build"] as const;
export const OutputSchemaIds = {
  lettaTrajectoryV1: "letta-trajectory-v1",
  lettaCanonicalV1: "letta-canonical-v1",
  hypabolicTrajectoryV1: "hypabolic-trajectory-v1",
  openAiChatMessages: "openai-chat-messages",
  jsonlMinimal: "jsonl-minimal",
} as const;

export type OutputSchemaId = typeof OutputSchemaIds[keyof typeof OutputSchemaIds];
export type OutputProjector<T = unknown> = (trajectory: TrajectoryIR) => T;

export function transcriptBytes(input: TranscriptInput): Uint8Array {
  return typeof input === "string" ? new TextEncoder().encode(input) : input;
}

import {
  normalizeAhp,
  normalizeClaudeCode,
  normalizeCodex,
  normalizeGrokBuild,
  normalizeHermes,
  normalizeOpenClaw,
  normalizePi,
  type TrajectoryIR,
  TrajectoryNormalizationError,
} from "./internal.js";
import {
  projectCanonical,
  projectHypabolic,
  projectLetta,
  projectMinimalJsonl,
  projectOpenAI,
  type ProjectionOptions,
  type ProjectionWriter,
  serializeProjection,
  minimalJsonlChunks,
  writeMinimalJsonl,
  writeSerializedProjection,
} from "./projections.js";

export type { TrajectoryIR };
export type { ProjectionOptions };
export type { ProjectionWriter };
export { TrajectoryNormalizationError };

function normalizedRequest(request: NormalizeRequest): NormalizeRequest & { transcriptBytes: Uint8Array } {
  const bytes = request.transcriptBytes ?? (request.transcript === undefined ? undefined : transcriptBytes(request.transcript));
  if (!bytes) throw new TypeError("NormalizeRequest requires transcript or transcriptBytes.");
  return { ...request, transcriptBytes: bytes };
}

export function normalizeToIR(request: NormalizeRequest): TrajectoryIR {
  const normalized = normalizedRequest(request);
  if (request.source === "pi") return normalizePi(normalized);
  if (request.source === "claude-code") return normalizeClaudeCode(normalized);
  if (request.source === "codex") return normalizeCodex(normalized);
  if (request.source === "openclaw") return normalizeOpenClaw(normalized);
  if (request.source === "hermes") return normalizeHermes(normalized);
  if (request.source === "ahp") return normalizeAhp(normalized);
  if (request.source === "grok-build") return normalizeGrokBuild(normalized);
  throw new TrajectoryNormalizationError("unknown_source", `No source adapter is registered for '${String(request.source)}'.`);
}

export function normalizeToLetta(request: NormalizeRequest): unknown {
  return projectLetta(normalizeToIR(request));
}

export const normalizeTranscript = normalizeToLetta;

export function normalizeToCanonical(request: NormalizeRequest): unknown {
  return projectCanonical(normalizeToIR(request));
}

export function normalizeToHypabolic(request: NormalizeRequest): unknown {
  return projectHypabolic(normalizeToIR(request));
}

export {
  projectCanonical,
  projectHypabolic,
  projectLetta,
  projectMinimalJsonl,
  projectOpenAI,
  serializeProjection,
  minimalJsonlChunks,
  writeMinimalJsonl,
  writeSerializedProjection,
};

export {
  STREAM_SCHEMA_ID,
  TrajectoryStream,
  anyLineTooLong,
  applyAppend,
  applyDeltaToSnapshot,
  applySnapshot,
  applyStream,
  createStream,
  cursorToDict,
  deltaToDict,
  diagnosticKey,
  diffSnapshots,
  finishStream,
  int64ToJson,
  matchKey,
  resetStream,
  snapshotToDict,
  splitCompleteLines,
  updateToDict,
  type BytePosition,
  type StreamCursor,
  type StreamDelta,
  type StreamInput,
  type StreamOptions,
  type StreamRecord,
  type StreamResetRequest,
  type StreamSnapshot,
  type StreamState,
  type StreamUpdate,
} from "./streaming.js";

export class TrajectoryEngine {
  readonly #outputs = new Map<string, OutputProjector>();

  static createDefault(): TrajectoryEngine {
    return new TrajectoryEngine()
      .addOutputAdapter(OutputSchemaIds.lettaTrajectoryV1, projectLetta)
      .addOutputAdapter(OutputSchemaIds.lettaCanonicalV1, projectCanonical)
      .addOutputAdapter(OutputSchemaIds.hypabolicTrajectoryV1, projectHypabolic)
      .addOutputAdapter(OutputSchemaIds.openAiChatMessages, projectOpenAI)
      .addOutputAdapter(OutputSchemaIds.jsonlMinimal, projectMinimalJsonl);
  }

  addOutputAdapter<T>(schemaId: string, projector: OutputProjector<T>): this {
    if (this.#outputs.has(schemaId)) {
      throw new Error(`An output adapter for schema '${schemaId}' is already registered.`);
    }
    this.#outputs.set(schemaId, projector);
    return this;
  }

  normalizeToIR(request: NormalizeRequest): TrajectoryIR {
    return normalizeToIR(request);
  }

  project<T = unknown>(trajectory: TrajectoryIR, schemaId: string): T {
    const projector = this.#outputs.get(schemaId);
    if (!projector) {
      throw new TrajectoryNormalizationError(
        "unknown_output_schema",
        `No output adapter is registered for schema '${schemaId}'.`,
      );
    }
    return projector(trajectory) as T;
  }

  normalizeTranscript(request: NormalizeRequest): unknown {
    return this.project(
      this.normalizeToIR(request),
      OutputSchemaIds.lettaTrajectoryV1,
    );
  }
}
