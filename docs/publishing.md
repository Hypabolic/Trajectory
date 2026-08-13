# Publishing Trajectory packages

Trajectory follows the same release model as **Hypabolic/Hypa**:

> **The git tag is the version.**  
> CI stamps NuGet / npm / crates / PyPI metadata at pack and publish time.  
> You do **not** need to run a version script before tagging.

| Ecosystem | Packages |
| --- | --- |
| NuGet | `Hypabolic.Trajectory`, `.OpenTelemetry`, `.Testing`, `.IO`, `.Ahp`, `.Hermes` |
| npm | `@hypabolic/trajectory`, `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel`, `@hypabolic/trajectory-ahp`, `@hypabolic/trajectory-hermes` |
| crates.io | `hypabolic-trajectory`, `hypabolic-trajectory-opentelemetry`, `hypabolic-trajectory-io`, `hypabolic-trajectory-ahp`, `hypabolic-trajectory-hermes` |
| PyPI | `hypabolic-trajectory` (core includes pure OTEL project + `hypabolic_trajectory.otel`; optional extras `[otel]` SDK sinks, `[io]` / `[ahp]` / `[hermes]` stream modules — same wheel) |

Cross-ecosystem package map (core vs optional):

| Ecosystem | Core | Optional |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | `.OpenTelemetry`, `.Testing`, `.IO`, `.Ahp`, `.Hermes` |
| TypeScript | `@hypabolic/trajectory` | `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel`, `@hypabolic/trajectory-ahp`, `@hypabolic/trajectory-hermes` |
| Rust | `hypabolic-trajectory` | `hypabolic-trajectory-opentelemetry`, `hypabolic-trajectory-io`, `hypabolic-trajectory-ahp`, `hypabolic-trajectory-hermes` |
| Python | `hypabolic-trajectory` | `[otel]` SDK sinks; `[io]` / `[ahp]` / `[hermes]` stream modules (stdlib) |

## Create a release (normal path)

`v0.1.3` is the live-session streaming cut (core stream APIs + optional I/O /
AHP / Hermes packages). That tag **partially published**: PyPI, existing npm
names, existing crates, and NuGet accepted `0.1.3`; brand-new npm/crates names
(`trajectory-ahp`, `trajectory-hermes`, `hypabolic-trajectory-io` / `-ahp` /
`-hermes`) cannot be created by OIDC and need a one-time token first-publish.
Do **not** retag `v0.1.0`, `v0.1.2`, or `v0.1.3`. Further releases use a new
synchronized tag (`v0.1.4`, `v0.2.0`, …).

```bash
git checkout main
git pull origin main

# Tag the commit you want to ship (must already be green on CI).
# Example: stream cut after checked-in 0.1.2 — never retag v0.1.2.
git tag -a v0.1.3 -m "Trajectory 0.1.3"
git push origin v0.1.3
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
| `tag` | Required, e.g. `v0.1.3` (tag should already exist, or push it first; never retag `v0.1.0` / `v0.1.2`) |
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
(`hypabolic-trajectory`, `hypabolic-trajectory-opentelemetry`,
`hypabolic-trajectory-io`, `hypabolic-trajectory-ahp`,
`hypabolic-trajectory-hermes`):

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
AHP). Python is **not** on PyPI at `0.1.0`; it first appears on the **next**
synchronized multi-registry tag after that cut (see release readiness). Use
`<tag-semver>` for Python until that tag ships. Unversioned NuGet/npm/crates
install commands resolve to published `0.1.0` until the next tag.

```bash
dotnet add package Hypabolic.Trajectory --version 0.1.0
npm install @hypabolic/trajectory@0.1.0
cargo add hypabolic-trajectory@0.1.0
# Python first ships on the next multi-registry tag (not published at 0.1.0):
pip install hypabolic-trajectory==<tag-semver>
pip install 'hypabolic-trajectory[otel]==<tag-semver>'   # optional OpenTelemetry SDK sinks
# Stream optional packages / extras (next tag; not in published 0.1.0):
#   NuGet: Hypabolic.Trajectory.IO | .Ahp | .Hermes
#   npm:   @hypabolic/trajectory-node | trajectory-ahp | trajectory-hermes
#   crates: hypabolic-trajectory-io | -ahp | -hermes
#   PyPI:  hypabolic-trajectory[io] | [ahp] | [hermes]
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
| NuGet already published | `--skip-duplicate` then `tools/verify_published_stream_artifact.py` downloads the registry nupkg and **fails** unless it contains stream capability manifests/APIs (a reused `0.1.2` is not a stream ship) |
| npm already published | Same content/digest check against the npm tarball; missing `stream-*` manifests fails the job |
| crates already uploaded | Same content check against the crates.io `.crate`; missing stream APIs fails (not retried as index lag) |
| PyPI already published | `skip-existing: true` then the same verifier against the PyPI wheel; a pre-stream artifact fails |
| OIDC 404 / NuGet login fail | Match Trusted Publisher: owner `hypabolic`, workflow `release.yml`, env `release` |
| crates.io OIDC auth fail | Match Trusted Publishing on **all** published crates (core + otel + io/ahp/hermes); workflow `release.yml`, env `release` |
| npm OIDC 404 | Fix Trusted Publisher or bootstrap package once (including `trajectory-ahp` / `trajectory-hermes`) |
| PyPI OIDC / pending publisher fail | Match pending publisher: org `Hypabolic`, package `hypabolic-trajectory`, workflow `release.yml`, env `release`; ensure environment protection allows the run |

## Related

- [Release readiness](release-readiness.md)
- [Contributing](contributing.md)
