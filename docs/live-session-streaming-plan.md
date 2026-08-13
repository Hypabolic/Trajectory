# Live session streaming — delivery work plan

Status: **LS-00 … LS-12 complete on tip** (all slices landed; see
[live-session-streaming-status.md](live-session-streaming-status.md))  
Normative product/technical spec: [live-session-streaming.md](live-session-streaming.md)

This plan delivers the **complete** streaming feature (file JSONL + AHP +
optional Hermes provider + optional I/O/clients + all four runtimes). Slices are
ordered for semantic safety, not for a reduced MVP.

**Slice landing summary (tip of `feature/live-session-streaming`):**

| Slice | Status |
| --- | --- |
| LS-00 | **Done on tip** — locked product/technical docs |
| LS-01 | **Done on tip** — contracts + schemas |
| LS-02 | **Done on tip** — stream conformance protocol + corpus skeleton |
| LS-03 | **Done on tip** — stream state & cursor primitives (×4) |
| LS-04 | **Done on tip** — snapshot apply + diff engine (×4) |
| LS-05 | **Done on tip** — JSONL append apply (×4) |
| LS-06 | **Done on tip** — AHP Shape A snapshot streaming (×4) |
| LS-07 | **Done on tip** — AHP Shape B action-log reducer (×4) |
| LS-07h | **Done on tip** — Hermes optional provider (×4; shared export-apply cases; SQLite I/O package-test-gated) |
| LS-08 | **Done on tip** — full stream matrix gate (41 cases; skips: 0 ×4) |
| LS-09 | **Done on tip** — optional file I/O packages (×4) |
| LS-10 | **Done on tip** — optional AHP clients (×4; fake-host) |
| LS-11 | **Done on tip** — sample CLI `stream` / `ahp-stream` (×4) |
| LS-12 | **Done on tip** — honest capability claims + release-gate docs |

Post-LS-12 remaining work (publish wiring on a **new tag after `0.1.2`**,
Hermes SQLite I/O package tests, `stream-file-watch`,
`stream-ahp-list-sessions`) is **out of slice scope** — see status doc.

---

## Principles

1. **Contracts before capability claims.** First merge freezes wire semantics
   without advertising `stream-*` capabilities as implemented.
2. **All four runtimes for core algorithm slices.** No single-runtime oracle.
3. **Prefix re-normalize is the correctness oracle;** append is required and
   must match.
4. **Core stays pure** (no FS watchers, network, SQLite). Optional packages
   carry I/O.
5. **Existing batch goldens must remain green** throughout.

---

## Dependency graph

```text
LS-00 product freeze
  → LS-01 contracts & schemas          ← first recommended merge
  → LS-02 conformance protocol & corpus skeleton
  → LS-03 stream state & cursor primitives (×4)
  → LS-04 snapshot apply + diff engine (×4)
  → LS-05 append apply + JSONL sources (×4)
  → LS-06 AHP Shape A snapshot streaming (×4)
  → LS-07 AHP Shape B action-log reducer (×4)
  → LS-08 full stream matrix gate (×4)
  → LS-09 optional file I/O packages (×4)
  → LS-10 optional AHP clients (×4)
  → LS-11 sample CLIs
  → LS-12 capability claims & release gate
       ↘ LS-07h Hermes optional provider (may parallel LS-09–10 after LS-04)
```

---

## LS-00 — Product semantics freeze

**Status: done on tip** (design + plan + architecture pointers).

**Goal:** Land the locked design docs and architecture pointers (this doc +
spec). No runtime code required beyond doc links.

| Area | Deliverable |
| --- | --- |
| Docs | `docs/live-session-streaming.md`, this plan, architecture “Further reading” link |
| Decisions | Cursor families, snapshot+delta, package names, Hermes optional, AHP reducer in core |
| Tests | None |
| Capabilities | None claimed |

**Acceptance**

- Spec answers package boundary, cursor, provisional/final, reset default,
  non-goals, and source matrix without open product questions that block LS-01.
- Multi-agent framing review issues are incorporated (state vs cursor, Shape B
  in core, oracle path).

**Dependencies:** none.

---

## LS-01 — Streaming contracts and schemas (first code-adjacent merge)

**Status: done on tip** (`contracts/spec/streaming.md` + stream schemas +
vectors; no runtime claims until LS-12).

**Goal:** Normative wire contracts only.

| Area | Deliverable |
| --- | --- |
| Add | `contracts/spec/streaming.md` |
| Add | `contracts/schemas/trajectory-stream-v1.schema.json` |
| Add | `contracts/schemas/streaming-cursor-v1.schema.json` |
| Add | `contracts/schemas/streaming-delta-v1.schema.json` |
| Add | `contracts/schemas/streaming-case-v1.schema.json` |
| Modify | `compatibility-manifest-v1.schema.json` to *allow* stream capability names (not mark implemented) |
| Tests | Schema valid/invalid vectors for cursors, empty snapshot, reset, finalize, privacy-negative cases |
| Docs | Cross-links from architecture and adapter-authoring |

**Acceptance**

- A third-party consumer can implement snapshot/delta replay from the spec alone.
- Batch schemas and identity baseline unchanged.
- No runtime `runtime-capabilities.json` lists stream caps as true yet.
- No secrets/paths allowed by stream diagnostic schema examples.

**Dependencies:** LS-00.

---

## LS-02 — Shared conformance protocol and fixture skeleton

**Status: done on tip** (`stream-sequence` / `stream-replay` protocol, verify
hooks, scaffold cases under `conformance/cases/streaming/`; goldens completed
by LS-08).

**Goal:** Runner can execute multi-step stream cases on all four runtimes
(initially may `unsupported` until LS-04+).

| Area | Deliverable |
| --- | --- |
| Protocol | Stream ops on request/response schemas or stream-specific protocol alongside v1 |
| Runner | `conformance/verify.py` + four runners: ordered steps, double-invoke, oracle mode hooks |
| Cases | Scaffold under `conformance/cases/streaming/` with hand-authored inputs; goldens filled as implementations land |
| Docs | `conformance/README.md` stream authoring section |

**Minimum case ids (goldens completed by LS-08):**

Generic: `empty-prefix`, `append-one-line`, `unterminated-line-held`,
`duplicate-input-idempotent`, `utf8-byte-boundary`, `cross-chunk-tool-result`,
`provisional-to-stable`, `stable-to-final`, `record-replacement`,
`record-removal`, `diagnostic-add-remove`, `file-truncate-reset`,
`file-compaction-reset`, `cursor-conflict`, `source-group-conflict`,
`delta-ordering`, `snapshot-delta-equivalence`, `append-equals-prefix-oracle`.

**Acceptance**

- Verifier runs sequence fixtures without breaking existing normalize cases.
- Comparison modes from the spec are implemented or stubbed with clear errors.
- Fixture privacy rules enforced.

**Dependencies:** LS-01.

---

## LS-03 — Stream state and cursor primitives (all four cores)

**Status: done on tip** (pure cursor/state modules ×4; no I/O in core).

**Goal:** Pure create/validate/advance cursor + state containers; no source
decode yet beyond framing buffers.

| Runtime | Location |
| --- | --- |
| .NET | `dotnet/src/Trajectory/Streaming/` |
| TypeScript | `typescript/packages/trajectory/src/streaming/` |
| Rust | `rust/crates/hypabolic-trajectory/src/streaming.rs` (or module tree) |
| Python | `python/src/hypabolic_trajectory/streaming/` |

| Area | Deliverable |
| --- | --- |
| Code | Cursor types, generation, pending buffer, typed stream errors, Create/Apply shell |
| Tests | Cursor arithmetic, int64 bounds, duplicate replay, atomic failure, no I/O |
| Docs | Package READMEs: pure core, caller owns scheduling |

**Acceptance**

- Zero file/network/SQLite imports in core stream modules.
- Same invalid cursor vectors → equivalent error codes across runtimes.

**Dependencies:** LS-01, LS-02.

---

## LS-04 — Snapshot apply, normalize, and diff engine (all four)

**Status: done on tip** (`apply_snapshot` / re-normalize + stable-id diff ×4;
`docs/streaming-core-api.md`).

**Goal:** `apply_snapshot` / `apply_snapshot-bytes` produces full
`StreamUpdate` with snapshot+delta via full re-normalize of supplied material.

| Area | Deliverable |
| --- | --- |
| Code | Wire existing source adapters + normalizer; build stream records; stable-id diff |
| Tests | Empty prefix, group conflict, replacement, diagnostic diff, delta-apply equivalence |
| Docs | `docs/streaming-core-api.md` |

**Acceptance**

- Every `updated` result includes coherent snapshot and delta.
- Delta applied to prior snapshot equals new snapshot.
- Duplicate snapshot revision → `unchanged`.
- Batch normalize conformance still green.

**Dependencies:** LS-03.

---

## LS-05 — Append apply for file JSONL sources (all four)

**Status: done on tip** (`apply_append` for file JSONL sources ×4; append ≡
prefix oracle; `docs/streaming-file-sources.md`).

**Goal:** `apply_append` for `pi`, `claude-code`, `codex`, `openclaw`,
`grok-build` with oracle parity.

| Area | Deliverable |
| --- | --- |
| Code | Incremental framing + decode state; fall back to full prefix on divergence detection |
| Tests | Per-source append sequences; incomplete line; cross-chunk tools; Grok compaction reset; native id stability; `append-equals-prefix-oracle` |
| Docs | `docs/streaming-file-sources.md`; source spec partial/streaming sections |

**Acceptance**

- Never consume past last complete line on ordinary apply.
- Append updates match prefix-oracle snapshots/deltas/diagnostics/finality.
- Compaction/truncation → `reset-required` with correct reason.
- Performance: documented bound for steady-state append work; automatic
  fallback to snapshot path when exceeded (observable diagnostic/info).

**Dependencies:** LS-04.

---

## LS-06 — AHP Shape A snapshot streaming (all four)

**Status: done on tip** (`apply_ahp_snapshot` ×4; provisional `activeTurn`;
core `stream-ahp-snapshot` claimed at LS-12).

**Goal:** Successive Shape A snapshots with provisional `activeTurn`.

| Area | Deliverable |
| --- | --- |
| Code | `apply_ahp_snapshot`; snapshot-revision cursor; provisional mapping |
| Tests | `ahp-snapshot-empty`, `active-turn`, growth, complete, duplicate revision, conflict, provisional-final |
| Docs | Update `ahp-source-spec.md`, `contracts/spec/sources/ahp.md`, `ahp-ingest-status.md` (shipped vs planned) |

**Acceptance**

- Cross-runtime identical sequences.
- No network dependencies in core.
- Idempotent duplicate host revisions.

**Dependencies:** LS-04 (LS-05 for shared reset/diff rules preferred).

---

## LS-07 — AHP Shape B action-log reducer (all four)

**Status: done on tip** (`apply_ahp_actions` + minimal complete reducer ×4;
`docs/ahp-action-streaming.md`; core `stream-ahp-action-log` claimed at LS-12).

**Goal:** Core reducer + `apply_ahp_actions`; `serverSeq` cursor authority.

| Area | Deliverable |
| --- | --- |
| Code | Minimal complete reducer: turn start, response/reasoning deltas, tool lifecycle, usage, terminal states, truncation; unknown actions → diagnostic; foreign channel ignore |
| Tests | Full `ahp-action-*` matrix including gap, replay, resync, equals-snapshot |
| Docs | `docs/ahp-action-streaming.md` |

**Acceptance**

- Action-prefix stream matches equivalent Shape A snapshot stream where state
  is equivalent.
- Gaps never silently advance cursor.
- All four runtimes green before any `stream-ahp-action-log` claim.

**Dependencies:** LS-06, LS-02.

---

## LS-07h — Hermes optional provider streaming (all four ecosystems)

**Goal:** Optional provider packages; not file-tail.

**Status: done on tip** (core `apply_hermes_export` + optional provider packages
×4; `stream-hermes-provider` claimed on those packages only at LS-12;
shared `hermes-provider-*` cases cover core `apply_hermes_export`; only optional
SQLite/query I/O remains package-test-gated).

| Area | Deliverable |
| --- | --- |
| Contract | Provider interface: list, query session, change token, invalidation |
| Code | Optional packages/extras; feed `hermes-export` into core stream apply |
| Tests | Provider fixtures: snapshot, insert delta, soft-delete reset, nonnumeric ids |
| Docs | Hermes README + streaming matrix note (`docs/streaming-hermes-provider.md`) |

**Acceptance**

- Core remains SQLite-free.
- Without provider → clear unsupported capability.
- With provider → shared behavioral tests pass on all four.

**Dependencies:** LS-04. May parallel LS-09/10.

---

## LS-08 — Full stream matrix gate (all four)

**Goal:** Complete goldens; parity fixes; no capability advertising yet (or
draft-only).

**Status: done on tip** (engines + shared goldens + oracle gate; 41 stream
cases; `stream_unsupported_skips: 0` on all four; core `stream-*` claimed at
LS-12).

| Area | Deliverable |
| --- | --- |
| Tests | Entire `conformance/cases/streaming/**` green on .NET, TS, Rust, Python |
| Code | Fix drift; enable `stream-oracle-parity` on file-JSONL growth/reset + AHP action≡snapshot |
| Docs | Definition of done in `adapter-authoring.md`, `release-readiness.md`, `multi-language-plan.md` |

**Acceptance**

- One shared corpus; no runtime-private goldens as authority.
- Append ≡ snapshot oracle.
- Existing non-stream conformance still green.

**Dependencies:** LS-05, LS-06, LS-07.

---

## LS-09 — Optional file I/O packages (all four)

**Status: done on tip** (poll/follow packages ×4; `docs/streaming-file-io.md`;
`stream-file-io` / `stream-async-iterator` on optional package manifests only;
`stream-file-watch` **not** claimed).

**Goal:** Path poll/watch helpers that only call core apply APIs.

| Runtime | Package |
| --- | --- |
| .NET | `Hypabolic.Trajectory.IO` |
| TypeScript | extend `@hypabolic/trajectory-node` or `@hypabolic/trajectory-streaming-node` |
| Rust | `hypabolic-trajectory-io` |
| Python | `hypabolic-trajectory[io]` |

| Area | Deliverable |
| --- | --- |
| Code | Follow path, pending line buffer, truncation detection, cancellation, optional watch |
| Tests | Temp roots; coalesced events; permission errors as host errors not transcript diagnostics |
| Docs | `docs/streaming-file-io.md` |

**Acceptance**

- Core packages gain no I/O dependencies.
- Incomplete lines never forwarded.
- Explicit root required; no implicit whole-home watch.

**Dependencies:** LS-05, LS-08.

---

## LS-10 — Optional AHP client packages (all four)

**Status: done on tip** (transport-only clients ×4; fake-host CI;
`docs/ahp-client.md`; `stream-ahp-client` on optional package manifests only;
real WebSocket host remains consumer-injected; `stream-ahp-list-sessions`
**not** claimed).

**Goal:** Transport only; fake-host CI.

| Runtime | Package |
| --- | --- |
| .NET | `Hypabolic.Trajectory.Ahp` |
| TypeScript | `@hypabolic/trajectory-ahp` |
| Rust | `hypabolic-trajectory-ahp` |
| Python | `hypabolic-trajectory[ahp]` |

| Area | Deliverable |
| --- | --- |
| Code | Connect, auth callback, subscribe, snapshot/action feed, reconnect/resync |
| Tests | Fake host: gap, replay, cancel, backpressure, auth failure |
| Docs | `docs/ahp-client.md` |

**Acceptance**

- Not imported by core by default.
- Auth never in snapshots/deltas/diagnostics.
- Resync required on sequence gap.

**Dependencies:** LS-07, LS-08.

---

## LS-11 — Sample CLIs (all four)

**Goal:** Demonstrate consumer ownership of process lifetime.

**Status: done on tip** (`stream` / `ahp-stream` on all four sample CLIs;
privacy defaults; temp-store + FakeAhpHost tests; docs state consumer process
not daemon; capability advertising completed in LS-12).

| Command | Purpose |
| --- | --- |
| `stream --source … --path … --emit snapshot+delta --follow` | File follow |
| `stream --source … --id … --root … --follow` | List then follow |
| `ahp-stream --url … --chat …` | Optional client demo |

| Runtime | Entry |
| --- | --- |
| .NET | `dotnet/samples/Trajectory.Cli` |
| TypeScript | `typescript/packages/trajectory-cli` |
| Rust | `rust/tools/trajectory-cli` |
| Python | `python/samples/trajectory_cli` |

**Acceptance**

- Privacy-safe defaults; content opt-in.
- Emits both snapshot and delta per update (or flag-controlled).
- Automated tests use temp stores + fake AHP host only.
- Docs never describe Trajectory as a daemon.

**Dependencies:** LS-09, LS-10.

---

## LS-12 — Capability claims and release gate

**Status: done on tip** (core `stream-*` in `contracts/compatibility.json`
required + four `runtime-capabilities.json`; optional package caps only on
I/O / AHP / Hermes `package-capabilities.json`; privacy + release-readiness
checklists; no claim for `stream-file-watch` / `stream-ahp-list-sessions`).

**Goal:** Honest manifests and product docs for the completed feature.

| Area | Deliverable |
| --- | --- |
| Manifests | `contracts/compatibility.json` + all four `runtime-capabilities.json` stream caps |
| Optional manifests | I/O / AHP / Hermes provider caps only on those packages |
| Docs | Root `README.md`, `CHANGELOG.md`, architecture, publishing notes |
| Tests | Full matrix + package smoke + AOT/portable where applicable + privacy audit |

**Acceptance**

- Every advertised capability has shared fixtures and four-runtime green.
- No global `stream` flag masking partial implementation.
- Release readiness checklist includes streaming privacy and multi-runtime parity.
- Feature marked complete only when LS-08–LS-11 and chosen optional matrix
  (file I/O + AHP client; Hermes provider if claimed) are green.

**Dependencies:** LS-08, LS-09, LS-10, LS-11; LS-07h if Hermes claimed.

---

## Repository file checklist

### Add

- `contracts/spec/streaming.md`
- `contracts/schemas/trajectory-stream-v1.schema.json`
- `contracts/schemas/streaming-cursor-v1.schema.json`
- `contracts/schemas/streaming-delta-v1.schema.json`
- `contracts/schemas/streaming-case-v1.schema.json`
- `docs/streaming-core-api.md` (LS-04)
- `docs/streaming-file-sources.md` (LS-05)
- `docs/streaming-file-io.md` (LS-09)
- `docs/ahp-action-streaming.md` (LS-07)
- `docs/ahp-client.md` (LS-10)
- `conformance/cases/streaming/**`
- Core `Streaming/` modules in four runtimes
- Optional IO / Ahp / Hermes packages

### Modify

- `docs/architecture.md` — streaming pointer and boundary
- `docs/adapter-authoring.md` — stream definition of done
- `docs/multi-language-plan.md` — post-v1 streaming slice table
- `docs/ahp-ingest-status.md` / AHP specs — as slices ship
- `docs/grok-build-source-spec.md` — active append + compaction streaming notes
- `conformance/README.md`, `conformance/verify.py`, runners
- Protocol schemas for stream ops
- Sample CLI READMEs
- `CHANGELOG.md` under Unreleased as slices land

### Do not

- Retag published `0.1.0` / rewrite historical capability sets
- Change batch Hypabolic `minItems` solely for live empty streams
- Put watchers or WebSockets in core packages

---

## Risk register

| Risk | Mitigation |
| --- | --- |
| Multi-runtime semantic drift | Shared sequence goldens; no single-runtime oracle |
| Append ≠ prefix | Hard gate `stream-oracle-parity`; fallback path |
| Provisional identity churn | Explicit provisional ids + finalize ops |
| Compaction / rewrite | `reset-required` default; prefix hash |
| AHP protocol churn | Pin protocol minor; fixture digests |
| Privacy leaks in stream errors | Schema + conformance privacy tests |
| Scope collapse into “MVP” | This plan; LS-12 blocked until full matrix |

---

## Suggested implementation ownership pattern

For each LS-03–LS-07 code slice:

1. Land contracts/fixtures first when behavior is new.
2. Implement all four runtimes to the same fixtures (parallel worktrees OK).
3. Run shared `conformance/verify.py` stream filter.
4. Only then merge capability bits (LS-12).

Optional packages (LS-09/10/07h) may trail core matrix but are required for
**full feature complete** as defined in the product spec §14.

---

## Immediate next action

**LS-00** … **LS-12** are delivered on tip: locked design, wire contracts,
stream conformance protocol + shared corpus, state/cursor, snapshot/delta,
JSONL append, AHP Shape A/B, full stream matrix gate (41 cases;
`stream_unsupported_skips: 0` on all four runtimes), optional file I/O (LS-09),
optional AHP clients (LS-10), sample CLI `stream` / `ahp-stream` (LS-11), and
honest capability advertising (LS-12).

Core `stream-*` names are claimed in `contracts/compatibility.json` (required)
and all four core `runtime-capabilities.json`. Optional package caps
(`stream-file-io`, `stream-async-iterator`, `stream-ahp-client`,
`stream-hermes-provider`) are claimed only on those packages’
`package-capabilities.json`. Unimplemented `stream-file-watch` /
`stream-ahp-list-sessions` are not claimed.

Tip packaging status (shipped vs remaining):
[live-session-streaming-status.md](live-session-streaming-status.md).

**Post-LS-12 next engineering work**

- Registry publish wiring for optional stream packages on the next multi-registry
  tag (NuGet IO/Ahp/Hermes, npm `trajectory-ahp`/`trajectory-hermes`, crates
  `io`/`ahp`/`hermes`, Python `[io]`/`[ahp]`/`[hermes]` extras already in the
  core wheel).
- Hermes SQLite/query I/O remains package-test-gated (`stream-hermes-provider`).
  Shared `hermes-provider-*` export-apply cases are in the stream matrix.
- File-watch (`stream-file-watch`) and AHP `listSessions` (`stream-ahp-list-sessions`)
  when product-ready — leave unclaimed until then.
