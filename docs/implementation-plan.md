# Implementation plan

## Objective

Deliver a production-quality .NET implementation of Trajectory with behavioural parity to the pinned `letta-ai/trajectory` reference, exact Letta trajectory and canonical outputs, the additional `hypabolic-trajectory-v1` output, and an optional OpenTelemetry GenAI span projection.

The plan is intentionally organized as vertical slices. Each source slice ends with a usable public API path:

```text
source input
  -> source decode
  -> shared normalization
  -> Letta trajectory output
  -> Letta canonical output
  -> Hypabolic output
  -> source listing
  -> retained execution metadata for later telemetry projection
  -> parity fixtures and AOT-safe execution
```

Shared infrastructure is introduced through the first source that needs it rather than as an isolated framework-building phase.

This document remains the detailed .NET behavioural and source-acceptance baseline. The [Rust and TypeScript implementation plan](multi-language-plan.md) now governs cross-runtime sequencing, shared conformance, repository layout, and release policy.

## Delivery status

| Slice | Status |
| --- | --- |
| 1 — Correct Pi end-to-end path | Complete |
| 2 — Pi normalization and canonical parity | Complete |
| 3 — Claude Code end-to-end parity | Complete |
| 4 — Codex end-to-end parity | Complete |
| 5 — Letta Code end-to-end parity | Post-v1 backlog as multi-language ML8 |
| 6 — OpenClaw end-to-end parity | Complete as multi-language ML9 |
| 7 — OpenHands end-to-end parity | Post-v1 backlog as multi-language ML10 |
| 8 — Hermes end-to-end parity | Re-sequenced as v1 multi-language ML11 |
| 9 — Deep Agents optional SQLite package | Post-v1 backlog as multi-language ML12 |
| 10 — Additional projections, OpenTelemetry, and extension API | Complete |
| 11 — Differential parity, performance, and package readiness | Expanded by ML1–ML7 and ML13 |

ML1 — shared contracts and repository foundation — ML2, the independent
TypeScript Pi vertical path, and ML3, the native Rust Pi vertical path, are
complete. ML4 brought TypeScript to the current Pi, Claude Code, and Codex
baseline, ML5–ML6 brought Rust to the same source baseline, and ML7 completed
output and preview-distribution parity. ML9 OpenClaw, ML11 Hermes, and ML13
1.0 parity and release hardening are complete across all three runtimes.
Packages remain unpublished at synchronized `0.1.0`; see
[release-readiness.md](release-readiness.md). Letta Code (ML8), OpenHands
(ML10), and Deep Agents checkpoint integrations (ML12) are retained below as
post-v1 behavioural backlogs. Do not begin any of them as a .NET-only slice.

## Planning rules

- The pinned upstream commit is the behavioural reference. The pin moves only in an explicit compatibility change.
- Existing prototype APIs may be broken before the first package release.
- Every slice adds sanitized fixtures and golden outputs before or with implementation.
- Every completed source is covered by all three identity-bearing outputs.
- Source adapters decode; the normalization core owns common policy.
- Source adapters preserve source-exposed invocation metadata such as provider, model, response IDs, finish reasons, usage, and reliable timing boundaries even when no current compatibility output uses it.
- Listing support ships with each source rather than as a late cross-cutting phase.
- Core changes must keep trimming/AOT analyzers clean.
- Optional SQLite and OpenTelemetry integrations must not add runtime dependencies to the BCL-only core package.
- A slice is not complete with placeholders, skipped tests, synthetic-only happy paths, or unverified schema claims.

## Slice 1 — Correct Pi end-to-end path

### Outcome

A consumer can normalize a real Pi session and receive an exact Letta trajectory-v1 result or a valid Hypabolic trajectory-v1 result through the new architecture.

### Work

- Rename package/root namespace to `Hypabolic.Trajectory` before public release compatibility exists.
- Replace the console test harness with standard test projects.
- Introduce the internal `DecodedSession` / `DecodedEvent` boundary.
- Move shared message/tool normalization out of `PiJsonlSourceAdapter` into `TrajectoryNormalizer`.
- Add typed roles, record kinds, diagnostics, fatal error codes, and resolved configuration.
- Track actual UTF-8 JSONL line offsets, native IDs, source sequence, source time, component index, and component ordinals.
- Implement the Pi decoder against native pi-coding-agent session shapes.
- Replace the current custom “Letta” envelope with exact `letta-trajectory-v1` models and serialization.
- Implement the initial `hypabolic-trajectory-v1` models and projection.
- Add Pi local-store listing with default root, root override, metadata, and shared cursor pagination.
- Add source-generated JSON contexts for every introduced public model.
- Add a Native AOT smoke executable that normalizes a Pi fixture into both outputs.

### Acceptance criteria

- Pinned Pi fixtures produce the same trajectory-v1 records as the upstream reference.
- The first record is exact Letta meta and the serialized output is a bare record array.
- Hypabolic output validates against an initial checked-in JSON Schema.
- Tool calls/results link correctly for native IDs.
- Input offsets are UTF-8 byte offsets, not character or record ordinals.
- Missing Pi store returns an empty listing.
- Core project remains BCL-only and analyzer-clean.
- Existing prototype envelope is removed or explicitly renamed as an unsupported legacy experiment; it is not exposed as Letta v1.

## Slice 2 — Pi normalization and canonical parity

### Outcome

Pi is fully parity-complete across normalization policy, canonical identity, chunking, and all three outputs.

### Work

- Implement deterministic duplicate tool-call renaming and missing-ID synthesis.
- Resolve tool calls/results in a pre-pass so reversed arrival order is supported.
- Implement orphan and duplicate result policy for whole and partial transcripts.
- Implement known harness-noise filtering.
- Implement exact default bounds and option resolution.
- Implement Unicode-code-point argument/result limits, valid JSON-object argument reshaping, and marker-inclusive `head` / `head-tail` truncation.
- Implement `toolResults: include|omit` filters.
- Implement timestamp preservation, interpolation, deterministic synthesis, and diagnostics.
- Implement strict whole-transcript validation and partial-mode relaxations.
- Implement canonical JSON, source identity kinds, source order IDs, semantic component keys, and SHA-256 hashes.
- Add exact `letta-canonical-v1` models, result envelope, version constants, and JSON Schema validation.
- Make Hypabolic IDs/provenance/hashes use the same identity basis without embedding Letta `record_json`.
- Preserve Pi provider, API family, requested/response model, response ID, stop reason, token usage, cache usage, and reliable invocation timing as source-neutral execution metadata for later projections.

### Acceptance criteria

- Pi trajectory and canonical goldens match the pinned upstream output.
- Reruns produce identical identity/order/hash fields.
- Reordered source events preserve identity and tool linkage where upstream guarantees arrival-order independence.
- Prefix/appended and chunked inputs preserve existing record IDs.
- Offset-zero partial input emits meta; continuation chunks do not emit canonical meta.
- Diagnostics match upstream code and structural fields and contain no source content.
- Property tests cover truncation limits, valid argument JSON, duplicate IDs, chunk boundaries, and ordering.
- Pi invocation metadata round-trips through the IR without being synthesized from model-name or content heuristics.

## Slice 3 — Claude Code end-to-end parity

### Outcome

Claude Code transcript files can be listed and normalized into all built-in identity-bearing outputs.

### Work

- Decode top-level and subagent JSONL structures.
- Preserve user/assistant text, thinking/reasoning, tool use, and tool results.
- Drop documented transport/system/compaction/fallback records without losing semantic content.
- Handle injected context, sidechain records, malformed lines, content arrays, and source-native UUIDs.
- Extract session/group metadata, cwd, git branch, model, producer version, and timestamps.
- Use native record identity where available and absolute byte location fallback otherwise.
- Implement default Claude Code store discovery and newest-first listing.
- Add version-family fixtures rather than branching on every producer release.

### Acceptance criteria

- All sanitized Claude Code fixtures match upstream trajectory and canonical outputs.
- Id-less records remain stable under non-zero `baseByteOffset`.
- Injected/noise/sidechain diagnostics match the compatibility contract.
- Mixed producer-version sessions decode structurally without assuming one version per file.
- Listing excludes non-session files and returns stable IDs/paths/metadata.
- Hypabolic output retains producer version and provenance omitted by Letta v1.

## Slice 4 — Codex end-to-end parity

### Outcome

Codex rollout JSONL supports full transcripts and arbitrary append-only chunks with stable canonical identity.

### Work

- Decode session metadata, messages, reasoning, function calls/results, custom tools, web search, and tool-search events.
- Preserve real byte anchors for every location-identified source record.
- Resolve group ID from session metadata or caller context.
- Enforce canonical `source_group_required` and `source_group_conflict` rules.
- Apply `baseByteOffset` only to byte-anchored identities.
- Support reversed chunk arrival without using normalized timestamps as order identity.
- Implement Codex default store discovery and listing.

### Acceptance criteria

- Complete and partial Codex fixtures match upstream trajectory/canonical outputs.
- Canonical normalization without a group fails with the expected typed code.
- Conflicting detected/provided groups fail deterministically.
- Splitting the same bytes at different chunk boundaries produces the same canonical IDs.
- Tool-search and custom tool pairs survive normalization and link correctly.
- Hypabolic provenance exposes byte anchors and resolved group information.

## Slice 5 — Letta Code end-to-end parity

Post-v1 backlog. This slice is not required for v1 and, when scheduled, must be
implemented as multi-language ML8 rather than as a .NET-only capability.

### Outcome

Letta Code client reflection transcripts normalize and list correctly, including historical id-less rows and unfinished tools.

### Work

- Decode the client-side append-only `transcript.jsonl` contract only; explicitly reject unrelated backend/payload artifacts.
- Support user, assistant, reasoning, and tool-call rows.
- Emit linked call/result components for completed tools and call-only records for unfinished tools.
- Derive identity from `source_message_id`, then `source_line_id`, then row position.
- Preserve reasoning and assistant components sharing one source message with semantic component ordinals.
- Implement nested default store discovery and omit empty transcripts from listing.

### Acceptance criteria

- Historical and current sanitized fixtures match upstream outputs.
- Id-less rows have stable, collision-free row-position identities within the supplied transcript.
- Completed and unfinished tools match upstream record structure.
- Empty transcripts fail normalization and are omitted from listing.
- Hypabolic output retains the source identity basis and component grouping.

## Slice 6 — OpenClaw end-to-end parity

### Outcome

OpenClaw session files normalize through the shared Pi-family semantic model without conflating OpenClaw and Pi source contracts.

### Work

- Decode header and wrapper-row JSONL.
- Accept only semantic message rows.
- Decode assistant text/tool blocks and `toolResult` records.
- Handle malformed lines as recoverable diagnostics.
- Exclude delivery-mirror placeholder model values from model metadata while retaining semantic prose.
- Use wrapper IDs as native identity and byte offsets as fallback.
- Implement OpenClaw default store discovery and listing.
- Share low-level Pi-agent block decoding where behaviour is truly common, while keeping separate source adapters and fixtures.

### Acceptance criteria

- OpenClaw fixtures match upstream trajectory/canonical output.
- Source remains `openclaw`, never `pi`, in all outputs.
- Fallback byte identity is stable across chunks.
- Delivery-mirror and malformed-line cleanup matches documented behaviour.
- Listing returns only valid session transcript files.

## Slice 7 — OpenHands end-to-end parity

Post-v1 backlog. This slice is not required for v1 and, when scheduled, must be
implemented as multi-language ML10 rather than as a .NET-only capability.

### Outcome

OpenHands event exports normalize from either array or API envelope forms and list from local event directories.

### Work

- Decode message, action, observation, agent-error, and user-rejection events.
- Preserve native event IDs and source timestamps.
- Map actions to tool calls and observations/errors/rejections to linked results with correct error semantics.
- Support raw arrays and `{ "items": [...] }` envelopes.
- Define documented content fallback identity only where stable native/location identity is unavailable.
- Implement event-directory discovery and listing metadata.

### Acceptance criteria

- Both accepted input containers produce identical normalized semantics.
- Compatibility fixtures match upstream trajectory and canonical records.
- Action/observation links are stable independent of transport order where IDs allow it.
- Error and rejection records remain distinguishable in Hypabolic output without breaking Letta compatibility.
- Listing locators identify the directory/input required for the next normalization step.

## Slice 8 — Hermes end-to-end parity

### Outcome

Hermes session exports normalize from row arrays or session envelopes and local stores can be listed.

### Work

- Decode string and multimodal-sentinel content.
- Decode reasoning aliases and OpenAI-compatible tool-call representations.
- Support decoded arrays and JSON-string `tool_calls` columns.
- Handle Codex Responses `call_id` extras and simplified id-less flush shapes.
- Preserve epoch-second timestamps and session metadata.
- Implement Hermes SQLite session listing without moving transcript normalization into the SQLite package.
- Provide an explicit export/read port so store access and normalization remain separable.

### Acceptance criteria

- Sanitized message and tool fixtures match upstream outputs.
- Row-array and session-envelope inputs normalize equivalently.
- Sessions lacking an assistant turn fail strict validation as expected.
- Missing Hermes store lists as empty; unreadable configured store returns a typed error.
- Core normalization remains independent of the concrete SQLite provider.

## Slice 9 — Deep Agents optional SQLite package

Post-v1 backlog. This integration is not required for v1 and, when scheduled,
must be implemented as multi-language ML12 through optional ecosystem packages.

### Outcome

A consumer can list Deep Agents threads and normalize the latest selected LangGraph checkpoint without adding SQLite/checkpoint dependencies to the core package.

### Work

- Add `Hypabolic.Trajectory.Sqlite`.
- Implement store discovery, thread listing, checkpoint selection, namespace-aware group IDs, and pending-write reduction.
- Define `IDeepAgentsCheckpointReader` and a typed decoded checkpoint model consumed by the shared normalizer.
- Implement exact HumanMessage, AIMessage text/reasoning/tool calls, and ToolMessage decoding.
- Preserve LangGraph overwrite/reducer semantics and per-thread isolation.
- Validate the actual Python-produced checkpoint serialization used by Deep Agents.
- If exact managed decoding cannot be achieved safely, isolate an optional official-Python bridge behind the checkpoint reader port; do not contaminate the core package or pretend partial blob decoding is parity.

### Acceptance criteria

- Python-generated reference fixtures normalize to upstream-equivalent outputs.
- Multiple threads and namespaces remain isolated.
- Latest checkpoint selection and pending writes match the upstream implementation.
- Deep Agents listing returns thread IDs and the SQLite store locator.
- Core package dependency graph is unchanged.
- Package capability limitations are explicit and tested on Windows, Linux, and macOS.

## Slice 10 — Additional projections, OpenTelemetry, and extension API

### Outcome

The adapter model is proven beyond Letta/Hypabolic by shipping OpenAI, minimal JSONL, and OpenTelemetry GenAI projections together with a documented custom adapter path.

### Work

- Add `openai-chat-messages` projection with explicit reasoning/tool mapping policy.
- Add streaming `jsonl-minimal` projection.
- Add the optional `Hypabolic.Trajectory.OpenTelemetry` package.
- Add `otel-genai-spans-v1` as a deterministic, side-effect-free typed span-set projection.
- Pin an OpenTelemetry GenAI semantic-convention schema URL; the initial planning baseline is `https://opentelemetry.io/schemas/gen-ai/1.42.0`, subject to verification immediately before implementation.
- Map logical agent turns to `invoke_agent`, reliable model calls to GenAI inference operations, linked tool calls/results to `execute_tool`, and explicit workflow constructs to `invoke_workflow`.
- Preserve and project provider, model, response ID, finish reason, usage, conversation, agent, and timing metadata only when source-native values exist.
- Disable prompt, response, system-instruction, tool-definition, tool-argument, and tool-result content attributes by default; add explicit bounded/redacted content-capture options.
- Add conversion to official OTLP trace data and optional `ActivitySource` emission without making projection depend on an ambient OpenTelemetry pipeline.
- Use span links for causal relationships that cannot be represented honestly as one parent edge, including concurrent subagent activity.
- Finalize typed `IOutputSchemaAdapter<TOutput>` and non-generic registry bridge.
- Add direct `Utf8JsonWriter` / stream APIs to avoid intermediate strings.
- Add explicit builder registration for custom source and output adapters.
- Add generated default registry and schema ID constants.
- Publish an adapter-authoring guide and test kit.
- Publish the detailed mapping and privacy contract in `otel-genai-output.md`.

### Acceptance criteria

- No reflection or dynamic code is introduced.
- Custom adapters can be registered without modifying core source.
- Typed projection rejects output-type/schema mismatches clearly.
- Streaming output works under Native AOT.
- OpenAI and JSONL fixtures are deterministic and documented as the .NET implementation of Trajectory contracts rather than Letta parity claims.
- Pi and at least one second source produce deterministic OpenTelemetry agent/tool span goldens.
- OpenTelemetry span names, kinds, attributes, parentage, links, and schema URL pass tests pinned to the selected GenAI semantic-convention version.
- Model inference spans are omitted rather than assigned fabricated durations when reliable invocation timing is unavailable.
- Provider/model/usage values are never synthesized from model-name, content-length, or other heuristics.
- Content-bearing GenAI attributes are absent by default and explicit content capture remains bounded and content-safe.
- OTLP conversion uses official OpenTelemetry protobuf/SDK contracts and the optional package publishes and runs under Native AOT.
- `Hypabolic.Trajectory` remains BCL-only and its dependency graph is unchanged.

## Slice 11 — Differential parity, performance, and package readiness

### Outcome

The complete library is continuously verified against a pinned upstream version and is ready for versioned NuGet publication.

### Work

- Add a parity harness that runs sanitized fixtures through pinned TypeScript and .NET implementations.
- Compare normalized structures first and canonical serialized bytes/hashes where defined.
- Add privacy-safe corpus tooling that compares hashes and structural signatures without logging transcript content.
- Add benchmark coverage for small, medium, and large JSONL sessions, malformed-line recovery, canonical hashing, and projection.
- Profile and reduce large avoidable allocations; introduce pooled buffers/span parsing only where benchmarks justify it.
- Add CI across Ubuntu, macOS, and Windows for supported SDKs.
- Publish and execute Native AOT smoke applications.
- Add package metadata, Source Link, symbols, license, README, schema assets, and deterministic build settings.
- Document the pinned upstream commit, intentional differences, and compatibility-update procedure.

### Acceptance criteria

- All supported source fixtures pass differential parity.
- Letta schemas validate every golden output.
- Hypabolic schema validates every Hypabolic golden output.
- AOT smoke applications publish and run in CI.
- No trimming/AOT warnings are suppressed without a reviewed justification.
- Benchmarks and allocation baselines are checked in and reproducible.
- NuGet package contents contain the documented schemas and no unintended dependencies.
- The parity definition in `parity-baseline.md` is fully satisfied.

## Cross-slice test requirements

Every implementation slice must add or update:

- decoder unit tests;
- normalization-core tests for any new semantic shape;
- exact Letta trajectory golden files;
- exact Letta canonical golden files;
- Hypabolic golden files and schema validation;
- invocation/execution metadata tests when the source exposes provider, response, usage, agent, workflow, or timing information;
- content-safety tests for diagnostics;
- deterministic rerun tests;
- listing tests using temporary stores;
- AOT smoke coverage for the newly reachable types.

The following properties remain continuously enforced after Slice 2:

- truncation never exceeds the configured Unicode-code-point bound;
- tool arguments remain valid JSON objects;
- synthesized IDs are deterministic;
- duplicate source call IDs cannot collide after normalization;
- identity is independent of input arrival order when native/location anchors permit it;
- non-zero byte offsets do not affect ordinal anchors;
- content hashes exclude transport metadata and timestamps;
- source-native provider, model, response, usage, and timing metadata are preserved without heuristic synthesis;
- diagnostic strings never contain fixture secrets.

## Suggested issue structure

Use one tracking issue for the parity milestone and one issue per slice. The tracking issue should contain:

- pinned upstream commit and version;
- links to these architecture/parity/schema documents;
- slice checklist;
- latest completed slice and current slice;
- test counts and CI matrix after each closeout;
- explicit deferred work and compatibility risks.

Each slice issue should include its outcome, acceptance criteria, fixture sources, and closeout evidence. Implementation and planning closeout may use separate pull requests when the closeout materially updates later slices.
