# Trajectory.NET

Trajectory.NET is a high-performance, Native AOT-compatible C# library for normalizing coding-agent session transcripts into a stable internal trajectory representation and projecting that representation into multiple output schemas.

It is a functional and behavioural port of [`letta-ai/trajectory`](https://github.com/letta-ai/trajectory), redesigned around ports and adapters:

```text
Claude Code / Codex / Pi / Letta Code / OpenClaw / OpenHands / Hermes / Deep Agents
                                      |
                                      v
                         Internal Trajectory IR
                                      |
                +---------------------+---------------------+
                |                     |                     |
                v                     v                     v
       Letta trajectory v1   Letta canonical v1   Hypabolic trajectory v1
```

## Status

The repository currently contains an early prototype, not a production-compatible implementation. The architecture and delivery plan now define the work required to:

- match the original Letta trajectory and canonical formats exactly;
- support every source and listing capability in the pinned Letta reference;
- add a richer, provenance-preserving Hypabolic export;
- keep the core BCL-only, trim-safe, and Native AOT-compatible;
- verify behaviour through golden fixtures and differential parity tests.

Do not treat the existing prototype output named `letta-trajectory-v1` as compatible with the upstream Letta schema. Replacing it is part of the first implementation slice.

## Intended outputs

| Schema ID | Purpose |
| --- | --- |
| `letta-trajectory-v1` | Exact Letta normalized trajectory record array |
| `letta-canonical-v1` | Exact library-owned Letta canonical ingestion contract |
| `hypabolic-trajectory-v1` | Loss-minimizing Hypabolic format with provenance, identity, hashes, resolved configuration, and diagnostics |
| `openai-chat-messages` | OpenAI-compatible message projection |
| `jsonl-minimal` | Compact streaming JSONL projection |

## Intended packages

- `Hypabolic.Trajectory` — BCL-only normalization core, transcript adapters, output adapters, and listing abstractions.
- `Hypabolic.Trajectory.Sqlite` — optional Deep Agents / LangGraph checkpoint support.
- `Hypabolic.Trajectory.Testing` — optional fixture and adapter-authoring helpers.

Target frameworks: `net8.0;net9.0;net10.0`.

## Documentation

- [Architecture](docs/architecture.md)
- [Letta parity baseline and current implementation audit](docs/parity-baseline.md)
- [Hypabolic trajectory v1 contract](docs/hypabolic-trajectory-v1.md)
- [Vertical-slice implementation plan](docs/implementation-plan.md)

## Core constraints

- deterministic normalization, ordering, identity, canonical JSON, and SHA-256 hashing;
- source-generated `System.Text.Json` serialization only for built-in contracts;
- no reflection, runtime assembly scanning, or dynamic code generation in the core path;
- no required third-party runtime dependency in the core package;
- explicit adapter registration at compile time or through a fluent builder;
- diagnostics never contain raw transcript content;
- source decoding and output projection remain independently testable.

## Planned high-level API

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

The exact public API will be finalized through the implementation slices; the wire-format and behavioural compatibility contracts take precedence over preserving the current prototype API.