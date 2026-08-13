# Live session streaming — implementation status

Packaging note for **repository tip** on `feature/live-session-streaming`
(LS-00 … LS-12). Normative product design:
[live-session-streaming.md](live-session-streaming.md). Delivery slices:
[live-session-streaming-plan.md](live-session-streaming-plan.md).

**Publication status:** **In-tree on tip.** **Not** included in published
registry packages at **`0.1.0`**. Checked-in `VERSION` is **`0.1.2`** (already
used for the Grok Build + AHP tip cut). Publishing stream engines and
optional I/O / AHP / Hermes packages requires a **new** synchronized
multi-registry version / tag **strictly after `0.1.2`**. **Do not retag
`v0.1.2`.** See [release-readiness.md](release-readiness.md) and
[CHANGELOG.md](../CHANGELOG.md) `## [Unreleased]`.

**Verdict:** Feature slices **LS-00 … LS-12 are complete on tip**. Shared
stream matrix is green on all four runtimes
(`stream_unsupported_skips: 0`). Core `stream-*` capabilities are claimed
honestly; optional package caps only on those packages. Remaining items below
are **post-LS-12** engineering / product follow-ons, not open plan slices.

---

## Shipped (in-tree on tip)

| Slice | Scope | Status |
| --- | --- | --- |
| **LS-00** | Product semantics freeze (spec + plan + architecture pointers) | **Shipped in-tree** |
| **LS-01** | Wire contracts + schemas (`trajectory-stream-v1`, cursor/delta/case) | **Shipped in-tree** |
| **LS-02** | Stream conformance protocol + fixture skeleton | **Shipped in-tree** |
| **LS-03** | Stream state & cursor primitives (×4 cores) | **Shipped in-tree** |
| **LS-04** | Snapshot apply + normalize + stable-id diff (×4) | **Shipped in-tree** |
| **LS-05** | JSONL `apply_append` for file sources (×4) | **Shipped in-tree** |
| **LS-06** | AHP Shape A `apply_ahp_snapshot` (×4) | **Shipped in-tree** |
| **LS-07** | AHP Shape B action-log reducer / `apply_ahp_actions` (×4) | **Shipped in-tree** |
| **LS-07h** | Hermes optional provider packages (×4) + core `apply_hermes_export` | **Shipped in-tree** (shared `hermes-provider-*` export apply; SQLite I/O still package-test-gated) |
| **LS-08** | Full stream matrix gate — 41 shared cases, goldens, oracle parity | **Shipped in-tree** |
| **LS-09** | Optional file I/O packages (poll/follow; ×4) | **Shipped in-tree** |
| **LS-10** | Optional AHP client packages (transport-only; fake-host; ×4) | **Shipped in-tree** |
| **LS-11** | Sample CLI `stream` / `ahp-stream` (×4) | **Shipped in-tree** |
| **LS-12** | Honest capability claims + release-gate docs | **Shipped in-tree** |

### Capability surface (claimed on tip)

**Core** (`contracts/compatibility.json` required + four
`runtime-capabilities.json`):

- `stream-core`, `stream-cursor-v1`, `stream-jsonl-framing`
- `stream-apply-snapshot`, `stream-apply-append`
- `stream-full-snapshot`, `stream-record-delta`, `stream-reset`
- `stream-provisional-records`, `stream-deterministic-replay`
- `stream-file-jsonl`, `stream-ahp-snapshot`, `stream-ahp-action-log`

**Optional packages only** (`package-capabilities.json`):

| Cap | Packages |
| --- | --- |
| `stream-file-io` | .NET `Trajectory.IO`, TS `@hypabolic/trajectory-node`, Rust `hypabolic-trajectory-io`, Python `[io]` |
| `stream-async-iterator` | same file-I/O packages where applicable |
| `stream-ahp-client` | .NET `Trajectory.Ahp`, TS `@hypabolic/trajectory-ahp`, Rust `hypabolic-trajectory-ahp`, Python `[ahp]` |
| `stream-hermes-provider` | .NET `Trajectory.Hermes`, TS `@hypabolic/trajectory-hermes`, Rust `hypabolic-trajectory-hermes`, Python `[hermes]` |

**Not claimed** (intentionally): `stream-file-watch`,
`stream-ahp-list-sessions`.

### Supporting docs shipped

| Doc | Role |
| --- | --- |
| [streaming-core-api.md](streaming-core-api.md) | Pure core apply surface |
| [streaming-file-sources.md](streaming-file-sources.md) | JSONL append sources |
| [streaming-file-io.md](streaming-file-io.md) | Optional path poll/follow |
| [ahp-action-streaming.md](ahp-action-streaming.md) | Shape B reducer |
| [ahp-client.md](ahp-client.md) | Optional AHP transport clients |
| [streaming-hermes-provider.md](streaming-hermes-provider.md) | Optional Hermes provider |
| [ahp-ingest-status.md](ahp-ingest-status.md) | AHP batch + stream notes |
| [release-readiness.md](release-readiness.md) | Stream matrix DoD + privacy checklist |

### Stream corpus

Shared authority: `conformance/cases/streaming/**` (**41** cases), including:

- Generic framing / cursor / reset / delta cases
- Per-source append sequences (`pi`, `claude-code`, `codex`, `openclaw`,
  `grok-build`)
- AHP snapshot + action-log + `action_equals_snapshot` / provisional cases
- Hermes core `apply_hermes_export`: `hermes-provider-append`,
  `hermes-provider-soft-delete`, `hermes-provider-invalidation`
- Oracle: append ≡ prefix re-normalize; AHP action ≡ Shape A where declared

---

## Remaining (post-LS-12; not open plan slices)

| Area | Notes |
| --- | --- |
| **Registry publish** | Wire optional stream packages on the **next synchronized tag after `0.1.2`** (NuGet IO/Ahp/Hermes, npm `trajectory-ahp` / `trajectory-hermes`, crates `io`/`ahp`/`hermes`, Python extras already in core wheel layout). **Do not retag `0.1.0` or `0.1.2`.** |
| **Hermes SQLite I/O** | Shared `hermes-provider-*` cases cover core `apply_hermes_export` (append, soft-delete, db-generation invalidation + reset). Optional `stream-hermes-provider` SQLite/query I/O remains **package-test-gated**. |
| **`stream-file-watch`** | Optional watch backends deferred; poll/follow shipped. Leave unclaimed until product-ready. |
| **`stream-ahp-list-sessions`** | AHP session listing on live client deferred. Leave unclaimed. |
| **Real AHP WebSocket host** | Clients are transport-injected; CI uses FakeAhpHost / `fake://`. Consumer supplies live transport. |
| **AHP Phase 2+ batch gaps** | Export-directory listing, multi-chat unpack — see [ahp-ingest-status.md](ahp-ingest-status.md). Orthogonal to core stream algorithm. |

---

## How to test

From repository root. Stream matrix (primary LS-08/LS-12 gate):

```bash
# .NET
dotnet build dotnet/tests/Trajectory.Conformance/Trajectory.Conformance.csproj -c Release
python3 conformance/verify.py --repository-root . --operation stream-sequence -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll

# TypeScript
cd typescript && npm ci && npm run build && cd ..
python3 conformance/verify.py --repository-root . --operation stream-sequence -- \
  node typescript/packages/trajectory-testing/dist/cli.js

# Rust
cargo build --manifest-path rust/Cargo.toml --release --bin trajectory-conformance
python3 conformance/verify.py --repository-root . --operation stream-sequence -- \
  rust/target/release/trajectory-conformance

# Python
python3 conformance/verify.py --repository-root . --operation stream-sequence -- \
  env PYTHONPATH=python/src:python/tools python3 -m trajectory_conformance
```

Expect **zero** `stream_unsupported_skips` and green stream-sequence on every
runtime.

Optional package / unit smoke (representative):

```bash
# .NET
dotnet test dotnet/tests/Trajectory.Tests/Trajectory.Tests.csproj -c Release --filter Stream

# TypeScript
cd typescript && npm test   # includes stream-core, stream-file-io, ahp-client, hermes, sample-cli-stream

# Rust
cargo test --manifest-path rust/Cargo.toml --locked

# Python
cd python && pytest -q
```

Schema / privacy tooling:

```bash
python3 tools/validate_streaming_schemas.py
python3 tools/validate_release_metadata.py
```

Full tip verify (batch + stream) remains the multi-runtime CI gate; see
[contributing.md](contributing.md) and [release-readiness.md](release-readiness.md).

---

## Definition of done (product §14) — tip check

| # | Requirement | Tip |
| --- | --- | --- |
| 1 | Stream contracts/schemas authoritative | Yes |
| 2 | Four cores: snapshot + append + AHP snapshot + action-log; shared goldens | Yes |
| 3 | Append ≡ prefix oracle on full corpus | Yes |
| 4 | Optional file I/O packages ×4 | Yes |
| 5 | Optional AHP clients ×4 (fake-host) | Yes |
| 6 | Hermes provider implemented or capability-gated unsupported | Yes (optional packages + package tests) |
| 7 | Sample CLIs file follow + AHP stream | Yes |
| 8 | Capability manifests match matrix | Yes |
| 9 | Privacy gates; no secrets in diagnostics/fixtures | Yes |
| 10 | Batch normalize/list still green | Yes (CI / tip verify) |

**Honest feature status:** **done on tip** for LS-00…LS-12. Remaining table
above is publish and follow-on product work, not incomplete plan slices.
