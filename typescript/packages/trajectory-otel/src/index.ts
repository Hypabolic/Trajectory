import { createHash } from "node:crypto";

import type { TrajectoryIR } from "@hypabolic/trajectory";

export const OTEL_GENAI_SCHEMA_VERSION = "1";

interface Attribute {
  key: string;
  string_value?: string;
  integer_value?: bigint;
  string_values?: string[];
}

interface Span {
  trace_id: string;
  span_id: string;
  parent_span_id?: string;
  name: string;
  kind: "INTERNAL" | "CLIENT";
  start_time: string;
  end_time: string;
  status: "UNSET" | "ERROR";
  attributes: Attribute[];
  links: [];
  events: [];
}

export function projectOpenTelemetry(trajectory: TrajectoryIR): unknown {
  const traceId = nonZero(sha256(`${trajectory.sourceName}|${trajectory.groupId}`).slice(0, 32));
  const body = trajectory.records.filter((record) => record.kind !== "meta");
  const spans: Span[] = [];
  const diagnostics: { code: string; message: string; record_id: string }[] = [];
  const turns: { startIndex: number; endIndex: number; start: number; end: number; spanId: string }[] = [];
  const users = body.flatMap((record, index) => record.role === "user" ? [{ record, index }] : []);
  for (let index = 0; index < users.length; index++) {
    const first = users[index]!;
    const endIndex = users[index + 1]?.index ?? body.length;
    const segment = body.slice(first.index, endIndex);
    const last = [...segment].reverse().find((record) => record.sourceTimestamp !== null);
    if (first.record.sourceTimestamp === null || !last || last.sourceTimestamp === null) continue;
    const spanId = spanIdFor(`agent|${first.record.id}`);
    spans.push({
      trace_id: traceId,
      span_id: spanId,
      name: "invoke_agent",
      kind: "INTERNAL",
      start_time: precise(first.record),
      end_time: last.sourceTimestamp < first.record.sourceTimestamp ? precise(first.record) : precise(last),
      status: "UNSET",
      attributes: attributes({
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.conversation.id": trajectory.groupId,
        "hypabolic.trajectory.id": traceId,
        "hypabolic.trajectory.source": trajectory.sourceName,
        "hypabolic.trajectory.record.id": first.record.id,
      }),
      links: [],
      events: [],
    });
    turns.push({
      startIndex: first.index,
      endIndex,
      start: first.record.sourceTimestamp,
      end: last.sourceTimestamp,
      spanId,
    });
  }

  for (const invocation of trajectory.execution.modelInvocations) {
    if (
      invocation.startedAt === undefined ||
      invocation.completedAt === undefined ||
      (
        invocation.provider === undefined &&
        invocation.requestedModel === undefined &&
        invocation.responseModel === undefined
      )
    ) {
      diagnostics.push({
        code: "model_span_omitted",
        message: "Model span omitted because source-native timing or provider/model metadata is incomplete.",
        record_id: invocation.id,
      });
      continue;
    }
    const parent = [...turns].reverse().find(
      (turn) => invocation.startedAt! >= turn.start && invocation.startedAt! <= turn.end,
    );
    const model = invocation.requestedModel ?? invocation.responseModel;
    spans.push({
      trace_id: traceId,
      span_id: spanIdFor(`model|${invocation.id}`),
      ...(parent === undefined ? {} : { parent_span_id: parent.spanId }),
      name: model === undefined ? "chat" : `chat ${model}`,
      kind: "CLIENT",
      start_time: preciseInvocation(
        invocation.startedAt,
        invocation.startedAtPrecise,
      ),
      end_time: invocation.completedAt < invocation.startedAt
        ? preciseInvocation(invocation.startedAt, invocation.startedAtPrecise)
        : preciseInvocation(invocation.completedAt, invocation.completedAtPrecise),
      status: "UNSET",
      attributes: attributes({
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": invocation.provider,
        "gen_ai.request.model": invocation.requestedModel,
        "gen_ai.response.model": invocation.responseModel,
        "gen_ai.response.id": invocation.responseId,
        "gen_ai.response.finish_reasons": invocation.stopReason === undefined
          ? undefined
          : [invocation.stopReason],
        "gen_ai.usage.input_tokens": invocation.usage?.inputTokens,
        "gen_ai.usage.output_tokens": invocation.usage?.outputTokens,
        "gen_ai.usage.cache_read.input_tokens": invocation.usage?.cacheReadTokens,
        "gen_ai.usage.cache_creation.input_tokens": invocation.usage?.cacheWriteTokens,
        "hypabolic.trajectory.invocation.id": invocation.id,
        "hypabolic.trajectory.api_family": invocation.apiFamily,
      }),
      links: [],
      events: [],
    });
  }

  const results = new Map(
    body.filter((record) => record.kind === "tool_result").map((record) => [record.toolCallId!, record]),
  );
  for (let index = 0; index < body.length; index++) {
    const record = body[index]!;
    if (record.kind !== "assistant_tool_calls") continue;
    for (const call of record.toolCalls!) {
      const result = results.get(call.id);
      if (!result || record.sourceTimestamp === null || result.sourceTimestamp === null) continue;
      const parent = [...turns].reverse().find(
        (turn) => index >= turn.startIndex && index < turn.endIndex,
      );
      spans.push({
        trace_id: traceId,
        span_id: spanIdFor(`tool|${call.id}|${record.id}`),
        ...(parent === undefined ? {} : { parent_span_id: parent.spanId }),
        name: `execute_tool ${call.name}`,
        kind: "INTERNAL",
        start_time: precise(record),
        end_time: result.sourceTimestamp < record.sourceTimestamp ? precise(record) : precise(result),
        status: result.isError ? "ERROR" : "UNSET",
        attributes: attributes({
          "gen_ai.operation.name": "execute_tool",
          "gen_ai.tool.name": call.name,
          "gen_ai.tool.call.id": call.id,
          "hypabolic.trajectory.call_record.id": record.id,
          "hypabolic.trajectory.result_record.id": result.id,
        }),
        links: [],
        events: [],
      });
    }
  }
  spans.sort((left, right) =>
    left.start_time.localeCompare(right.start_time) ||
    left.name.localeCompare(right.name) ||
    left.span_id.localeCompare(right.span_id),
  );

  return {
    schema_url: "https://opentelemetry.io/schemas/gen-ai/1.42.0",
    trace_id: traceId,
    instrumentation_scope: "Hypabolic.Trajectory.OpenTelemetry",
    instrumentation_version: "0.1.0",
    resource_attributes: [],
    spans,
    diagnostics,
    content_policy: {
      messages_included: false,
      tool_arguments_included: false,
      tool_results_included: false,
      maximum_characters: 1024,
    },
  };
}

function precise(record: TrajectoryIR["records"][number]): string {
  return record.sourceTimestampPrecise ??
    new Date(record.sourceTimestamp!).toISOString().replace("Z", "0000+00:00");
}

function preciseInvocation(value: number, preciseValue?: string): string {
  return preciseValue ??
    new Date(value).toISOString().replace("Z", "0000+00:00");
}

function attributes(
  values: Record<string, string | bigint | string[] | undefined>,
): Attribute[] {
  return Object.entries(values)
    .filter((entry): entry is [string, string | bigint | string[]] =>
      entry[1] !== undefined
    )
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) =>
      typeof value === "bigint"
        ? { key, integer_value: value }
        : Array.isArray(value)
          ? { key, string_values: value }
          : { key, string_value: value }
    );
}

function spanIdFor(value: string): string {
  return nonZero(sha256(value).slice(0, 16));
}

function nonZero(value: string): string {
  return /^0+$/.test(value) ? value.slice(0, -1) + "1" : value;
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}
