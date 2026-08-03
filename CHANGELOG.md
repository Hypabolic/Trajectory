# Changelog

All notable product and contract changes are recorded here. Package versions
remain synchronized across NuGet, npm, and crates.io preview artifacts.

## [Unreleased]

Post-`0.1.0` work on branch tip (not yet published to NuGet / npm / crates).
The next public capability release that includes these items **must** use a new
tag (for example `v0.1.1` or `v0.2.0`); do not retag or republish `0.1.0`.

- Added **AHP** (Agent Host Protocol) Shape A offline snapshot source adapters
  across .NET, TypeScript, and Rust. Wire source name is `ahp`; protocol pin is
  `0.7.x` (`conformance/vendor/ahp/PROTOCOL_VERSION`). Phase 1 covers offline
  ChatState export envelopes (`ahp-export-v1`), chat-unit identity, cancel-safe
  tool mapping (including `toolCall.error.message`), and shared conformance
  cases `ahp/tool-calls`, `ahp/multi-turn`, and `ahp/cancelled-turn`. Branch-tip
  `contracts/compatibility.json` and runtime capability manifests advertise
  `ahp` alongside the published v1 sources.
- **Deferred (not shipped):** Shape B action-log reduce, export-directory
  listing (Phase 3 empty stubs only), multi-chat unpack, and live host /
  WebSocket clients. See `docs/ahp-ingest-status.md` and
  `docs/ahp-source-spec.md`.

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
