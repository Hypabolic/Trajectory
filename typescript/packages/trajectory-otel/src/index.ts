import { createHash } from "node:crypto";

import type { TrajectoryIR } from "@hypabolic/trajectory";

export const OTEL_GENAI_SCHEMA_VERSION = "1";

interface Attribute {
  key: string;
  string_value: string;
}

interface Span {
  trace_id: string;
  span_id: string;
  parent_span_id?: string;
  name: string;
  kind: "INTERNAL";
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

  const invocationIds = new Set<string>();
  const diagnostics: { code: string; message: string; record_id: string }[] = [];
  for (const record of body) {
    if (record.role !== "assistant" || !record.provenance.nativeRecordId) continue;
    const id = sha256(JSON.stringify([
      trajectory.groupId,
      record.provenance.nativeRecordId,
      "model-invocation",
    ]));
    if (invocationIds.has(id)) continue;
    invocationIds.add(id);
    diagnostics.push({
      code: "model_span_omitted",
      message: "Model span omitted because source-native timing or provider/model metadata is incomplete.",
      record_id: id,
    });
  }

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

function attributes(values: Record<string, string>): Attribute[] {
  return Object.entries(values)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, string_value]) => ({ key, string_value }));
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
