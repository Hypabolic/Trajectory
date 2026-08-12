# Python implementation status (first-ship join green)

**Branch:** `feature/python-impl`  
**Review date:** 2026-08-04  
**Reviewer:** orchestrator (Codex final step unavailable — usage limit; PY-17 self-review + join gate; ship-continuation pass)

## Verdict

**First-ship join (PY-17) green on this branch — ready for a multi-registry ship tag, but not yet tagged or published.**

§11 Definition of Done holds for independence, tip capabilities honesty, full shared verify (90 ops / 63 cases, including 24 stream unsupported skips), identity baseline (21 goldens), free-function + `TrajectoryEngine` API, pure OTEL + always-importable `otel` submodule, listing, packaging (two-column pack-smoke + root-anchored sdist artifacts), CI tip gate + OIDC release path, and docs. **Do not retag `0.1.0`.** First public PyPI cut is the **next** synchronized multi-registry tag after existing NuGet/npm/crates `0.1.0`.

**Ship-continuation pass (DoD recheck):** 526 pytest green; filtered pi verify **16 ops / 7 cases** green; unfiltered tip **90 ops / 63 cases** green (`stream_unsupported_skips: 24`); identity **21/21**; pack-smoke + tip capabilities honesty OK. Hermes/AHP empty-page listing is **documented policy** (SQLite-free hermes / AHP Phase 3 deferred), not a silent stub.

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
| PY-08 OTEL pure + extra | **Done** (pure `project_otel_genai` in core; `otel.SpanSetSink`/`emit_to`; no SDK in core; unit + unicode-boundaries golden) |
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
| PY-10-full | **Done** (all 7 protocol-v1 ops wired; `PROTOCOL_V1_OPERATIONS` pins request schema enum; filtered tip sources×ops verify green; join tests in `test_py10_full.py`) |
| PY-11 full shared conformance | **Done** (unfiltered tip verify 90 ops / 63 cases green with `stream_unsupported_skips: 24`; identity-baseline 21 goldens; tip capabilities equality claim formalized vs compatibility + TS/Rust peers; `test_py11_full_conformance.py`) |
| PY-12 TrajectoryEngine ship surface | **Done** (`create_default` tip matrix incl. pure otel; `project` / `add_output_adapter`; duplicate→ValueError; unknown→`unknown_output_schema`; root `__all__`; binding isolation units) |
| PY-13 sample CLI | **Done** (`python/samples/trajectory_cli` browse/list/show; unpublished; no console script; unit tests) |
| PY-14a packaging stamp / pack-smoke | Done |
| PY-14b OIDC / release.yml PyPI | **Done** (validate packs `artifacts/release/pypi` + pack-smoke; `publish-pypi` download-only + `skip-existing` + OIDC `id-token: write` / env `release`; github-release needs publish-pypi + attaches pypi; `docs/publishing.md` PyPI pending-publisher + install lines; no live publish) |
| PY-15-scaffold | **Done** (`python-unit` matrix + `python-package-smoke` on 3.11) |
| PY-15a progressive conformance | **Done** (`python-conformance` job; `tools/conformance_argv_from_capabilities.py` §5 maps + fail-closed; artifact `python-conformance-candidates`; no continue-on-error) |
| PY-15b tip CI gate | **Done** (unfiltered tip verify; jq tip equality incl. capabilities; generator `proper_subset_of_tip==false`; `validate_release_metadata` ship equality; `test_py15b_tip_gate.py`) |
| PY-16 docs integration | **Done** (package map, install, imports, filters, dual timestamps, escape/formulas, OTEL matrix, filtered runner argv; product docs + `python/README.md` + this status) |
| PY-17 first-ship join | **Done** (§11 DoD join tests in `test_py17_first_ship_join.py`; sdist root-anchored artifacts so `samples/**` cannot leak via nested README; pack-smoke green; no tag/publish) |

`python/runtime-capabilities.json` claims the full tip surface with **tip equality formalized (PY-11)**: all six sources (`pi` … `ahp`), all six outputs (letta / canonical / hypabolic / openai / jsonl-minimal / otel-genai-spans-v1), required capabilities including `list-explicit-root`, and `slice: ML13` — equal to `contracts/compatibility.json` and peer TS/Rust manifests. Do not treat `IMPLEMENTED_SOURCES` in `__init__.py` as registry claims.

## Verification performed

- `pytest` under `python/`: **526** green, including **PY-17** §11 join (`test_py17_first_ship_join.py`) + prior PY-08…PY-16 gates.
- Filtered pi shared verify green: **16 operations / 7 cases** (early runner / progressive path sanity).
- Filtered shared verify green (full tip sources × all protocol-v1 ops), including via **generator argv**:  
  `python conformance/verify.py --repository-root . $(python tools/conformance_argv_from_capabilities.py --repository-root .) -- env PYTHONPATH=python/src:python/tools python -m trajectory_conformance` → **90 operations / 63 cases** (`stream_unsupported_skips: 24`).
- **Bare unfiltered** `verify.py` (tip defaults from compatibility.json) green: **90 operations / 63 cases** with `stream_unsupported_skips: 24` (PY-11 / PY-15b / PY-17 CI; 39 batch + 24 stream scaffolds).
- **Identity baseline** `conformance/identity-baseline.sha256` green (21 identity-bearing goldens).
- **Tip capabilities equality**: Python `runtime-capabilities.json` sources/outputs/capabilities/slice equal tip ML13 + TS/Rust peers (PY-11 claim ceremony; **PY-15b** CI jq + `validate_release_metadata` ship equality).
- **Pack-smoke** two-column green after PY-17 sdist artifact root-anchoring (`/README.md`, `/LICENSE`, `/pyproject.toml`); sdist/wheel free of `tests/`/`samples/`/`tools/`.
- **Hermes / AHP listing:** empty-page listers are intentional documented policy (core SQLite-free Hermes; AHP Phase 3 export-tree listing deferred). Normalize paths are claimed and verify-green; listing is not a silent incomplete stub.
- Protocol: domain fatal exit 0 (`pi/missing-assistant`); protocol-error exit 2 (bad JSON / wrong version / path escape / undeclared op / unknown op).
- Free-function exports: `from hypabolic_trajectory import normalize_to_ir, project_letta, project_canonical, project_hypabolic, project_openai, project_minimal_jsonl, project_otel_genai, serialize_projection, list_trajectories`.
- Sample CLI (PY-13): `PYTHONPATH=python/samples python -m trajectory_cli show --source pi --path conformance/cases/pi/tool-calls/input.jsonl` (unpublished; no console script).

## Residual non-blockers (post-join; not ship-blocking)

1. **Next multi-registry tag** must be a new version (e.g. `v0.1.1` / `v0.2.0`) — **not** a `0.1.0` / `v0.1.0` retag of existing NuGet/npm/crates artifacts.
2. **Pending PyPI publisher** must be confirmed on pypi.org org Hypabolic before the first live `publish-pypi` (documented; OIDC path wired).
3. **Optional SDK Activity sink** not shipped (`[otel]` helper); pure `project_otel_genai` + `emit_to`/`SpanSetSink` cover the import matrix.

## Recommended next steps

1. Operator: cut next synchronized multi-registry tag (e.g. `v0.1.1` / `v0.2.0`) with Python included — **not** retag `v0.1.0`.
2. Confirm PyPI Trusted Publishing pending publisher for org `Hypabolic` / package `hypabolic-trajectory` / workflow `release.yml` / env `release`.
3. Optional: post-ship polish for SDK Activity sink if product wants it.

## Non-goals of this join

- Cutting a version tag or publishing to PyPI  
- Retagging or overwriting existing registry `0.1.0` artifacts  
- Shipping sample CLI or conformance runner as console scripts  
