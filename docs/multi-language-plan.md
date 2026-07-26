# Rust and TypeScript implementation plan

Status: accepted; ML1–ML6 implemented, ML7 next

Planning baseline:

- Trajectory repository: `8644ff31c62ee398df8126aaed20961d0d0830f8`
- pinned Letta compatibility reference: `letta-ai/trajectory@f165ecf0af35da40512a288c4380a36b3102403c`
- pinned upstream package version: `0.2.0`

Research references:

- [pinned upstream TypeScript package](https://github.com/letta-ai/trajectory/blob/f165ecf0af35da40512a288c4380a36b3102403c/package.json)
- [pinned upstream internal decode boundary](https://github.com/letta-ai/trajectory/blob/f165ecf0af35da40512a288c4380a36b3102403c/src/internal.ts)
- [Node.js release schedule](https://nodejs.org/en/about/previous-releases)
- [Rust 1.85 and Rust 2024 announcement](https://blog.rust-lang.org/2025/02/20/Rust-1.85.0/)

## Decision

Trajectory should become one product with three native implementations:

| Ecosystem | Package | Implementation strategy |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | Existing implementation and behavioural baseline |
| Rust | `hypabolic-trajectory` | Independent idiomatic Rust implementation |
| TypeScript | `@hypabolic/trajectory` | Independent implementation written from the Hypabolic specification and conformance suite |

The Rust and TypeScript packages must not call the .NET package through a
subprocess, FFI, WebAssembly, or a hosted service. Consumers should get the
normal installation, deployment, debugging, and extension model of their
ecosystem.

The implementations do not need identical public APIs. They do need identical
observable behaviour for the same operation, source bytes, options, contract
version, and capability set.

The cross-language authority is:

1. versioned public wire contracts and behavioural specifications;
2. shared sanitized source fixtures;
3. shared expected outputs and failure vectors;
4. the pinned upstream Letta reference where the Hypabolic specification has
   not intentionally diverged.

The internal IR remains implementation-private. Making it a public interchange
format would couple all implementations to one language's object model and
turn internal refactoring into a wire-format migration.

## Why TypeScript is a fresh implementation

The Letta repository inspired the original product and remains a useful
compatibility oracle, but `@hypabolic/trajectory` must be a fresh Hypabolic
implementation. Its design and code are derived from this repository's
language-neutral specifications, schemas, conformance cases, and architecture.

Do not vendor, fork, copy, translate, or incrementally refactor the upstream
TypeScript source. Do not preserve its internal module layout or treat its
implementation choices as architectural requirements.

The TypeScript processing architecture is implemented independently:

```text
source bytes
  -> source decoder
  -> shared normalizer
  -> TypeScript IR
  -> Letta / canonical / Hypabolic / OpenAI / JSONL output adapters
  -> optional OpenTelemetry emission
```

The pinned upstream release may be executed as a black-box compatibility
oracle for Letta outputs and historical behaviour. If its behaviour exposes a
case not covered by the Hypabolic specification, first add a normative rule and
sanitized conformance vector, then implement that rule independently in every
runtime. Upstream source code is not the specification.

Convenience functions compatible with the observable upstream API may be
provided where useful, but they are facades over the independently designed
Hypabolic engine and adapter registry.

## Why Rust is independent

Rust is valuable as a low-overhead library and CLI building block, not as a
different launcher for the .NET implementation. A native implementation gives:

- direct use in Rust ingestion, indexing, telemetry, and agent tooling;
- predictable memory use and streaming over transcript bytes;
- static binaries for conformance tools and future command-line use;
- normal Rust traits, errors, iterators, and package composition;
- no managed runtime or Node deployment dependency.

The Rust implementation should port behaviour from the specification and
conformance cases, consulting both the pinned TypeScript reference and the
.NET implementation when a case is underspecified. It should not be a
line-by-line transliteration of either.

## Recommended roadmap change

Do not continue adding source adapters only to .NET and defer the other
languages until the end. That would create three independently moving
normalizers and make every later parity defect harder to classify.

The recommended order is:

1. establish the shared conformance substrate;
2. bring TypeScript and Rust to the currently implemented .NET source baseline
   of Pi, Claude Code, and Codex;
3. add future sources across all three implementations, one source family at
   a time.

Accordingly, the current .NET Slice 5 (Letta Code) should become the first
multi-language source slice after baseline convergence rather than landing as
another .NET-only capability.

## Repository shape

Restructure once, before more implementation accumulates:

```text
contracts/
  compatibility.json
  schemas/
  spec/
    canonical-json.md
    diagnostics.md
    identity.md
    listing.md
    normalization.md
    timestamps.md
conformance/
  protocol/
  cases/
    pi/
    claude-code/
    codex/
  stores/
  README.md
dotnet/
  src/
  tests/
  benchmarks/
rust/
  Cargo.toml
  crates/
typescript/
  package.json
  packages/
docs/
```

Move the existing solution, sources, tests, and benchmarks under `dotnet/` in
the foundation slice. Git preserves file history, and doing the move before
the Rust and TypeScript implementations exist avoids permanently privileging
one runtime in root-level paths.

Public JSON Schemas, source-independent golden outputs, and source fixtures
move to `contracts/` and `conformance/`. Implementation-specific unit fixtures
remain under the relevant implementation. Do not use symlinks: they make
Windows checkouts, package archives, and fixture discovery less reliable.

Root documentation describes the product and compatibility policy.
Runtime-specific API, build, and adapter-authoring documentation lives with
each implementation.

## Shared behavioural contract

### Normative surfaces

The shared contract covers:

- accepted source container and record shapes;
- transcript validation and partial-mode behaviour;
- source group resolution;
- source identity, component keys, and ordering;
- tool call/result linking, duplicate IDs, and orphan policy;
- noise filtering;
- Unicode-code-point bounds and marker-inclusive truncation;
- timestamp preservation, interpolation, synthesis, and formatting;
- diagnostic and fatal-error codes plus content-safety rules;
- canonical JSON and SHA-256 input bytes;
- all public output schemas;
- listing discovery, ordering, locators, pagination, and error behaviour;
- deterministic OpenTelemetry span projection before SDK emission.

Wire-format field names, nullability, omission, array order, timestamp text,
JSON escaping, and hash input are compatibility behaviour, not serializer
preferences.

### Cross-language traps that require explicit vectors

The existing rules must be written down more precisely than any one runtime's
standard library:

| Area | Required contract |
| --- | --- |
| JSON object order | Sort by UTF-16 code units to preserve the JavaScript/.NET ordinal baseline; Rust must implement the same comparator |
| JSON escaping | Define the exact compact UTF-8 representation used for hashes and byte-equality outputs |
| Unicode bounds | Count Unicode scalar values/code points, never UTF-16 units or UTF-8 bytes |
| Byte anchors | Count absolute UTF-8 byte offsets before decoding into runtime strings |
| Integers | Define the accepted range and lossless parsing rules for sequences, offsets, timestamps, and token counts |
| Timestamps | Define parsing, UTC conversion, fractional-second precision, interpolation, and output formatting |
| Newlines | Define JSON and JSONL final-newline policy |
| Paths | Normalize only the fields the contract declares portable; retain native locators where required |

Do not silently adopt RFC 8785/JCS for canonical JSON. The current contract
sorts keys and writes compact JSON but is not documented as full JCS.
Changing algorithms would change canonical IDs and hashes. Any future move to
JCS requires a new canonical schema/identity version.

### Contract versioning

Add `contracts/compatibility.json` containing:

- the pinned upstream repository and commit;
- normalizer contract version;
- each public schema ID and version;
- diagnostic/error contract version;
- conformance protocol version;
- required and optional capabilities.

During pre-1.0 development, all three packages should use a synchronized
release version sourced from one root version file. This matters because the
normalizer version appears in canonical output. NuGet, npm, and crates.io
artifacts are published from the same release commit and tag.

After 1.0, implementation package versions may diverge only if canonical
output records the language-neutral normalizer contract version rather than an
ecosystem package version.

## Conformance system

### Case format

Each case contains:

```text
conformance/cases/<source>/<case>/
  case.json
  input.jsonl              # or the source's native container
  expected.letta.json
  expected.canonical.json
  expected.hypabolic.json
  expected.openai.json     # when applicable
  expected.minimal.jsonl   # when applicable
  expected.otel.json       # when applicable
```

`case.json` declares:

- operation and source;
- transcript filename;
- source context, bounds, filters, and projection options;
- whole versus partial mode;
- expected success, diagnostics, or fatal error;
- comparison mode: byte-exact, JSON-exact, or contract-specific;
- capabilities required by the case.

Listing cases use a declarative temporary-store layout under
`conformance/stores/` rather than a developer's real home directory.

### Runner protocol

Every implementation supplies a private `trajectory-conformance` executable.
It accepts a versioned request naming a case and operation and writes only the
result envelope to stdout. Logs go to stderr.

The protocol supports:

- normalize to Letta trajectory;
- normalize to Letta canonical;
- normalize to Hypabolic;
- project OpenAI and minimal JSONL;
- project deterministic OpenTelemetry spans;
- list trajectories from an explicit root;
- report typed fatal failures.

The executable is test infrastructure, not a public cross-runtime RPC API.

### CI gates

CI has two layers:

1. each implementation runs its unit, property, integration, packaging, and
   platform tests;
2. the cross-language job runs every shared case through all implementations,
   compares each result with the checked-in golden, and then compares the
   implementation results directly.

The cross-language job runs on Ubuntu for fast classification. Runtime-specific
tests then cover:

- .NET: existing target frameworks and Native AOT platforms;
- Rust: MSRV plus stable on Linux, macOS, and Windows;
- TypeScript: Node.js 22 and 24 on Linux, macOS, and Windows, with a Node 26
  smoke job while it remains the Current release.

Fixture updates require an explicit compatibility-change label or manifest
version change. CI must not regenerate and accept goldens in the same command.

## TypeScript design

### Packages

Use a small workspace:

| Package | Responsibility |
| --- | --- |
| `@hypabolic/trajectory` | IR, normalization, transcript adapters, identity-bearing outputs, OpenAI and JSONL projections |
| `@hypabolic/trajectory-node` | local-store listing and SQLite/checkpoint integrations |
| `@hypabolic/trajectory-otel` | deterministic GenAI span projection and OpenTelemetry SDK emission |
| `@hypabolic/trajectory-testing` | conformance and adapter-authoring helpers |

`@hypabolic/trajectory` is ESM-first and supports Node.js 22 or newer. Keep
Node filesystem, SQLite, and OpenTelemetry dependencies out of the core package.
Publish explicit package exports and declarations; do not expose build-layout
paths.

The normalization API should accept `Uint8Array` as the primary exact-input
form plus a string convenience overload. UTF-8 byte anchors must be calculated
from bytes, not JavaScript string indices.

### Extension model

Use discriminated unions for decoded events and IR records. Register adapters
explicitly:

- source adapters decode bytes into an internal `DecodedSession`;
- one shared normalizer owns common policy;
- typed output adapters project the TypeScript IR;
- a string schema registry provides dynamic projection where needed.

Preserve upstream convenience functions such as `normalizeTranscript`,
`normalizeToCanonical`, and `listTrajectories` as facades over the engine.

Do not expose the decoded session or a serialized IR as stable public
contracts in the first release.

### Implementation independence

The first TypeScript slice must:

- be authored from Hypabolic specifications and conformance cases;
- contain no copied, translated, vendored, or forked upstream implementation code;
- use Hypabolic's ports-and-adapters boundaries rather than upstream module structure;
- record the pinned upstream version only as a black-box compatibility reference;
- provide a repeatable differential comparison process that does not make
  upstream source code a build or runtime dependency;
- resolve ambiguous behaviour by improving the shared specification before
  implementation.

## Rust design

### Crates

Use a Cargo workspace:

| Crate | Responsibility |
| --- | --- |
| `hypabolic-trajectory` | IR, normalization, transcript adapters, core output adapters, listing abstractions |
| `hypabolic-trajectory-sqlite` | Hermes and Deep Agents SQLite/checkpoint access |
| `hypabolic-trajectory-otel` | deterministic span projection and OpenTelemetry emission |
| `hypabolic-trajectory-testing` | fixture and adapter contract helpers |
| `trajectory-conformance` | unpublished conformance runner |

Use Rust 2024 with an initial MSRV of 1.85, the version that stabilized the
edition. The core crate forbids unsafe code unless a later, measured
optimization has a separately reviewed justification.

Keep optional integrations in separate crates rather than accumulating Cargo
feature combinations in the core crate.

### API direction

Prefer ecosystem-native traits:

```rust
pub trait SourceAdapter {
    fn source(&self) -> TrajectorySource;
    fn decode(
        &self,
        input: &[u8],
        context: &SourceContext,
    ) -> Result<DecodedSession, TrajectoryError>;
}

pub trait OutputAdapter {
    type Output;

    fn schema_id(&self) -> &'static str;
    fn project(&self, ir: &Trajectory) -> Result<Self::Output, TrajectoryError>;
}
```

Provide typed projection for normal Rust use and a JSON-oriented registry
bridge for dynamic schema IDs. Use enums for record/diagnostic families and
structured errors rather than stringly typed maps.

Filesystem listing should remain synchronous in the core crate. Introducing an
async runtime solely for directory enumeration would impose the wrong
dependency boundary; callers can schedule blocking work according to their
runtime. SQLite integrations may expose async APIs in their own crate if the
selected driver justifies it.

Make byte slices the primary normalization input. Streaming parsers may be
introduced after parity is established, but streaming must preserve the same
absolute byte anchors and diagnostics as whole-buffer normalization.

## OpenTelemetry parity

The portable contract is `otel-genai-spans-v1`, a deterministic data
projection. Each language maps that projection to its ecosystem's OpenTelemetry
SDK in an optional package.

Cross-language conformance compares the deterministic projection. SDK
integration tests verify names, attributes, parentage, timestamps, status, and
privacy mode, but do not require byte equality between OTLP encoders.

No implementation may infer provider, model, usage, finish reason, or timing
from prose or model-name heuristics. Missing source metadata remains absent.

## Deep Agents and SQLite

Deep Agents checkpoint compatibility is an integration boundary, not a reason
to contaminate each core package.

The existing upstream investigation found that the Python-produced checkpoint
serializer is not automatically interoperable with the official JavaScript
SQLite saver. Each ecosystem package must validate against the same
Python-generated fixture and reduction semantics.

If exact native decoding is not demonstrably correct, use an isolated optional
official-Python bridge behind the checkpoint-reader port. Do not claim parity
from partial MessagePack/SQLite decoding.

## Vertical delivery slices

### ML1 — Shared contracts and repository foundation

Outcome: the existing .NET implementation runs unchanged against shared,
language-neutral conformance assets.

Work:

- restructure implementation directories;
- extract schemas, fixtures, goldens, and normative rules;
- define `compatibility.json`, the case manifest, and runner protocol;
- implement the .NET conformance runner;
- add canonical JSON, Unicode, byte-offset, timestamp, diagnostics, and failure
  edge vectors;
- update root branding from Trajectory.NET to Trajectory, with .NET documented
  as one implementation.

Acceptance:

- every currently implemented .NET Pi, Claude Code, Codex, output, listing, and
  OpenTelemetry test still passes;
- the .NET runner passes all shared cases;
- moving assets changes no identity-bearing output byte;
- no generated golden is accepted implicitly.

### ML2 — TypeScript Pi vertical path

Outcome: `@hypabolic/trajectory` normalizes and projects Pi sessions through the
new architecture.

Work:

- scaffold the workspace and engine/registry from the shared specification;
- implement the Pi decoder and normalization policy independently, using the
  pinned upstream package only for black-box differential comparison;
- implement byte-oriented Pi decoding, shared normalization, identity, and
  Letta/canonical/Hypabolic projections;
- add Pi listing in the Node package;
- add upstream-compatible convenience functions.

Acceptance:

- shared Pi success, partial, chunking, diagnostics, bounds, and error cases
  match .NET and goldens;
- canonical IDs and hashes are byte-identical;
- package exports and declaration files pass an install-and-import smoke test.

### ML3 — Rust Pi vertical path

Outcome: `hypabolic-trajectory` provides the same complete Pi path natively.

Work:

- scaffold the Cargo workspace and public error/model surface;
- implement Pi decode, normalization, canonical JSON/identity, and the three
  identity-bearing outputs;
- implement Pi listing;
- add MSRV, stable, clippy, rustfmt, docs, and package smoke gates.

Acceptance:

- all shared Pi cases match TypeScript, .NET, and goldens;
- no .NET/Node/FFI runtime is required;
- the core crate has no SQLite or OpenTelemetry dependency;
- repeated normalization and projection are byte-identical.

### ML4 — TypeScript Claude Code and Codex baseline

Outcome: TypeScript reaches the currently implemented .NET source baseline.

Work:

- implement Claude Code and Codex decoders independently from the shared source specifications;
- preserve producer metadata and execution metadata needed by Hypabolic and
  OpenTelemetry outputs;
- implement explicit-root listing;
- add whole/partial/chunked/location-identity cases.

Acceptance:

- all current shared Claude Code and Codex cases pass;
- Codex group-required/conflict rules and byte anchors are identical;
- TypeScript advertises Pi, Claude Code, and Codex in its capability manifest.

### ML5 — Rust Claude Code baseline

Outcome: Rust supports Claude Code normalization and listing across all current
outputs.

Acceptance:

- mixed producer versions, sidechains, malformed rows, injected context, and
  native UUID identity match shared cases;
- listings are stable across Linux, macOS, and Windows test stores.

### ML6 — Rust Codex baseline

Outcome: Rust reaches the currently implemented .NET source baseline.

Acceptance:

- full and arbitrary append-only chunks preserve canonical identity;
- tool-search, custom tools, reasoning, calls, and results match shared cases;
- Rust advertises the same baseline capability set as .NET and TypeScript.

### ML7 — Output and distribution parity

Outcome: all three implementations ship the currently implemented output set.

Work:

- add OpenAI chat and minimal JSONL output adapters to Rust and TypeScript;
- add deterministic GenAI span projections and optional SDK packages;
- add direct-streaming/writer APIs appropriate to each ecosystem;
- establish synchronized package metadata, changelog, SBOM/provenance, and
  preview publishing dry runs;
- add representative benchmarks and output-size/allocation measurements.

Acceptance:

- deterministic projections match shared goldens;
- optional packages do not leak dependencies into core packages;
- package archives contain only intended public files and contract assets;
- install examples pass from empty consumer projects.

### ML8 — Letta Code across all runtimes

This replaces the current .NET-only Slice 5.

Acceptance retains the existing Slice 5 source requirements and adds:

- the same shared fixtures pass in all three implementations;
- native/source-line/row-position identity is identical;
- completed, failed, and unfinished tool calls have identical semantics;
- listing omits empty transcripts everywhere.

### ML9 — OpenClaw across all runtimes

This replaces the current .NET-only Slice 6. Share semantic block-decoding
rules where valid without conflating the Pi and OpenClaw source contracts.

### ML10 — OpenHands across all runtimes

This replaces the current .NET-only Slice 7. Both array and API-envelope inputs,
action/observation linkage, rejection, and error semantics must conform.

### ML11 — Hermes across all runtimes

This replaces the current .NET-only Slice 8. Transcript normalization remains
in core packages; SQLite discovery/export remains optional and independently
testable.

### ML12 — Deep Agents checkpoint integrations

This replaces the current .NET-only Slice 9. All three optional integration
packages validate against the same official Python-produced checkpoint fixture,
thread isolation, checkpoint selection, pending writes, and reducer semantics.

### ML13 — 1.0 parity and release hardening

Outcome: NuGet, npm, and crates.io packages describe and deliver the same
contract release.

Acceptance:

- every required conformance case passes for all advertised capabilities;
- package compatibility manifests agree;
- golden, property, fuzz, differential, and platform suites pass;
- privacy review confirms fixtures and diagnostics contain no source secrets;
- release artifacts are reproducible enough to trace to one commit and contract
  version;
- upgrade and intentional-difference documentation is complete.

## Capability and release policy

A package may be published before complete source parity, but it must contain a
machine-readable capability manifest and must not advertise unsupported
sources or outputs.

A source capability is complete only when that implementation supports:

- normalization and all applicable built-in outputs;
- typed diagnostics and fatal errors;
- partial/chunk semantics where the source permits them;
- listing where the source has a local store;
- shared conformance fixtures and edge vectors;
- package-level documentation.

The repository's product-level 1.0 requires the common required capability set
to pass in all three ecosystems.

## Performance policy

Parity comes before performance tuning. Establish representative fixtures for:

- small conversational sessions;
- tool-heavy sessions;
- large results near and above bounds;
- long JSONL transcripts;
- arbitrary Codex chunks;
- listing large stores.

Track throughput, peak memory, allocations, and output size per implementation.
Budgets are implementation-specific; output and diagnostic behaviour are not.
Do not introduce a shared native core solely to make benchmark numbers look
uniform.

## Principal risks

| Risk | Mitigation |
| --- | --- |
| Behaviour drifts across three implementations | Shared normative spec, direct differential CI, synchronized compatibility upgrades |
| A fresh TypeScript implementation drifts from established behaviour | Make the Hypabolic specification normative and use pinned upstream execution only for black-box differential evidence |
| Internal IR becomes an accidental wire contract | Test public outputs; keep any IR observation format private to conformance tooling |
| JSON/Unicode/runtime defaults change hashes | Explicit canonicalization spec and adversarial vectors |
| Repository restructuring disrupts active work | Perform it once in ML1 before new source slices |
| Optional integrations bloat core packages | Separate NuGet packages, crates, and npm packages |
| Deep Agents blobs are decoded approximately | Validate official fixtures or use an isolated official-Python bridge |
| Three-language source work reduces velocity | Converge cores first, then add each source once at the contract level and implement it across runtimes |

## Non-goals for the first multi-language release

- one generated codebase transpiled into three languages;
- one runtime implementation exposed through FFI or subprocess wrappers;
- identical language APIs;
- a stable serialized internal IR;
- browser/Wasm support;
- automatic upstream-main synchronization;
- cloud ingestion worker, tenancy, or storage responsibilities.

## Immediate next action

Implement ML7: output and distribution parity. Add the remaining OpenAI chat,
minimal JSONL, and deterministic GenAI span projections where absent; keep
OpenTelemetry SDK dependencies optional; add ecosystem-appropriate streaming
surfaces, synchronized package metadata, preview publishing dry runs,
provenance/SBOM evidence, and representative benchmarks without changing
identity-bearing contract bytes.
