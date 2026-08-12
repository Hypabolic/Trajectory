# Trajectory architecture

## Purpose

Trajectory is a high-performance library for turning native coding-agent session
transcripts into a stable intermediate representation and projecting that
representation into multiple output schemas.

Primary uses include:

- memory formation, reflection, and continual learning;
- cross-harness experience aggregation;
- evaluation, search, replay, and training-data pipelines;
- feeding structured experience into Hypabolic systems and other stores;
- observability via deterministic OpenTelemetry GenAI span projections.

Native implementations exist for .NET, TypeScript, Rust, and Python. Each is
idiomatic to its ecosystem and governed by the same contracts and conformance
suite.

## Design principles

1. **Decode once, normalize centrally, project many times.** Source adapters
   decode source-native records. They do not own shared normalization policy or
   output formatting.
2. **The IR is richer than every output schema.** Output adapters may omit or
   reshape information, but they must not force source adapters to discard it
   early.
3. **Wire formats are versioned products.** The internal model is not a public
   interchange contract.
4. **Determinism is part of correctness.** Identity, ordering, canonical JSON,
   hashes, truncation, diagnostics, and synthesized values are testable
   contracts.
5. **Optional integrations stay optional.** OpenTelemetry and future store
   backends remain outside the core package for each ecosystem.
6. **AOT / portable by default.** .NET is trim- and Native AOT–friendly; other
   runtimes avoid hidden runtime scanning for core paths.

## Processing architecture

```text
native transcript
        │
        ▼
┌───────────────────────┐
│ source adapter        │  source-specific decoding
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│ decoded session       │  native identity + events
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│ normalization core    │  linking, bounds, timestamps, diagnostics
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│ private trajectory IR │  normalized + provenance
└───────────┬───────────┘
            │
     ┌──────┼──────┬──────────┬────────────┐
     ▼      ▼      ▼          ▼            ▼
  Hypabolic  canonical  message   OpenAI   OTEL GenAI
  trajectory  identity  trajectory chat    spans (optional)
```

## Target packages

| Ecosystem | Core | Optional |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | `Hypabolic.Trajectory.OpenTelemetry`, `Hypabolic.Trajectory.Testing` |
| TypeScript | `@hypabolic/trajectory` | `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel` |
| Rust | `hypabolic-trajectory` | `hypabolic-trajectory-opentelemetry` |
| Python | `hypabolic-trajectory` | `[otel]` SDK sinks only (pure OTEL project + `otel` submodule always in core) |

Python is an independent native **3.11+** implementation under `python/`
(import `hypabolic_trajectory`). First public PyPI ships on the next
synchronized multi-registry tag after published `0.1.0` (tip sources include
AHP Shape A). Package docs: [`python/README.md`](../python/README.md). Spec:
[python-implementation-spec.md](python-implementation-spec.md).

## Source adapters

Built-in sources: **Pi**, **Claude Code**, **Codex**, **OpenClaw**,
**Hermes**, **AHP** (Shape A offline ChatState snapshot; listing deferred),
and **Grok Build** (`grok-build`).

AHP export-directory listing, Shape B action-log reduce, multi-chat unpack,
and live host clients are deferred. See
[ahp-ingest-status.md](ahp-ingest-status.md) and
[`contracts/spec/sources/ahp.md`](../contracts/spec/sources/ahp.md).

Adapters decode only. Shared policy (tool linking, bounds, timestamps,
diagnostics, identity) lives in the normalizer.

## Outputs

| Output | Role |
| --- | --- |
| Hypabolic trajectory v1 | Provenance-rich product format |
| Canonical identity v1 | Stable IDs, ordering, hashes for identity-bearing pipelines |
| Message trajectory v1 | Compact role/message record array |
| OpenAI chat messages | Chat-style projection |
| Minimal JSONL | Streaming-friendly line format |
| OTEL GenAI spans v1 | Deterministic span set (optional SDK emission) |

Technical schema IDs are versioned under `contracts/`; see
[hypabolic-trajectory-v1.md](hypabolic-trajectory-v1.md) and
[otel-genai-output.md](otel-genai-output.md).

## Cross-runtime authority

1. Versioned schemas and behavioural specs in `contracts/`
2. Shared fixtures and goldens in `conformance/`
3. Capability manifests (`compatibility.json`, runtime `runtime-capabilities.json`)

There is no shared native core, FFI bridge, or cross-language subprocess.

## Live session streaming (core + optional file I/O)

One-shot normalize and listing remain the shipped batch path. A **library**
streaming surface (not a Trajectory daemon) has **normative wire contracts**
(`trajectory-stream-v1`) and pure stream engines in all four cores (LS-03–LS-08):

- pure stream state machine in each core package (cursor, apply, snapshot +
  delta, provisional records, reset, AHP reducer);
- optional file I/O packages (LS-09; poll/follow only; explicit root);
- optional AHP client and Hermes provider packages (later slices);
- shared `trajectory-stream-v1` contracts and multi-runtime conformance.

Normative design: [live-session-streaming.md](live-session-streaming.md).  
Wire contract: [streaming.md](../contracts/spec/streaming.md).  
File I/O: [streaming-file-io.md](streaming-file-io.md).  
Delivery slices: [live-session-streaming-plan.md](live-session-streaming-plan.md).

## Further reading

- [Normative normalization](../contracts/spec/normalization.md)
- [Identity](../contracts/spec/identity.md)
- [Diagnostics](../contracts/spec/diagnostics.md)
- [Streaming contract](../contracts/spec/streaming.md)
- [Live session streaming (product)](live-session-streaming.md)
- [Live session streaming plan](live-session-streaming-plan.md)
- [Publishing](publishing.md)
