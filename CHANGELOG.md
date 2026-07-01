# Changelog

All notable product and contract changes are recorded here. Package versions
remain synchronized across NuGet, npm, and crates.io preview artifacts.

## 0.1.0 — Unreleased

- Added multi-registry **Release** workflow (`.github/workflows/release.yml`) to
  pack and publish synchronized NuGet, npm, and crates.io packages from a
  version tag (`v*.*.*`) or `workflow_dispatch`. Includes dry-run mode, package
  content checks, npm provenance, crates.io ordered publish with index retry,
  GitHub Release notes/assets, MIT `LICENSE`, shared NuGet metadata
  (`dotnet/Directory.Build.props`), and `tools/assert_release_version.py`.
  Steady-state npm uses **OIDC trusted publishing**. First-time
  `@hypabolic/*` package creation is a local CLI bootstrap
  (`./tools/bootstrap_npm_packages.sh` / `npm publish` from a logged-in
  machine) so Trusted Publisher can be configured afterward—no long-lived
  npm token in GitHub. Operator guide: `docs/publishing.md`.

- Completed ML13 1.0 parity and release hardening. Runtime capability manifests
  advertise slice `ML13` with the full v1 source set (`pi`, `claude-code`,
  `codex`, `openclaw`, `hermes`) and six deterministic outputs. CI capability
  gates and `tools/validate_release_metadata.py` require Hermes and ML13
  agreement; preview packaging remains dry-run only with provenance evidence
  tied to commit and contract version. Documented privacy/fixture sanitization,
  intentional differences from the pinned upstream oracle, upgrade guidance,
  and product-level 1.0 readiness criteria in `docs/release-readiness.md`.
  Packages are not published.

- Added unpublished sample CLIs for local session browsing across .NET
  (`dotnet/samples/Trajectory.Cli`), TypeScript (`@hypabolic/trajectory-cli`),
  and Rust (`trajectory-cli`). Each lists default agent-store roots, supports
  `--root` / env overrides, interactive session pick, and privacy-safe Letta
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
- Added deterministic Letta trajectory, Letta canonical, Hypabolic, OpenAI
  chat, minimal JSONL, and OpenTelemetry GenAI span projections across all
  three runtimes.
- Added explicit-root listing, typed diagnostics and fatal errors, incremental
  writer surfaces, optional telemetry package boundaries, preview packaging
  evidence, and representative benchmarks.

The normalizer contract remains `0.2.0`; existing identity-bearing golden bytes
are unchanged.
