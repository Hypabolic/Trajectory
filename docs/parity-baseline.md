# Letta parity baseline

Baseline reviewed: `Hypabolic/Trajectory` `main` at merge commit `61a195d074ad6a547cf0061c12a386b0eabece99`.

Reference reviewed: `letta-ai/trajectory` `main`, package version `0.2.0` at the time of planning.

This document defines what “feature parity” means for the .NET implementation of Trajectory and records the gap between that target and the current prototype.

For v1 release scope, Letta Code, OpenHands, and Deep Agents checkpoint
integrations are deferred support goals. Their parity requirements remain
recorded here for future implementation but are not v1 acceptance criteria.

## Parity target

The .NET implementation of Trajectory reaches Letta parity when the same supported source input and equivalent options produce:

1. structurally and semantically identical `trajectory-v1` records;
2. identical library-owned canonical records, identity fields, ordering fields, hashes, configuration, and diagnostics;
3. equivalent fatal error codes and recoverable cleanup behaviour;
4. equivalent local trajectory listings and cursor semantics;
5. for the post-v1 Deep Agents capability, equivalent checkpoint selection and normalized messages;
6. deterministic results across reruns, chunk boundaries, and input arrival order where the upstream contract guarantees it.

Exact JSON byte equality is required for golden compatibility fixtures after line-ending normalization and a defined canonical serializer. Where the TypeScript runtime exposes typed objects rather than serialized bytes, field values, ordering, omission/null rules, and timestamp text must still match exactly.

The Hypabolic output is additive. It must preserve the same normalized semantics and identity basis while exposing more provenance; it is not required to match an upstream format.

## Upstream public capability surface

### Normalization

- `normalizeTranscript` for transcript-backed sources;
- `normalizeToCanonical`;
- `normalizeCheckpoint` and `normalizeCheckpointToCanonical` for Deep Agents;
- strict whole-transcript validation;
- explicit and implied partial mode;
- source group conflict/required rules;
- configurable tool argument and result bounds;
- tool-result inclusion/omission filters;
- deterministic timestamps, identity, canonical JSON, and SHA-256 hashing;
- structured diagnostics that never expose raw transcript content.

### Sources

| Source | Input | Listing locator | Important identity behaviour |
| --- | --- | --- | --- |
| Claude Code | Native JSONL | Transcript file | Native UUID where available; byte location fallback |
| Codex | Native rollout JSONL | Transcript file | Byte location identity; canonical output requires group ID |
| Hermes | Message-row array or session envelope | SQLite store | Native/session metadata and source sequence |
| Letta Code | Client `transcript.jsonl` | Transcript file | Source message/line ID, then row-position fallback |
| OpenClaw | Pi-agent session JSONL | Transcript file | Wrapper ID, then byte location fallback |
| OpenHands | Event array or `{ items: [...] }` | Event directory | Native event ID with content fallback where unavoidable |
| Pi | Native pi-coding-agent JSONL | Transcript file | Native wrapper/session identity with location fallback |
| Deep Agents | LangGraph SQLite checkpoint | SQLite store/thread | Thread + namespace group; checkpoint message ordering |

### Listing

- default local store discovery for every source;
- caller-supplied root override;
- newest-first results;
- item ID, path, optional update time/title/size;
- opaque versioned cursor;
- page limit default 50 and maximum 1000;
- graceful empty result for a missing store;
- positional degradation when a cursor item disappears between pages.

### Outputs and schemas

- Letta `trajectory-v1` JSON Schema;
- Letta `trajectory-canonical-v1` JSON Schema;
- normalizer and canonical schema version constants;
- validation API for normalized transcripts.

The upstream Python wrapper is not a separate parity requirement. The NuGet library is the .NET-facing equivalent. Behaviour exercised by the Python wrapper remains in scope when it is part of the underlying normalization/checkpoint contract.

## Current implementation assessment

### What is usable

- The project multi-targets `net8.0`, `net9.0`, and `net10.0`.
- Trimming and AOT analyzers are enabled and the core remains BCL-only.
- Pi, Claude Code, Codex, OpenClaw, and Hermes have complete decode-normalize-project
  paths, default listing (Hermes is empty when the SQLite store is missing), pinned
  upstream goldens, and Native AOT smoke coverage where applicable.
- Exact Letta trajectory/canonical adapters and the provenance-rich Hypabolic
  adapter share one deterministic IR, identity, canonical JSON, and SHA-256
  implementation.
- Partial chunks, absolute UTF-8 byte anchors, source-group conflict handling,
  bounds, filters, timestamp policy, and tool linking are implemented in the
  shared normalization path.

Public API preservation remains secondary to the documented wire and behavioural
contracts until the first package release.

### Architecture status

| Area | Current state | Required change |
| --- | --- | --- |
| Adapter boundary | Source adapters decode to internal events; shared normalization owns common policy | Extend without moving common policy back into sources |
| IR provenance | Native identity, source time/order/location, component identity, and hashes implemented | Extend additively when later sources expose new provenance |
| Roles and record kinds | Typed IR and exact serializer mappings implemented | Keep the IR independent of output wire models |
| Engine output typing | Typed adapters and a safe non-generic bridge implemented | Add direct stream/UTF-8 APIs in Slice 10 |
| Registration | Explicit default registration without reflection or scanning | Add the builder/generated registry surface in Slice 10 |
| Namespace/package | `Hypabolic.Trajectory` established | Preserve through package readiness |

### Behavioural gaps

| Capability | Current state | Parity requirement |
| --- | --- | --- |
| Pi | Implemented through all three outputs with pinned parity fixtures | Keep covered by differential parity and regression tests |
| Claude Code | Implemented through all three outputs with version-family fixtures and listing | Keep covered by differential parity and regression tests |
| Codex | Implemented with true UTF-8 byte offsets, arbitrary partial chunks, group enforcement, semantic tool variants, and listing | Keep covered by differential parity and regression tests |
| Letta Code | Enum value only; post-v1 backlog | Full client transcript decoder and row/native identity rules |
| OpenClaw | Implemented through built-in outputs with shared fixtures, delivery-mirror masking, and listing | Keep covered by differential parity and regression tests |
| OpenHands | Enum value only; post-v1 backlog | Array/envelope event decoding and event identity |
| Hermes | Implemented through built-in outputs with array/envelope fixtures; SQLite listing is empty without a provider | Keep covered by differential parity; optional SQLite provider for sessions-table listing |
| Deep Agents | Enum value only; post-v1 backlog | Optional SQLite/checkpoint package and exact checkpoint reduction |
| Tool linking | Shared pre-pass implements duplicate repair, reverse-arrival linking, and partial orphan policy | Extend source fixtures as later adapters introduce new native shapes |
| Timestamps | Exact preservation/interpolation/synthesis policy implemented | Keep source-specific timestamp representations covered |
| Validation | Whole/partial normalized validation and checked schemas implemented | Add public validation surface in Slice 10 |
| Bounds | Unicode-code-point limits, valid object reshaping, markers, and defaults implemented | Differential regression coverage |
| Filters | `toolResults: include|omit` implemented | Differential regression coverage |
| Partial mode | Role relaxation, cross-chunk results, and offset-controlled canonical meta implemented | Differential regression coverage |
| Byte offsets | Real UTF-8 byte positions implemented for current JSONL sources | Preserve for later byte-anchored sources |
| Diagnostics | Upstream-compatible structural diagnostics and typed fatal errors implemented | Extend additively for later sources |
| Canonical identity | Stable source identity, ordering, component keys, hashes, and flattened fields implemented | Differential regression coverage |
| Deterministic JSON | Dedicated canonical JSON writer used by all hash-bearing contracts | Differential regression coverage |

### Output gaps

The existing `LettaTrajectoryV1OutputAdapter` is not compatible with Letta trajectory-v1. It writes a custom object containing:

- `format`;
- `trajectory_id`;
- `source`;
- `messages`;
- optional diagnostics.

The actual Letta trajectory-v1 contract is a bare JSON array of strict role-specific records. Tool calls use `args`, tool results are separate records, meta has no timestamp, and the custom envelope fields do not exist.

Required output work:

- replace the current adapter with exact `letta-trajectory-v1` typed models and JSON schema tests;
- add `letta-canonical-v1`;
- add `hypabolic-trajectory-v1` as a separate, explicitly versioned format;
- add OpenAI and minimal JSONL projections after identity-bearing outputs are stable.

### Test and delivery gaps

| Area | Current state | Target |
| --- | --- | --- |
| Test framework | Console executable with nine hand-written checks | Standard test projects with unit, fixture, property, differential, and integration categories |
| Golden fixtures | One synthetic Pi input/output pair matching the prototype envelope | Sanitized upstream fixtures for every source and both Letta outputs, plus Hypabolic goldens |
| Differential parity | None | Run fixtures through pinned TypeScript reference and compare normalized structures/hashes |
| Property tests | None | Truncation, canonical JSON, ID stability, ordering, chunking, duplicate/reverse arrival |
| AOT execution | Analyzer flags only | Publish and execute smoke app in CI |
| CI | No workflow in the initial implementation | Build/test/AOT matrix across supported OS and SDKs |
| Benchmarks | None | Throughput/allocation benchmarks for representative JSONL sizes |
| Packaging | No package metadata/release workflow | Deterministic NuGet packages, symbols, source link, README, license, schema assets |

## Compatibility policy

- The pinned upstream commit used by the parity suite is recorded in the repository and updated intentionally.
- Every upstream update is reviewed for schema, diagnostic, source, and default-policy changes before the pin moves.
- A new source shape requires a sanitized fixture before decoder support is merged.
- Unknown semantic record/content kinds emit diagnostics rather than being silently discarded.
- Known transport-only records may be dropped without a per-record diagnostic when that policy is documented by the source adapter.
- Letta output changes require fixture review and, where applicable, a schema-version decision.
- Hypabolic output changes follow the evolution rules in `hypabolic-trajectory-v1.md`.

## Definition of parity complete

Parity is complete when all of the following are true:

- all eight upstream sources are supported;
- source listing is supported for all eight sources;
- transcript and checkpoint normalization match the pinned upstream reference;
- Letta trajectory and canonical fixtures match exactly;
- bounds, filters, diagnostics, errors, partial mode, chunk identity, and arrival-order tests pass;
- the Hypabolic projection has a published JSON Schema and complete golden suite;
- Native AOT smoke tests execute successfully for the core package;
- the core path has no required runtime dependency outside the BCL;
- the optional SQLite package does not leak dependencies into the core package;
- package documentation identifies the pinned upstream compatibility version and known intentional differences.
