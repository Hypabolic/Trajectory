# Python implementation status (final review, human)

**Branch:** `feature/python-impl`  
**Review date:** 2026-08-04  
**Reviewer:** orchestrator (Codex final step unavailable — usage limit)

This note replaces the workflow’s planned **Codex gpt-5.6-sol** final review.

## Verdict

**Partial vertical, shippable as a progress PR — not first-public-PyPI ready.**

Core scaffold, identity/JSON, IR freezes, normalization, all six source adapters (Pi, Claude Code, Codex, OpenClaw, Hermes, AHP Shape A), listing helpers + per-source listers (stubs where expected), projections through letta / canonical / hypabolic / openai / jsonl-minimal / pure OTEL GenAI, `hypabolic_trajectory.otel` (`SpanSetSink`/`emit_to`), the `list_trajectories` registry dispatcher (PY-09b), and the early protocol-v1 conformance runner with progressive pi normalize claims (PY-10a), and the **TrajectoryEngine ship surface (PY-12)** are in tree with unit tests. Full tip claims, remaining listing-runner/claim expansion, and CI/OIDC release integration are **not** done.

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
| PY-10b-sources-openclaw | **Done** (claim-writer: `openclaw` in sources; filtered normalize-letta/canonical green; listing not claimed) |
| PY-10a early runner (pi normalize) | **Done** (`python/tools/trajectory_conformance`; protocol v1; free-function normalize-letta/canonical; filtered verify green; claim-writer pi + letta/canonical + coverage caps; list-trajectories wired (PY-10b-list)) |
| PY-10b-sources-claude-codex | **Done** (claim-writer: `claude-code` + `codex` progressive sources; filtered normalize-letta/canonical green — 21 ops / 14 cases) |
| PY-10b-sources-ahp | **Done** (claim-writer: `ahp` when filtered normalize-letta/canonical green — 3 cases / 4 ops) |
| PY-10b-hypabolic | **Done** (`normalize-hypabolic` via free-function `project_hypabolic` + `serialize_projection`; filtered verify green; claim-writer adds `hypabolic-trajectory-v1`) |
| PY-10b-openai-jsonl | **Done** (runner ops wired; claim-writer `openai-chat-messages` + `jsonl-minimal`; filtered verify 12 ops / 6 cases green) |
| PY-10b-list | **Done** (full §7 listing algorithm + `$ROOT` rewrite; `list-explicit-root` claimed; filtered verify green for list-trajectories) |
| PY-10b-sources-hermes | **Done** (claim-writer: `hermes` in sources tip-order; filtered hermes normalize-letta/canonical green; listing not claimed) |
| PY-10b-otel | **Done** (runner `project-otel` via free-function `project_otel_genai` + `serialize_projection`; filtered verify green; claim-writer adds `otel-genai-spans-v1`) |
| PY-10b-* remaining claim expansion | **Missing** (any residual claim gaps after parallel writers) |
| PY-11 full shared conformance | **Missing** |
| PY-12 TrajectoryEngine ship surface | **Done** (`create_default` tip matrix incl. pure otel; `project` / `add_output_adapter`; duplicate→ValueError; unknown→`unknown_output_schema`; root `__all__`; binding isolation units) |
| PY-13 sample CLI | **Missing** |
| PY-14a packaging stamp / pack-smoke | Done |
| PY-14b OIDC / release.yml PyPI | **Missing** |
| PY-15* CI jobs | **Missing** |
| PY-16 docs integration | Partial (spec + this status) |
| PY-17 first-ship join | **Not ready** |

`python/runtime-capabilities.json` claims progressive pi surface (`sources` includes `pi`, `claude-code`, `codex` (and any concurrent progressive claims), outputs letta/canonical + openai-chat-messages + jsonl-minimal, coverage capabilities). Do not treat `IMPLEMENTED_SOURCES` in `__init__.py` as registry claims. Remaining outputs/sources require later claim-writer issues with filtered verify green.

## Verification performed

- `pytest` under `python/`: progressive suite includes PY-08 OTEL + PY-09b list dispatcher + PY-10a runner + PY-10b-openai-jsonl.
- Filtered shared verify green (claimed surface):  
  `python conformance/verify.py --repository-root . --source pi --operation normalize-letta --operation normalize-canonical --operation project-openai --operation project-minimal-jsonl -- env PYTHONPATH=python/tools python -m trajectory_conformance` → **12 operations / 6 cases**.
- Protocol: domain fatal exit 0 (`pi/missing-assistant`); protocol-error exit 2 (bad JSON / wrong version / path escape / undeclared op).
- Free-function exports: `from hypabolic_trajectory import normalize_to_ir, project_letta, project_canonical, project_openai, project_minimal_jsonl, serialize_projection`.
- Bare `--source pi` without `--operation` filters is **not** used for progressive claims (would pull unclaimed later outputs when filters omit them).

## Issues / follow-ups (non-blocking for this PR)

2. **Claim expansion** residual gaps only (otel claimed by PY-10b-otel).
3. **SDK Activity sink** not shipped (optional `[otel]` helper); pure project + `emit_to`/`SpanSetSink` cover the import matrix.
4. **Engine** shipped (PY-12); free-function isolation pin covered by unit tests.
5. **CI progressive conformance job** (PY-15a argv generator/checker) not wired yet — runner path exists for local/CI invocation.
6. Workflow agents that hit Codex for “final review” failed; this document is the substitute gate for opening the PR.

## Recommended next PR sequence

1. Residual PY-10b-* claim gaps if any after parallel writers; then PY-11  
2. PY-11 + PY-15 CI + PY-14b OIDC → PY-17  

## Non-goals of this PR

- Cutting a version tag or publishing to PyPI  
- Claiming full tip ML13 matrix on Python  
- Merging incomplete stubs as “done” in release-readiness
