#!/usr/bin/env python3
"""Validate synchronized preview metadata and optionally emit provenance evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

VERSION = "0.1.0"
SLICE = "ML13"
OUTPUTS = [
    "letta-trajectory-v1",
    "letta-canonical-v1",
    "hypabolic-trajectory-v1",
    "openai-chat-messages",
    "jsonl-minimal",
    "otel-genai-spans-v1",
]
EXPECTED_SOURCES = ["pi", "claude-code", "codex", "openclaw", "hermes"]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_toml(path: Path) -> dict:
    """Load TOML. Prefer stdlib tomllib (3.11+); fall back to a tiny subset parser."""
    try:
        import tomllib
    except ModuleNotFoundError:
        return _load_toml_subset(path.read_text(encoding="utf-8"))
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _load_toml_subset(text: str) -> dict:
    """Parse the nested tables this script needs from Cargo.toml.

    Supports string values and records dependency keys whose values are tables
    or other non-string forms (values stored as None; only keys are checked).
    """
    root: dict = {}
    section: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = [part.strip() for part in line[1:-1].split(".")]
            cursor = root
            for part in section:
                cursor = cursor.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        cursor = root
        for part in section:
            cursor = cursor.setdefault(part, {})
        if value.startswith('"') and value.endswith('"'):
            cursor[key] = value[1:-1]
        else:
            cursor[key] = None
    return root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()

    compatibility = load_json(root / "contracts/compatibility.json")
    if compatibility["implemented"]["outputs"] != OUTPUTS:
        raise SystemExit("Compatibility output order differs from the release output set.")
    if compatibility["implemented"]["sources"] != EXPECTED_SOURCES:
        raise SystemExit(
            "Compatibility implemented sources must be the v1 set "
            f"{EXPECTED_SOURCES}."
        )

    expected_sources = compatibility["implemented"]["sources"]
    runtime_manifests = [
        root / "typescript/packages/trajectory/runtime-capabilities.json",
        root / "rust/crates/hypabolic-trajectory/runtime-capabilities.json",
    ]
    for path in runtime_manifests:
        manifest = load_json(path)
        if (
            manifest.get("slice") != SLICE
            or manifest.get("outputs") != OUTPUTS
            or manifest.get("sources") != expected_sources
            or "hermes" not in manifest.get("sources", [])
        ):
            raise SystemExit(
                f"{path.relative_to(root)} does not advertise ML13 source/output parity "
                f"(slice={SLICE}, sources including hermes)."
            )

    npm_paths = [
        root / "typescript/package.json",
        root / "typescript/packages/trajectory/package.json",
        root / "typescript/packages/trajectory-node/package.json",
        root / "typescript/packages/trajectory-otel/package.json",
        root / "typescript/packages/trajectory-testing/package.json",
    ]
    for path in npm_paths:
        package = load_json(path)
        if package["version"] != VERSION:
            raise SystemExit(f"{path.relative_to(root)} is not version {VERSION}.")
    for path in npm_paths[1:4]:
        package = load_json(path)
        if package.get("private", False):
            raise SystemExit(f"{path.relative_to(root)} cannot participate in a preview dry run.")

    cargo = load_toml(root / "rust/Cargo.toml")
    if cargo["workspace"]["package"]["version"] != VERSION:
        raise SystemExit("Rust workspace package version is not synchronized.")
    core_dependencies = cargo["workspace"].get("dependencies", {})
    if any("opentelemetry" in name.lower() for name in core_dependencies):
        raise SystemExit("The Rust core dependency catalog contains OpenTelemetry.")

    projects = [
        root / "dotnet/src/Trajectory/Trajectory.csproj",
        root / "dotnet/src/Trajectory.OpenTelemetry/Trajectory.OpenTelemetry.csproj",
        root / "dotnet/src/Trajectory.Testing/Trajectory.Testing.csproj",
    ]
    for path in projects:
        version = ET.parse(path).findtext(".//Version")
        if version != VERSION:
            raise SystemExit(f"{path.relative_to(root)} is not version {VERSION}.")

    inputs = [
        root / "contracts/compatibility.json",
        *runtime_manifests,
        *npm_paths,
        root / "typescript/package-lock.json",
        root / "rust/Cargo.toml",
        root / "rust/Cargo.lock",
        *projects,
    ]
    source_commit = os.environ.get("GITHUB_SHA") or os.environ.get("SOURCE_COMMIT")
    evidence = {
        "format": "trajectory-preview-provenance-v1",
        "version": VERSION,
        "slice": SLICE,
        "normalizer_contract_version": compatibility["contracts"]["normalizer"],
        "source_commit": source_commit,
        "implemented_sources": expected_sources,
        "implemented_outputs": OUTPUTS,
        "manifests": {
            str(path.relative_to(root)): sha256(path)
            for path in sorted(inputs)
        },
    }
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": "success",
                "version": VERSION,
                "slice": SLICE,
                "manifests": len(inputs),
                "source_commit": source_commit,
            }
        )
    )


if __name__ == "__main__":
    main()
