#!/usr/bin/env python3
"""Verify the core NuGet package contains only the intended runtime assets."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_directory", type=Path)
    args = parser.parse_args()
    packages = sorted(
        args.package_directory.glob("Hypabolic.Trajectory.[0-9]*.nupkg")
    )
    packages = [
        path
        for path in packages
        if ".OpenTelemetry." not in path.name and ".Testing." not in path.name
    ]
    if len(packages) != 1:
        raise SystemExit(f"expected one core package, found {packages}")

    with zipfile.ZipFile(packages[0]) as archive:
        names = set(archive.namelist())
    required = {
        "README.md",
        "contentFiles/any/any/contracts/compatibility.json",
        "contentFiles/any/any/contracts/schemas/hypabolic-trajectory-v1.schema.json",
        "contentFiles/any/any/contracts/schemas/letta-canonical-v1.schema.json",
        "lib/net8.0/Hypabolic.Trajectory.dll",
        "lib/net9.0/Hypabolic.Trajectory.dll",
        "lib/net10.0/Hypabolic.Trajectory.dll",
    }
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"core package is missing: {missing}")
    forbidden = sorted(
        name
        for name in names
        if "OpenTelemetry" in name or name.endswith("Trajectory.Testing.dll")
    )
    if forbidden:
        raise SystemExit(f"core package contains optional dependencies: {forbidden}")
    print(f"PASS {packages[0].name}: {len(names)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
