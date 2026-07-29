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
- **npm** — create / own the **`@hypabolic` organization** on npmjs.com and
  ensure your user can create packages under that scope.
- **crates.io** — owner for `hypabolic-trajectory` and
  `hypabolic-trajectory-opentelemetry`.

### 2. GitHub Environment `release`

Live publish jobs use the **`release`** environment. Create it under
**Settings → Environments**. Optionally require reviewers and restrict
deployment branches to `main` (and tags).

The environment name **`release`** must match the Trusted Publisher
configuration on npm (see bootstrap below).

### 3. GitHub repository secrets

| Secret | When required |
| --- | --- |
| `NUGET_API_KEY` | Every live multi-registry Release |
| `CARGO_REGISTRY_TOKEN` | Every live multi-registry Release |
| `NPM_TOKEN` | **Bootstrap only** — first create of `@hypabolic/*` (or recovery). Not used for steady-state OIDC publishes. |

Steady-state npm publishes use **OIDC trusted publishing** (`id-token: write`);
they do **not** read `NPM_TOKEN`.

### 4. Local package metadata gate

```bash
python3 tools/validate_release_metadata.py --repository-root .
python3 tools/assert_release_version.py --repository-root . --version 0.1.0
```

Both must pass before tagging.

## npm: bootstrap `@hypabolic` (required once)

npm **Trusted Publishing** (OIDC) can only be configured on a package that
already exists. The first version must therefore be published with a token.

### Packages created

- `@hypabolic/trajectory`
- `@hypabolic/trajectory-node`
- `@hypabolic/trajectory-otel`

### Bootstrap steps

1. On [npmjs.com](https://www.npmjs.com), create the **`@hypabolic`** org (if
   it does not exist) and invite the publishing account.
2. Create a **granular access token** with permission to publish under
   `@hypabolic` (and to create packages on first publish). Store it briefly as
   the GitHub Actions secret **`NPM_TOKEN`**.
3. Ensure the GitHub Environment **`release`** exists (same name as will be used
   for OIDC).
4. Merge packaging changes to `main` (CI green).
5. Actions → **npm bootstrap (@hypabolic)** → **Run workflow**:
   - first run with **dry_run = true** (inspect the packed tarballs artifact);
   - then re-run with **dry_run = false** and the correct **version** (e.g.
     `0.1.0`) to create the packages on the registry.
6. On npmjs.com, for **each** of the three packages:
   - **Package settings → Trusted Publisher → GitHub Actions**
   - Organization or user: `Hypabolic`
   - Repository: `Trajectory`
   - Workflow filename: **`release.yml`** (exact)
   - Environment name: **`release`** (exact)
7. **Revoke** the granular npm token and **delete** the `NPM_TOKEN` GitHub
   secret. Steady-state releases must not depend on a long-lived publish token.
8. Subsequent releases: push `v*.*.*` or run **Release** with `npm_auth=oidc`
   (default). Tag pushes always use OIDC for npm.

### Bootstrap via the full Release workflow (alternative)

Actions → **Release** → Run workflow with:

- `dry_run = false`
- `npm_auth = token`
- version set

That path also publishes NuGet and crates, so those secrets must be present.
Prefer **npm bootstrap** when you only need to create the npm packages.

### Troubleshooting OIDC 404s

A `404` on `npm publish` with OIDC almost always means:

- the package was never bootstrapped, or
- Trusted Publisher fields do not **exactly** match
  (`Hypabolic/Trajectory`, `release.yml`, environment `release`), or
- the workflow is not running under environment `release`, or
- npm CLI is too old (the workflows upgrade to `npm@latest`).

Re-bootstrap with token only if the package truly does not exist; otherwise fix
the Trusted Publisher configuration.

## Release process

### Preferred: tag on `main` (after npm bootstrap + OIDC)

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

5. The **Release** workflow runs automatically (`dry_run=false`, `npm_auth=oidc`):
   - validates metadata and version;
   - packs NuGet / npm / crates artifacts;
   - publishes to all three registries (npm via OIDC);
   - creates a GitHub Release with notes and attached packages.

### Manual: workflow_dispatch

1. Actions → **Release** → **Run workflow**.
2. Leave **dry_run** checked to pack-only, or uncheck to publish.
3. Set **version** (e.g. `0.1.0`) when not running from a tag.
4. **npm_auth**: leave `oidc` after bootstrap; use `token` only for bootstrap
   recovery.

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
| `resolve` | Version / tag / mode / npm_auth | Same |
| `validate` | Pack + content checks + consumer smoke | Same |
| `publish-nuget` | Skipped | `dotnet nuget push` (+ symbols) |
| `publish-npm` | Skipped | `npm publish --access public --provenance` (OIDC or token) in dep order |
| `publish-crates` | Skipped | `cargo publish` core then otel (with index retry) |
| `github-release` | Summary only | GitHub Release + asset upload |

Separate workflow **npm bootstrap (@hypabolic)** only creates the three npm
packages with `NPM_TOKEN` and does not touch NuGet or crates.

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
