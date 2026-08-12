# Changelog

All notable product and contract changes are recorded here. Package versions
remain synchronized across NuGet, npm, and crates.io preview artifacts.

## [Unreleased]

- **LS-06 / LS-07 AHP stream snapshot and action-log (core, all four runtimes):**
  pure `apply_ahp_snapshot` (successive Shape A, provisional `activeTurn`,
  snapshot-revision cursor) and `apply_ahp_actions` (Shape B minimal complete
  reducer, serverSeq cursor, gaps → `reset-required` / `sequence-gap`, unknown
  actions / foreign channels as content-safe diagnostics). Shared fixtures under
  `conformance/cases/streaming/ahp-snapshot-*`, `ahp-action-*`, and
  `provisional-to-stable`. Docs: `docs/ahp-action-streaming.md` plus updates to
  `ahp-source-spec`, `ahp-ingest-status`, `streaming-core-api`. Protocol pin
  remains vendor `0.7.0`. No WebSocket/JSON-RPC client in core. **Do not**
  advertise `stream-ahp-snapshot` / `stream-ahp-action-log` until LS-12.

- **LS-02 stream conformance protocol & fixture skeleton:** protocol ops
  (`stream-sequence` / `stream-replay` plus reserved per-step apply ops) on
  request-v1; `conformance/verify.py` branches batch vs multi-step stream cases
  with comparison modes from `contracts/spec/streaming.md` (including
  normative delta-apply); all four runtime runners accept stream ops and return
  `status: unsupported` until engines land (LS-04+); 18 scaffold cases under
  `conformance/cases/streaming/` with privacy scans. No stream capability claims.

- **LS-01 streaming contracts:** normative `contracts/spec/streaming.md` and
  schemas `trajectory-stream-v1`, `streaming-cursor-v1`, `streaming-delta-v1`,
  `streaming-case-v1`; compatibility manifest schema allows `stream-*`
  capability names without claiming them implemented. Schema valid/invalid
  vectors under `contracts/vectors/streaming/`. Stream engines not implemented
  yet; no runtime capability claims.

- Documented locked **live session streaming** design (library stream state
  machine, snapshot + delta envelopes, file JSONL + AHP + optional Hermes
  provider, all four runtimes) and delivery plan LS-00…LS-12. Spec:
  `docs/live-session-streaming.md`; plan:
  `docs/live-session-streaming-plan.md`.

## 0.1.2 — 2026-08-11

Tip release merging `develop` (Grok Build + listing titles) onto `main` (Python + AHP).

- Added **Grok Build** (`grok-build`, CLI alias `grok`) multi-runtime source adapters
  and shared conformance fixtures across **.NET, TypeScript, Rust, and Python**.
  Native container is `chat_history.jsonl` under `$GROK_HOME/sessions` or
  `~/.grok/sessions` (`<cwd-dir>/<session-uuid>/…`). Supports system/user/assistant
  turns, tool calls, backend tool synthesis, optional encrypted reasoning via
  `source_context.include_encrypted_reasoning`, and explicit-root listing with
  `summary.json` titles. Advertised in `contracts/compatibility.json` and all
  runtime capability manifests (slice `ML13`).

- Listing **titles** for Codex / Claude Code / Pi / OpenClaw (skip harness injection
  noise) and optional `title` on listing items; Grok Build titles from
  `generated_title` / `session_summary`.

- **Conflict resolution (release merge):** unioned tip sources to
  `pi`, `claude-code`, `codex`, `openclaw`, `hermes`, `ahp`, `grok-build` across
  compatibility manifests, peer/Python capability files, CI gates, and
  `tools/validate_release_metadata.py`. Python gained a Grok Build adapter/lister
  for tip equality with peers; normalizer/projection map `meta` message roles for
  Grok system and synthetic-user rows. Identity baseline is the union of main
  (AHP) and develop (Grok Build) hashes.

## 0.1.1 — 2026-08-05

Synchronized multi-registry release (NuGet / npm / crates / **PyPI**). Do not
retag or republish `0.1.0`.

- Added **Python** native runtime `hypabolic-trajectory` (import
  `hypabolic_trajectory`) under PyPI org Hypabolic: decode → normalize →
  project for tip sources (`pi`, `claude-code`, `codex`, `openclaw`, `hermes`,
  `ahp`), listing, pure OTEL GenAI projection + optional `[otel]` extra,
  shared conformance runner, CI gates, and OIDC publish path. Package version
  locksteps with NuGet/npm/crates on this tag.
- Added **AHP** (Agent Host Protocol) Shape A offline snapshot source adapters
  across .NET, TypeScript, Rust, and Python. Wire source name is `ahp`;
  protocol pin is `0.7.x` (`conformance/vendor/ahp/PROTOCOL_VERSION`). Phase 1
  covers offline ChatState export envelopes (`ahp-export-v1`), chat-unit
  identity, cancel-safe tool mapping (including `toolCall.error.message`), and
  shared conformance cases `ahp/tool-calls`, `ahp/multi-turn`, and
  `ahp/cancelled-turn`.
- **Deferred at 0.1.1 (later tip work):** AHP Shape B action-log reduce (now
  LS-07 core on tip), export-directory listing (Phase 3 empty stubs only),
  multi-chat unpack, and live host / WebSocket clients. See
  `docs/ahp-ingest-status.md` and `docs/ahp-source-spec.md`.

## 0.1.0 — 2026-07-01

First public multi-ecosystem release of Trajectory.

Published registry packages at this version advertise sources
`pi`, `claude-code`, `codex`, `openclaw`, and `hermes` only (**no** `ahp`).

- Hardened **versioned releases**: root `VERSION` file as single source of
  truth; `tools/set_package_version.py` / `extract_changelog.py`; Release
  workflow packs version-stamped NuGet/npm/crates artifacts, publishes to all
  three registries, and creates a GitHub Release with changelog notes and
  attached packages. Tag `vX.Y.Z` (after VERSION sync) or dispatch with
  bump/version. Operator guide: `docs/publishing.md`.

- Added multi-registry **Release** workflow baseline, MIT `LICENSE`, NuGet
  package metadata, npm OIDC steady-state publish, and local
  `./tools/bootstrap_npm_packages.sh` for first `@hypabolic/*` create.

- Completed ML13 1.0 parity and release hardening. Runtime capability manifests
  advertise slice `ML13` with the full v1 source set (`pi`, `claude-code`,
  `codex`, `openclaw`, `hermes`) and six deterministic outputs. CI capability
  gates and `tools/validate_release_metadata.py` require Hermes and ML13
  agreement; preview packaging remains dry-run only with provenance evidence
  tied to commit and contract version. Documented privacy/fixture sanitization,
  upgrade guidance, and product-level 1.0 readiness criteria in
  `docs/release-readiness.md`.

- Added unpublished sample CLIs for local session browsing across .NET
  (`dotnet/samples/Trajectory.Cli`), TypeScript (`@hypabolic/trajectory-cli`),
  and Rust (`trajectory-cli`). Each lists default agent-store roots, supports
  `--root` / env overrides, interactive session pick, and privacy-safe
  and Hypabolic summaries (`--show-content` is opt-in with a warning).

- Added Hermes source adapters and shared conformance fixtures across .NET,
  TypeScript, and Rust. Message-row arrays and session envelopes normalize with
  soft-delete filtering, AUTOINCREMENT ordering, multimodal `\u0000json:` content,
  reasoning aliases, OpenAI and id-less tool-call shapes, and epoch-second
  timestamps. Wire source name is always `hermes`. Core packages remain
  SQLite-free; missing Hermes stores list as empty pages.

- Added OpenClaw (Pi-family) source adapters, listing, and shared conformance
  fixtures across .NET, TypeScript, and Rust. Delivery-mirror model placeholders
  keep assistant prose but are excluded from model metadata.

- Established language-neutral contracts and shared conformance assets.
- Added native .NET, TypeScript, and Rust implementations for Pi, Claude Code,
  and Codex.
- Added deterministic message trajectory, canonical identity, Hypabolic, OpenAI
  chat, minimal JSONL, and OpenTelemetry GenAI span projections across all
  three runtimes.
- Added explicit-root listing, typed diagnostics and fatal errors, incremental
  writer surfaces, optional telemetry package boundaries, preview packaging
  evidence, and representative benchmarks.

The normalizer contract remains `0.2.0`; existing identity-bearing golden bytes
are unchanged.
