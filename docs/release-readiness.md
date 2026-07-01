# Release readiness (0.1.0 preview and 1.0 criteria)

This document records ML13 release hardening: package/capability agreement,
preview packaging policy, provenance, privacy, upgrade guidance, intentional
differences from the pinned upstream oracle, and the criteria that must hold
before a product-level 1.0.

Packages are **not** published from this repository yet. CI dry-runs NuGet, npm,
and crates packaging only.

## Current preview status

| Item | Value |
| --- | --- |
| Package version (all ecosystems) | `0.1.0` (synchronized, unpublished) |
| Capability slice | `ML13` |
| Normalizer contract | `0.2.0` |
| Conformance protocol | `1` |
| Diagnostics contract | `1` |
| Upstream oracle | `letta-ai/trajectory@f165ecf0af35da40512a288c4380a36b3102403c` (`0.2.0`) |
| v1 required sources | `pi`, `claude-code`, `codex`, `openclaw`, `hermes` |
| Deterministic outputs | Letta trajectory, Letta canonical, Hypabolic, OpenAI chat, minimal JSONL, OTEL GenAI spans |

Authoritative machine-readable surfaces:

- [`contracts/compatibility.json`](../contracts/compatibility.json) — product contract and implemented sources/outputs;
- `typescript/packages/trajectory/runtime-capabilities.json` and
  `rust/crates/hypabolic-trajectory/runtime-capabilities.json` — per-runtime
  advertised capabilities (must match the product manifest for ML13);
- `tools/validate_release_metadata.py` — synchronized version and capability gate;
- CI `preview-release` job — dry-run packaging, checksums, provenance artifact.

## Provenance and traceability

Every preview evidence bundle produced by CI records:

- package version (`0.1.0`);
- capability slice (`ML13`);
- normalizer contract version;
- upstream pin commit;
- source commit (`GITHUB_SHA` when run in GitHub Actions, else `SOURCE_COMMIT`);
- SHA-256 digests of compatibility, capability, and package metadata files;
- SHA-256 digests of dry-run package archives under `artifacts/preview/`.

To regenerate metadata validation and optional provenance locally:

```bash
python3 tools/validate_release_metadata.py \
  --repository-root . \
  --evidence artifacts/evidence/provenance.json
```

Preview packaging remains dry-run only:

- NuGet: `dotnet pack` into `artifacts/preview/nuget` and install into a temp consumer;
- npm: `npm pack` and `npm publish --dry-run` for public workspaces;
- crates: `cargo package` for the core crate and file-list evidence for the
  optional OpenTelemetry crate.

Do not run live `dotnet nuget push`, `npm publish` (without `--dry-run`), or
`cargo publish` from this repository until an explicit publish decision.

## Privacy review and fixture sanitization

ML13 privacy review conclusion: checked-in conformance fixtures, store
fixtures, diagnostic messages, and golden outputs do not contain real user
secrets, live API keys, personal email addresses, or production filesystem
paths belonging to end users.

### Fixture sanitization rules

Shared fixtures under `conformance/` must be authored as **synthetic or
sanitized** native shapes:

1. Prefer the smallest invented transcript that exposes one behaviour.
2. Replace real home directories, org names, emails, and hostnames with
   portable placeholders (`/workspace/demo`, `hermes-session-0001`, etc.).
3. Never paste production tool arguments, prompts, credentials, cookies, or
   private keys into inputs or expected outputs.
4. Base64 or binary-looking payloads in fixtures must be synthetic (for example
   `AAAA`) and non-functional.
5. Listing fixtures use declarative temporary stores under `conformance/stores/`;
   runners substitute `$ROOT` so developer machine paths never land in goldens.
6. Diagnostic and fatal-error text must obey
   [`contracts/spec/diagnostics.md`](../contracts/spec/diagnostics.md) content
   safety: no transcript prose, tool args/results, raw JSON excerpts, secrets,
   or developer paths.

When importing behaviour observed against real transcripts or the upstream
oracle, rewrite the case into a sanitized minimal vector before checking it in.
CI never regenerates and accepts goldens in one step; reviewed expected files
are the only accepted outputs.

Sample CLIs default to privacy-safe summaries and require an explicit
`--show-content` opt-in that prints a warning before emitting transcript prose.

## Intentional differences from upstream Letta

Trajectory is behaviourally inspired by the pinned `letta-ai/trajectory`
package but is an independent Hypabolic product. Known intentional differences:

| Area | Trajectory policy |
| --- | --- |
| Implementation | Three native runtimes (.NET, TypeScript, Rust); no vendored upstream source |
| Authority | Hypabolic contracts, schemas, and shared conformance cases — not upstream source layout |
| Sources (v1) | Pi, Claude Code, Codex, OpenClaw, Hermes; Letta Code / OpenHands / Deep Agents deferred post-v1 |
| OpenClaw | Delivery-mirror model placeholders keep assistant prose but are excluded from model metadata |
| Hermes listing | Core packages remain SQLite-free; missing Hermes stores list as empty pages |
| Canonical JSON | Documented Trajectory algorithm (UTF-16 code-unit key sort, compact UTF-8); not RFC 8785/JCS |
| Hypabolic output | Additive provenance schema; not an upstream format |
| OpenTelemetry | Deterministic GenAI span projection with optional SDK packages outside core |
| Package surface | Ecosystem-native APIs; wire/behavioural parity, not identical language APIs |
| Internal IR | Implementation-private; not a public interchange format |

The upstream package may be executed as a black-box oracle for historical Letta
outputs. Gaps discovered that way become Hypabolic normative rules and shared
vectors first, then implementations.

See also [`upstream-reference.md`](upstream-reference.md) for pin-update
procedure.

## Upgrade guidance (pre-1.0 → later releases)

While packages remain at synchronized `0.1.0`:

- normalizer contract version `0.2.0` participates in identity-bearing
  canonical output; changing identity under the same contract version is
  forbidden;
- new sources or outputs must update `contracts/compatibility.json`, both
  runtime capability manifests, shared cases, and CI capability gates together;
- intentional wire or diagnostic changes require a contract or schema version
  decision before goldens move;
- public language APIs may still evolve before the first published release;
  consumers should pin against wire contracts and conformance, not unpublished
  method names.

After the first published release:

1. Publish NuGet, npm, and crates from the **same** git commit and tag.
2. Keep normalizer contract version explicit in canonical output so ecosystem
   package versions may later diverge without breaking identity semantics.
3. Document migration notes in `CHANGELOG.md` for any consumer-visible change
   to sources, diagnostics, listing, or output schemas.
4. Never re-purpose diagnostic or fatal-error codes; only add.

## Product-level 1.0 readiness criteria

A product `1.0.0` release may proceed only when all of the following hold.
ML13 completes the engineering readiness work; the remaining items are
**release-process gates**, not missing runtime capabilities.

### Capability (complete at ML13)

- [x] Every required conformance case passes for all advertised capabilities on
      .NET, TypeScript, and Rust.
- [x] Package compatibility manifests agree (`compatibility.json` + both
      `runtime-capabilities.json` at slice `ML13`).
- [x] CI capability `jq` checks and `validate_release_metadata.py` include
      Hermes and the full v1 source/output set.
- [x] Privacy review and fixture sanitization rules are documented; fixtures
      and diagnostics contain no real user secrets.
- [x] Preview packaging dry-runs produce evidence traceable to commit and
      contract version.
- [x] Upgrade and intentional-difference documentation is complete.
- [x] Post-v1 sources (Letta Code, OpenHands, Deep Agents) are **not**
      advertised as v1 capabilities.

### Publish process (remaining for an actual 1.0/0.1 publish)

- [ ] Explicit product decision to publish packages (still dry-run only).
- [ ] Signed or otherwise attested release from one tagged commit.
- [ ] Live publish of synchronized NuGet, npm, and crates artifacts with
      matching versions and the same normalizer contract version.
- [ ] Public package READMEs and registry metadata point at this repository's
      contracts and pin.
- [ ] Optional: broaden property/fuzz corpus beyond the current representative
      suites if production traffic surfaces new classes of input.

### Explicit non-blockers for v1

- Letta Code (ML8), OpenHands (ML10), and Deep Agents (ML12) remain post-v1.
- Optional Hermes SQLite store providers remain optional and outside core.
- Browser/Wasm, cloud ingestion, and identical language APIs remain non-goals.
