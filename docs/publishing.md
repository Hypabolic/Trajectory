# Publishing Trajectory packages

This repository publishes **synchronized** library packages to three registries
from a single git commit and version:

| Ecosystem | Packages | Registry |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory`, `Hypabolic.Trajectory.OpenTelemetry`, `Hypabolic.Trajectory.Testing` | NuGet.org |
| TypeScript | `@hypabolic/trajectory`, `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel` | npm |
| Rust | `hypabolic-trajectory`, `hypabolic-trajectory-opentelemetry` | crates.io |

Not published (private / sample / test infrastructure):

- `@hypabolic/trajectory-testing`, `@hypabolic/trajectory-cli`
- `trajectory-conformance`, `trajectory-cli` (Rust binaries)
- `dotnet/samples/Trajectory.Cli`

Package versions must stay synchronized pre-1.0 because the normalizer version
participates in canonical identity. The capability slice and source set are
gated by `tools/validate_release_metadata.py`.

## Prerequisites (one-time)

### 1. Registry accounts and package ownership

- **NuGet.org** — organization or user that may push `Hypabolic.*` package IDs.
- **npm** — access to the `@hypabolic` scope (create the org if needed).
- **crates.io** — owner for `hypabolic-trajectory` and
  `hypabolic-trajectory-opentelemetry`.

### 2. GitHub repository secrets

Configure under **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
| --- | --- |
| `NUGET_API_KEY` | NuGet.org API key with **Push** permission |
| `NPM_TOKEN` | npm **Automation** token that can publish `@hypabolic/*` |
| `CARGO_REGISTRY_TOKEN` | crates.io API token |

`GITHUB_TOKEN` is provided automatically for creating GitHub Releases and for
npm provenance (`id-token: write`).

### 3. GitHub Environment `release`

The live publish jobs use the **`release`** environment. Create it under
**Settings → Environments** and optionally require:

- required reviewers before deploy;
- deployment branches restricted to `main` (and tags).

Dry runs do not require secrets and do not use the environment for registry
writes.

### 4. Local package metadata gate

```bash
python3 tools/validate_release_metadata.py --repository-root .
python3 tools/assert_release_version.py --repository-root . --version 0.1.0
```

Both must pass before tagging.

## Release process

### Preferred: tag on `main`

1. Merge the release-ready PR to `main` (CI green).
2. Confirm version strings are synchronized at the intended SemVer (today
   `0.1.0` in NuGet csproj, npm `package.json`, Rust workspace, and
   `tools/validate_release_metadata.py`).
3. Update `CHANGELOG.md` — move Unreleased notes under the version heading.
4. Tag and push:

   ```bash
   git checkout main
   git pull
   git tag -a v0.1.0 -m "Trajectory 0.1.0"
   git push origin v0.1.0
   ```

5. The **Release** workflow runs automatically (`dry_run=false`):
   - validates metadata and version;
   - packs NuGet / npm / crates artifacts;
   - publishes to all three registries;
   - creates a GitHub Release with notes and attached packages.

### Manual: workflow_dispatch

1. Actions → **Release** → **Run workflow**.
2. Leave **dry_run** checked to pack-only, or uncheck to publish.
3. Set **version** (e.g. `0.1.0`) when not running from a tag.

### Dry run only

```text
workflow_dispatch with dry_run=true
```

Produces the `trajectory-release-<version>` artifact (nupkg, tgz, crate,
checksums, provenance) without calling registries. Use this to verify packing
before the first real publish.

## What each job does

| Job | Dry run | Live publish |
| --- | --- | --- |
| `resolve` | Version / tag / mode | Same |
| `validate` | Pack + content checks + consumer smoke | Same |
| `publish-nuget` | Skipped | `dotnet nuget push` (+ symbols) |
| `publish-npm` | Skipped | `npm publish --access public --provenance` in dep order |
| `publish-crates` | Skipped | `cargo publish` core then otel (with index retry) |
| `github-release` | Summary only | GitHub Release + asset upload |

## Bumping the version

Edit **all** of the following to the same SemVer in one commit:

1. `tools/validate_release_metadata.py` → `VERSION`
2. `dotnet/src/Trajectory*/**/*.csproj` → `<Version>`
3. `typescript/package.json` and each `typescript/packages/*/package.json`
4. `rust/Cargo.toml` → `[workspace.package] version` (and path dep pins if present)
5. `CHANGELOG.md`

Then re-run:

```bash
python3 tools/assert_release_version.py --repository-root . --version <new>
python3 tools/validate_release_metadata.py --repository-root .
```

Do not publish mismatched versions across ecosystems.

## Consumer install (after first publish)

```bash
# .NET
dotnet add package Hypabolic.Trajectory --version 0.1.0

# TypeScript
npm install @hypabolic/trajectory@0.1.0

# Rust
cargo add hypabolic-trajectory@0.1.0
```

Optional telemetry packages:

```bash
dotnet add package Hypabolic.Trajectory.OpenTelemetry --version 0.1.0
npm install @hypabolic/trajectory-otel@0.1.0
cargo add hypabolic-trajectory-opentelemetry@0.1.0
```

## Failure and recovery

- **`--skip-duplicate` (NuGet)** — re-running a successful version is a no-op.
- **npm / crates** — re-publishing the same version fails; bump or yank per
  registry policy (prefer a patch bump over yank for public consumers).
- **crates.io index lag** — the workflow retries the OpenTelemetry crate publish
  after the core crate lands.
- **Secret missing** — live publish jobs fail fast with a clear message; fix
  secrets and re-run the failed jobs (or re-push the tag if needed).

## Related docs

- [Release readiness](release-readiness.md) — ML13 gates and 1.0 criteria
- [Compatibility policy](../README.md#compatibility-policy)
- CI dry-run job `preview-release` in `.github/workflows/ci.yml` (every main/PR)
