#!/usr/bin/env python3
"""Stamp package metadata to a release version for CI pack/publish only.

Like Hypa: the git tag is the version source of truth. This script rewrites
NuGet/npm/Cargo metadata in the working tree for the current job so packs and
publishes use that version — it is not a developer release step and does not
need to be committed before tagging.

Usage (CI):
  python3 tools/stamp_release_version.py --version 0.1.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Reuse the mutators from set_package_version (same file formats).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from set_package_version import (  # noqa: E402
    apply_version,
    parse_version,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True, help="SemVer from the release tag")
    args = parser.parse_args()
    root = args.repository_root.resolve()
    version = parse_version(args.version)
    changed = apply_version(root, version)
    # Also rewrite Cargo.lock package versions that pin the workspace crate.
    lock = root / "rust/Cargo.lock"
    if lock.is_file():
        text = lock.read_text(encoding="utf-8")
        # Keep Cargo.lock in sync with stamped workspace.package.version so
        # `cargo package --locked` does not try to rewrite the lockfile.
        # Includes tools (trajectory-cli / trajectory-conformance), not just
        # published hypabolic-trajectory* crates.
        text2 = re.sub(
            r'(name = "(?:hypabolic-trajectory(?:-opentelemetry|-io|-ahp|-hermes)?|trajectory-cli|trajectory-conformance)"\nversion = ")[^"]+(")',
            rf"\g<1>{version}\g<2>",
            text,
        )
        if text2 != text:
            lock.write_text(text2, encoding="utf-8")
            changed.append("rust/Cargo.lock")
    print(
        json.dumps(
            {"status": "stamped", "version": version, "files": changed},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
