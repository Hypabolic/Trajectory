# Authoring sources and outputs

This guide is the cross-runtime checklist for extending Trajectory. Language
specific registration APIs differ; the **contracts and conformance steps do
not**.

Related:

- [.NET extension seams](../dotnet/docs/adapter-authoring.md)
- [Normative normalization](../contracts/spec/normalization.md)
- [Identity](../contracts/spec/identity.md)
- [Listing](../contracts/spec/listing.md)
- [Conformance case authoring](../conformance/README.md)
- [Contributing](contributing.md)
- [AHP source design](ahp-source-spec.md) — Agent Host Protocol Shape A offline
  snapshot source (`ahp`); action-log reduce, listing, and live host are later
  phases. Phase status: [ahp-ingest-status.md](ahp-ingest-status.md). AHP is
  in-tree / next package version after published `0.1.0`.

## Architecture reminder

```text
native bytes → source decoder → shared normalizer → private IR → output adapter
```

| Layer | Owns | Must not |
| --- | --- | --- |
| Source adapter | Container parse, native IDs, source timestamps, execution metadata exposed by the agent | Shared bounds/linking/hash policy |
| Normalizer | Tool linking, bounds, timestamps policy, diagnostics, identity | Source-specific file formats |
| Output adapter | Projection to a versioned schema | Mutating IR or re-decoding the source |

## Adding a source (all runtimes)

### 1. Specify behaviour first

1. Document accepted container shapes and identity rules in
   `contracts/spec/` (or extend existing rules if they already apply).
2. Add the source name to the vocabulary in:
   - `contracts/compatibility.json` → `implemented.sources` (only when complete);
   - `contracts/schemas/conformance-case-v1.schema.json` / capability enums if needed;
   - `contracts/spec/listing.md` when there is a local store.
3. Do **not** advertise the source in runtime `runtime-capabilities.json` until
   shared cases pass on every runtime that claims it.

### 2. Author shared conformance

Under `conformance/cases/<source>/<case-name>/`:

| File | Purpose |
| --- | --- |
| `case.json` | Operations, mode, context, options, expected success/error |
| `input.jsonl` or `input.json` | Sanitized native transcript |
| `expected.*.json` | Reviewed goldens per operation |
| listing: store under `conformance/stores/` | Declarative tree for list tests |

Minimum useful case set for a new source:

- happy path with tools (or messages if the source has no tools);
- at least one cleanup / diagnostics path (malformed line, noise, orphan);
- fatal validation if the source has strict whole-transcript rules;
- listing + pagination when a filesystem store exists;
- partial/chunk identity if the source supports append-only chunks.

Privacy: synthetic IDs and paths only. See
[release-readiness privacy rules](release-readiness.md#privacy-and-fixture-sanitization).

Generate candidates with a trusted local build, **review by hand**, then check
in expected files. Never regenerate-and-accept goldens in CI.

### 3. Implement decoders independently

Implement in each runtime without sharing IR types across languages:

| Runtime | Where to look |
| --- | --- |
| .NET | `dotnet/src/Trajectory/Adapters/<Source>/`, register in generated default registry |
| TypeScript | `typescript/packages/trajectory/src/internal.ts` (+ listing in `trajectory-node`) |
| Rust | `rust/crates/hypabolic-trajectory/src/normalize.rs` (+ `listing.rs`) |
| Python | `python/src/hypabolic_trajectory/sources/<source>.py` (+ `listing/<source>.py`; register on package import) |

Checklist for decode quality:

- [ ] UTF-8 **byte** offsets for location identity (not char or UTF-16 indices)
- [ ] Native IDs preferred; documented fallback identity when missing
- [ ] Tool call/result linking inputs for the shared pre-pass
- [ ] Provider/model/usage/timing retained when source-native (never invented)
- [ ] Wire source name is stable (`"openclaw"`, never confused with `"pi"`)
- [ ] Partial mode and group-id rules if the source needs them (see Codex)

### 4. Listing (when applicable)

- Default root discovery documented and overridable.
- Missing store → empty page (not a fatal error).
- Newest-first ordering; opaque cursor pagination per listing contract.
- Conformance must use an explicit temporary root from `stores/`, never the
  developer home directory.

Hermes is an example of **normalize in core, SQLite listing optional**: cores
stay free of SQLite dependencies.

### 5. Wire runners and capabilities

- Conformance runners accept the new source name.
- Unit/parity tests under the runtime as needed.
- Sample CLIs can list/normalize the source if defaults exist.
- Bump capability slice / docs only when the full multi-runtime bar is met.

### 6. Verify

```bash
python3 conformance/verify.py --repository-root . --source <source> -- <runner>
# then full suite on .NET, TypeScript, Rust, and Python runners
# Python example:
#   env PYTHONPATH=python/src:python/tools python -m trajectory_conformance
```

Update `conformance/identity-baseline.sha256` only when intentionally adding
identity-bearing goldens (reviewed).

## Adding an output projection

### 1. Define the contract

- Schema ID and version (e.g. `my-format-v1`).
- JSON Schema under `contracts/schemas/` when the format is structured JSON.
- Document field nullability, ordering, truncation, and privacy defaults.
- Decide identity relationship: does it participate in identity-bearing
  hashes, or is it a non-identity convenience projection?

### 2. Conformance

Add operations to existing cases (prefer Pi `unicode-boundaries` style for
multi-output) or new cases:

- `expected.<format>.json` or `.jsonl` with the correct comparison mode
  (`json-exact`, `byte-exact`, `jsonl-exact`).

### 3. Implement projectors

| Runtime | Pattern |
| --- | --- |
| .NET | `OutputSchemaAdapter<T>` + register on `TrajectoryEngine` |
| TypeScript | Projector function + `TrajectoryEngine.addOutputAdapter` / exports |
| Rust | `OutputAdapter` / `project_*` + `schema_ids` constant |
| Python | `project_*` free functions + `TrajectoryEngine.add_output_adapter` / root exports |

Rules:

- Side-effect free and deterministic.
- Never invent provider, model, usage, or timing.
- Prefer streaming/write APIs for large outputs where the ecosystem has them.
- Optional SDK dependencies (e.g. OpenTelemetry) stay out of core packages.

### 4. Advertise

Only after goldens pass on all claiming runtimes:

- `contracts/compatibility.json` → `implemented.outputs` / `public_schemas`
- runtime capability manifests
- README output list
- package release notes

## .NET registration sketch

See the full examples in
[dotnet/docs/adapter-authoring.md](../dotnet/docs/adapter-authoring.md).

```csharp
var engine = TrajectoryEngine.CreateDefault()
    .AddSourceAdapter(new MySourceAdapter())
    .AddOutputAdapter(new MyOutputAdapter());
```

## TypeScript sketch

```ts
import {
  TrajectoryEngine,
  normalizeToIR,
  type TrajectoryIR,
} from "@hypabolic/trajectory";

const engine = TrajectoryEngine.createDefault().addOutputAdapter(
  "my-format-v1",
  (ir: TrajectoryIR) => projectMyFormat(ir),
);

const ir = normalizeToIR({ source: "pi", transcriptBytes });
const out = engine.project(ir, "my-format-v1");
```

Built-in sources are selected by `request.source`. Custom sources today require
a core change to the normalize switch (or a future pluggable registry)—prefer
upstreaming shared sources with conformance rather than one-off forks.

## Rust sketch

```rust
use hypabolic_trajectory::{
    normalize_pi, project_hypabolic, NormalizeRequest, OutputAdapter,
};

// Built-in path
let ir = normalize_pi(NormalizeRequest {
    transcript: bytes,
    ..Default::default()
})?;
let hypabolic = project_hypabolic(&ir)?;

// Custom output: implement OutputAdapter and call project methods, or map IR
// fields explicitly in application code for one-off formats.
```

Custom sources implement `SourceAdapter` and are invoked from application code
or added to the public normalize surface when promoted to a first-class source.

## Python sketch

```python
from hypabolic_trajectory import (
    NormalizeRequest,
    TrajectoryEngine,
    TrajectorySource,
    normalize_to_ir,
    project_hypabolic,
    serialize_projection,
)

request = NormalizeRequest(
    source=TrajectorySource.PI,
    transcript=transcript_bytes,
)
ir = normalize_to_ir(request)
hypabolic = project_hypabolic(ir)
wire = serialize_projection(hypabolic)

# Engine tip matrix (pure otel included); free functions ignore engine mutations.
engine = TrajectoryEngine.create_default()
out = engine.project(ir, "hypabolic-trajectory-v1")
engine.add_output_adapter("my-format-v1", project_my_format)  # duplicate → ValueError
```

Built-in sources self-register on package import. Only root / `ir` / `otel`
`__all__` names are semver-stable — see [`python/README.md`](../python/README.md)
for the OTEL import matrix, dual timestamps, identity formulas, and filters.

## Anti-patterns

- Advertising a source in one runtime only without shared cases.
- Putting shared normalization policy inside a single source adapter.
- Inferring model/provider from content or filenames.
- Logging or diagnostic messages that embed tool arguments or prompts.
- Copying goldens from another source case without re-validating identity.
- Checking in real user session paths or secrets “temporarily”.

## Definition of done

A source or output is complete when:

1. Spec + schema vocabulary updated.
2. Shared conformance cases pass on **every** runtime that advertises it.
3. Capability manifests agree.
4. Sample CLIs / docs mention it if user-visible.
5. CI green including cross-runtime verify.
