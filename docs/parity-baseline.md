# Letta parity baseline

Baseline reviewed: `Hypabolic/Trajectory` `main` at merge commit `61a195d074ad6a547cf0061c12a386b0eabece99`.

Reference reviewed: `letta-ai/trajectory` `main`, package version `0.2.0` at the time of planning.

This document defines what “feature parity” means for Trajectory.NET and records the gap between that target and the current prototype.

## Parity target

Trajectory.NET reaches Letta parity when the same supported source input and equivalent options produce:

1. structurally and semantically identical `trajectory-v1` records;
2. identical library-owned canonical records, identity fields, ordering fields, hashes, configuration, and diagnostics;
3. equivalent fatal error codes and recoverable cleanup behaviour;
4. equivalent local trajectory listings and cursor semantics;
5. equivalent Deep Agents checkpoint selection and normalized messages;
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

- The project already multi-targets `net8.0`, `net9.0`, and `net10.0`.
- Trimming and AOT analyzers are enabled in the core project.
- The core package currently has no third-party dependencies.
- SHA-256 helpers and source-generated JSON scaffolding exist.
- A registry-style engine, a Pi parser, an initial IR, and a static convenience facade exist.
- The current tests establish a small executable smoke path for deterministic IDs, Pi parsing, safe diagnostics, registration, options, serialization, and one fixture.

These pieces are prototypes, not compatibility anchors. Public API preservation is not a priority before the first real package release.

### Architectural gaps

| Area | Current state | Required change |
| --- | --- | --- |
| Adapter boundary | `ISourceAdapter.Parse` produces final IR and owns normalization policy | Source adapters decode to an internal source model; shared core owns linking, repair, bounds, filters, timestamps, validation, and diagnostics |
| IR provenance | IR records contain normalized ID/order/time but no native identity basis, source time, component key, offset, sequence, or identity kind | Add explicit source provenance and semantic component identity |
| Roles and record kinds | Primarily string roles and a limited polymorphic model | Typed record kind/role contracts with exact serializer mappings |
| Engine output typing | Every output adapter returns `string` | Typed adapters plus direct UTF-8 JSON writing and a safe non-generic registry bridge |
| Registration | Runtime dictionaries only | Keep explicit registration, add generated/default registry, avoid scanning/reflection |
| Namespace/package | Generic `Trajectory` naming | Move to `Hypabolic.Trajectory` package and namespace before publication |

### Behavioural gaps

| Capability | Current state | Parity requirement |
| --- | --- | --- |
| Pi | Broad permissive parser with source-specific normalization embedded in one large adapter | Exact pi-coding-agent decoding fixtures plus shared normalization semantics |
| Claude Code | Enum value only | Full decoder, listing, identity, diagnostics, and fixture parity |
| Codex | Enum value only | Full decoder, true UTF-8 byte offsets, partial chunks, group requirement/conflict, listing |
| Letta Code | Enum value only | Full client transcript decoder and row/native identity rules |
| OpenClaw | Enum value only | Full wrapper/message decoder and byte fallback identity |
| OpenHands | Enum value only | Array/envelope event decoding and event identity |
| Hermes | Enum value only | Array/envelope decoding and SQLite listing/export support |
| Deep Agents | Enum value only | Optional SQLite/checkpoint package and exact checkpoint reduction |
| Tool linking | Implemented only inside Pi parsing and dependent on local record flow | Shared pre-pass, duplicate handling, reverse-arrival linking, partial orphan policy |
| Timestamps | Source values may remain null | Exact preservation/interpolation/synthesis with stable diagnostics |
| Validation | No equivalent normalized schema validator | Whole/partial invariant validation and output-schema validation |
| Bounds | Tool arguments use `MaxBytes` and source-specific truncation; defaults are not resolved | Unicode code-point limits, valid JSON object preservation, exact markers/defaults |
| Filters | Empty marker record | `toolResults: include|omit` with resolved defaults |
| Partial mode | Context is accepted but not implemented to upstream semantics | Relax role invariants, retain cross-chunk results, offset-controlled meta emission |
| Byte offsets | Pi adapter treats candidate ordinal as an offset | Track actual UTF-8 byte positions while decoding JSONL |
| Diagnostics | Free-form string codes, severity, line/path shape | Upstream-compatible code/message/line/index/count shape plus typed fatal errors |
| Canonical identity | Absent | Full stable source identity, source ordering, component keys, hashes, flattened fields |
| Deterministic JSON | Limited argument sorting helper | Dedicated canonical JSON writer for all hash-bearing contracts |

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