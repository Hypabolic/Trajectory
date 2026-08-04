# Python implementation status (final review, human)

**Branch:** `feature/python-impl`  
**Review date:** 2026-08-04  
**Reviewer:** orchestrator (Codex final step unavailable — usage limit)

This note replaces the workflow’s planned **Codex gpt-5.6-sol** final review.

## Verdict

**Partial vertical, shippable as a progress PR — not first-public-PyPI ready.**

Core scaffold, identity/JSON, IR freezes, normalization, all six source adapters (Pi, Claude Code, Codex, OpenClaw, Hermes, AHP Shape A), listing helpers + per-source listers (stubs where expected), projections through letta / canonical / hypabolic / openai / jsonl-minimal / pure OTEL GenAI, `hypabolic_trajectory.otel` (`SpanSetSink`/`emit_to`), and the `list_trajectories` registry dispatcher (PY-09b) are in tree with unit tests. Shared conformance runner, capability claims, engine ship surface, and CI/OIDC release integration are **not** done.

## What landed (mapped to spec §9)

| Slice | Status |
| --- | --- |
| PY-01 scaffold | Done |
| PY-02 canonical JSON + identity | Done |
| PY-03 timestamps / diagnostics / errors | Done |
| PY-04a IR + DTO freezes + skeleton | Done |
| PY-04b normalization core | Done |
| PY-09a listing common + registry shell | Done |
| PY-05a/b, PY-06-* sources + listers | Done |
| PY-07a core projections + `serialize_projection` | Done |
| PY-07b openai + jsonl-minimal | Done |
| PY-08 OTEL pure + extra | **Done** (pure `project_otel_genai` in core; `otel.SpanSetSink`/`emit_to`; no SDK in core; no capabilities claim; unit + unicode-boundaries golden) |
| PY-09b `list_trajectories` dispatcher | **Done** (dispatch-by-registry; invalid_input / listing_unavailable; unit tests) |
| PY-10* conformance runner + claims | **Missing** |
| PY-11 full shared conformance | **Missing** |
| PY-12 TrajectoryEngine ship surface | Intermediate stub; not verified as DoD |
| PY-13 sample CLI | **Missing** |
| PY-14a packaging stamp / pack-smoke | Done |
| PY-14b OIDC / release.yml PyPI | **Missing** |
| PY-15* CI jobs | **Missing** |
| PY-16 docs integration | Partial (spec + this status) |
| PY-17 first-ship join | **Not ready** |

`python/runtime-capabilities.json` correctly advertises **empty** sources/outputs/capabilities (progressive claim-writer rule). Do not treat `IMPLEMENTED_SOURCES` in `__init__.py` as registry claims.

## Verification performed

- `pytest` under `python/`: **406 passed** (includes PY-08 OTEL + PY-09b list dispatcher).
- Manual smoke: `normalize_to_ir` + projections on `conformance/cases/pi/tool-calls/input.jsonl` produced 8 IR records and successful letta/canonical/hypabolic/openai/jsonl + `serialize_projection`.
- `list_trajectories` dispatches by registry only; missing lister → `listing_unavailable`; bad limit/cursor → `invalid_input` at free-function entry; no `$HOME` defaults.
- Pure `project_otel_genai` matches `pi/unicode-boundaries/expected.otel.json`; `emit_to` works without `opentelemetry-*`; no capabilities claim edit.

## Issues / follow-ups (non-blocking for this PR)

1. **No `verify.py` runner** — cannot yet gate golden parity for pi (or any source) in CI.
2. **No capability claims** — correct for now; must not publish to PyPI advertising tip matrix until PY-10/11 green.
3. **SDK Activity sink** not shipped (optional `[otel]` helper); pure project + `emit_to`/`SpanSetSink` cover the import matrix. Add thin `SdkActivitySink` only if product needs live emission before first tag.
4. **Engine** present as intermediate module; confirm isolation vs free functions before PY-17.
5. **Golden byte parity** for non-OTEL outputs not re-checked against shared expected.*.json in this review (unit coverage only); OTEL unicode-boundaries golden is unit-checked.
6. Workflow agents that hit Codex for “final review” failed; this document is the substitute gate for opening the PR.

## Recommended next PR sequence

1. PY-10a early runner (pi normalize ops) + progressive capabilities  
2. PY-10b-* claim expansion parallel (incl. project-otel when runner green)  
3. PY-11 + PY-15 CI + PY-14b OIDC → PY-17  

## Non-goals of this PR

- Cutting a version tag or publishing to PyPI  
- Claiming full tip ML13 matrix on Python  
- Merging incomplete stubs as “done” in release-readiness
