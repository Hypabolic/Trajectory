# Python implementation status (final review, human)

**Branch:** `feature/python-impl`  
**Review date:** 2026-08-04  
**Reviewer:** orchestrator (Codex final step unavailable — usage limit)

This note replaces the workflow’s planned **Codex gpt-5.6-sol** final review.

## Verdict

**Partial vertical, shippable as a progress PR — not first-public-PyPI ready.**

Core scaffold, identity/JSON, IR freezes, normalization, all six source adapters (Pi, Claude Code, Codex, OpenClaw, Hermes, AHP Shape A), listing helpers + per-source listers (stubs where expected), projections through letta / canonical / hypabolic / openai / jsonl-minimal / pure OTEL GenAI, `hypabolic_trajectory.otel` (`SpanSetSink`/`emit_to`), the `list_trajectories` registry dispatcher (PY-09b), the **protocol-v1 conformance runner with all seven ops wired (PY-10-full)** plus progressive tip claims from PY-10a/PY-10b-*, the **full shared tip gate (PY-11)** (unfiltered verify + identity baseline + tip capabilities equality formalization), the **TrajectoryEngine ship surface (PY-12)**, the **unpublished sample CLI (PY-13)** (`python/samples/trajectory_cli` browse/list/show), **CI progressive conformance (PY-15a)** (argv generator/checker), **CI tip gate (PY-15b)** (unfiltered `python-conformance` + jq tip equality + `validate_release_metadata` ship equality), and **docs integration (PY-16)** (product docs + package README: package map, install, imports, filters, dual timestamps, escape/formulas, OTEL import matrix, filtered runner argv) are in tree with unit tests. OIDC PyPI release path (PY-14b) is in tree.

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
| PY-10b-* remaining claim expansion | **Done** (tip sources + tip outputs + required capabilities all claimed via PY-10a/PY-10b-* writers; no residual gaps) |
| PY-10-full | **Done** (all 7 protocol-v1 ops wired; `PROTOCOL_V1_OPERATIONS` pins request schema enum; filtered tip sources×ops verify **47 ops / 27 cases** green; join tests in `test_py10_full.py`) |
| PY-11 full shared conformance | **Done** (unfiltered tip verify 47 ops / 27 cases green; identity-baseline 21 goldens; tip capabilities equality claim formalized vs compatibility + TS/Rust peers; `test_py11_full_conformance.py`) |
| PY-12 TrajectoryEngine ship surface | **Done** (`create_default` tip matrix incl. pure otel; `project` / `add_output_adapter`; duplicate→ValueError; unknown→`unknown_output_schema`; root `__all__`; binding isolation units) |
| PY-13 sample CLI | **Done** (`python/samples/trajectory_cli` browse/list/show; unpublished; no console script; unit tests) |
| PY-14a packaging stamp / pack-smoke | Done |
| PY-14b OIDC / release.yml PyPI | **Done** (validate packs `artifacts/release/pypi` + pack-smoke; `publish-pypi` download-only + `skip-existing` + OIDC `id-token: write` / env `release`; github-release needs publish-pypi + attaches pypi; `docs/publishing.md` PyPI pending-publisher + install lines; no live publish) |
| PY-15-scaffold | **Done** (`python-unit` matrix + `python-package-smoke` on 3.11) |
| PY-15a progressive conformance | **Done** (`python-conformance` job; `tools/conformance_argv_from_capabilities.py` §5 maps + fail-closed; artifact `python-conformance-candidates`; no continue-on-error) |
| PY-15b tip CI gate | **Done** (unfiltered tip verify; jq tip equality incl. capabilities; generator `proper_subset_of_tip==false`; `validate_release_metadata` ship equality; `test_py15b_tip_gate.py`) |
| PY-16 docs integration | **Done** (package map, install, imports, filters, dual timestamps, escape/formulas, OTEL matrix, filtered runner argv; product docs + `python/README.md` + this status) |
| PY-17 first-ship join | **Not ready** |

`python/runtime-capabilities.json` claims the full tip surface with **tip equality formalized (PY-11)**: all six sources (`pi` … `ahp`), all six outputs (letta / canonical / hypabolic / openai / jsonl-minimal / otel-genai-spans-v1), required capabilities including `list-explicit-root`, and `slice: ML13` — equal to `contracts/compatibility.json` and peer TS/Rust manifests. Do not treat `IMPLEMENTED_SOURCES` in `__init__.py` as registry claims.

## Verification performed

- `pytest` under `python/`: progressive suite includes PY-08 OTEL + PY-09b list dispatcher + PY-10a runner + PY-10b-* claim-writers + **PY-10-full** join + **PY-11** tip gate + **PY-15a** progressive generator + **PY-15b** tip CI gate (`test_py15b_tip_gate.py`).
- Filtered shared verify green (full tip sources × all protocol-v1 ops), including via **generator argv**:  
  `python conformance/verify.py --repository-root . $(python tools/conformance_argv_from_capabilities.py --repository-root .) -- env PYTHONPATH=python/src:python/tools python -m trajectory_conformance` → **47 operations / 27 cases**.
- **Bare unfiltered** `verify.py` (tip defaults from compatibility.json) green: **47 operations / 27 cases** (PY-11 / PY-15b CI).
- **Identity baseline** `conformance/identity-baseline.sha256` green (21 identity-bearing goldens; PY-11).
- **Tip capabilities equality**: Python `runtime-capabilities.json` sources/outputs/capabilities/slice equal tip ML13 + TS/Rust peers (PY-11 claim ceremony; **PY-15b** CI jq + `validate_release_metadata` ship equality).
- Protocol: domain fatal exit 0 (`pi/missing-assistant`); protocol-error exit 2 (bad JSON / wrong version / path escape / undeclared op / unknown op).
- Free-function exports: `from hypabolic_trajectory import normalize_to_ir, project_letta, project_canonical, project_hypabolic, project_openai, project_minimal_jsonl, project_otel_genai, serialize_projection, list_trajectories`.
- Sample CLI (PY-13): `PYTHONPATH=python/samples python -m trajectory_cli show --source pi --path conformance/cases/pi/tool-calls/input.jsonl` (unpublished; no console script).

## Issues / follow-ups (non-blocking for this PR)

2. **SDK Activity sink** not shipped (optional `[otel]` helper); pure project + `emit_to`/`SpanSetSink` cover the import matrix.
3. **Engine** shipped (PY-12); free-function isolation pin covered by unit tests.
4. **CI tip job** (PY-15b) wired: unfiltered tip gate + validate_release_metadata ship equality.
5. Workflow agents that hit Codex for “final review” failed; this document is the substitute gate for opening the PR.

## Recommended next PR sequence

1. **PY-17** first-ship join (docs PY-16 + tip formalization PY-11 + OIDC PY-14b + progressive/tip CI PY-15a/b + engine PY-12 already landed; optional PY-13 sample CLI also landed)

## Non-goals of this PR

- Cutting a version tag or publishing to PyPI  
- Merging incomplete stubs as “done” in release-readiness
