# Implementation plan (.NET baseline)

Status: **slices for v1 sources and projections complete.** Further work is
tracked as multi-language capability slices in
[multi-language-plan.md](multi-language-plan.md).

## Objective

Deliver a production-quality .NET implementation of Trajectory:

- native source adapters (Pi, Claude Code, Codex, OpenClaw, Hermes) for the
  historical published v1 / `0.1.0` set;
- shared normalization (identity, bounds, timestamps, diagnostics);
- Hypabolic, canonical identity, message trajectory, OpenAI chat, minimal
  JSONL, and optional OpenTelemetry GenAI projections;
- explicit-root listing;
- Native AOT / trim-safe core package.

**Post-v1 / repository tip:** AHP (`ahp`) Shape A offline snapshot adapters
exist across runtimes on `main` (see
[ahp-ingest-status.md](ahp-ingest-status.md) and
[multi-language-plan.md](multi-language-plan.md)). Export listing and Shape B
remain out of scope for this cut. First registry ship of AHP needs a new
package version after `0.1.0`.

## Delivery status

| Slice | Status |
| --- | --- |
| 1 — Pi end-to-end | Complete |
| 2 — Pi normalization and canonical parity | Complete |
| 3 — Claude Code | Complete |
| 4 — Codex | Complete |
| 5–7 — Other source families | Superseded by multi-language roadmap; OpenClaw and Hermes shipped as ML9/ML11 |
| 8 — Hermes | Complete as ML11 |
| 9 — Optional SQLite package | Deferred / optional post-v1 |
| 10 — Additional projections + OTEL | Complete |
| 11 — Package readiness | Expanded by ML1–ML7 and ML13 |

## Planning rules

- Wire contracts and shared conformance are authoritative.
- Source adapters decode; the normalizer owns common policy.
- Preserve source-exposed execution metadata for later projections.
- Listing ships with each filesystem-backed source.
- Core remains BCL-only; optional integrations stay optional.
- A slice is incomplete with placeholders, skipped tests, or unverified schema
  claims.

## Vertical path (every source)

```text
source input
  → decode
  → shared normalization
  → message / canonical / Hypabolic outputs
  → listing (where applicable)
  → fixtures + AOT-safe execution
```

Detailed historical work items for slices 1–4 and 10 remain in git history if
needed for archaeology. Product behaviour is defined by `contracts/` and
`conformance/`.
