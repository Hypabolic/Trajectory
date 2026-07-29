#!/usr/bin/env python3
"""Extract a CHANGELOG section for a given package version."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def extract(changelog: str, version: str) -> str:
    # Match ## 0.1.0 or ## 0.1.0 — Unreleased / ## [0.1.0]
    pattern = re.compile(
        rf"^##\s+\[?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^##\s+|\Z)",
        re.M | re.S,
    )
    match = pattern.search(changelog)
    if not match:
        return (
            f"## Trajectory {version}\n\n"
            f"No CHANGELOG section found for {version}. "
            f"See the repository CHANGELOG for details.\n"
        )
    body = match.group(1).strip()
    return f"## Trajectory {version}\n\n{body}\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    version = args.version.strip().lstrip("v")
    text = extract((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
