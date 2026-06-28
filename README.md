# Trajectory.NET

Trajectory.NET is a high-performance, Native AOT-compatible C# library for normalizing coding-agent session transcripts into a stable internal trajectory representation and projecting that representation into multiple output schemas.

It is a functional and behavioural port of [`letta-ai/trajectory`](https://github.com/letta-ai/trajectory), redesigned around ports and adapters:

```text
Claude Code / Codex / Pi / Letta Code / OpenClaw / OpenHands / Hermes / Deep Agents
                                      |
                                      v
                         Internal Trajectory IR
                                      |
          +---------------------------+---------------------------+
          |                           |                           |
          v                           v                           v
 Letta compatibility          Hypabolic trajectory v1     OpenTelemetry GenAI spans
 trajectory + canonical
```

## Status

Slices 1 through 4 and Slice 10 are implemented. Pi, Claude Code, and Codex
transcripts now normalize through the shared IR into all three identity-bearing
outputs, with source listing, pinned upstream goldens, cross-platform tests, and
Native AOT smoke coverage. Slice 10 also supplies OpenAI chat and minimal JSONL
stream projections, public adapter registration, and an optional OpenTelemetry
package. The remaining plan will:

- match the original Letta trajectory and canonical formats exactly;
- support every source and listing capability in the pinned Letta reference;
- add a richer, provenance-preserving Hypabolic export;
- retain the optional OpenTelemetry GenAI package without adding telemetry dependencies to the core package;
- keep the core BCL-only, trim-safe, and Native AOT-compatible;
- verify behaviour through golden fixtures and differential parity tests.

The [Rust and TypeScript implementation plan](docs/multi-language-plan.md) turns this into a three-runtime product governed by shared contracts and differential conformance, while retaining native APIs and packages in each ecosystem.

## Intended outputs

| Schema ID | Purpose |
| --- | --- |
| `letta-trajectory-v1` | Exact Letta normalized trajectory record array |
| `letta-canonical-v1` | Exact library-owned Letta canonical ingestion contract |
| `hypabolic-trajectory-v1` | Loss-minimizing Hypabolic format with provenance, identity, hashes, resolved configuration, and diagnostics |
| `otel-genai-spans-v1` | Deterministic OpenTelemetry GenAI span projection with optional OTLP and `ActivitySource` emission |
| `openai-chat-messages` | OpenAI-compatible message projection |
| `jsonl-minimal` | Compact streaming JSONL projection |

## Intended packages

- `Hypabolic.Trajectory` — BCL-only normalization core, transcript adapters, output adapters, and listing abstractions.
- `Hypabolic.Trajectory.Sqlite` — optional Deep Agents / LangGraph checkpoint support.
- `Hypabolic.Trajectory.OpenTelemetry` — optional OpenTelemetry GenAI projection, OTLP conversion, and emission support.
- `Hypabolic.Trajectory.Testing` — optional fixture and adapter-authoring helpers.

Target frameworks: `net8.0;net9.0;net10.0`.

## Documentation

- [Architecture](docs/architecture.md)
- [Pinned upstream compatibility reference](docs/upstream-reference.md)
- [Letta parity baseline and current implementation audit](docs/parity-baseline.md)
- [Hypabolic trajectory v1 contract](docs/hypabolic-trajectory-v1.md)
- [OpenTelemetry GenAI span output plan](docs/otel-genai-output.md)
- [Adapter authoring](docs/adapter-authoring.md)
- [Rust and TypeScript implementation plan](docs/multi-language-plan.md)
- [Vertical-slice implementation plan](docs/implementation-plan.md)

## Core constraints

- deterministic normalization, ordering, identity, canonical JSON, and SHA-256 hashing;
- source-generated `System.Text.Json` serialization only for built-in contracts;
- no reflection, runtime assembly scanning, or dynamic code generation in the core path;
- no required third-party runtime dependency in the core package;
- explicit adapter registration at compile time or through a fluent builder;
- diagnostics never contain raw transcript content;
- source decoding and output projection remain independently testable.

## High-level API

```csharp
var engine = TrajectoryEngine.CreateDefault();

var ir = engine.NormalizeToIR(new NormalizeInput
{
    Source = TrajectorySource.Codex,
    Transcript = transcript,
    SourceContext = new SourceContext
    {
        GroupId = sessionId,
        BaseByteOffset = offset,
        Partial = true,
    },
});

var letta = engine.Project<LettaNormalizeResult>(
    ir,
    OutputSchemaIds.LettaTrajectoryV1);

var hypabolic = engine.Project<HypabolicTrajectoryV1>(
    ir,
    OutputSchemaIds.HypabolicTrajectoryV1);
```

The API may still evolve before the first package release; the wire-format and
behavioural compatibility contracts take precedence over preserving pre-release
surface details.
