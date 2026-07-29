# Publishing Trajectory packages

Trajectory follows the same release model as **Hypabolic/Hypa**:

> **The git tag is the version.**  
> CI stamps NuGet / npm / crates metadata at pack and publish time.  
> You do **not** need to run a Python version script before tagging.

| Ecosystem | Packages |
| --- | --- |
| NuGet | `Hypabolic.Trajectory`, `.OpenTelemetry`, `.Testing` |
| npm | `@hypabolic/trajectory`, `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel` |
| crates.io | `hypabolic-trajectory`, `hypabolic-trajectory-opentelemetry` |

## Create a release (normal path)

```bash
git checkout main
git pull origin main

# Tag the commit you want to ship (must already be green on CI)
git tag -a v0.1.0 -m "Trajectory 0.1.0"
git push origin v0.1.0
```

That is the whole developer step. The **Release** workflow then:

1. Optionally waits for CI checks on the tagged commit (Hypa-style gate)
2. Checks out the tag
3. **Stamps** package versions from the tag (`tools/stamp_release_version.py` — CI only)
4. Packs versioned NuGet / npm / crates artifacts
5. Publishes to NuGet.org, npm, and crates.io
6. Creates/updates a **GitHub Release** with notes and attached packages

### Manual dispatch

**Actions → Release → Run workflow**

| Input | Meaning |
| --- | --- |
| `tag` | Required, e.g. `v0.1.0` (tag should already exist, or push it first) |
| `dry_run` | Pack only (no publish / no GitHub Release) |
| `npm_auth` | `oidc` (default) or `token` |

## Why not “every push to main releases”?

Same reason as Hypa: main is continuous integration. Releases are intentional
versioned cuts. Auto-publishing on every merge would republish the same
version or force a version bump on docs-only commits.

| Event | What runs |
| --- | --- |
| Push / PR to `main` | **CI** — build, test, pack dry-run evidence |
| Tag `vX.Y.Z` | **Release** — stamp, publish, GitHub Release |

## Optional: keep `VERSION` in the repo in sync

Root [`VERSION`](../VERSION) and package metadata can lag the latest tag during
development; CI stamps over them when publishing. To keep main’s checked-in
metadata tidy for local `dotnet pack` / `npm pack`:

```bash
# Optional housekeeping after a release — not required for tagging
python3 tools/set_package_version.py --version 0.1.0
```

That helper is for **repo hygiene**, not the release trigger.

## Prerequisites (one-time)

| Item | Purpose |
| --- | --- |
| Environment **`release`** | Gates live publish; must match Trusted Publisher policies |
| NuGet Trusted Publishing | OIDC for `hypabolic` owner: workflow `release.yml`, env `release` |
| npm Trusted Publisher | OIDC for `@hypabolic/*` on workflow `release.yml`, env `release` |
| crates.io Trusted Publishing | OIDC per crate: workflow `release.yml`, env `release` |

No long-lived registry API tokens are required for NuGet, npm, or crates.io.

### NuGet Trusted Publishing

On [nuget.org](https://www.nuget.org/) → Trusted Publishing, register:

| Field | Value |
| --- | --- |
| Package owner | `hypabolic` |
| Publisher | GitHub Actions |
| Repository | `Hypabolic/Trajectory` |
| Workflow | `release.yml` |
| Environment | `release` |

The Release job uses `NuGet/login@v1` with `user: hypabolic` and
`id-token: write` — no long-lived `NUGET_API_KEY`.

### crates.io Trusted Publishing

On [crates.io](https://crates.io/docs/trusted-publishing), for **each** crate
(`hypabolic-trajectory`, `hypabolic-trajectory-opentelemetry`):

| Field | Value |
| --- | --- |
| Repository | `Hypabolic/Trajectory` |
| Workflow | `release.yml` |
| Environment | `release` |

The Release job uses `rust-lang/crates-io-auth-action@v1` (OIDC → short-lived
token) and `cargo publish` with `CARGO_REGISTRY_TOKEN` set from that token.
No long-lived crates.io API token is stored in GitHub.

### npm first create (bootstrap)

OIDC cannot create brand-new package names. Once per package, from a logged-in
machine (after a tag or with stamped version):

```bash
./tools/bootstrap_npm_packages.sh --publish
```

Then configure Trusted Publisher on each package. Later releases use OIDC only.

## Install a released version

```bash
dotnet add package Hypabolic.Trajectory --version 0.1.0
npm install @hypabolic/trajectory@0.1.0
cargo add hypabolic-trajectory@0.1.0
```

## Comparison with Hypa

| Concern | Hypa | Trajectory (aligned) |
| --- | --- | --- |
| Version source | Tag `vX.Y.Z` | Tag `vX.Y.Z` |
| Stamp timing | CI pack/publish (`/p:Version=`, `jq`) | CI `stamp_release_version.py` + MSBuild `/p:Version=` |
| Human pre-step | Push tag | Push tag |
| GitHub Release | Pipeline creates it | Pipeline creates it |
| npm auth | OIDC | OIDC (after bootstrap) |
| NuGet auth | API key / OIDC | OIDC Trusted Publishing (`NuGet/login@v1`) |
| crates auth | API token / OIDC | OIDC Trusted Publishing (`crates-io-auth-action`) |

## Failure recovery

| Symptom | Action |
| --- | --- |
| CI gate failed | Fix main, retag or move tag to a green commit |
| NuGet already published | `--skip-duplicate` makes re-run a no-op |
| npm already published | Workflow continues if version exists on registry |
| crates already uploaded | Treated as success |
| OIDC 404 / NuGet login fail | Match Trusted Publisher: owner `hypabolic`, workflow `release.yml`, env `release` |
| crates.io OIDC auth fail | Match Trusted Publishing on **both** crates; workflow `release.yml`, env `release` |
| npm OIDC 404 | Fix Trusted Publisher or bootstrap package once |

## Related

- [Release readiness](release-readiness.md)
- [Contributing](contributing.md)
