# Publishing Trajectory packages

Trajectory follows the same release model as **Hypabolic/Hypa**:

> **The git tag is the version.**  
> CI stamps NuGet / npm / crates / PyPI metadata at pack and publish time.  
> You do **not** need to run a version script before tagging.

| Ecosystem | Packages |
| --- | --- |
| NuGet | `Hypabolic.Trajectory`, `.OpenTelemetry`, `.Testing` |
| npm | `@hypabolic/trajectory`, `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel` |
| crates.io | `hypabolic-trajectory`, `hypabolic-trajectory-opentelemetry` |
| PyPI | `hypabolic-trajectory` (optional extra `[otel]` for SDK sinks) |

## Create a release (normal path)

`v0.1.0` is **already published** on NuGet / npm / crates (sources through
`hermes` only). Capability additions such as **AHP** Shape A require a **new**
tag (`v0.1.1`, `v0.2.0`, …). Do not retag `v0.1.0`. skip-duplicate / OIDC re-runs
will not replace existing package contents with AHP.

```bash
git checkout main
git pull origin main

# Tag the commit you want to ship (must already be green on CI).
# Example for the next cut after 0.1.0:
git tag -a v0.1.1 -m "Trajectory 0.1.1"
git push origin v0.1.1
```

That is the whole developer step. The **Release** workflow then:

1. Optionally waits for CI checks on the tagged commit (Hypa-style gate)
2. Checks out the tag
3. **Stamps** package versions from the tag (`tools/stamp_release_version.py` — CI only)
4. Packs versioned NuGet / npm / crates / **PyPI** artifacts (Python: prepare + build into `artifacts/release/pypi` + pack-smoke)
5. Publishes to NuGet.org, npm, crates.io, and **PyPI** (PyPI job downloads validated artifacts only — **no rebuild**)
6. Creates/updates a **GitHub Release** with notes and attached packages

### Manual dispatch

**Actions → Release → Run workflow**

| Input | Meaning |
| --- | --- |
| `tag` | Required, e.g. `v0.1.1` (tag should already exist, or push it first; `v0.1.0` is already cut) |
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
| PyPI Trusted Publishing | OIDC for org **`Hypabolic`**, package **`hypabolic-trajectory`**: workflow `release.yml`, env `release` (pending publisher until first ship) |

No long-lived registry API tokens are required for NuGet, npm, crates.io, or PyPI.

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

### PyPI Trusted Publishing (pending publisher)

On [pypi.org](https://pypi.org/) → Publishing → Trusted publishers (or
[pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
before the first upload of `hypabolic-trajectory`), register:

| Field | Value |
| --- | --- |
| Owner / org | **`Hypabolic`** ([pypi.org/org/Hypabolic](https://pypi.org/org/Hypabolic/)) |
| Package | `hypabolic-trajectory` |
| Publisher | GitHub Actions |
| Repository | `Hypabolic/Trajectory` |
| Workflow | `release.yml` |
| Environment | `release` |

The Release **`publish-pypi`** job uses `pypa/gh-action-pypi-publish` with
`id-token: write`, environment `release`, `packages-dir: artifacts/release/pypi`,
and `skip-existing: true`. It **downloads** the validate-job artifact only —
it does **not** rebuild sdist/wheel. No long-lived `PYPI_API_TOKEN` is stored
in GitHub.

## Install a released version

Latest published public packages are **`0.1.0`** for NuGet / npm / crates (no
AHP). Python on PyPI ships with the first tag that includes the Python package
(see release readiness). Unversioned install commands resolve to that surface
until the next tag ships.

```bash
dotnet add package Hypabolic.Trajectory --version 0.1.0
npm install @hypabolic/trajectory@0.1.0
cargo add hypabolic-trajectory@0.1.0
pip install hypabolic-trajectory==0.1.0
pip install 'hypabolic-trajectory[otel]==0.1.0'   # optional OpenTelemetry SDK sinks
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
| PyPI auth | — | OIDC Trusted Publishing (`pypa/gh-action-pypi-publish`, pending publisher) |

## Failure recovery

| Symptom | Action |
| --- | --- |
| CI gate failed | Fix main, retag or move tag to a green commit |
| NuGet already published | `--skip-duplicate` makes re-run a no-op (does **not** replace package contents — new capability such as AHP needs a new version) |
| npm already published | Workflow continues if version exists on registry (same: new features need a new version) |
| crates already uploaded | Treated as success (same: new features need a new version) |
| PyPI already published | `skip-existing: true` makes re-run a no-op (same: new features need a new version) |
| OIDC 404 / NuGet login fail | Match Trusted Publisher: owner `hypabolic`, workflow `release.yml`, env `release` |
| crates.io OIDC auth fail | Match Trusted Publishing on **both** crates; workflow `release.yml`, env `release` |
| npm OIDC 404 | Fix Trusted Publisher or bootstrap package once |
| PyPI OIDC / pending publisher fail | Match pending publisher: org `Hypabolic`, package `hypabolic-trajectory`, workflow `release.yml`, env `release`; ensure environment protection allows the run |

## Related

- [Release readiness](release-readiness.md)
- [Contributing](contributing.md)
