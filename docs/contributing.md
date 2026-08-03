# Contributing to Trajectory

Thanks for contributing. Trajectory is a multi-language product: changes that
affect wire behaviour must stay aligned across **.NET**, **TypeScript**, and
**Rust**, with shared evidence under `contracts/` and `conformance/`.

## Before you start

1. Read the [architecture](architecture.md) overview.
2. Skim the normative specs in [`contracts/spec/`](../contracts/spec/).
3. If you are adding a **source** or **output**, follow
   [adapter authoring](adapter-authoring.md).
4. Keep fixtures sanitized ([privacy rules](release-readiness.md#privacy-and-fixture-sanitization)).

## Development setup

Clone the repo and use the toolchain for the runtime you touch:

| Runtime | Requirements |
| --- | --- |
| .NET | SDK for `net8.0` / `net9.0` / `net10.0` (tests/conformance on `net10.0`) |
| TypeScript | Node.js 22+ |
| Rust | 1.85 (MSRV) and stable |

```bash
# .NET
dotnet restore dotnet/Trajectory.sln
dotnet test dotnet/tests/Trajectory.Tests/Trajectory.Tests.csproj -c Release

# TypeScript
cd typescript && npm ci && npm test

# Rust
cargo test --manifest-path rust/Cargo.toml --workspace --locked
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets -- -D warnings
```

### Shared conformance (required for behaviour changes)

Build a runner, then:

```bash
# .NET
dotnet build dotnet/tests/Trajectory.Conformance/Trajectory.Conformance.csproj -c Release
python3 conformance/verify.py --repository-root . -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll

# TypeScript
cd typescript && npm run build
python3 conformance/verify.py --repository-root . -- \
  node typescript/packages/trajectory-testing/dist/cli.js

# Rust
cargo build --manifest-path rust/Cargo.toml --release --bin trajectory-conformance
python3 conformance/verify.py --repository-root . -- \
  rust/target/release/trajectory-conformance
```

Filter by source while iterating:

```bash
python3 conformance/verify.py --repository-root . --source pi -- \
  rust/target/release/trajectory-conformance
```

## What belongs where

| Path | Put here |
| --- | --- |
| `contracts/` | Normative schemas and behavioural specs |
| `conformance/cases/` | Shared inputs + reviewed goldens (all runtimes must match) |
| `conformance/stores/` | Declarative listing store fixtures |
| `dotnet/`, `typescript/`, `rust/` | Idiomatic runtime code and unit fixtures |
| `docs/` | Product and contributor documentation |

A fixture is shared only when another independent implementation must produce
the same observable result. Parser unit fixtures stay under the runtime.

## Pull request checklist

- [ ] Behaviour change has or updates a shared conformance case (when applicable).
- [ ] Goldens are hand-reviewed; CI never regenerates and accepts in one step.
- [ ] All three runtimes updated for source/output/capability changes (or an
      explicit temporary capability gap is documented and not advertised).
- [ ] `contracts/compatibility.json` and runtime `runtime-capabilities.json`
      stay in sync when capabilities change.
- [ ] Diagnostics remain content-safe (no transcript secrets in messages).
- [ ] Identity-bearing bytes unchanged under the same normalizer contract
      version (`0.2.0`), or a contract version bump is included.
- [ ] Package versions remain synchronized (currently checked-in `VERSION` is
      `0.1.0`) unless this PR is an intentional version bump across NuGet, npm,
      and crates. **New advertised sources or outputs** (AHP is the first
      post-`0.1.0` case) require a synchronized package version bump before the
      next public registry release; do not retag or expect republish of
      `0.1.0` to deliver them.
- [ ] Sample CLIs still build if listing/normalize surfaces changed.
- [ ] Docs updated (README / adapter authoring / this file as needed). Distinguish
      published registry capability from repository-tip capability when they differ.

## Code style

- Match existing patterns in the runtime you edit.
- Prefer small, reviewable commits with clear intent.
- Do not introduce reflection-based adapter discovery on the .NET core path.
- Rust: `unsafe` is forbidden in the core crate; keep clippy clean with
  `-D warnings` as in CI.

## Sample CLIs

The unpublished TUIs under `dotnet/samples/Trajectory.Cli`,
`typescript/packages/trajectory-cli`, and `rust/tools/trajectory-cli` are the
fastest way to exercise listing and normalize against real local stores. See
the [README sample CLI section](../README.md#sample-clis) and each sample’s
own README.

## Reporting issues

Use the GitHub **issue forms** (Bug report, Feature request, Documentation,
Question). Include:

- runtime and package version;
- source family and whether the input is whole or partial;
- a **sanitized** minimal fixture (never paste production secrets);
- expected vs actual behaviour (hashes, diagnostics codes, or structural
  mismatch)—not full private transcripts.

Pull requests should use the repository **PR template** checklist
(`.github/PULL_REQUEST_TEMPLATE.md`).

## License

By contributing, you agree that your contributions are licensed under the
repository [MIT License](../LICENSE).
