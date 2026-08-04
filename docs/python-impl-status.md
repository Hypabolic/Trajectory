# Python implementation status (final review, human)

**Branch:** `feature/python-impl`  
**Review date:** 2026-08-04  
**Reviewer:** orchestrator (Codex final step unavailable — usage limit)

This note replaces the workflow’s planned **Codex gpt-5.6-sol** final review.

## Verdict

**Partial vertical, shippable as a progress PR — not first-public-PyPI ready.**

Core scaffold, identity/JSON, IR freezes, normalization, all six source adapters (Pi, Claude Code, Codex, OpenClaw, Hermes, AHP Shape A), listing helpers + per-source listers (stubs where expected), projections through letta / canonical / hypabolic / openai / jsonl-minimal / pure OTEL GenAI, `hypabolic_trajectory.otel` (`SpanSetSink`/`emit_to`), the `list_trajectories` registry dispatcher (PY-09b), and the early protocol-v1 conformance runner with progressive pi normalize claims (PY-10a) are in tree with unit tests. Full tip claims, listing-runner algorithm, engine ship surface, and CI/OIDC release integration are **not** done.

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
| PY-10a early runner (pi normalize) | **Done** (`python/tools/trajectory_conformance`; protocol v1; free-function normalize-letta/canonical; filtered verify green; claim-writer pi + letta/canonical + coverage caps; list-trajectories deferred to PY-10b-list) |
| PY-10b-* claim expansion | **Missing** (ops mostly wired in runner; claims/listing still open) |
| PY-11 full shared conformance | **Missing** |
| PY-12 TrajectoryEngine ship surface | Intermediate stub; not verified as DoD |
| PY-13 sample CLI | **Missing** |
| PY-14a packaging stamp / pack-smoke | Done |
| PY-14b OIDC / release.yml PyPI | **Missing** |
| PY-15* CI jobs | **Missing** |
| PY-16 docs integration | Partial (spec + this status) |
| PY-17 first-ship join | **Not ready** |

`python/runtime-capabilities.json` now claims progressive pi normalize surface only (`sources: [pi]`, outputs letta/canonical, coverage capabilities). Do not treat `IMPLEMENTED_SOURCES` in `__init__.py` as registry claims. Remaining outputs/sources require later claim-writer issues with filtered verify green.

## Verification performed

- `pytest` under `python/`: **417 passed** (includes PY-08 OTEL + PY-09b list dispatcher + PY-10a runner).
- Filtered shared verify green:  
  `python conformance/verify.py --repository-root . --source pi --operation normalize-letta --operation normalize-canonical -- env PYTHONPATH=python/tools python -m trajectory_conformance` → **10 operations / 6 cases**.
- Protocol: domain fatal exit 0 (`pi/missing-assistant`); protocol-error exit 2 (bad JSON / wrong version / path escape / undeclared op).
- Free-function exports: `from hypabolic_trajectory import normalize_to_ir, project_letta, project_canonical, serialize_projection`.
- Bare `--source pi` without `--operation` filters is **not** used for progressive claims (would pull unclaimed listing / other outputs).

## Issues / follow-ups (non-blocking for this PR)

1. **Listing runner algorithm** (`list-trajectories` + `$ROOT` rewrite) deferred to PY-10b-list.
2. **Claim expansion** for hypabolic / openai / jsonl / otel / other sources still open (PY-10b-*).
3. **SDK Activity sink** not shipped (optional `[otel]` helper); pure project + `emit_to`/`SpanSetSink` cover the import matrix.
4. **Engine** present as intermediate module; confirm isolation vs free functions before PY-17.
5. **CI progressive conformance job** (PY-15a argv generator/checker) not wired yet — runner path exists for local/CI invocation.
6. Workflow agents that hit Codex for “final review” failed; this document is the substitute gate for opening the PR.

## Recommended next PR sequence

1. PY-10b-* claim expansion parallel (hypabolic, openai/jsonl, list, otel, other sources)  
2. PY-11 + PY-15 CI + PY-14b OIDC → PY-17  

## Non-goals of this PR

- Cutting a version tag or publishing to PyPI  
- Claiming full tip ML13 matrix on Python  
- Merging incomplete stubs as “done” in release-readiness
