# OpenTelemetry GenAI span output

Status: proposed output and optional package plan.

Output schema ID: `otel-genai-spans-v1`

Initial OpenTelemetry GenAI semantic-convention baseline: schema URL `https://opentelemetry.io/schemas/gen-ai/1.42.0`.

## Purpose

Trajectory.NET should be able to project normalized coding-agent trajectories into OpenTelemetry spans that follow the OpenTelemetry Generative AI semantic conventions. This allows historical or captured agent sessions to feed standard observability systems without coupling source decoders to a particular telemetry backend.

The output is not a Letta parity contract. It is a Trajectory.NET projection whose compatibility target is a pinned OpenTelemetry GenAI semantic-convention version.

## Package boundary

The BCL-only core package must not acquire OpenTelemetry SDK, OTLP protobuf, or exporter dependencies.

The planned package split is:

| Package | Responsibility |
| --- | --- |
| `Hypabolic.Trajectory` | Decode, normalize, retain execution metadata, and expose the output-adapter abstraction. |
| `Hypabolic.Trajectory.OpenTelemetry` | Project `TrajectoryIR` into `otel-genai-spans-v1`, materialize OTLP trace data, and optionally emit through an `ActivitySource`/OpenTelemetry pipeline. |

The projection itself must remain deterministic and side-effect free. Sending telemetry is a separate sink/export operation:

```text
TrajectoryIR
    |
    v
OpenTelemetryGenAiOutputAdapter
    |
    v
OtelGenAiSpanSetV1
    |-----------------------------|
    v                             v
OTLP trace-data conversion     ActivitySource emission
```

`Project(...)` must never contact a collector, start a live exporter, or depend on an ambient `TracerProvider`.

## Why the IR needs execution metadata

Message records alone are insufficient for accurate GenAI spans. A source may expose model-call information that is not visible in the final prose/tool records, including:

- provider and API family;
- requested and response model names;
- response/request IDs;
- stop or finish reasons;
- input, output, cache-read, and cache-creation token counts;
- request start, first-response, and completion timestamps;
- agent, workflow, or subagent invocation boundaries;
- tool-call causality and parent invocation identity.

Source adapters must preserve these values in source-neutral execution/invocation metadata when they are present. They must not fabricate missing provider, usage, response, or timing values merely to populate telemetry.

The preferred IR direction is additive execution metadata beside semantic records, for example:

```csharp
public sealed record TrajectoryIR
{
    public required IReadOnlyList<IRRecord> Records { get; init; }
    public required IReadOnlyList<AgentInvocationIR> AgentInvocations { get; init; }
    public required IReadOnlyList<ModelInvocationIR> ModelInvocations { get; init; }
}
```

The exact public shape remains an implementation decision, but it must be generic execution metadata rather than OpenTelemetry-specific fields embedded in source adapters.

## Span mapping

### Agent invocation

Emit one `invoke_agent` span per logical agent invocation, normally a user request and the resulting assistant/tool loop until the next independent request or terminal response.

- span name: `invoke_agent {gen_ai.agent.name}` when an agent name is known, otherwise `invoke_agent`;
- span kind: `INTERNAL` for in-process/local agent execution, `CLIENT` only when the transcript represents a remote agent service call;
- `gen_ai.operation.name`: `invoke_agent`;
- `gen_ai.conversation.id`: normalized source group/session ID when available;
- agent ID, name, version, provider, and model attributes only when known;
- retain the deterministic trajectory/invocation identity as `hypabolic.trajectory.*` correlation attributes.

A whole transcript must not automatically become one `invoke_agent` span when it contains multiple independent user turns.

### Model inference

Emit a GenAI inference span for an assistant model invocation only when the source exposes a defensible invocation boundary and model/provider metadata.

- use the standard operation appropriate to the source, normally `chat` or `generate_content`;
- span name follows `{gen_ai.operation.name} {gen_ai.request.model}` when the model is known;
- record requested/response model, provider, response ID, finish reasons, and usage attributes only when supplied by the source;
- do not derive token usage from string length or infer provider from model-name heuristics;
- do not invent a duration from adjacent transcript messages by default.

If a source exposes only a completion timestamp and no reliable start boundary, the default projection omits the inference span and emits a projection diagnostic. A future explicit timing policy may permit point or bounded/inferred spans, but inferred timing must be distinguishable from source-native timing.

### Tool execution

A linked tool call/result pair maps naturally to an `execute_tool` internal span.

- span name: `execute_tool {gen_ai.tool.name}`;
- `gen_ai.operation.name`: `execute_tool`;
- `gen_ai.tool.name`: normalized tool name;
- `gen_ai.tool.call.id`: normalized tool-call ID when available;
- start: tool-call timestamp;
- end: linked tool-result timestamp;
- error status and `error.type` are populated from normalized error semantics;
- arguments and results are opt-in content attributes.

Call-only or result-only records remain representable but must carry an explicit incomplete-link diagnostic rather than a fabricated interval.

### Workflow invocation

Emit `invoke_workflow` only when the source explicitly distinguishes a workflow/crew/graph invocation from an ordinary agent invocation. The adapter must not relabel every multi-step agent trajectory as a workflow.

## Parentage and links

- Agent invocations form the primary trace hierarchy.
- Model inference and tool execution spans are children of the owning agent invocation.
- Tool execution follows the model response that requested the call, but it need not be a child of a completed inference span; `gen_ai.tool.call.id` and stable Hypabolic IDs provide causal correlation.
- Concurrent subagents should become sibling or nested agent spans according to explicit invocation/continuation metadata, not transcript order alone.
- When a causal relationship cannot be represented as a single parent, use OpenTelemetry span links rather than inventing a tree edge.

## Identity and replay

The typed projection should produce deterministic trace/span identities from stable trajectory and invocation identities so replaying the same normalized trajectory produces the same span set.

Direct OTLP conversion should preserve those projected IDs. An `ActivitySource` emitter may use runtime-generated IDs where the .NET API does not permit exact projected IDs; in that mode it must retain deterministic trajectory, invocation, and record IDs as correlation attributes.

Zero trace/span IDs are invalid and must never be emitted.

## Privacy and content capture

Content-bearing GenAI attributes are disabled by default:

- `gen_ai.system_instructions`;
- `gen_ai.input.messages`;
- `gen_ai.output.messages`;
- `gen_ai.tool.definitions`;
- `gen_ai.tool.call.arguments`;
- `gen_ai.tool.call.result`;
- retrieval query/document bodies.

Enabling content capture requires explicit projection options. Existing normalization bounds still apply, and the OpenTelemetry adapter may impose stricter telemetry-specific limits or redaction hooks. Diagnostics must remain content-safe.

## Semantic-convention versioning

OpenTelemetry GenAI conventions are versioned independently and may change while still marked development.

The adapter must therefore:

- pin a semantic-convention schema URL and expose it in the output;
- publish the pinned convention version in package documentation and fixtures;
- never silently change attribute names, span names, parentage, or default content-capture behaviour in a patch release;
- add an explicit compatibility update when moving the pin;
- maintain golden fixtures per supported semantic-convention version if more than one version is supported.

The initial planning baseline is GenAI schema `1.42.0`; implementation must verify the current upstream release before coding begins.

## Output model and transport

`otel-genai-spans-v1` is a typed span-set projection, not a custom observability backend protocol. It should contain:

- resource attributes;
- instrumentation scope and schema URL;
- trace/span IDs, parent IDs, links, names, kinds, timestamps, status, attributes, and events;
- projection diagnostics and resolved privacy/timing options.

The optional package should support:

1. typed in-memory span data for tests and custom sinks;
2. conversion to standard OTLP trace protobuf/JSON structures;
3. emission through an `ActivitySource` for applications already using the OpenTelemetry .NET SDK.

OTLP wire compatibility should be delegated to official OpenTelemetry protobuf/SDK packages rather than reimplementing protobuf contracts in the core library.

## Acceptance criteria

- Pi and at least one second source produce deterministic `invoke_agent` and `execute_tool` span goldens.
- Model inference spans are emitted only when required metadata and timing are available.
- Span names, kinds, attributes, and schema URL pass a convention-compliance test suite pinned to the selected GenAI semantic-convention version.
- Tool arguments, results, prompts, and responses are absent by default.
- Enabling content capture is explicit, bounded, and covered by content-safety tests.
- Token usage and provider/model fields are never synthesized from heuristics.
- Direct OTLP conversion validates against official OpenTelemetry protobuf types.
- The optional package publishes and runs under Native AOT on supported targets.
- The core package remains BCL-only and its dependency graph is unchanged.
- Re-projecting the same `TrajectoryIR` produces identical span identities and ordering.

## Implementation placement

This work belongs in Slice 10 alongside the additional projections and extension API. Earlier source slices must preserve available invocation metadata so Slice 10 does not require reparsing source transcripts or reopening already-decoded source formats.
