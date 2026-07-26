import { createHash } from "node:crypto";

import type {
  NormalizeRequest,
  NormalizationOptions,
  TrajectoryDiagnostic,
  TrajectorySource,
} from "./index.js";

export type JsonObject = { [key: string]: JsonValue };
export type JsonValue = null | boolean | number | bigint | string | JsonValue[] | JsonObject;
type Role = "meta" | "user" | "reasoning" | "assistant" | "tool";
type EventKind = "message" | "reasoning" | "tool-call" | "tool-result";

interface DecodedEvent {
  kind: EventKind;
  role: Role;
  content?: string;
  toolCallId?: string;
  toolName?: string;
  argumentsJson?: string;
  isError?: boolean;
  nativeId?: string;
  sourceSequence: number;
  sourceOffset: bigint;
  inputLine: number;
  timestamp?: number;
  timestampPrecise?: string;
  componentIndex: number;
  model?: string;
}

type DecodedEventValues = {
  [Key in keyof DecodedEvent]?: DecodedEvent[Key] | undefined;
};

interface DecodedSession {
  source: TrajectorySource;
  sourceName: string;
  groupId?: string;
  groupResolved: boolean;
  cwd?: string;
  gitBranch?: string;
  producerVersion?: string;
  createdAt?: number;
  events: DecodedEvent[];
  modelInvocations: DecodedModelInvocation[];
  diagnostics: TrajectoryDiagnostic[];
}

interface DecodedModelInvocation {
  nativeId?: string;
  sourceSequence?: number;
  sourceOffset?: bigint;
  provider?: string;
  apiFamily?: string;
  requestedModel?: string;
  responseModel?: string;
  responseId?: string;
  stopReason?: string;
  producerVersion?: string;
  inputTokens?: bigint;
  outputTokens?: bigint;
  cacheReadTokens?: bigint;
  cacheWriteTokens?: bigint;
  totalTokens?: bigint;
  startedAt?: number;
  startedAtPrecise?: string;
  firstResponseAt?: number;
  firstResponseAtPrecise?: string;
  completedAt?: number;
  completedAtPrecise?: string;
}

export interface AppliedConfig {
  bounds: {
    toolArguments: { maxCharacters: number | null };
    toolResults: { maxCharacters: number | null; strategy: "head" | "head-tail" };
  };
  filters: { toolResults: "include" | "omit" };
  sourceContext: {
    groupId?: string;
    baseByteOffset: bigint;
    partial: boolean;
  };
}

interface Provenance {
  stableSourceRecordId: string;
  sourceIdentityKind: "native" | "location" | "content" | "synthetic";
  sourceOrderId: string;
  componentKey: string;
  componentIndex: number;
  componentTypeOrdinal: number;
  nativeRecordId?: string;
  sourceSequence?: number;
  sourceOffset?: bigint;
  sourceAnchorKind?: "byte";
}

export interface IRRecord {
  id: string;
  kind: "meta" | "message" | "assistant_tool_calls" | "tool_result";
  role: Role;
  order: number;
  sourceTimestamp: number | null;
  sourceTimestampPrecise?: string;
  timestamp: number | null;
  content?: string;
  sourceName?: string;
  cwd?: string;
  gitBranch?: string;
  model?: string;
  producerVersion?: string;
  toolCalls?: { id: string; name: string; argumentsJson: string }[];
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
  provenance: Provenance;
  hashes: { contentSha256: string; recordSha256: string };
}

export interface TrajectoryIR {
  source: TrajectorySource;
  sourceName: string;
  groupId: string;
  groupResolved: boolean;
  producerVersion?: string;
  records: IRRecord[];
  diagnostics: TrajectoryDiagnostic[];
  execution: {
    modelInvocations: ModelInvocationIR[];
    workflowInvocations: readonly [];
  };
  config: AppliedConfig;
}

export interface ModelInvocationIR {
  id: string;
  nativeRecordId?: string;
  sourceSequence?: number;
  sourceOffset?: bigint;
  provider?: string;
  apiFamily?: string;
  requestedModel?: string;
  responseModel?: string;
  responseId?: string;
  stopReason?: string;
  producerVersion?: string;
  usage?: {
    inputTokens?: bigint;
    outputTokens?: bigint;
    cacheReadTokens?: bigint;
    cacheWriteTokens?: bigint;
    totalTokens?: bigint;
  };
  startedAt?: number;
  startedAtPrecise?: string;
  firstResponseAt?: number;
  firstResponseAtPrecise?: string;
  completedAt?: number;
  completedAtPrecise?: string;
}

interface PlannedCall {
  sourceId: string;
  finalId: string;
  synthesized: boolean;
  renamed: boolean;
  consumed: boolean;
}

const syntheticBase = Date.parse("2026-01-01T00:00:00.000Z");

export function normalizePi(request: NormalizeRequest & { transcriptBytes: Uint8Array }): TrajectoryIR {
  return normalizeDecoded(request, decodePi(request.transcriptBytes));
}

export function normalizeClaudeCode(request: NormalizeRequest & { transcriptBytes: Uint8Array }): TrajectoryIR {
  return normalizeDecoded(request, decodeClaudeCode(request.transcriptBytes));
}

export function normalizeCodex(request: NormalizeRequest & { transcriptBytes: Uint8Array }): TrajectoryIR {
  return normalizeDecoded(request, decodeCodex(request.transcriptBytes));
}

function normalizeDecoded(
  request: NormalizeRequest & { transcriptBytes: Uint8Array },
  decoded: DecodedSession,
): TrajectoryIR {
  const config = resolveConfig(request.options, request.sourceContext);
  const detected = decoded.groupId;
  const provided = config.sourceContext.groupId;
  if (detected && provided && detected !== provided) {
    fail(
      "source_group_conflict",
      `Detected source group ${quote(detected)} conflicts with the provided source context group ${quote(provided)}.`,
    );
  }
  const groupId = detected ?? provided ?? "default";
  const groupResolved = detected !== undefined || provided !== undefined;
  const partial = config.sourceContext.partial || config.sourceContext.baseByteOffset > 0n;
  const diagnostics = [...decoded.diagnostics];
  const plan = planEvents(decoded.events);
  const records: IRRecord[] = [];
  const anchors = new Map<number, number>();
  const modelCounts = new Map<string, number>();

  for (let eventIndex = 0; eventIndex < decoded.events.length; eventIndex++) {
    const event = decoded.events[eventIndex]!;
    if (event.model) modelCounts.set(event.model, (modelCounts.get(event.model) ?? 0) + 1);
    const recordIndex = records.length + 1;
    const record = normalizeEvent(
      event,
      eventIndex,
      recordIndex,
      groupId,
      config,
      partial,
      plan,
      diagnostics,
    );
    if (!record) continue;
    if (event.timestamp !== undefined) anchors.set(records.length, event.timestamp);
    records.push(record);
  }

  if (!partial && !records.some((record) => record.role === "user")) {
    fail("missing_user_records", "Transcript did not contain any normalizable user records.");
  }
  if (!partial && !records.some((record) => record.role === "assistant")) {
    fail("missing_assistant_records", "Transcript did not contain any normalizable assistant records.");
  }

  const timestamps = fillTimestamps(records.length, anchors, decoded.createdAt, diagnostics);
  for (let index = 0; index < records.length; index++) {
    const record = records[index]!;
    record.timestamp = timestamps[index]!;
    record.hashes = hashRecord(record, decoded.source);
  }

  const model = [...modelCounts].sort((left, right) =>
    right[1] - left[1] || utf16Compare(left[0], right[0]),
  )[0]?.[0];
  const meta = createMeta(
    decoded.source,
    decoded.sourceName,
    groupId,
    decoded.cwd,
    decoded.gitBranch,
    model,
    decoded.producerVersion,
  );
  const modelInvocations = decoded.modelInvocations.map((invocation) =>
    normalizeInvocation(invocation, groupId, config.sourceContext.baseByteOffset)
  );
  return {
    source: decoded.source,
    sourceName: decoded.sourceName,
    groupId,
    groupResolved,
    ...(decoded.producerVersion === undefined ? {} : { producerVersion: decoded.producerVersion }),
    records: [meta, ...records],
    diagnostics,
    execution: { modelInvocations, workflowInvocations: [] },
    config,
  };
}

function decodePi(bytes: Uint8Array): DecodedSession {
  const events: DecodedEvent[] = [];
  const modelInvocations: DecodedModelInvocation[] = [];
  const diagnostics: TrajectoryDiagnostic[] = [];
  let groupId: string | undefined;
  let cwd: string | undefined;
  let producerVersion: string | undefined;
  let requestedProvider: string | undefined;
  let requestedModel: string | undefined;
  let createdAt: number | undefined;
  let sawMessage = false;
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let offset = 0;
  let line = 1;

  while (offset <= bytes.length) {
    let end = bytes.indexOf(0x0a, offset);
    if (end < 0) end = bytes.length;
    let lineEnd = end;
    if (lineEnd > offset && bytes[lineEnd - 1] === 0x0d) lineEnd--;
    const slice = bytes.subarray(offset, lineEnd);
    if (!isAsciiWhitespace(slice)) {
      let row: JsonObject | undefined;
      let rawLine: string | undefined;
      try {
        rawLine = decoder.decode(slice);
        const parsed: unknown = JSON.parse(rawLine);
        if (!isObject(parsed)) {
          diagnostics.push({
            code: "non_object_json_line",
            message: `Skipped non-object JSON on line ${line}.`,
            inputLine: line,
          });
        } else {
          row = parsed as JsonObject;
        }
      } catch {
        diagnostics.push({
          code: "invalid_json_line",
          message: `Skipped invalid JSON on line ${line}.`,
          inputLine: line,
        });
      }
      if (row) {
        const type = stringValue(row.type);
        if (type === "session") {
          cwd ??= stringValue(row.cwd);
          groupId ??= stringValue(row.id);
          createdAt ??= timestampValue(row.timestamp);
          producerVersion ??= scalarString(row.version);
        } else if (type === "model_change") {
          requestedProvider = stringValue(row.provider);
          requestedModel = stringValue(row.modelId);
        } else if (type === "message" && isObject(row.message)) {
          sawMessage = true;
          const message = row.message as JsonObject;
          const role = stringValue(message.role);
          const nativeId = stringValue(row.id);
          const timestampData = parseTimestamp(row.timestamp) ?? parseTimestamp(message.timestamp);
          const timestamp = timestampData?.milliseconds;
          const timestampPrecise = timestampData?.precise;
          const model = stringValue(message.model);
          let componentIndex = 0;
          const emit = (event: Omit<DecodedEvent, "nativeId" | "sourceSequence" | "sourceOffset" | "inputLine" | "componentIndex">): void => {
            events.push({
              ...event,
              ...(nativeId === undefined ? {} : { nativeId }),
              sourceSequence: line - 1,
              sourceOffset: BigInt(offset),
              inputLine: line,
              componentIndex: componentIndex++,
              ...(timestampPrecise === undefined ? {} : { timestampPrecise }),
            });
          };
          if (role === "user") {
            const content = readBlocksText(message.content);
            if (content) emit({ kind: "message", role: "user", content, ...(timestamp === undefined ? {} : { timestamp }) });
          } else if (role === "assistant") {
            const usage = readTokenUsage(message, rawLine!, {
              inputTokens: ["input"],
              outputTokens: ["output"],
              cacheReadTokens: ["cacheRead"],
              cacheWriteTokens: ["cacheWrite"],
              totalTokens: ["totalTokens"],
            });
            const provider = stringValue(message.provider) ?? requestedProvider;
            const apiFamily = stringValue(message.api);
            const responseId = stringValue(message.responseId);
            const stopReason = stringValue(message.stopReason);
            const started = parseTimestamp(message.startTimestamp) ??
              parseTimestamp(message.requestTimestamp);
            const firstResponse = parseTimestamp(message.firstResponseTimestamp);
            const completed = parseTimestamp(message.timestamp) ?? timestampData;
            modelInvocations.push({
              ...(nativeId === undefined ? {} : { nativeId }),
              sourceSequence: line - 1,
              sourceOffset: BigInt(offset),
              ...(provider === undefined ? {} : { provider }),
              ...(apiFamily === undefined ? {} : { apiFamily }),
              ...(requestedModel === undefined ? {} : { requestedModel }),
              ...(model === undefined ? {} : { responseModel: model }),
              ...(responseId === undefined ? {} : { responseId }),
              ...(stopReason === undefined ? {} : { stopReason }),
              ...usage,
              ...(started === undefined ? {} : {
                startedAt: started.milliseconds,
                startedAtPrecise: started.precise,
              }),
              ...(firstResponse === undefined ? {} : {
                firstResponseAt: firstResponse.milliseconds,
                firstResponseAtPrecise: firstResponse.precise,
              }),
              ...(completed === undefined ? {} : {
                completedAt: completed.milliseconds,
                completedAtPrecise: completed.precise,
              }),
            });
            const content = message.content;
            if (typeof content === "string") {
              if (content) emit({ kind: "message", role: "assistant", content, ...(timestamp === undefined ? {} : { timestamp }), ...(model === undefined ? {} : { model }) });
            } else if (Array.isArray(content)) {
              for (const value of content) {
                if (!isObject(value)) continue;
                const part = value as JsonObject;
                const partType = stringValue(part.type);
                if (partType === "thinking") {
                  const thinking = stringValue(part.thinking);
                  if (thinking) emit({ kind: "reasoning", role: "reasoning", content: thinking, ...(timestamp === undefined ? {} : { timestamp }), ...(model === undefined ? {} : { model }) });
                } else if (partType === "text") {
                  const text = stringValue(part.text);
                  if (text) emit({ kind: "message", role: "assistant", content: text, ...(timestamp === undefined ? {} : { timestamp }), ...(model === undefined ? {} : { model }) });
                } else if (partType === "toolCall") {
                  const toolCallId = stringValue(part.id);
                  const toolName = stringValue(part.name);
                  emit({
                    kind: "tool-call",
                    role: "assistant",
                    argumentsJson: part.arguments === undefined
                      ? "{}"
                      : typeof part.arguments === "string"
                        ? part.arguments
                        : compactJson(part.arguments as JsonValue),
                    ...(toolCallId === undefined ? {} : { toolCallId }),
                    ...(toolName === undefined ? {} : { toolName }),
                    ...(timestamp === undefined ? {} : { timestamp }),
                    ...(model === undefined ? {} : { model }),
                  });
                }
              }
            }
          } else if (role === "toolResult" || role === "tool") {
            let content = readBlocksText(message.content);
            const isError = message.isError === true;
            if (isError && !content.toLowerCase().startsWith("error")) content = `Error: ${content}`;
            const toolCallId = stringValue(message.toolCallId);
            const toolName = stringValue(message.toolName);
            emit({
              kind: "tool-result",
              role: "tool",
              content,
              isError,
              ...(toolCallId === undefined ? {} : { toolCallId }),
              ...(toolName === undefined ? {} : { toolName }),
              ...(timestamp === undefined ? {} : { timestamp }),
            });
          }
        }
      }
    }
    if (end === bytes.length) break;
    offset = end + 1;
    line++;
  }
  if (!sawMessage && !groupId) {
    fail("invalid_input", "Pi transcript must be session JSONL containing a session header or message entries.");
  }
  return {
    source: "pi",
    sourceName: "pi",
    groupResolved: groupId !== undefined,
    events,
    modelInvocations,
    diagnostics,
    ...(groupId === undefined ? {} : { groupId }),
    ...(cwd === undefined ? {} : { cwd }),
    ...(producerVersion === undefined ? {} : { producerVersion }),
    ...(createdAt === undefined ? {} : { createdAt }),
  };
}

function decodeClaudeCode(bytes: Uint8Array): DecodedSession {
  const diagnostics: TrajectoryDiagnostic[] = [];
  const events: DecodedEvent[] = [];
  const modelInvocations: DecodedModelInvocation[] = [];
  const sessionIds = new Set<string>();
  let cwd: string | undefined;
  let gitBranch: string | undefined;
  let producerVersion: string | undefined;
  const transportTypes = new Set([
    "progress", "queue-operation", "file-history-snapshot", "summary", "system",
    "pr-link", "last-prompt", "custom-title", "ai-title", "agent-name",
    "permission-mode", "attachment", "mode",
  ]);

  forEachJsonLine(bytes, diagnostics, (row, inputLine, sourceOffset, rawLine) => {
    const rowType = stringValue(row.type);
    if (row.isSidechain === true) {
      diagnostics.push({
        code: "sidechain_record_dropped",
        message: `Dropped a Claude Code sidechain record on line ${inputLine}.`,
        inputLine,
      });
      return;
    }
    if (rowType !== undefined && transportTypes.has(rowType)) return;

    cwd ??= stringValue(row.cwd);
    gitBranch ??= stringValue(row.gitBranch);
    producerVersion ??= scalarString(row.version);
    const sessionId = stringValue(row.sessionId);
    if (sessionId) sessionIds.add(sessionId);
    if (rowType !== "user" && rowType !== "assistant") {
      if (rowType) {
        diagnostics.push({
          code: "unknown_semantic_record",
          message: `Skipped an unknown Claude Code semantic record on line ${inputLine}.`,
          inputLine,
        });
      }
      return;
    }
    if (!isObject(row.message)) return;
    const message = row.message;
    const nativeId = stringValue(row.uuid);
    const timestampData = parseTimestamp(row.timestamp);
    const model = stringValue(message.model);
    let componentIndex = 0;
    const emit = (
      kind: EventKind,
      role: Role,
      values: DecodedEventValues,
    ): void => {
      const defined = Object.fromEntries(
        Object.entries(values).filter((entry) => entry[1] !== undefined),
      ) as Partial<DecodedEvent>;
      events.push({
        kind,
        role,
        sourceSequence: 0,
        sourceOffset,
        inputLine,
        componentIndex: componentIndex++,
        ...(nativeId === undefined ? {} : { nativeId }),
        ...(timestampData === undefined ? {} : {
          timestamp: timestampData.milliseconds,
          timestampPrecise: timestampData.precise,
        }),
        ...defined,
      });
    };
    const content = message.content;
    if (rowType === "user") {
      if (typeof content === "string") {
        emit("message", "user", { content });
        return;
      }
      if (!Array.isArray(content)) return;
      const textParts: string[] = [];
      for (const value of content) {
        if (!isObject(value)) continue;
        const blockType = stringValue(value.type);
        if (blockType === "tool_result") {
          emit("tool-result", "tool", {
            toolCallId: stringValue(value.tool_use_id),
            content: readBlocksText(value.content),
            isError: value.is_error === true,
          });
        } else if (blockType === "text") {
          const text = stringValue(value.text);
          if (text) textParts.push(text);
        } else if (blockType === "image") {
          textParts.push("[image]");
        } else {
          diagnostics.push({
            code: "unknown_content_block",
            message: `Skipped an unknown Claude Code user content block on line ${inputLine}.`,
            inputLine,
          });
        }
      }
      if (textParts.length > 0) {
        emit("message", "user", { content: textParts.join("\n") });
      }
      return;
    }
    const usage = readTokenUsage(message, rawLine, {
      inputTokens: ["input_tokens", "input"],
      outputTokens: ["output_tokens", "output"],
      cacheReadTokens: ["cache_read_input_tokens", "cacheRead"],
      cacheWriteTokens: ["cache_creation_input_tokens", "cacheWrite"],
      totalTokens: ["total_tokens"],
    });
    const responseId = stringValue(message.id);
    const stopReason = stringValue(message.stop_reason) ?? stringValue(message.stopReason);
    const invocationProducerVersion = scalarString(row.version);
    modelInvocations.push({
      ...(nativeId === undefined ? {} : { nativeId }),
      sourceOffset,
      ...(model === undefined ? {} : { responseModel: model }),
      ...(responseId === undefined ? {} : { responseId }),
      ...(stopReason === undefined ? {} : { stopReason }),
      ...(invocationProducerVersion === undefined ? {} : {
        producerVersion: invocationProducerVersion,
      }),
      ...usage,
      ...(timestampData === undefined ? {} : {
        completedAt: timestampData.milliseconds,
        completedAtPrecise: timestampData.precise,
      }),
    });
    if (typeof content === "string") {
      if (content.trim()) emit("message", "assistant", { content, model });
      return;
    }
    if (!Array.isArray(content)) return;
    for (const value of content) {
      if (!isObject(value)) continue;
      const blockType = stringValue(value.type);
      if (blockType === "thinking") {
        emit("reasoning", "reasoning", {
          content: stringValue(value.thinking) ?? "",
          model,
        });
      } else if (blockType === "text") {
        emit("message", "assistant", {
          content: stringValue(value.text) ?? "",
          model,
        });
      } else if (blockType === "tool_use") {
        emit("tool-call", "assistant", {
          toolCallId: stringValue(value.id),
          toolName: stringValue(value.name),
          argumentsJson: value.input === undefined ? "{}" : compactJson(value.input),
          model,
        });
      } else if (blockType !== "fallback") {
        diagnostics.push({
          code: "unknown_content_block",
          message: `Skipped an unknown Claude Code assistant content block on line ${inputLine}.`,
          inputLine,
        });
      }
    }
  });

  if (sessionIds.size > 1) {
    const formatted = [...sessionIds].sort(utf16Compare).map(quote).join(", ");
    fail(
      "source_group_conflict",
      `Claude Code transcript contains multiple session ids: ${formatted}.`,
    );
  }
  const groupId = sessionIds.values().next().value as string | undefined;
  return {
    source: "claude-code",
    sourceName: "claude-code",
    groupResolved: groupId !== undefined,
    events,
    modelInvocations,
    diagnostics,
    ...(groupId === undefined ? {} : { groupId }),
    ...(cwd === undefined ? {} : { cwd }),
    ...(gitBranch === undefined ? {} : { gitBranch }),
    producerVersion: producerVersion ?? "unknown",
  };
}

function decodeCodex(bytes: Uint8Array): DecodedSession {
  const diagnostics: TrajectoryDiagnostic[] = [];
  const events: DecodedEvent[] = [];
  let groupId: string | undefined;
  let cwd: string | undefined;
  let gitBranch: string | undefined;
  let model: string | undefined;
  let producerVersion: string | undefined;
  let createdAt: number | undefined;
  const injectedPrefixes = [
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "<turn_context>",
  ];

  forEachJsonLine(bytes, diagnostics, (row, inputLine, sourceOffset) => {
    const recordType = stringValue(row.type);
    const timestampData = parseTimestamp(row.timestamp);
    const payload = isObject(row.payload) ? row.payload : {};
    const payloadType = stringValue(payload.type);
    const emit = (
      kind: EventKind,
      role: Role,
      values: DecodedEventValues,
    ): void => {
      const defined = Object.fromEntries(
        Object.entries(values).filter((entry) => entry[1] !== undefined),
      ) as Partial<DecodedEvent>;
      events.push({
        kind,
        role,
        sourceSequence: 0,
        sourceOffset,
        inputLine,
        componentIndex: 0,
        ...(timestampData === undefined ? {} : {
          timestamp: timestampData.milliseconds,
          timestampPrecise: timestampData.precise,
        }),
        ...defined,
      });
    };

    if (recordType === "session_meta") {
      cwd ??= nonEmpty(stringValue(payload.cwd));
      groupId ??= nonEmpty(stringValue(payload.id));
      producerVersion ??= nonEmpty(scalarString(payload.cli_version));
      createdAt ??= parseTimestamp(payload.timestamp)?.milliseconds ??
        timestampData?.milliseconds;
      if (isObject(payload.git)) gitBranch ??= nonEmpty(stringValue(payload.git.branch));
      return;
    }
    if (recordType === "turn_context") {
      cwd ??= nonEmpty(stringValue(payload.cwd));
      model ??= nonEmpty(stringValue(payload.model));
      return;
    }
    if (recordType === "event_msg") {
      const content = stringValue(payload.text);
      if (payloadType === "agent_reasoning" && content?.trim()) {
        emit("reasoning", "reasoning", { content, model });
      }
      return;
    }
    if (recordType !== "response_item") return;
    if (payloadType === "message") {
      const role = stringValue(payload.role);
      const content = readBlocksText(payload.content);
      if (role === "user") {
        const head = content.trimStart();
        if (injectedPrefixes.some((prefix) => head.startsWith(prefix))) {
          diagnostics.push({
            code: "injected_context_dropped",
            message: `Dropped Codex system-injected user content on line ${inputLine}.`,
            inputLine,
          });
        } else {
          emit("message", "user", { content, model });
        }
      } else if (role === "assistant") {
        emit("message", "assistant", { content, model });
      }
      return;
    }
    if (payloadType === "function_call") {
      emit("tool-call", "assistant", {
        toolCallId: stringValue(payload.call_id),
        toolName: stringValue(payload.name),
        argumentsJson: nonEmpty(stringValue(payload.arguments)) ?? "{}",
        model,
      });
      return;
    }
    if (payloadType === "custom_tool_call") {
      emit("tool-call", "assistant", {
        toolCallId: stringValue(payload.call_id),
        toolName: stringValue(payload.name),
        argumentsJson: compactJson({ input: payload.input ?? "" }),
        model,
      });
      return;
    }
    if (payloadType === "web_search_call") {
      const filtered: JsonObject = {};
      for (const [key, value] of Object.entries(payload)) {
        if (key !== "type" && key !== "call_id" && key !== "status") {
          filtered[key] = value;
        }
      }
      emit("tool-call", "assistant", {
        toolCallId: stringValue(payload.call_id),
        toolName: "web_search",
        argumentsJson: compactJson(filtered),
        model,
      });
      return;
    }
    if (payloadType === "tool_search_call") {
      const argumentsValue = payload.arguments;
      emit("tool-call", "assistant", {
        toolCallId: stringValue(payload.call_id),
        toolName: "tool_search",
        argumentsJson: typeof argumentsValue === "string" && argumentsValue
          ? argumentsValue
          : compactJson(argumentsValue ?? null),
        model,
      });
      return;
    }
    if (
      payloadType === "function_call_output" ||
      payloadType === "custom_tool_call_output" ||
      payloadType === "tool_search_output"
    ) {
      const content = payloadType === "tool_search_output"
        ? compactJson(payload.tools ?? [])
        : outputText(payload.output);
      emit("tool-result", "tool", {
        toolCallId: stringValue(payload.call_id),
        content,
        model,
      });
    }
  });

  return {
    source: "codex",
    sourceName: "codex",
    groupResolved: groupId !== undefined,
    events,
    modelInvocations: [],
    diagnostics,
    ...(groupId === undefined ? {} : { groupId }),
    ...(cwd === undefined ? {} : { cwd }),
    ...(gitBranch === undefined ? {} : { gitBranch }),
    ...(producerVersion === undefined ? {} : { producerVersion }),
    ...(createdAt === undefined ? {} : { createdAt }),
  };
}

function forEachJsonLine(
  bytes: Uint8Array,
  diagnostics: TrajectoryDiagnostic[],
  visit: (
    row: JsonObject,
    inputLine: number,
    sourceOffset: bigint,
    rawLine: string,
  ) => void,
): void {
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let offset = 0;
  let line = 1;
  while (offset <= bytes.length) {
    let end = bytes.indexOf(0x0a, offset);
    if (end < 0) end = bytes.length;
    let lineEnd = end;
    if (lineEnd > offset && bytes[lineEnd - 1] === 0x0d) lineEnd--;
    const slice = bytes.subarray(offset, lineEnd);
    if (!isAsciiWhitespace(slice)) {
      try {
        const rawLine = decoder.decode(slice);
        const parsed: unknown = JSON.parse(rawLine);
        if (isObject(parsed)) {
          visit(parsed, line, BigInt(offset), rawLine);
        } else {
          diagnostics.push({
            code: "non_object_json_line",
            message: `Skipped non-object JSON on line ${line}.`,
            inputLine: line,
          });
        }
      } catch {
        diagnostics.push({
          code: "invalid_json_line",
          message: `Skipped invalid JSON on line ${line}.`,
          inputLine: line,
        });
      }
    }
    if (end === bytes.length) break;
    offset = end + 1;
    line++;
  }
}

function outputText(value: JsonValue | undefined): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return readBlocksText(value) || compactJson(value);
  if (isObject(value)) return nonEmpty(stringValue(value.content)) ?? compactJson(value);
  if (value === null || value === undefined) return "";
  return String(value);
}

function nonEmpty(value: string | undefined): string | undefined {
  return value ? value : undefined;
}

function readTokenUsage(
  message: JsonObject,
  rawLine: string,
  names: Record<
    "inputTokens" | "outputTokens" | "cacheReadTokens" | "cacheWriteTokens" | "totalTokens",
    string[]
  >,
): Partial<DecodedModelInvocation> {
  if (!isObject(message.usage)) return {};
  const rawUsage = rawObjectProperty(rawLine, "usage");
  const usage: Partial<DecodedModelInvocation> = {};
  for (const [target, sourceNames] of Object.entries(names) as [
    keyof Pick<
      DecodedModelInvocation,
      "inputTokens" | "outputTokens" | "cacheReadTokens" | "cacheWriteTokens" | "totalTokens"
    >,
    string[],
  ][]) {
    for (const sourceName of sourceNames) {
      const raw = rawUsage === undefined
        ? undefined
        : rawSignedIntegerProperty(rawUsage, sourceName);
      const parsed = raw ?? safeInteger(message.usage[sourceName]);
      if (parsed !== undefined) {
        usage[target] = parsed;
        break;
      }
    }
  }
  return usage;
}

function rawObjectProperty(json: string, property: string): string | undefined {
  const key = `"${property.replaceAll("\\", "\\\\").replaceAll("\"", "\\\"")}"`;
  const match = new RegExp(`${key}\\s*:\\s*\\{`).exec(json);
  if (!match) return undefined;
  const start = match.index + match[0].lastIndexOf("{");
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < json.length; index++) {
    const character = json[index]!;
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === "\"") quoted = false;
      continue;
    }
    if (character === "\"") quoted = true;
    else if (character === "{") depth++;
    else if (character === "}" && --depth === 0) return json.slice(start, index + 1);
  }
  return undefined;
}

function rawSignedIntegerProperty(json: string, property: string): bigint | undefined {
  const escaped = property.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`"${escaped}"\\s*:\\s*(-?\\d+)(?=\\s*[,}])`).exec(json);
  if (!match) return undefined;
  const value = BigInt(match[1]!);
  return value >= -9_223_372_036_854_775_808n &&
      value <= 9_223_372_036_854_775_807n
    ? value
    : undefined;
}

function safeInteger(value: JsonValue | undefined): bigint | undefined {
  return typeof value === "number" && Number.isSafeInteger(value)
    ? BigInt(value)
    : undefined;
}

function normalizeEvent(
  event: DecodedEvent,
  eventIndex: number,
  recordIndex: number,
  groupId: string,
  config: AppliedConfig,
  partial: boolean,
  plan: ReturnType<typeof planEvents>,
  diagnostics: TrajectoryDiagnostic[],
): IRRecord | undefined {
  if (event.kind === "message" || event.kind === "reasoning") {
    const content = event.content ?? "";
    if (!content.trim()) return undefined;
    if (event.role === "user" && isHarnessNoise(content)) {
      diagnostics.push({
        code: "noise_record_dropped",
        message: "Dropped a harness-noise user record.",
        inputLine: event.inputLine,
        recordIndex,
      });
      return undefined;
    }
    const role = event.kind === "reasoning" ? "reasoning" : event.role;
    const bucket = event.kind === "reasoning" ? "reasoning" : "message";
    return createRecord(event, eventIndex, recordIndex, groupId, config.sourceContext.baseByteOffset, plan.ordinals[eventIndex]!, `${bucket}:${plan.ordinals[eventIndex]!}`, role, content);
  }
  if (event.kind === "tool-call") {
    const callPlan = plan.calls.get(eventIndex)!;
    if (callPlan.synthesized) diagnostics.push(diag("tool_call_id_synthesized", `Synthesized tool-call ID ${quote(callPlan.sourceId)}.`, event, recordIndex));
    if (callPlan.renamed) diagnostics.push(diag("duplicate_tool_call_id", `Renamed duplicate tool-call ID ${quote(callPlan.sourceId)} to ${quote(callPlan.finalId)}.`, event, recordIndex));
    const name = event.toolName || "unknown_tool";
    if (!event.toolName) diagnostics.push(diag("unknown_tool_name", `Substituted ${quote(name)} for a missing tool name.`, event, recordIndex));
    const shrunk = shrinkArguments(event.argumentsJson, config.bounds.toolArguments.maxCharacters);
    if (shrunk.reshaped) diagnostics.push(diag("tool_arguments_reshaped", `Reshaped arguments for tool call ${quote(callPlan.finalId)} into a JSON object.`, event, recordIndex));
    if (shrunk.truncated) diagnostics.push(diag("tool_arguments_truncated", `Truncated arguments for tool call ${quote(callPlan.finalId)} to at most ${config.bounds.toolArguments.maxCharacters} Unicode code points.`, event, recordIndex));
    return createRecord(event, eventIndex, recordIndex, groupId, config.sourceContext.baseByteOffset, plan.ordinals[eventIndex]!, `tool-call:${callPlan.finalId}`, "assistant", undefined, {
      toolCalls: [{ id: callPlan.finalId, name, argumentsJson: shrunk.arguments }],
    });
  }
  const sourceId = event.toolCallId ?? "";
  const entries = plan.openCalls.get(sourceId);
  const open = entries?.find((entry) => !entry.consumed);
  const crossChunk = !open && partial && sourceId.length > 0 && (!entries || entries.length === 0);
  if (!open && !crossChunk) {
    const duplicate = (entries?.length ?? 0) > 0;
    diagnostics.push(diag(
      duplicate ? "duplicate_tool_result" : "orphan_tool_result",
      duplicate
        ? `Dropped a duplicate result for tool call ${quote(sourceId)}.`
        : `Dropped a tool result without a preceding call for ${quote(sourceId)}.`,
      event,
      recordIndex,
    ));
    return undefined;
  }
  if (open) open.consumed = true;
  if (config.filters.toolResults === "omit") return undefined;
  const finalId = open?.finalId ?? sourceId;
  const original = event.content ?? "";
  const content = truncateResult(original, config.bounds.toolResults.maxCharacters, config.bounds.toolResults.strategy);
  if (content !== original) diagnostics.push(diag(
    "tool_result_truncated",
    `Truncated the result for tool call ${quote(finalId)} to at most ${config.bounds.toolResults.maxCharacters} Unicode code points using the ${quote(config.bounds.toolResults.strategy)} strategy.`,
    event,
    recordIndex,
  ));
  return createRecord(event, eventIndex, recordIndex, groupId, config.sourceContext.baseByteOffset, plan.ordinals[eventIndex]!, `tool-result:${finalId}`, "tool", content, {
    toolCallId: finalId,
    ...(event.toolName === undefined ? {} : { toolName: event.toolName }),
    isError: event.isError ?? false,
  });
}

function normalizeInvocation(
  invocation: DecodedModelInvocation,
  groupId: string,
  baseByteOffset: bigint,
): ModelInvocationIR {
  const absoluteOffset = invocation.sourceOffset === undefined
    ? undefined
    : invocation.sourceOffset + baseByteOffset;
  const identity = invocation.nativeId ??
    (absoluteOffset === undefined
      ? invocation.responseId ?? "model-invocation"
      : sha256(`${groupId}|byte|${absoluteOffset}`));
  const usage = {
    ...(invocation.inputTokens === undefined ? {} : { inputTokens: invocation.inputTokens }),
    ...(invocation.outputTokens === undefined ? {} : { outputTokens: invocation.outputTokens }),
    ...(invocation.cacheReadTokens === undefined ? {} : {
      cacheReadTokens: invocation.cacheReadTokens,
    }),
    ...(invocation.cacheWriteTokens === undefined ? {} : {
      cacheWriteTokens: invocation.cacheWriteTokens,
    }),
    ...(invocation.totalTokens === undefined ? {} : { totalTokens: invocation.totalTokens }),
  };
  return {
    id: sha256(compactJson([groupId, identity, "model-invocation"])),
    ...(invocation.nativeId === undefined ? {} : { nativeRecordId: invocation.nativeId }),
    ...(invocation.sourceSequence === undefined ? {} : {
      sourceSequence: invocation.sourceSequence,
    }),
    ...(absoluteOffset === undefined ? {} : { sourceOffset: absoluteOffset }),
    ...(invocation.provider === undefined ? {} : { provider: invocation.provider }),
    ...(invocation.apiFamily === undefined ? {} : { apiFamily: invocation.apiFamily }),
    ...(invocation.requestedModel === undefined ? {} : {
      requestedModel: invocation.requestedModel,
    }),
    ...(invocation.responseModel === undefined ? {} : {
      responseModel: invocation.responseModel,
    }),
    ...(invocation.responseId === undefined ? {} : { responseId: invocation.responseId }),
    ...(invocation.stopReason === undefined ? {} : { stopReason: invocation.stopReason }),
    ...(invocation.producerVersion === undefined ? {} : {
      producerVersion: invocation.producerVersion,
    }),
    ...(Object.keys(usage).length === 0 ? {} : { usage }),
    ...(invocation.startedAt === undefined ? {} : { startedAt: invocation.startedAt }),
    ...(invocation.startedAtPrecise === undefined ? {} : {
      startedAtPrecise: invocation.startedAtPrecise,
    }),
    ...(invocation.firstResponseAt === undefined ? {} : {
      firstResponseAt: invocation.firstResponseAt,
    }),
    ...(invocation.firstResponseAtPrecise === undefined ? {} : {
      firstResponseAtPrecise: invocation.firstResponseAtPrecise,
    }),
    ...(invocation.completedAt === undefined ? {} : { completedAt: invocation.completedAt }),
    ...(invocation.completedAtPrecise === undefined ? {} : {
      completedAtPrecise: invocation.completedAtPrecise,
    }),
  };
}

function createRecord(
  event: DecodedEvent,
  eventIndex: number,
  recordIndex: number,
  groupId: string,
  baseByteOffset: bigint,
  ordinal: number,
  componentKey: string,
  role: Role,
  content?: string,
  extra: Partial<IRRecord> = {},
): IRRecord {
  const absoluteOffset = event.sourceOffset + baseByteOffset;
  const stableId = event.nativeId ?? sha256(`${groupId}|byte|${absoluteOffset}`);
  const provenance: Provenance = {
    stableSourceRecordId: stableId,
    sourceIdentityKind: event.nativeId ? "native" : "location",
    sourceOrderId: `1|${event.timestamp === undefined ? "0000-00-00T00:00:00.001Z" : formatMs(event.timestamp)}|${String(event.sourceSequence).padStart(20, "0")}|${stableId}`,
    componentKey,
    componentIndex: event.componentIndex,
    componentTypeOrdinal: ordinal,
    ...(event.nativeId === undefined ? {} : { nativeRecordId: event.nativeId }),
    sourceSequence: event.sourceSequence,
    sourceOffset: event.nativeId === undefined ? absoluteOffset : event.sourceOffset,
    sourceAnchorKind: "byte",
  };
  const kind = extra.toolCalls ? "assistant_tool_calls" : role === "tool" ? "tool_result" : "message";
  return {
    id: sha256(compactJson([groupId, stableId, componentKey])),
    kind,
    role,
    order: recordIndex - 1,
    sourceTimestamp: event.timestamp ?? null,
    ...(event.timestampPrecise === undefined ? {} : { sourceTimestampPrecise: event.timestampPrecise }),
    timestamp: null,
    ...(content === undefined ? {} : { content }),
    provenance,
    hashes: { contentSha256: "", recordSha256: "" },
    ...extra,
  };
}

function createMeta(
  source: TrajectorySource,
  sourceName: string,
  groupId: string,
  cwd?: string,
  gitBranch?: string,
  model?: string,
  producerVersion?: string,
): IRRecord {
  const record: IRRecord = {
    id: sha256(compactJson([groupId, "meta", "meta"])),
    kind: "meta",
    role: "meta",
    order: -1,
    sourceTimestamp: null,
    timestamp: null,
    sourceName,
    ...(cwd === undefined ? {} : { cwd }),
    ...(gitBranch === undefined ? {} : { gitBranch }),
    ...(model === undefined ? {} : { model }),
    ...(producerVersion === undefined ? {} : { producerVersion }),
    provenance: {
      stableSourceRecordId: "meta",
      sourceIdentityKind: "synthetic",
      sourceOrderId: "0|0000-00-00T00:00:00.000Z|00000000000000000000|meta",
      componentKey: "meta",
      componentIndex: 0,
      componentTypeOrdinal: 0,
    },
    hashes: { contentSha256: "", recordSha256: "" },
  };
  record.hashes = hashRecord(record, source);
  return record;
}

function hashRecord(record: IRRecord, source: TrajectorySource): IRRecord["hashes"] {
  const recordType = recordTypeName(record);
  const semantic = record.kind === "meta"
    ? withoutUndefined({ source, cwd: record.cwd, git_branch: record.gitBranch, model: record.model })
    : record.kind === "assistant_tool_calls"
      ? { name: record.toolCalls![0]!.name, args: record.toolCalls![0]!.argumentsJson }
      : { content: record.content ?? "" };
  return {
    contentSha256: sha256(canonicalJson({ type: recordType, content: semantic } as JsonValue)),
    recordSha256: sha256(canonicalJson(toLetta(record))),
  };
}

export function toLetta(record: IRRecord): JsonObject {
  if (record.kind === "meta") {
    return withoutUndefined({
      role: "meta",
      source: record.sourceName,
      cwd: record.cwd,
      git_branch: record.gitBranch,
      model: record.model,
    });
  }
  if (record.kind === "assistant_tool_calls") {
    const call = record.toolCalls![0]!;
    return { role: "assistant", content: null, tool_calls: [{ id: call.id, name: call.name, args: call.argumentsJson }], timestamp: formatMs(record.timestamp!) };
  }
  if (record.kind === "tool_result") {
    return { role: "tool", tool_call_id: record.toolCallId!, content: record.content ?? "", timestamp: formatMs(record.timestamp!) };
  }
  return { role: record.role, content: record.content ?? "", timestamp: formatMs(record.timestamp!) };
}

function planEvents(events: DecodedEvent[]): {
  calls: Map<number, PlannedCall>;
  openCalls: Map<string, PlannedCall[]>;
  ordinals: number[];
} {
  const calls = new Map<number, PlannedCall>();
  const openCalls = new Map<string, PlannedCall[]>();
  const used = new Set<string>();
  const ordinals: number[] = [];
  const seen = new Map<string, number>();
  let occurrence = -1;
  for (let index = 0; index < events.length; index++) {
    const event = events[index]!;
    if (event.componentIndex === 0) occurrence++;
    const bucket = event.kind === "tool-call" ? "tool_call" : event.kind === "tool-result" ? "tool_result" : event.kind;
    const key = `${occurrence}:${bucket}`;
    const ordinal = seen.get(key) ?? 0;
    ordinals.push(ordinal);
    seen.set(key, ordinal + 1);
    if (event.kind !== "tool-call") continue;
    const sourceId = event.toolCallId || `call_${index + 1}`;
    let finalId = sourceId;
    let suffix = 2;
    while (used.has(finalId)) finalId = `${sourceId}__${suffix++}`;
    const entry: PlannedCall = {
      sourceId,
      finalId,
      synthesized: !event.toolCallId,
      renamed: finalId !== sourceId,
      consumed: false,
    };
    used.add(finalId);
    calls.set(index, entry);
    const entries = openCalls.get(sourceId) ?? [];
    entries.push(entry);
    openCalls.set(sourceId, entries);
  }
  return { calls, openCalls, ordinals };
}

function fillTimestamps(
  count: number,
  anchors: Map<number, number>,
  createdAt: number | undefined,
  diagnostics: TrajectoryDiagnostic[],
): number[] {
  if (count === 0) return [];
  if (anchors.size === 0) {
    diagnostics.push({ code: "timestamps_synthesized", message: `Synthesized timestamps for ${count} normalized records.`, count });
    const start = createdAt ?? syntheticBase;
    return Array.from({ length: count }, (_, index) => start + index * 15_000);
  }
  const output = Array<number>(count);
  const indexes = [...anchors.keys()].sort((a, b) => a - b);
  const first = indexes[0]!;
  const last = indexes.at(-1)!;
  for (let index = 0; index < first; index++) output[index] = anchors.get(first)! - (first - index) * 1_000;
  for (let cursor = 0; cursor + 1 < indexes.length; cursor++) {
    const startIndex = indexes[cursor]!;
    const endIndex = indexes[cursor + 1]!;
    const start = anchors.get(startIndex)!;
    const span = anchors.get(endIndex)! - start;
    output[startIndex] = start;
    for (let index = startIndex + 1; index < endIndex; index++) {
      output[index] = Math.trunc(start + span * (index - startIndex) / (endIndex - startIndex));
    }
  }
  output[last] = anchors.get(last)!;
  for (let index = last + 1; index < count; index++) output[index] = anchors.get(last)! + (index - last) * 1_000;
  const interpolated = count - anchors.size;
  if (interpolated > 0) diagnostics.push({ code: "timestamps_interpolated", message: `Interpolated timestamps for ${interpolated} normalized records.`, count: interpolated });
  return output;
}

function shrinkArguments(rawInput: string | undefined, limit: number | null): {
  arguments: string;
  reshaped: boolean;
  truncated: boolean;
} {
  const raw = rawInput || "{}";
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { parsed = undefined; }
  if (!isObject(parsed)) {
    const full = compactJson({ _raw: raw });
    if (limit === null || codePointLength(full) <= limit) return { arguments: full, reshaped: true, truncated: false };
    return { arguments: wrapRaw(raw, limit), reshaped: true, truncated: true };
  }
  if (limit === null || codePointLength(raw) <= limit) return { arguments: raw, reshaped: false, truncated: false };
  const clone = structuredClone(parsed) as JsonObject;
  const leaves: { parent: JsonObject | JsonValue[]; key: string | number; original: string; current: number }[] = [];
  collectLeaves(clone, leaves);
  let serialized = relaxedJson(clone);
  while (codePointLength(serialized) > limit) {
    const largest = leaves.filter((leaf) => leaf.current > 0).sort((a, b) => b.current - a.current)[0];
    if (!largest) break;
    largest.parent[largest.key as never] = "" as never;
    largest.current = 0;
    serialized = relaxedJson(clone);
  }
  return codePointLength(serialized) <= limit
    ? { arguments: serialized, reshaped: false, truncated: true }
    : { arguments: wrapRaw(raw, limit), reshaped: true, truncated: true };
}

function collectLeaves(
  value: JsonObject | JsonValue[],
  output: { parent: JsonObject | JsonValue[]; key: string | number; original: string; current: number }[],
): void {
  for (const [key, child] of Object.entries(value)) {
    if (typeof child === "string") output.push({ parent: value, key: Array.isArray(value) ? Number(key) : key, original: child, current: codePointLength(child) });
    else if (Array.isArray(child) || isObject(child)) collectLeaves(child as JsonObject | JsonValue[], output);
  }
}

function wrapRaw(raw: string, limit: number): string {
  const points = [...raw];
  let low = 0;
  let high = Math.min(points.length, limit);
  let best = "{}";
  while (low <= high) {
    const keep = Math.floor((low + high) / 2);
    const candidate = canonicalJson({ _raw: points.slice(0, keep).join("") + truncationMarker(points.length - keep) });
    if (codePointLength(candidate) <= limit) { best = candidate; low = keep + 1; } else high = keep - 1;
  }
  return best;
}

function truncateResult(text: string, limit: number | null, strategy: "head" | "head-tail"): string {
  const points = [...text];
  if (limit === null || points.length <= limit) return text;
  let low = 0;
  let high = Math.min(points.length - 1, limit);
  let keep = -1;
  let marker = "";
  while (low <= high) {
    const candidateKeep = Math.floor((low + high) / 2);
    const candidateMarker = truncationMarker(points.length - candidateKeep);
    if (candidateKeep + codePointLength(candidateMarker) <= limit) {
      keep = candidateKeep;
      marker = candidateMarker;
      low = candidateKeep + 1;
    } else high = candidateKeep - 1;
  }
  if (keep < 0) {
    marker = [..."…"].slice(0, limit).join("");
    keep = limit - codePointLength(marker);
  }
  if (strategy === "head") return points.slice(0, keep).join("") + marker;
  const head = Math.floor((keep + 1) / 2);
  return points.slice(0, head).join("") + marker + points.slice(points.length - (keep - head)).join("");
}

function truncationMarker(removed: number): string {
  return removed > 0 ? "…" : "";
}

function resolveConfig(options?: NormalizationOptions, context?: NormalizeRequest["sourceContext"]): AppliedConfig {
  const argumentMaximum = options?.bounds?.toolArguments?.maxCharacters;
  const resultMaximum = options?.bounds?.toolResults?.maxCharacters;
  return {
    bounds: {
      toolArguments: { maxCharacters: argumentMaximum === undefined ? 20_000 : argumentMaximum },
      toolResults: {
        maxCharacters: resultMaximum === undefined ? 2_500 : resultMaximum,
        strategy: options?.bounds?.toolResults?.strategy ?? "head-tail",
      },
    },
    filters: { toolResults: options?.filters?.toolResults ?? "include" },
    sourceContext: {
      ...(context?.groupId === undefined ? {} : { groupId: context.groupId }),
      baseByteOffset: context?.baseByteOffset ?? 0n,
      partial: context?.partial ?? false,
    },
  };
}

export function canonicalJson(value: JsonValue): string {
  if (value === null) return "null";
  if (typeof value === "string") return canonicalString(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" || typeof value === "bigint") return String(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value).sort(utf16Compare).map((key) => `${canonicalString(key)}:${canonicalJson(value[key]!)}`).join(",")}}`;
}

export function relaxedJson(value: JsonValue): string {
  if (value === null) return "null";
  if (typeof value === "string") return canonicalString(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" || typeof value === "bigint") return String(value);
  if (Array.isArray(value)) return `[${value.map(relaxedJson).join(",")}]`;
  return `{${Object.keys(value).map((key) => `${canonicalString(key)}:${relaxedJson(value[key]!)}`).join(",")}}`;
}

function compactJson(value: JsonValue): string {
  return relaxedJson(value);
}

function canonicalString(value: string): string {
  let output = '"';
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    if (code === 0x22) output += '\\"';
    else if (code === 0x5c) output += "\\\\";
    else if (code === 0x08) output += "\\b";
    else if (code === 0x09) output += "\\t";
    else if (code === 0x0a) output += "\\n";
    else if (code === 0x0c) output += "\\f";
    else if (code === 0x0d) output += "\\r";
    else if (code < 0x20 || (code >= 0xe000 && code <= 0xf8ff) || code === 0x2028 || code === 0x2029 || (code >= 0xd800 && code <= 0xdfff)) {
      output += `\\u${code.toString(16).toUpperCase().padStart(4, "0")}`;
    } else output += value[index];
  }
  return output + '"';
}

export function formatMs(value: number): string {
  return new Date(value).toISOString();
}

function recordTypeName(record: IRRecord): string {
  if (record.kind === "meta") return "meta";
  if (record.kind === "assistant_tool_calls") return "assistant-tool-call";
  if (record.kind === "tool_result") return "tool";
  return record.role === "user" ? "user" : record.role === "reasoning" ? "reasoning" : "assistant";
}

function diag(code: string, message: string, event: DecodedEvent, recordIndex: number): TrajectoryDiagnostic {
  return { code, message, inputLine: event.inputLine, recordIndex };
}

function parseTimestamp(value: JsonValue | undefined): { milliseconds: number; precise: string } | undefined {
  if (typeof value === "number" && Number.isSafeInteger(value) && value > 100_000_000_000) {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? undefined : {
      milliseconds: date.valueOf(),
      precise: date.toISOString().replace("Z", "0000+00:00"),
    };
  }
  if (typeof value !== "string") return undefined;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return undefined;
  const fraction = /\.(\d{1,7})/.exec(value)?.[1] ?? "";
  const seven = fraction.padEnd(7, "0").slice(0, 7);
  const base = new Date(parsed).toISOString().slice(0, 19);
  return { milliseconds: parsed, precise: `${base}.${seven}+00:00` };
}

function timestampValue(value: JsonValue | undefined): number | undefined {
  return parseTimestamp(value)?.milliseconds;
}

function readBlocksText(value: JsonValue | undefined): string {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return "";
  const parts: string[] = [];
  for (const item of value) {
    if (!isObject(item)) continue;
    const type = stringValue(item.type);
    if (type === "image") parts.push("[image]");
    else if (type === undefined || type === "text" || type === "input_text" || type === "output_text") {
      const text = stringValue(item.text);
      if (text) parts.push(text);
    }
  }
  return parts.join("\n");
}

function withoutUndefined(value: Record<string, JsonValue | undefined>): JsonObject {
  return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, JsonValue] => entry[1] !== undefined));
}

function isObject(value: unknown): value is Record<string, JsonValue> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isAsciiWhitespace(bytes: Uint8Array): boolean {
  return bytes.every((value) => value === 0x20 || value === 0x09 || value === 0x0d);
}

function stringValue(value: JsonValue | undefined): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function scalarString(value: JsonValue | undefined): string | undefined {
  return typeof value === "string" || typeof value === "number" ? String(value) : undefined;
}

function codePointLength(value: string): number {
  return [...value].length;
}

function isHarnessNoise(value: string): boolean {
  const head = value.trimStart();
  return [
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<task-notification",
  ].some((prefix) => head.startsWith(prefix));
}

function utf16Compare(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function quote(value: string): string {
  return canonicalString(value);
}

function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function fail(code: string, message: string): never {
  throw new TrajectoryNormalizationError(code, message);
}

export class TrajectoryNormalizationError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "TrajectoryNormalizationError";
  }
}
