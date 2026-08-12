# Release readiness

This document records packaging policy, privacy rules, upgrade guidance, and
the gates for product-level 1.0. It is operator-facing, not a customer quick
start (see the root [README](../README.md)).

## Current status

Distinguish **published registry packages** from **this repository tip**.

### Published release (`v0.1.0`)

| Item | Value |
| --- | --- |
| Package version | `0.1.0` (NuGet / npm / crates) |
| Capability slice | `ML13` |
| Normalizer contract | `0.2.0` |
| Conformance protocol | `1` |
| Diagnostics contract | `1` |
| Implemented sources (published 0.1.0) | `pi`, `claude-code`, `codex`, `openclaw`, `hermes` |
| Outputs | Hypabolic, canonical identity, message trajectory, OpenAI chat, minimal JSONL, OTEL GenAI spans |

### Repository tip (`main`, not yet re-tagged)

| Item | Value |
| --- | --- |
| Checked-in `VERSION` | `0.1.2` on the release tip |
| Capability slice | `ML13` (historical slice id; AHP is a post-`0.1.0` source addition) |
| Normalizer contract | `0.2.0` |
| Implemented sources (tip) | Published set **plus** `ahp` and `grok-build` |
| AHP scope | Phase 0–1 shipped in-tree on `main`; listing, Shape B action log, and live host are **not** shipped |
| Next registry publish | Tag `v0.1.2` after merge to main when CI is green |

AHP phase truth: [ahp-ingest-status.md](ahp-ingest-status.md). Design:
[ahp-source-spec.md](ahp-source-spec.md).

Machine-readable surfaces (repository tip advertises `ahp`):

- [`contracts/compatibility.json`](../contracts/compatibility.json)
- Runtime `runtime-capabilities.json` files (TypeScript, Rust, Python)
- `tools/validate_release_metadata.py`
- CI preview packaging + **Release** workflow (see [publishing.md](publishing.md))

### Python runtime (first-ship join green; public PyPI pending tag)

| Item | Value |
| --- | --- |
| Package | `hypabolic-trajectory` (import `hypabolic_trajectory`) |
| Layout | [`python/`](../python/) |
| Capabilities | [`python/runtime-capabilities.json`](../python/runtime-capabilities.json) — tip equality with compatibility + peers |
| Conformance | Unpublished protocol v1 runner `python/tools/trajectory_conformance` |
| Optional extra | `[otel]` — SDK sinks only; pure `project_otel_genai` + `otel` submodule always in core |
| First-ship join (PY-17) | §11 DoD green on `feature/python-impl` (`test_py17_first_ship_join.py`); no tag/publish yet |
| First public PyPI | Next synchronized multi-registry tag after `v0.1.0` (includes AHP Shape A; do **not** retag `0.1.0`) |
| Status | [python-impl-status.md](python-impl-status.md) · package docs [python/README.md](../python/README.md) |

## Provenance of release evidence

CI evidence bundles record:

- package version and capability slice;
- normalizer contract version;
- source commit (`GITHUB_SHA` / `SOURCE_COMMIT`);
- SHA-256 digests of manifests and packed archives.

```bash
python3 tools/validate_release_metadata.py \
  --repository-root . \
  --evidence artifacts/evidence/provenance.json
```

## Privacy and fixture sanitization

Checked-in conformance fixtures, store fixtures, diagnostic messages, and
golden outputs must not contain real user secrets, live API keys, personal
emails, or production filesystem paths.

### Rules

1. Prefer the smallest synthetic transcript that exposes one behaviour.
2. Replace real homes, org names, emails, and hostnames with portable
   placeholders (`/workspace/demo`, `session-0001`, etc.).
3. Never paste production tool arguments, prompts, credentials, cookies, or
   private keys into inputs or expected outputs.
4. Base64-looking payloads in fixtures must be synthetic and non-functional.
5. Listing fixtures use declarative temporary stores; runners substitute
   `$ROOT` so developer machine paths never land in goldens.
6. Diagnostics must obey
   [`contracts/spec/diagnostics.md`](../contracts/spec/diagnostics.md): no
   transcript prose, tool args/results, raw JSON excerpts, secrets, or
   developer paths.

Sample CLIs default to privacy-safe summaries; `--show-content` is an explicit
opt-in with a warning.

## Upgrade guidance (pre-1.0)

- Normalizer contract version `0.2.0` participates in identity-bearing
  canonical output; changing identity under the same contract version is
  forbidden.
- New sources or outputs must update `compatibility.json`, runtime capability
  manifests, shared cases, and CI gates together.
- Intentional wire or diagnostic changes require a contract or schema version
  decision before goldens move.
- Public language APIs may still evolve before 1.0; pin against wire contracts
  and conformance where stability matters.

After the first multi-registry release:

1. Publish NuGet, npm, and crates from the **same** git commit and tag.
2. Keep the normalizer contract version explicit in canonical output.
3. Document consumer-visible changes in `CHANGELOG.md`.
4. Never re-purpose diagnostic or fatal-error codes; only add.

## 1.0 readiness checklist

### Capability (complete at ML13)

- [x] Required conformance cases pass on .NET, TypeScript, and Rust for
      advertised capabilities; Python tip suite green in-tree (first public
      PyPI on next multi-registry tag).
- [x] Compatibility and runtime manifests agree at slice `ML13`.
- [x] CI and `validate_release_metadata.py` enforce the v1 source/output set.
- [x] Privacy and fixture sanitization rules are documented.
- [x] Preview packaging evidence is traceable to commit and contract version.
- [x] Publishing process is documented.

### Publish process

- [x] Release workflow packs and can publish NuGet / npm / crates / PyPI.
- [x] npm packages bootstrapped under `@hypabolic` (local CLI); OIDC Trusted
      Publisher configured for steady-state CI publishes.
- [ ] GitHub Environment `release` protection rules as desired.
- [x] NuGet / npm / crates.io Trusted Publishing (OIDC) for multi-registry Release.
- [x] PyPI Trusted Publishing path (OIDC pending publisher org `Hypabolic`,
      package `hypabolic-trajectory`) wired in `release.yml` (first live upload
      on the next tag).
- [x] Public version tag `v0.1.0` published on NuGet / npm / crates (sources
      through `hermes` only; **no** PyPI `0.1.0`).
- [ ] Explicit decision to cut the **next** public version tag when ready
      (required before AHP, Python on PyPI, or other post-`0.1.0` capabilities
      reach registries).

### Explicit non-goals for v1

- Browser/Wasm runtimes
- Cloud ingestion, tenancy, or hosted storage
- Identical public APIs across languages
- A stable serialized internal IR

## Live session streaming (post-v1; LS-08 matrix)

Core stream engines are **in-tree on tip** (file JSONL append/snapshot, AHP
snapshot + action-log, delta+snapshot delivery). They are **not** yet
advertised in runtime capability manifests (`stream-*` claims are LS-12).

### Stream matrix definition of done (LS-08)

| Gate | Requirement |
| --- | --- |
| Corpus | Entire `conformance/cases/streaming/**` green on .NET, TypeScript, Rust, Python |
| Oracle | Append path ≡ prefix re-normalize (`stream-oracle-parity`); AHP action ≡ Shape A when declared |
| Goldens | Per-step `expected.result` stream-json-exact goldens are shared authority |
| Batch | Existing normalize/list conformance + identity baseline remain green |
| Privacy | Stream diagnostics/fixtures obey the same sanitization rules as batch |
| Capabilities | Do **not** claim `stream-*` in manifests until LS-12 (optional I/O + clients + CLIs) |
| Core purity | No FS watchers, network, or SQLite in core packages |

Operator verify (stream filter only):

```bash
python3 conformance/verify.py --repository-root . --operation stream-sequence -- \
  <runtime-stream-capable-runner>
```

Product + slice plan: [live-session-streaming.md](live-session-streaming.md),
[live-session-streaming-plan.md](live-session-streaming-plan.md). Adapter
authoring stream DoD: [adapter-authoring.md](adapter-authoring.md#live-session-streaming--definition-of-done).
