import {
  canonicalJson,
  formatMs,
  type IRRecord,
  type JsonObject,
  type JsonValue,
  relaxedJson,
  TrajectoryNormalizationError,
  type TrajectoryIR,
  toLetta,
} from "./internal.js";
import { createHash } from "node:crypto";

export interface ProjectionOptions {
  readonly writeIndented?: boolean;
}

export type ProjectionWriter = (chunk: string) => void;

export function projectLetta(trajectory: TrajectoryIR): JsonObject {
  return {
    records: trajectory.records.map(toLetta),
    diagnostics: trajectory.diagnostics.map((diagnostic) => ({
      code: diagnostic.code,
      message: diagnostic.message,
      ...(diagnostic.inputLine === undefined ? {} : { inputLine: diagnostic.inputLine }),
      ...(diagnostic.recordIndex === undefined ? {} : { recordIndex: diagnostic.recordIndex }),
      ...(diagnostic.count === undefined ? {} : { count: diagnostic.count }),
    })),
  };
}

export function projectOpenAI(trajectory: TrajectoryIR): JsonValue[] {
  return trajectory.records.flatMap((record): JsonValue[] => {
    if (record.kind === "meta" || record.role === "reasoning") return [];
    if (record.kind === "assistant_tool_calls") {
      return [{
        role: "assistant",
        tool_calls: record.toolCalls!.map((call) => ({
          id: call.id,
          type: "function",
          function: { name: call.name, arguments: call.argumentsJson },
        })),
      }];
    }
    if (record.kind === "tool_result") {
      return [{
        role: "tool",
        content: record.content ?? "",
        tool_call_id: record.toolCallId!,
        ...(record.toolName === undefined ? {} : { name: record.toolName }),
      }];
    }
    return [{ role: record.role, content: record.content ?? "" }];
  });
}

export function projectMinimalJsonl(trajectory: TrajectoryIR): string {
  return [...minimalJsonlChunks(trajectory)].join("");
}

/** Yields one complete JSONL record at a time, including its final newline. */
export function* minimalJsonlChunks(trajectory: TrajectoryIR): Generator<string> {
  for (const record of trajectory.records) {
    yield `${relaxedJson(minimalRecord(record))}\n`;
  }
}

/** Writes minimal JSONL incrementally without materializing the complete output. */
export function writeMinimalJsonl(
  trajectory: TrajectoryIR,
  write: ProjectionWriter,
): void {
  for (const chunk of minimalJsonlChunks(trajectory)) write(chunk);
}

/** Writes any materialized JSON projection through an ecosystem-native callback. */
export function writeSerializedProjection(
  value: JsonValue,
  write: ProjectionWriter,
  options?: ProjectionOptions,
): void {
  write(serializeProjection(value, options));
}

export function projectCanonical(trajectory: TrajectoryIR): JsonObject {
  if (trajectory.source === "codex" && !trajectory.groupResolved) {
    throw new TrajectoryNormalizationError(
      "source_group_required",
      "Canonical Codex normalization requires a source group: include session_meta or pass sourceContext.groupId.",
    );
  }
  return {
    records: trajectory.records
      .filter((record) =>
        trajectory.config.sourceContext.baseByteOffset === 0n || record.kind !== "meta",
      )
      .map((record) => canonicalRecord(trajectory, record)),
    diagnostics: trajectory.diagnostics.map((diagnostic) => ({
      code: diagnostic.code,
      message: diagnostic.message,
      ...(diagnostic.inputLine === undefined ? {} : { inputLine: diagnostic.inputLine }),
      ...(diagnostic.recordIndex === undefined ? {} : { recordIndex: diagnostic.recordIndex }),
      ...(diagnostic.count === undefined ? {} : { count: diagnostic.count }),
    })),
    normalizer_version: "0.2.0",
    canonical_schema_version: 1,
    config: {
      bounds: {
        toolArguments: { maxCharacters: trajectory.config.bounds.toolArguments.maxCharacters },
        toolResults: {
          maxCharacters: trajectory.config.bounds.toolResults.maxCharacters,
          strategy: trajectory.config.bounds.toolResults.strategy,
        },
      },
      filters: { toolResults: trajectory.config.filters.toolResults },
    },
  };
}

export function projectHypabolic(trajectory: TrajectoryIR): JsonObject {
  return {
    schema_id: "hypabolic-trajectory-v1",
    schema_version: 1,
    trajectory_id: sha256(relaxedJson([trajectory.source, trajectory.groupId])),
    source: {
      type: trajectory.source,
      name: trajectory.sourceName,
      group_id: trajectory.groupId,
      ...(trajectory.producerVersion === undefined ? {} : { producer_version: trajectory.producerVersion }),
    },
    segment: {
      partial: trajectory.config.sourceContext.partial || trajectory.config.sourceContext.baseByteOffset > 0n,
      base_byte_offset: trajectory.config.sourceContext.baseByteOffset,
    },
    normalizer: { name: "Hypabolic.Trajectory", version: "0.1.0" },
    config: {
      bounds: {
        tool_arguments: { max_characters: trajectory.config.bounds.toolArguments.maxCharacters },
        tool_results: {
          max_characters: trajectory.config.bounds.toolResults.maxCharacters,
          strategy: trajectory.config.bounds.toolResults.strategy,
        },
      },
      filters: { tool_results: trajectory.config.filters.toolResults },
    },
    records: trajectory.records.map(hypabolicRecord),
    diagnostics: trajectory.diagnostics.map((diagnostic) => ({
      code: diagnostic.code,
      message: diagnostic.message,
      ...(diagnostic.inputLine === undefined ? {} : { input_line: diagnostic.inputLine }),
      ...(diagnostic.recordIndex === undefined ? {} : { record_index: diagnostic.recordIndex }),
      ...(diagnostic.count === undefined ? {} : { count: diagnostic.count }),
    })),
  };
}

export function serializeProjection(value: JsonValue, options?: ProjectionOptions): string {
  if (!options?.writeIndented) return relaxedJson(value);
  return JSON.stringify(value, (_key, item: unknown) => typeof item === "bigint" ? Number(item) : item, 2);
}

function canonicalRecord(trajectory: TrajectoryIR, record: IRRecord): JsonObject {
  const call = record.toolCalls?.[0];
  return {
    source_type: trajectory.source,
    source_group_id: trajectory.groupId,
    stable_source_record_id: record.provenance.stableSourceRecordId,
    source_identity_kind: record.provenance.sourceIdentityKind,
    source_order_id: record.provenance.sourceOrderId,
    component_index: record.provenance.componentIndex,
    record_type: recordType(record),
    record_id: record.id,
    record_hash: record.hashes.recordSha256,
    content_hash: record.hashes.contentSha256,
    source_timestamp: record.sourceTimestamp === null ? null : formatMs(record.sourceTimestamp),
    record_timestamp: record.timestamp === null ? null : formatMs(record.timestamp),
    content: record.kind === "message" ? record.content ?? "" : null,
    tool_call_id: call?.id ?? record.toolCallId ?? null,
    tool_name: call?.name ?? null,
    tool_arguments_json: call?.argumentsJson ?? null,
    tool_result_json: record.kind === "tool_result" ? record.content ?? "" : null,
    record_json: canonicalJson(toLetta(record)),
  };
}

function hypabolicRecord(record: IRRecord): JsonObject {
  const output: JsonObject = {
    id: record.id,
    kind: record.kind,
    role: record.role,
    order: record.order,
    source_timestamp: record.sourceTimestamp === null ? null : formatMs(record.sourceTimestamp),
    timestamp: record.timestamp === null ? null : formatMs(record.timestamp),
  };
  if (record.kind === "meta") {
    output.source_name = record.sourceName ?? "unknown";
    if (record.cwd !== undefined) output.cwd = record.cwd;
    if (record.gitBranch !== undefined) output.git_branch = record.gitBranch;
    if (record.model !== undefined) output.model = record.model;
    if (record.producerVersion !== undefined) output.producer_version = record.producerVersion;
  } else if (record.kind === "assistant_tool_calls") {
    output.content = null;
    output.tool_calls = record.toolCalls!.map((call) => ({
      id: call.id,
      name: call.name,
      arguments_json: call.argumentsJson,
    }));
  } else if (record.content !== undefined) {
    output.content = record.content;
  }
  if (record.toolCallId !== undefined) output.tool_call_id = record.toolCallId;
  if (record.toolName !== undefined) output.tool_name = record.toolName;
  if (record.isError !== undefined) output.is_error = record.isError;
  const provenance: JsonObject = {
    stable_source_record_id: record.provenance.stableSourceRecordId,
    source_identity_kind: record.provenance.sourceIdentityKind,
    source_order_id: record.provenance.sourceOrderId,
    component_key: record.provenance.componentKey,
    component_index: record.provenance.componentIndex,
    component_type_ordinal: record.provenance.componentTypeOrdinal,
  };
  if (record.provenance.nativeRecordId !== undefined) provenance.native_record_id = record.provenance.nativeRecordId;
  if (record.provenance.producerVersion !== undefined) provenance.producer_version = record.provenance.producerVersion;
  if (record.provenance.sourceSequence !== undefined) provenance.source_sequence = record.provenance.sourceSequence;
  if (record.provenance.sourceOffset !== undefined) provenance.source_offset = record.provenance.sourceOffset;
  if (record.provenance.sourceAnchorKind !== undefined) provenance.source_anchor_kind = record.provenance.sourceAnchorKind;
  output.provenance = provenance;
  output.hashes = {
    content_sha256: record.hashes.contentSha256,
    record_sha256: record.hashes.recordSha256,
  };
  return output;
}

function minimalRecord(record: IRRecord): JsonObject {
  const value: JsonObject = {
    id: record.id,
    order: record.order,
    kind: record.kind.replaceAll("_", ""),
    role: record.role,
  };
  if (record.timestamp !== null) value.timestamp = formatOffset(record.timestamp);
  if (record.content !== undefined) value.content = record.content;
  if (record.toolCallId !== undefined) value.tool_call_id = record.toolCallId;
  if (record.toolName !== undefined) value.tool_name = record.toolName;
  if (record.isError !== undefined) value.is_error = record.isError;
  if (record.toolCalls !== undefined) {
    value.tool_calls = record.toolCalls.map((call) => ({
      id: call.id,
      name: call.name,
      arguments_json: call.argumentsJson,
    }));
  }
  return value;
}

function recordType(record: IRRecord): string {
  if (record.kind === "assistant_tool_calls") return "assistant-tool-call";
  if (record.kind === "tool_result") return "tool";
  return record.role;
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function formatOffset(value: number): string {
  return formatMs(value).replace("Z", "+00:00");
}
