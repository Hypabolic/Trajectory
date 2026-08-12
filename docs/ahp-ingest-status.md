# AHP ingest — Phase 0–1 implementation status

Packaging note for **repository tip (`main`)** after PR #25. Normative design:
[ahp-source-spec.md](ahp-source-spec.md). Wire contract:
[`contracts/spec/sources/ahp.md`](../contracts/spec/sources/ahp.md).

**Publication status:** Shipped **in-tree on `main`**. **Not** included in
published registry packages at **`0.1.0`**. Publishing AHP requires a **new**
synchronized package version / tag after `v0.1.0` (do not retag or republish
`0.1.0`). See [release-readiness.md](release-readiness.md) and
[CHANGELOG.md](../CHANGELOG.md) `## [Unreleased]`.

| Phase | Scope | Status |
| --- | --- | --- |
| **0 (AHP-0)** | Spec freeze, export schema, vendor pin, synthetic fixtures | **Shipped in-tree** |
| **1 (AHP-1)** | Shape A snapshot → IR on .NET / TypeScript / Rust / Python + conformance + CLI | **Shipped in-tree** |
| **Stream (LS-06)** | Successive Shape A snapshot streaming + provisional `activeTurn` | **In-tree on tip**; core `stream-ahp-snapshot` claimed (LS-12) |
| **Stream (LS-07)** | Shape B action-log reducer + `apply_ahp_actions` + serverSeq cursor | **In-tree on tip**; core `stream-ahp-action-log` claimed (LS-12) |
| **2+ remaining** | Export listing + real WebSocket host transport | **Not shipped** (optional client packages are **in-tree** and capability-claimed on package manifests only; fake-host tested) |

Protocol pin: **0.7.0** (`conformance/vendor/ahp/PROTOCOL_VERSION`). Wire source
name: **`ahp`**. Advertised on this tip in `contracts/compatibility.json` →
`implemented.sources` and runtime capability manifests (not in the published
`0.1.0` package capability surface).

---

## What shipped

### Contracts and fixtures (AHP-0)

- Normative source contract: `contracts/spec/sources/ahp.md`
- Trajectory export envelope: `contracts/schemas/ahp-export-v1.schema.json`
- Vendor pin + notes: `conformance/vendor/ahp/`
- Shared cases (Shape A `input.json` + reviewed goldens):
  - `conformance/cases/ahp/tool-calls`
  - `conformance/cases/ahp/multi-turn`
  - `conformance/cases/ahp/cancelled-turn`

### Runtime adapters (AHP-1)

| Runtime | Location | Notes |
| --- | --- | --- |
| .NET | `dotnet/src/Trajectory/Adapters/Ahp/` | `AhpJsonSourceAdapter`; listing stub returns empty |
| TypeScript | `typescript/packages/trajectory/src/internal.ts` (+ node listing stub) | Registered on engine / CLI |
| Rust | `rust/crates/hypabolic-trajectory/src/normalize.rs` (+ listing stub) | Registered on engine / CLI |
| Python | `python/src/hypabolic_trajectory/sources/ahp.py` (+ listing stub) | Registered on package import / engine |

Also: sample CLIs accept `--source ahp` and `show --path` for Shape A
snapshots; runners include `ahp` in multi-runtime conformance.

### Product surfaces

- Root README multi-source list + source table row for AHP
- `docs/adapter-authoring.md` points at the AHP design doc
- Diagnostics for version / container / mapping edge cases (see source contract)

---

## How to test

From repository root (AHP-filtered conformance is the primary gate):

```bash
# .NET unit + AHP parity
dotnet test dotnet/tests/Trajectory.Tests/Trajectory.Tests.csproj -c Release --filter Ahp

# .NET conformance (ahp only)
dotnet build dotnet/tests/Trajectory.Conformance/Trajectory.Conformance.csproj -c Release
python3 conformance/verify.py --repository-root . --source ahp -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll

# TypeScript
cd typescript && npm ci && npm run build && npm test
python3 conformance/verify.py --repository-root . --source ahp -- \
  node typescript/packages/trajectory-testing/dist/cli.js

# Rust
cargo test --manifest-path rust/Cargo.toml -p hypabolic-trajectory --locked
cargo build --manifest-path rust/Cargo.toml --release --bin trajectory-conformance
python3 conformance/verify.py --repository-root . --source ahp -- \
  rust/target/release/trajectory-conformance
```

Smoke CLI (any runtime):

```bash
# .NET example
dotnet run --project dotnet/samples/Trajectory.Cli -- show \
  --source ahp \
  --path conformance/cases/ahp/tool-calls/input.json
```

Release metadata gate (includes `ahp` in implemented sources):

```bash
python3 tools/validate_release_metadata.py --repository-root .
```

---

## Streaming (LS-06 / LS-07) — in-tree algorithm

Core pure APIs (no network):

- `apply_ahp_snapshot` — successive Shape A; provisional `activeTurn`;
  snapshot-revision cursor
- `apply_ahp_actions` — Shape B reducer; serverSeq cursor; gaps → resync

Docs: [`ahp-action-streaming.md`](ahp-action-streaming.md).  
Fixtures: `conformance/cases/streaming/ahp-snapshot-*`,
`conformance/cases/streaming/ahp-action-*`,
`conformance/cases/streaming/provisional-to-stable`.

Core capabilities `stream-ahp-snapshot` / `stream-ahp-action-log` are claimed
(LS-12) after the shared matrix is green on all four runtimes. Optional AHP
**client** packages advertise `stream-ahp-client` only on those packages.

## Known gaps vs listing / live host

| Area | Gap |
| --- | --- |
| **Live host client** | Optional packages (LS-10): transport-only clients with auth callback + fake-host tests; **not** in core; `stream-ahp-client` on optional package manifests only |
| **Listing** | Phase 3 — no export-directory discoverer; listers are empty stubs |
| **Multi-chat unpack** | Combined session export helper deferred (one chat per normalize) |
| **Official reducer parity** | Trajectory ships a **minimal complete** chat reducer subset, not a vendored copy of every AHP client fixture |
| **Extra batch goldens** | Spec lists `ahp/reasoning`, `ahp/list-export` — not all landed |
| **Real-world export path** | Synthetic fixtures only; capture path from VS Code / AHPX still doc-only |
| **Capability advertising** | Core stream AHP caps claimed (LS-12); optional client cap only on AHP packages |

Offline Shape A normalize remains the batch path. Streaming is layered on top
via stream apply APIs; batch goldens stay green.