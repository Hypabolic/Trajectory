# Multi-language implementation plan

Status: **complete for v1** — ML1–ML7, ML9 (OpenClaw), ML11 (Hermes), and
ML13 (release hardening) delivered. Published packages at synchronized `0.1.0`
cover that historical v1 set only.

This document retains the historical sequencing for how .NET, TypeScript, and
Rust reached parity. Day-to-day product docs live in the root
[README](../README.md) and [architecture](architecture.md). Post-`0.1.0` source
work (AHP Shape A) is summarized under [Post-v1 landed](#post-v1-landed-after-published-010)
and [ahp-ingest-status.md](ahp-ingest-status.md).

## Decision

Trajectory is one product with four native implementations:

| Ecosystem | Package | Strategy |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | Original implementation and baseline |
| TypeScript | `@hypabolic/trajectory` | Independent implementation from Hypabolic specs + conformance |
| Rust | `hypabolic-trajectory` | Independent idiomatic Rust implementation |
| Python | `hypabolic-trajectory` (PyPI) | Independent native Python 3.11+ from the same specs + conformance |

No implementation may call another through FFI, subprocess, WASM, or a hosted
service. Observable behaviour for the same inputs, options, and contract
version must match.

### Authority order

1. Versioned public wire contracts and behavioural specifications
2. Shared sanitized source fixtures
3. Shared expected outputs and failure vectors

The internal IR remains private to each runtime.

## Delivery slices (historical)

| Slice | Outcome | Status |
| --- | --- | --- |
| ML1 | Shared contracts, repo layout, .NET conformance runner | Done |
| ML2 | TypeScript Pi vertical path | Done |
| ML3 | Rust Pi vertical path | Done |
| ML4 | TypeScript Claude Code + Codex | Done |
| ML5–ML6 | Rust Claude Code + Codex | Done |
| ML7 | Output + distribution parity (all projections, packaging dry-run) | Done |
| ML9 | OpenClaw across all runtimes | Done |
| ML11 | Hermes across all runtimes | Done |
| ML13 | Release hardening, manifests, privacy docs | Done |

## v1 capability set

Historical published **`0.1.0` / ML13** product surface (do not rewrite this set
when adding later sources):

**Sources:** Pi, Claude Code, Codex, OpenClaw, Hermes  

**Outputs:** Hypabolic trajectory, canonical identity, message trajectory,
OpenAI chat, minimal JSONL, OTEL GenAI spans  

**Cross-cutting:** explicit-root listing, typed diagnostics/fatal errors,
partial mode where applicable, synchronized package metadata

## Post-v1 landed (after published `0.1.0`)

| Slice | Outcome | Status |
| --- | --- | --- |
| AHP-0 / AHP-1 | Agent Host Protocol (`ahp`) Shape A offline ChatState snapshot ingest on .NET, TypeScript, Rust, and Python; shared conformance cases; sample CLI `show --path` (Python sample CLI optional) | **In-tree on tip**; **not** in registry packages at `0.1.0` |

Scope notes:

- Wire source name `ahp`; protocol pin `0.7.x`.
- Listing is Phase 3 (empty stubs); Shape B action-log reduce and live host are
  not shipped.
- First registry ship of AHP requires a **new** synchronized package version /
  tag after `0.1.0` (never retag `v0.1.0`).

Authoritative phase table: [ahp-ingest-status.md](ahp-ingest-status.md). Design:
[ahp-source-spec.md](ahp-source-spec.md).

## Python runtime (in-tree)

An independent native **Python 3.11+** implementation lives under `python/`
and publishes as PyPI **`hypabolic-trajectory`** (org Hypabolic; import
`hypabolic_trajectory`). Pure OTEL GenAI projection and `hypabolic_trajectory.otel`
(`SpanSetSink` / `emit_to`) ship in the **core** wheel; optional extra
`[otel]` adds SDK sink helpers only.

| Item | Status |
| --- | --- |
| Sources / outputs / tip capabilities | Tip ML13 matrix including `ahp` Shape A (`python/runtime-capabilities.json`) |
| Shared conformance | Protocol v1 runner + tip verify green (see [python-impl-status.md](python-impl-status.md)) |
| First public PyPI | Next synchronized multi-registry tag after published `0.1.0` (do not retag `v0.1.0`) |

Package quickstart: [`python/README.md`](../python/README.md). Full product,
packaging, conformance, and work-breakdown plan:
[python-implementation-spec.md](python-implementation-spec.md). Status:
[python-impl-status.md](python-impl-status.md).

## Post-v1 scheduled: live session streaming

Full-feature library streaming for consumer applications (file JSONL + AHP
snapshot/action-log + optional Hermes provider; snapshot and delta delivery;
all four runtimes). Trajectory does not become a daemon; pure algorithm in
core, optional I/O/clients.

| Doc | Role |
| --- | --- |
| [live-session-streaming.md](live-session-streaming.md) | Locked product + technical specification |
| [live-session-streaming-plan.md](live-session-streaming-plan.md) | Slices LS-00 … LS-12 |

| Slice | Outcome | Status |
| --- | --- | --- |
| LS-00 … LS-02 | Product freeze, contracts/schemas, stream conformance protocol + fixtures | **In-tree** |
| LS-03 … LS-05 | Stream state/cursor, snapshot apply + delta, JSONL append (×4) | **In-tree** |
| LS-06 … LS-07 | AHP Shape A snapshot stream + Shape B action-log reducer in core (×4) | **In-tree** |
| **LS-08** | **Full stream matrix gate** — shared `conformance/cases/streaming/**` green on all four runtimes; per-step goldens; append ≡ prefix oracle; batch still green; **no** `stream-*` capability claims | **In-tree on tip** |
| LS-09 | Optional file I/O packages (×4) | Delivered on tip (`streaming-file-io.md`) |
| LS-10 … LS-11 | Optional AHP clients, sample CLIs | Scheduled |
| LS-12 | Capability advertising + release gate | Blocked on LS-10–11 |

### LS-08 definition of done (core matrix)

- One shared corpus under `conformance/cases/streaming/` is authority (no
  runtime-private stream goldens).
- `python3 conformance/verify.py --operation stream-sequence -- <runner>` is
  green for .NET, TypeScript, Rust, and Python with
  `stream_unsupported_skips: 0` for the landed input kinds.
- `stream-oracle-parity` / `append_equals_prefix` covers file-JSONL growth
  cases; `action_equals_snapshot` covers AHP action≡snapshot.
- Batch tip verify and identity baseline remain green.
- `runtime-capabilities.json` still omits `stream-*` until LS-12.

This supersedes the informal “AHP Shape B / live host later” note as a concrete
roadmap; optional I/O/clients and capability advertising remain later slices.

## Post-v1 ideas (not scheduled)

Further source families, store backends, or other post-streaming work may be
added later as multi-runtime slices with shared fixtures. They are not
advertised as historical v1 capabilities and must not be backfilled into
published `0.1.0` notes.

## Non-goals (still)

- One generated codebase for three languages
- Shared native core via FFI
- Identical language APIs
- Stable serialized IR
- Browser/Wasm as a first-class target
- Cloud ingestion product surface
