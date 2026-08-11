"""PY-01 smoke: editable import, version resolution, package constants."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

import hypabolic_trajectory as ht


def test_import_package() -> None:
    assert ht.__name__ == "hypabolic_trajectory"


def _expected_package_version() -> str:
    """Expected SemVer from monorepo root VERSION (stamp lockstep SoT)."""
    root_version = (
        Path(__file__).resolve().parents[2] / "VERSION"
    ).read_text(encoding="utf-8").strip()
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    # Minimal parse: static `version = "…"` under [project] (no tomllib dep in tests).
    project_version: str | None = None
    in_project = False
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project and stripped.startswith("version"):
            # version = "X.Y.Z"  optionally with trailing comment
            _, _, rhs = stripped.partition("=")
            project_version = rhs.split("#", 1)[0].strip().strip("\"'")
            break
    assert project_version is not None, "missing [project].version in pyproject.toml"
    assert project_version == root_version, (
        f"pyproject version {project_version!r} != root VERSION {root_version!r}"
    )
    return root_version


def test_version_resolution() -> None:
    # Editable install exposes distribution metadata matching stamp SoT.
    expected = _expected_package_version()
    dist_version = importlib.metadata.version("hypabolic-trajectory")
    assert dist_version == expected
    assert ht.PACKAGE_VERSION == dist_version
    assert ht.__version__ == dist_version
    assert ht.__version__ == expected


def test_wire_and_contract_pins() -> None:
    assert ht.NORMALIZER_CONTRACT_VERSION == "0.2.0"
    assert ht.WIRE_PACKAGE_VERSION == "0.1.0"


def test_schema_and_source_constants() -> None:
    assert "letta-trajectory-v1" in ht.SCHEMA_IDS
    assert "otel-genai-spans-v1" in ht.SCHEMA_IDS
    assert len(ht.SCHEMA_IDS) == 6
    assert ht.IMPLEMENTED_SOURCES == (
        "pi",
        "claude-code",
        "codex",
        "openclaw",
        "hermes",
        "ahp",
        "grok-build",
    )
    assert ht.TrajectorySource.PI == "pi"
    assert ht.TrajectorySource.AHP == "ahp"
    assert ht.TrajectorySource.GROK_BUILD == "grok-build"
    assert set(ht.TrajectorySource) == set(ht.IMPLEMENTED_SOURCES)


def test_root_all_is_explicit() -> None:
    # Scaffold subset only — exhaustive inventory lands with later export owner.
    for name in ht.__all__:
        assert hasattr(ht, name), f"missing export: {name}"


def test_authoritative_runtime_capabilities_exists() -> None:
    caps_path = Path(__file__).resolve().parents[1] / "runtime-capabilities.json"
    assert caps_path.is_file()
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    # Progressive claims owned by claim-writer issues (PY-10a+). Structural shape only.
    assert isinstance(data["sources"], list)
    assert isinstance(data["outputs"], list)
    assert isinstance(data["capabilities"], list)


def test_py_typed_marker_present() -> None:
    package_dir = Path(ht.__file__).resolve().parent
    assert (package_dir / "py.typed").is_file()
