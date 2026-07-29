# Publishing Trajectory packages

Synchronized multi-registry releases: **NuGet**, **npm**, and **crates.io**, plus
a **GitHub Release** with packed artifacts.

| Ecosystem | Packages |
| --- | --- |
| NuGet | `Hypabolic.Trajectory`, `.OpenTelemetry`, `.Testing` |
| npm | `@hypabolic/trajectory`, `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel` |
| crates.io | `hypabolic-trajectory`, `hypabolic-trajectory-opentelemetry` |

Not published: sample CLIs, conformance runners, `@hypabolic/trajectory-testing`.

## Versioning

**Single source of truth:** repository-root [`VERSION`](../VERSION).

```bash
# Show current
cat VERSION

# Set explicit version (updates NuGet / npm / Cargo metadata)
python3 tools/set_package_version.py --version 0.1.1

# Or bump
python3 tools/set_package_version.py --bump patch   # 0.1.0 → 0.1.1
python3 tools/set_package_version.py --bump minor
python3 tools/set_package_version.py --bump major

# Verify everything matches
python3 tools/assert_release_version.py --repository-root . --version 0.1.1
python3 tools/validate_release_metadata.py --repository-root .
```

`tools/set_package_version.py` updates:

- `VERSION`
- `dotnet/src/**/*.csproj` `<Version>`
- all workspace `package.json` versions and `@hypabolic/*` dependency pins
- `rust/Cargo.toml` workspace version and path-dep pin

It does **not** rewrite conformance goldens. Package version can appear in
identity-bearing Hypabolic/OTEL fixtures (`normalizer.version` /
`instrumentation_version`). If a bump changes those outputs, update goldens and
`conformance/identity-baseline.sha256` in the **same release commit** before
tagging.

## Prerequisites (one-time)

### Registries

- NuGet.org push rights for `Hypabolic.*`
- npm `@hypabolic` org (packages bootstrapped once — see below)
- crates.io ownership for both crates

### GitHub

| Item | Purpose |
| --- | --- |
| Environment **`release`** | Gates live publish; must match npm Trusted Publisher |
| Secret `NUGET_API_KEY` | NuGet push |
| Secret `CARGO_REGISTRY_TOKEN` | crates.io push |
| `GITHUB_TOKEN` | Tags, commits (dispatch prepare), GitHub Releases, npm OIDC |

### npm bootstrap (first create only)

Trusted Publishing requires packages to exist. From a logged-in machine:

```bash
./tools/bootstrap_npm_packages.sh            # dry-run
./tools/bootstrap_npm_packages.sh --publish  # creates 0.1.0 (no provenance)
```

Then on each package → **Trusted Publisher → GitHub Actions**:

- Repository: `Hypabolic/Trajectory`
- Workflow: `release.yml`
- Environment: `release`

Steady-state publishes use OIDC (no long-lived `NPM_TOKEN`).

## Release process

### Preferred: tag on main

1. On a release branch / main, set version and update CHANGELOG:

   ```bash
   python3 tools/set_package_version.py --version 0.1.1
   # Edit CHANGELOG.md: add ## 0.1.1 section
   # If identity goldens break, update them and identity-baseline.sha256
   ```

2. Open PR, wait for CI green, merge to `main`.

3. Tag and push (from `main` at the release commit):

   ```bash
   git checkout main && git pull
   git tag -a v0.1.1 -m "Trajectory 0.1.1"
   git push origin v0.1.1
   ```

4. **Release** workflow runs automatically:

   - asserts `VERSION` / package metadata == `0.1.1`
   - packs versioned NuGet (`.nupkg` / `.snupkg`), npm tarballs, crates
   - publishes all three registries
   - creates **GitHub Release** `Trajectory 0.1.1` with notes from CHANGELOG +
     package links and attaches artifacts

### workflow_dispatch (Actions UI)

| Input | Meaning |
| --- | --- |
| `dry_run` | `true` (default): pack/validate only |
| `version` | Explicit SemVer (optional) |
| `bump` | `patch` / `minor` / `major` if `version` empty |
| `create_tag` | On live publish, commit version (if needed), push tag |
| `npm_auth` | `oidc` (default) or `token` |

**Dry run:** Actions → Release → Run workflow → leave dry_run checked.

**Live publish from UI:** dry_run=false, set version or bump, create_tag=true.
Requires permission for the workflow to push to `main` and create tags
(`contents: write`). Prefer the manual tag flow for production cuts.

## What each job does

| Job | Dry run | Live |
| --- | --- | --- |
| `resolve` | Compute version / mode | Same |
| `prepare` | Skipped | Version commit + annotated tag (dispatch only) |
| `validate` | Pack versioned artifacts, checksums, changelog extract | Same |
| `publish-nuget` | Skipped | `dotnet nuget push` (+ symbols), `--skip-duplicate` |
| `publish-npm` | Skipped | `npm publish --access public --provenance` @ VERSION |
| `publish-crates` | Skipped | `cargo publish` core then otel (index retry) |
| `github-release` | Skipped | GitHub Release + asset upload |

## Install a released version

```bash
dotnet add package Hypabolic.Trajectory --version 0.1.0
npm install @hypabolic/trajectory@0.1.0
cargo add hypabolic-trajectory@0.1.0
```

## Failure recovery

| Symptom | Action |
| --- | --- |
| Assert version failed | Run `set_package_version.py` and commit; tag the matching commit |
| NuGet already published | `--skip-duplicate` makes re-run a no-op for that package |
| npm 403/404 OIDC | Fix Trusted Publisher (workflow `release.yml`, env `release`) or bootstrap |
| crates otel publish lag | Workflow retries; re-run job if index still stale |
| Goldens fail after bump | Update identity-bearing expected files before tagging |

## Related

- [Release readiness](release-readiness.md)
- [Contributing](contributing.md)
- Root [README](../README.md)
