# Changelog

All notable product and contract changes are recorded here. Package versions
remain synchronized across NuGet, npm, and crates.io preview artifacts.

## [Unreleased]

- **Source:** added Cursor Agent (`cursor`) transcript JSONL decoding, listing,
  and file streaming across all four runtimes.
- **Docs:** removed delivery-plan / status trackers
  (`implementation-plan`, `multi-language-plan`, `live-session-streaming-plan`,
  `live-session-streaming-status`, `python-implementation-spec`,
  `python-impl-status`, `ahp-ingest-status`). Root README now documents how to
  follow a live session from the sample CLIs and optional I/O packages.

## 0.1.3 — 2026-08-13

Live session streaming cut. Pure library engines on all four runtimes, optional
file I/O / AHP client / Hermes provider packages, honest `stream-*` capability
claims, and sample CLI follow of real local sessions. **Do not retag `v0.1.2`.**
This is the first synchronized multi-registry publish of the stream surface
(NuGet / npm / crates / PyPI).

- **Sample CLI live watch tail:** `stream` / `browse --watch` print the latest
  records (tail) with wall-clock and record timestamps, plus a ticking
  `watching` heartbeat while waiting for new complete JSONL lines. The previous
  view showed the first 40 snapshot records and then went silent, so a long
  live session looked historical.

- **Sample CLI picker last-active times:** session pickers and `list` tables
  show relative last-active labels (`just now`, `5m ago`, …) and keep most
  recently active sessions first (title + id; no huge native paths in the
  picker). Same shape on all four unpublished sample CLIs.

- **Sample CLI live-session picker:** `browse` asks Watch live / Show snapshot
  after you pick a session (file JSONL sources). `--watch` skips the prompt and
  follows immediately. `stream` without `--path`/`--id` picks from the resolved
  store on a TTY and follows. Same shape on all four unpublished sample CLIs.

- **Streaming review-fix (H1–H4, M1–M8):** Codex Luna deep-review findings
  are closed on tip (see `docs/live-session-streaming-status.md`). Shared
  stream matrix is **41** cases. High: AHP `reorder=reject`, diagnostic
  catalog sanitization, stable `prov-active:{native_id}`, transactional
  file-host finish flush. Medium: `auto-reset` + replacement material,
  same-size file identity, unknown delta-op reject, JSON-safe integers,
  .NET AOT/Hermes claims, verifier honesty for advertised stream caps,
  Hermes `hermes-provider-*` corpus, post-`0.1.2` publish + artifact
  verify. No root `VERSION` bump.

- **Streaming M7/M8 residuals:** LS-07h plan status matches the shipped
  `hermes-provider-*` export-apply corpus (SQLite/query I/O stays
  package-test-gated). Release workflow examples use a post-`0.1.2` tag
  (`v0.1.3`). `skip-duplicate` / already-published / `skip-existing` fallbacks
  now fail unless `tools/verify_published_stream_artifact.py` proves the
  registry artifact contains stream capability manifests and APIs.

- **Streaming M5–M8:** .NET `Trajectory.Ahp` stays trim/AOT-clean (`JsonNode.WriteTo`,
  `IL2026`/`IL3050` as errors). `Trajectory.Hermes` **stops claiming**
  AOT/trim compatibility (native SQLite) and pins
  `SQLitePCLRaw.lib.e_sqlite3` 3.50.3 to clear NU1903 / CVE-2025-6965.
  `conformance/verify.py` fails `unsupported` when the runner advertises
  required core `stream-*` capabilities (`--capabilities-file`; default
  `compatibility.json` required). Shared `hermes-provider-append` /
  `hermes-provider-soft-delete` / `hermes-provider-invalidation` stream-sequence
  cases exercise core `apply_hermes_export` (41 stream cases). **Next
  registry publish is a new synchronized tag after `0.1.2`** — do not retag
  `v0.1.2` for the stream cut.

- **Streaming M1–M4 (all four runtimes):** `reset_policy=auto-reset` atomically
  installs a new generation when the same call supplies replacement material
  (default remains `return-reset-required`). File hosts detect same-size
  replacement via inode/device and mtime even when `reconcile_every=0`.
  .NET file hosts now populate `VolumeSerial`/`FileIndex` from
  `GetFileInformationByHandle` / Unix `stat` (not stub zeros). Delta apply
  rejects unknown operations with `invalid_input` and leaves prior snapshot
  state unchanged. Stream wire integers are constrained to the JSON
  safe-integer domain (`±9007199254740991`); Rust and .NET serializers/parsers
  reject overflow with `invalid_input` (no decimal-string fallback).

- **Streaming H1/H3 fixes (all four runtimes):** AHP reducers enforce
  `reorder=reject` — reject non-monotonic / duplicate `serverSeq` and mixed
  sequenced+unsequenced batches without advancing the cursor (no silent
  sort-then-apply). Active-turn provisional IDs are derived from stable
  turn/part/tool native IDs (`prov-active:{native_id}`) with mapping persisted
  in `StreamState`. Shared stream cases expanded (then 38; now 41 with M7): batch reorder /
  duplicate / mixed-seq negatives and multi-part provisional growth+finalize.

- **Streaming H2 diagnostic sanitization (all four runtimes):** stream
  projection never forwards raw normalizer messages. Wire diagnostics keep
  stable codes plus safe structural fields and a fixed catalog; source-native
  IDs, paths, and AHP body text cannot leak via snapshot/top-level
  diagnostics, delta diagnostic ops, or stream errors.

- **Streaming H4 file-host finish flush (all four runtimes):** optional file
  I/O treats pending flush as transactional — retain the host buffer until
  core `apply_append` succeeds, propagate non-success updates, and do not
  call `finish()` after a failed flush.

- **Live session streaming packaging docs:** tip status note for LS-00…LS-12
  shipped vs post-LS-12 remaining work
  (`docs/live-session-streaming-status.md`); plan slice status markers; cross-
  links from product spec, architecture, multi-language plan, and
  release-readiness.

- **LS-12 capability claims and release gate:** advertise completed live-session
  streaming honestly. `contracts/compatibility.json` required list and all four
  core `runtime-capabilities.json` (dotnet / typescript / rust / python) claim
  core `stream-*` capabilities after the LS-08 matrix is green on every runtime.
  Optional package caps (`stream-file-io`, `stream-async-iterator`,
  `stream-ahp-client`, `stream-hermes-provider`) live only on optional package
  `package-capabilities.json` manifests — not on core. Unimplemented
  `stream-file-watch` / `stream-ahp-list-sessions` are not claimed. Docs, CI tip
  equality, release-metadata validators, progressive capability maps, and
  privacy/schema gates updated. No root `VERSION` bump / no registry publish.

- **LS-11 sample CLI stream commands (all four ecosystems):** unpublished
  sample CLIs gain `stream` (JSONL file follow via optional file I/O + core
  apply; default emit `snapshot+delta`; `--follow` is process-owned, not a
  daemon) and `ahp-stream` (optional AHP client demo with in-memory
  `fake://` FakeAhpHost + fixture paths; live WebSocket remains
  consumer-injected `AhpTransport`). Privacy-safe defaults with
  `--show-content` opt-in. Automated tests use temp stores and fake hosts
  only.

- **LS-10 optional AHP client packages (all four ecosystems):** transport-only
  live-host clients (`Hypabolic.Trajectory.Ahp`, `@hypabolic/trajectory-ahp`,
  `hypabolic-trajectory-ahp`, `hypabolic_trajectory.ahp_client`) with injected
  transport, auth callback, subscribe/snapshot/action feed into core
  `apply_ahp_*`, auto-resync on sequence gap, cancel, and backpressure.
  Fake-host CI tests cover gap, replay, cancel, backpressure, and auth failure.
  Auth never appears in stream snapshots/deltas/diagnostics. Docs:
  `docs/ahp-client.md`. Not imported by core by default; **do not** advertise
  `stream-ahp-client` until LS-12.

- **LS-09 optional file I/O packages (all four ecosystems):** path poll/follow
  helpers that feed only complete-line material into core `apply_snapshot` /
  `apply_append`. Packages: `Hypabolic.Trajectory.IO`,
  `@hypabolic/trajectory-node` (extended), `hypabolic-trajectory-io`,
  `hypabolic-trajectory[io]` (`hypabolic_trajectory.io`). Explicit root required;
  host errors (`FileStreamHostError` / peers) are not transcript diagnostics.
  Docs: `docs/streaming-file-io.md`. Core packages gain no I/O dependencies.
  **Do not** advertise `stream-file-io` / `stream-file-watch` until LS-12.

- **LS-08 full stream matrix gate (all four runtimes):** complete shared
  `conformance/cases/streaming/**` corpus with per-step `expected.result`
  goldens; `stream-oracle-parity` expanded across file-JSONL growth/reset cases;
  TypeScript provenance parity with peers (omit synthetic `source_sequence: 0`
  for Claude/Codex/Grok; surface Codex `cli_version` on provenance); oracle
  null-snapshot ≡ empty incomplete snapshot for pure-pending; docs DoD in
  `adapter-authoring.md`, `release-readiness.md`, `multi-language-plan.md`.
  Batch normalize/list still green. **Do not** advertise `stream-*` capabilities
  until LS-12.

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
