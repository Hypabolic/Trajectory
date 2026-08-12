#!/usr/bin/env python3
"""Validate synchronized preview metadata and optionally emit provenance evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

def read_package_version(root: Path) -> str:
    """Package version from repository-root VERSION file (single source of truth)."""
    path = root / "VERSION"
    if not path.is_file():
        raise SystemExit("VERSION file is missing at repository root.")
    return path.read_text(encoding="utf-8").strip()


SLICE = "ML13"
OUTPUTS = [
    "letta-trajectory-v1",
    "letta-canonical-v1",
    "hypabolic-trajectory-v1",
    "openai-chat-messages",
    "jsonl-minimal",
    "otel-genai-spans-v1",
]
EXPECTED_SOURCES = ["pi", "claude-code", "codex", "openclaw", "hermes", "ahp", "grok-build"]
# Tip capability set advertised by peer runtimes (ML13 + LS-12 stream core).
# PY-15b / ship require python/runtime-capabilities.json equality to this set
# (order-sensitive). Optional package stream caps (file-io / ahp-client /
# hermes-provider) live only on optional package-capabilities.json manifests.
TIP_CAPABILITIES = [
    "normalize",
    "normalize-partial",
    "list-explicit-root",
    "typed-diagnostics",
    "typed-fatal-errors",
    "deterministic-rerun",
    "stream-core",
    "stream-cursor-v1",
    "stream-jsonl-framing",
    "stream-apply-snapshot",
    "stream-apply-append",
    "stream-full-snapshot",
    "stream-record-delta",
    "stream-reset",
    "stream-provisional-records",
    "stream-deterministic-replay",
    "stream-file-jsonl",
    "stream-ahp-snapshot",
    "stream-ahp-action-log",
]



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
    version = read_package_version(root)

    compatibility = load_json(root / "contracts/compatibility.json")
    if compatibility["implemented"]["outputs"] != OUTPUTS:
        raise SystemExit("Compatibility output order differs from the release output set.")
    if compatibility["implemented"]["sources"] != EXPECTED_SOURCES:
        raise SystemExit(
            "Compatibility implemented sources must be the v1 set "
            f"{EXPECTED_SOURCES}."
        )

    expected_sources = compatibility["implemented"]["sources"]
    if compatibility["capabilities"]["required"] != TIP_CAPABILITIES:
        raise SystemExit(
            "contracts/compatibility.json capabilities.required must equal tip "
            f"{TIP_CAPABILITIES} (got {compatibility['capabilities']['required']})."
        )
    # Optional stream package caps may appear only under capabilities.optional —
    # never as a global "stream" flag, and never on core-required alone.
    optional_caps = compatibility["capabilities"]["optional"]
    for forbidden in ("stream-file-watch", "stream-ahp-list-sessions"):
        if forbidden in TIP_CAPABILITIES or forbidden in optional_caps:
            raise SystemExit(
                f"compatibility.json must not claim unimplemented capability {forbidden!r}."
            )
    for optional_stream in (
        "stream-file-io",
        "stream-async-iterator",
        "stream-ahp-client",
        "stream-hermes-provider",
    ):
        if optional_stream in TIP_CAPABILITIES:
            raise SystemExit(
                f"optional package capability {optional_stream!r} must not be in "
                "capabilities.required (claim only on optional packages)."
            )

    runtime_manifests = [
        root / "dotnet/src/Trajectory/runtime-capabilities.json",
        root / "typescript/packages/trajectory/runtime-capabilities.json",
        root / "rust/crates/hypabolic-trajectory/runtime-capabilities.json",
    ]
    for path in runtime_manifests:
        manifest = load_json(path)
        if (
            manifest.get("slice") != SLICE
            or manifest.get("outputs") != OUTPUTS
            or manifest.get("sources") != expected_sources
            or manifest.get("capabilities") != TIP_CAPABILITIES
        ):
            raise SystemExit(
                f"{path.relative_to(root)} does not advertise tip source/output/"
                f"capability parity (slice={SLICE}, sources={expected_sources}, "
                f"capabilities={TIP_CAPABILITIES})."
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
        if package["version"] != version:
            raise SystemExit(f"{path.relative_to(root)} is not version {version}.")
    for path in npm_paths[1:4]:
        package = load_json(path)
        if package.get("private", False):
            raise SystemExit(f"{path.relative_to(root)} cannot participate in a preview dry run.")

    cargo = load_toml(root / "rust/Cargo.toml")
    if cargo["workspace"]["package"]["version"] != version:
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
        project_version = ET.parse(path).findtext(".//Version")
        if project_version != version:
            raise SystemExit(f"{path.relative_to(root)} is not version {version}.")

    # Python ship release metadata (PY-15b / §5 ship rules).
    # When python/pyproject.toml exists: version lockstep + tip equality for
    # sources / outputs / capabilities / slice (order-sensitive, same honesty
    # peers enforce for TS/Rust sources+outputs+slice).
    pyproject_path = root / "python" / "pyproject.toml"
    python_inputs: list[Path] = []
    if pyproject_path.is_file():
        pyproject = load_toml(pyproject_path)
        py_version = pyproject.get("project", {}).get("version")
        if py_version != version:
            raise SystemExit(
                f"python/pyproject.toml [project].version is not {version} "
                f"(got {py_version!r})."
            )
        caps_path = root / "python" / "runtime-capabilities.json"
        if not caps_path.is_file():
            raise SystemExit(
                "python/runtime-capabilities.json is required when "
                "python/pyproject.toml exists."
            )
        py_caps = load_json(caps_path)
        if py_caps.get("runtime") != "python":
            raise SystemExit(
                "python/runtime-capabilities.json runtime must be 'python'."
            )
        if py_caps.get("normalizer_contract_version") != "0.2.0":
            raise SystemExit(
                "python/runtime-capabilities.json normalizer_contract_version "
                "must be '0.2.0'."
            )
        if py_caps.get("slice") != SLICE:
            raise SystemExit(
                f"python/runtime-capabilities.json slice must be {SLICE!r}."
            )
        claimed_sources = py_caps.get("sources")
        claimed_outputs = py_caps.get("outputs")
        claimed_capabilities = py_caps.get("capabilities")
        if not isinstance(claimed_sources, list):
            raise SystemExit("python/runtime-capabilities.json sources must be a list.")
        if not isinstance(claimed_outputs, list):
            raise SystemExit("python/runtime-capabilities.json outputs must be a list.")
        if not isinstance(claimed_capabilities, list):
            raise SystemExit(
                "python/runtime-capabilities.json capabilities must be a list."
            )
        if claimed_sources != expected_sources:
            raise SystemExit(
                "python/runtime-capabilities.json sources must equal tip "
                f"{expected_sources} (got {claimed_sources})."
            )
        if claimed_outputs != OUTPUTS:
            raise SystemExit(
                "python/runtime-capabilities.json outputs must equal tip "
                f"{OUTPUTS} (got {claimed_outputs})."
            )
        if claimed_capabilities != TIP_CAPABILITIES:
            raise SystemExit(
                "python/runtime-capabilities.json capabilities must equal tip "
                f"{TIP_CAPABILITIES} (got {claimed_capabilities})."
            )
        python_inputs = [pyproject_path, caps_path]

    inputs = [
        root / "VERSION",
        root / "contracts/compatibility.json",
        *runtime_manifests,
        *npm_paths,
        root / "typescript/package-lock.json",
        root / "rust/Cargo.toml",
        root / "rust/Cargo.lock",
        *projects,
        *python_inputs,
    ]

    source_commit = os.environ.get("GITHUB_SHA") or os.environ.get("SOURCE_COMMIT")
    evidence = {
        "format": "trajectory-preview-provenance-v1",
        "version": version,
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
                "version": version,
                "slice": SLICE,
                "manifests": len(inputs),
                "source_commit": source_commit,
            }
        )
    )


if __name__ == "__main__":
    main()
