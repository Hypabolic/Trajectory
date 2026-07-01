#!/usr/bin/env bash
# Bootstrap @hypabolic/* packages on npmjs.com from a developer machine.
#
# Trusted Publishing (OIDC) can only be configured after each package exists.
# Run this once with a logged-in npm CLI user that can publish under @hypabolic,
# then configure Trusted Publisher on npmjs.com and use the Release workflow
# for all subsequent versions (no long-lived NPM_TOKEN in GitHub).
#
# Usage:
#   ./tools/bootstrap_npm_packages.sh              # dry-run (default)
#   ./tools/bootstrap_npm_packages.sh --publish     # real publish
#   ./tools/bootstrap_npm_packages.sh --publish --skip-checks
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PUBLISH=false
SKIP_CHECKS=false
for arg in "$@"; do
  case "$arg" in
    --publish) PUBLISH=true ;;
    --skip-checks) SKIP_CHECKS=true ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

WORKSPACES=(
  @hypabolic/trajectory
  @hypabolic/trajectory-node
  @hypabolic/trajectory-otel
)

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required on PATH." >&2
  exit 1
fi

if ! npm whoami >/dev/null 2>&1; then
  echo "Not logged in to npm. Run: npm login" >&2
  echo "Use an account that can create packages under the @hypabolic org." >&2
  exit 1
fi

echo "npm user: $(npm whoami)"

if [[ "$SKIP_CHECKS" != true ]]; then
  python3 tools/assert_release_version.py --repository-root . --version 0.1.0
  python3 tools/validate_release_metadata.py --repository-root .
fi

echo "Building TypeScript workspaces..."
(
  cd typescript
  npm ci
  npm run build
)

echo "Packing for inspection..."
mkdir -p artifacts/npm-bootstrap
(
  cd typescript
  npm pack \
    --workspace @hypabolic/trajectory \
    --workspace @hypabolic/trajectory-node \
    --workspace @hypabolic/trajectory-otel \
    --pack-destination ../artifacts/npm-bootstrap
)
ls -la artifacts/npm-bootstrap

if [[ "$PUBLISH" != true ]]; then
  echo
  echo "Dry-run publish (no registry write):"
  (
    cd typescript
    for workspace in "${WORKSPACES[@]}"; do
      echo "---- $workspace ----"
      npm publish --dry-run --workspace "$workspace" --access public
    done
  )
  echo
  echo "Dry-run only. Re-run with --publish after reviewing the tarballs."
  exit 0
fi

echo
echo "Publishing ${#WORKSPACES[@]} packages to npmjs.com as $(npm whoami)..."
echo "Note: local bootstrap does not attach npm provenance (CI-only)."
(
  cd typescript
  # Provenance requires a supported CI provider (GitHub Actions OIDC, etc.).
  # Do not pass --provenance here; package.json also omits publishConfig.provenance
  # so a laptop publish is not forced into the CI path.
  for workspace in "${WORKSPACES[@]}"; do
    echo "==== Publishing $workspace ===="
    npm publish --workspace "$workspace" --access public
  done
)

cat <<'EOF'

Bootstrap publish finished (no provenance attestation — expected for local CLI).

Configure Trusted Publishing on npmjs.com for EACH package:
  @hypabolic/trajectory
  @hypabolic/trajectory-node
  @hypabolic/trajectory-otel

Package settings → Trusted Publisher → GitHub Actions:
  Organization or user : Hypabolic
  Repository           : Trajectory
  Workflow filename    : release.yml
  Environment name     : release

Later releases from GitHub Actions use OIDC + --provenance.
EOF
