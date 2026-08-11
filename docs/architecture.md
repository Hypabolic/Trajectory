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

Native implementations exist for .NET, TypeScript, and Rust. Each is idiomatic
to its ecosystem and governed by the same contracts and conformance suite.

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

## Source adapters

Built-in sources for v1: **Pi**, **Claude Code**, **Codex**, **OpenClaw**,
**Hermes**, **Grok Build**.

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

## Further reading

- [Normative normalization](../contracts/spec/normalization.md)
- [Identity](../contracts/spec/identity.md)
- [Diagnostics](../contracts/spec/diagnostics.md)
- [Publishing](publishing.md)
