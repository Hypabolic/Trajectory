#!/usr/bin/env python3
"""Assert a release version matches the repository VERSION and package metadata."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_release_metadata import (  # noqa: E402
    load_json,
    load_toml,
    read_package_version,
)


TAG_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")


def normalize_version(raw: str) -> str:
    value = raw.strip()
    match = TAG_RE.match(value)
    if not match:
        raise SystemExit(
            f"Invalid version '{raw}'. Expected SemVer like 0.1.0 or tag v0.1.0."
        )
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--version",
        required=True,
        help="Release version or git tag (e.g. 0.1.0 or v0.1.0).",
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    version = normalize_version(args.version)
    file_version = read_package_version(root)

    if version != file_version:
        raise SystemExit(
            f"Requested version {version} does not match VERSION file ({file_version}). "
            "Run: python3 tools/set_package_version.py --version "
            f"{version}"
        )

    npm_paths = [
        root / "typescript/packages/trajectory/package.json",
        root / "typescript/packages/trajectory-node/package.json",
        root / "typescript/packages/trajectory-otel/package.json",
    ]
    for path in npm_paths:
        package = load_json(path)
        if package.get("version") != version:
            raise SystemExit(f"{path.relative_to(root)} version is not {version}.")
        if package.get("private", False):
            raise SystemExit(f"{path.relative_to(root)} is private and cannot be published.")

    cargo = load_toml(root / "rust/Cargo.toml")
    if cargo["workspace"]["package"]["version"] != version:
        raise SystemExit(f"Rust workspace version is not {version}.")

    projects = [
        root / "dotnet/src/Trajectory/Trajectory.csproj",
        root / "dotnet/src/Trajectory.OpenTelemetry/Trajectory.OpenTelemetry.csproj",
        root / "dotnet/src/Trajectory.Testing/Trajectory.Testing.csproj",
    ]
    for path in projects:
        project_version = ET.parse(path).findtext(".//Version")
        if project_version != version:
            raise SystemExit(f"{path.relative_to(root)} Version is not {version}.")

    print(
        json.dumps(
            {
                "status": "success",
                "version": version,
                "tag": f"v{version}",
                "nuget": [
                    "Hypabolic.Trajectory",
                    "Hypabolic.Trajectory.OpenTelemetry",
                    "Hypabolic.Trajectory.Testing",
                ],
                "npm": [
                    "@hypabolic/trajectory",
                    "@hypabolic/trajectory-node",
                    "@hypabolic/trajectory-otel",
                ],
                "crates": [
                    "hypabolic-trajectory",
                    "hypabolic-trajectory-opentelemetry",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
