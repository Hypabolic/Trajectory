# Multi-language implementation plan

Status: **complete for v1** — ML1–ML7, ML9 (OpenClaw), ML11 (Hermes), and
ML13 (release hardening) delivered. Packages publish at synchronized `0.1.0`.

This document retains the historical sequencing for how .NET, TypeScript, and
Rust reached parity. Day-to-day product docs live in the root
[README](../README.md) and [architecture](architecture.md).

## Decision

Trajectory is one product with three native implementations:

| Ecosystem | Package | Strategy |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | Original implementation and baseline |
| TypeScript | `@hypabolic/trajectory` | Independent implementation from Hypabolic specs + conformance |
| Rust | `hypabolic-trajectory` | Independent idiomatic Rust implementation |

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

**Sources:** Pi, Claude Code, Codex, OpenClaw, Hermes  

**Outputs:** Hypabolic trajectory, canonical identity, message trajectory,
OpenAI chat, minimal JSONL, OTEL GenAI spans  

**Cross-cutting:** explicit-root listing, typed diagnostics/fatal errors,
partial mode where applicable, synchronized package metadata

## Post-v1 ideas (not scheduled)

Additional source families or store backends may be added later as multi-runtime
slices with shared fixtures. They are not advertised as v1 capabilities.

## Non-goals (still)

- One generated codebase for three languages
- Shared native core via FFI
- Identical language APIs
- Stable serialized IR
- Browser/Wasm as a first-class target
- Cloud ingestion product surface
