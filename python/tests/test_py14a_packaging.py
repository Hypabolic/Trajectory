"""PY-14a: stamp lockstep, prepare overwrite, progressive metadata validation."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from set_package_version import replace_pyproject_version  # noqa: E402


def test_replace_pyproject_version_only_project_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            name = "hypabolic-trajectory"
            version = "0.1.0"  # stamp target
            description = "demo"

            [project.optional-dependencies]
            otel = ["opentelemetry-api>=1.27.0"]

            [build-system]
            requires = ["hatchling>=1.27"]
            """
        ),
        encoding="utf-8",
    )
    replace_pyproject_version(pyproject, "0.2.3")
    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "0.2.3"  # stamp target' in text
    assert text.count('version = "') == 1
    # optional-deps table untouched
    assert "opentelemetry-api>=1.27.0" in text


def test_replace_pyproject_version_rejects_missing(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = \"x\"\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        replace_pyproject_version(pyproject, "1.0.0")


def test_prepare_overwrite_semantics(tmp_path: Path) -> None:
    """Staged contracts are always replaced when monorepo sources change."""
    # Minimal monorepo layout
    (tmp_path / "contracts" / "schemas").mkdir(parents=True)
    (tmp_path / "contracts" / "compatibility.json").write_text(
        '{"v":1}\n', encoding="utf-8"
    )
    (tmp_path / "contracts" / "schemas" / "a.schema.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (tmp_path / "LICENSE").write_text("MIT\n", encoding="utf-8")
    pkg = tmp_path / "python" / "src" / "hypabolic_trajectory"
    pkg.mkdir(parents=True)
    (tmp_path / "python" / "runtime-capabilities.json").write_text(
        '{"runtime":"python","sources":[],"outputs":[],"capabilities":[],'
        '"normalizer_contract_version":"0.2.0","slice":"ML13"}\n',
        encoding="utf-8",
    )
    # Stale staged tree must be overwritten
    stale = pkg / "contracts"
    stale.mkdir()
    (stale / "compatibility.json").write_text('{"v":"stale"}\n', encoding="utf-8")
    (stale / "old.txt").write_text("should vanish\n", encoding="utf-8")

    prep = TOOLS / "prepare_python_package.py"
    subprocess.run(
        [sys.executable, str(prep), "--repository-root", str(tmp_path)],
        check=True,
    )
    assert (pkg / "contracts" / "compatibility.json").read_text(
        encoding="utf-8"
    ) == '{"v":1}\n'
    assert not (pkg / "contracts" / "old.txt").exists()
    assert (pkg / "contracts" / "schemas" / "a.schema.json").is_file()
    assert (pkg / "runtime-capabilities.json").is_file()
    assert (tmp_path / "python" / "LICENSE").read_text(encoding="utf-8") == "MIT\n"

    # Second run with identical sources: short-circuit (still valid tree)
    subprocess.run(
        [sys.executable, str(prep), "--repository-root", str(tmp_path)],
        check=True,
    )
    assert (pkg / "contracts" / "compatibility.json").read_text(
        encoding="utf-8"
    ) == '{"v":1}\n'

    # Source change forces overwrite again
    (tmp_path / "contracts" / "compatibility.json").write_text(
        '{"v":2}\n', encoding="utf-8"
    )
    subprocess.run(
        [sys.executable, str(prep), "--repository-root", str(tmp_path)],
        check=True,
    )
    assert (pkg / "contracts" / "compatibility.json").read_text(
        encoding="utf-8"
    ) == '{"v":2}\n'


def test_assert_release_version_includes_pypi() -> None:
    out = subprocess.check_output(
        [
            sys.executable,
            str(TOOLS / "assert_release_version.py"),
            "--repository-root",
            str(ROOT),
            "--version",
            "0.1.0",
        ],
        text=True,
    )
    data = json.loads(out)
    assert data["status"] == "success"
    assert "hypabolic-trajectory" in data.get("pypi", [])


def test_validate_release_metadata_progressive_python() -> None:
    out = subprocess.check_output(
        [
            sys.executable,
            str(TOOLS / "validate_release_metadata.py"),
            "--repository-root",
            str(ROOT),
        ],
        text=True,
    )
    data = json.loads(out)
    assert data["status"] == "success"
    assert data["version"] == (
        ROOT / "VERSION"
    ).read_text(encoding="utf-8").strip()


def test_stamp_apply_version_lists_pyproject() -> None:
    """Real pyproject currently matches VERSION (lockstep on branch)."""
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    text = (ROOT / "python" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{version}"' in text


def test_requires_dist_audit_unconditional_only() -> None:
    """Bare otel/sqlite fails; extra==otel allowed; compounds fail-closed."""
    sys.path.insert(0, str(TOOLS))
    from python_package_smoke import (  # noqa: E402
        _marker_true_for_bare_install,
        audit_requires_dist,
    )

    with pytest.raises(SystemExit):
        audit_requires_dist(
            "Metadata-Version: 2.4\nProvides-Extra: otel\n"
            "Requires-Dist: opentelemetry-api>=1.0\n\n"
        )
    with pytest.raises(SystemExit):
        audit_requires_dist(
            "Metadata-Version: 2.4\nProvides-Extra: otel\n"
            "Requires-Dist: pysqlite3-binary>=0.1\n\n"
        )
    # PEP 503 equivalent spelling with dots
    with pytest.raises(SystemExit):
        audit_requires_dist(
            "Metadata-Version: 2.4\nProvides-Extra: otel\n"
            "Requires-Dist: pysqlite3.binary>=0.1\n\n"
        )

    # extra-gated allowed
    audit_requires_dist(
        'Metadata-Version: 2.4\nProvides-Extra: otel\n'
        'Requires-Dist: opentelemetry-api>=1.0; extra == "otel"\n\n'
    )
    audit_requires_dist(
        'Metadata-Version: 2.4\nProvides-Extra: otel\n'
        'Requires-Dist: pysqlite3; extra == "otel"\n\n'
    )
    # marker false on bare 3.11+ install allowed
    audit_requires_dist(
        'Metadata-Version: 2.4\nProvides-Extra: otel\n'
        'Requires-Dist: opentelemetry-api; python_version < "3.0"\n\n'
    )
    # extra != "otel" is TRUE on bare install — must fail
    with pytest.raises(SystemExit):
        audit_requires_dist(
            'Metadata-Version: 2.4\nProvides-Extra: otel\n'
            'Requires-Dist: opentelemetry-api>=1.0; extra != "otel"\n\n'
        )
    # Compound extra== or python_version: bare-true → must fail audit
    with pytest.raises(SystemExit):
        audit_requires_dist(
            'Metadata-Version: 2.4\nProvides-Extra: otel\n'
            'Requires-Dist: opentelemetry-api; extra == "otel" or python_version >= "3.11"\n\n'
        )
    # packaging path: pure extra == is bare-false
    assert _marker_true_for_bare_install('extra == "otel"') is False
    assert _marker_true_for_bare_install('extra != "otel"') is True


def test_license_expression_exact_mit() -> None:
    """License-Expression must be exact MIT, not a substring match."""
    sys.path.insert(0, str(TOOLS))
    from python_package_smoke import assert_wheel_column  # noqa: E402

    schemas = ["a.schema.json"]
    members = {
        "hypabolic_trajectory/contracts/compatibility.json",
        "hypabolic_trajectory/contracts/schemas/a.schema.json",
        "hypabolic_trajectory/runtime-capabilities.json",
        "hypabolic_trajectory/py.typed",
        "hypabolic_trajectory-0.1.0.dist-info/licenses/LICENSE",
        "hypabolic_trajectory-0.1.0.dist-info/METADATA",
    }
    good_meta = (
        "Summary: demo\nDescription-Content-Type: text/markdown\n"
        "License-Expression: MIT\nLicense-File: LICENSE\n\nbody\n"
    )
    assert_wheel_column(members, schemas, good_meta)
    with pytest.raises(SystemExit):
        assert_wheel_column(
            members,
            schemas,
            "Summary: x\nDescription-Content-Type: text/markdown\n"
            "License-Expression: NOT-MITISH\nLicense-File: LICENSE\n\nbody\n",
        )
    with pytest.raises(SystemExit):
        assert_wheel_column(
            members,
            schemas,
            "Summary: x\nDescription-Content-Type: text/markdown\n"
            "License-Expression: MIT License\nLicense-File: LICENSE\n\nbody\n",
        )
    # Legacy License: field alone must not satisfy SPDX License-Expression pin
    with pytest.raises(SystemExit):
        assert_wheel_column(
            members,
            schemas,
            "Summary: x\nDescription-Content-Type: text/markdown\n"
            "License: MIT\nLicense-File: LICENSE\n\nbody\n",
        )


def test_prepare_partial_monorepo_fails(tmp_path: Path) -> None:
    """Partial monorepo sources must not existence-skip."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "compatibility.json").write_text("{}", encoding="utf-8")
    # Missing schemas/, LICENSE, caps, package dir
    prep = TOOLS / "prepare_python_package.py"
    proc = subprocess.run(
        [sys.executable, str(prep), "--repository-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "incomplete monorepo sources" in (proc.stderr + proc.stdout)


def test_apply_version_lists_python_pyproject(tmp_path: Path) -> None:
    """apply_version rewrites and lists python/pyproject.toml when present."""
    # Minimal stubs so apply_version's other mutators have targets.
    (tmp_path / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    for rel in (
        "dotnet/src/Trajectory/Trajectory.csproj",
        "dotnet/src/Trajectory.OpenTelemetry/Trajectory.OpenTelemetry.csproj",
        "dotnet/src/Trajectory.Testing/Trajectory.Testing.csproj",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '<Project><PropertyGroup><Version>0.1.0</Version></PropertyGroup></Project>\n',
            encoding="utf-8",
        )
    for rel in (
        "typescript/package.json",
        "typescript/packages/trajectory/package.json",
        "typescript/packages/trajectory-node/package.json",
        "typescript/packages/trajectory-otel/package.json",
        "typescript/packages/trajectory-testing/package.json",
        "typescript/packages/trajectory-cli/package.json",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"name":"x","version":"0.1.0"}\n', encoding="utf-8")
    cargo = tmp_path / "rust" / "Cargo.toml"
    cargo.parent.mkdir(parents=True, exist_ok=True)
    cargo.write_text(
        '[workspace.package]\nversion = "0.1.0"\n\n'
        '[workspace.dependencies]\nhypabolic-trajectory = { version = "=0.1.0", path = "crates/hypabolic-trajectory" }\n',
        encoding="utf-8",
    )
    pyproject = tmp_path / "python" / "pyproject.toml"
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    pyproject.write_text(
        '[project]\nname = "hypabolic-trajectory"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    sys.path.insert(0, str(TOOLS))
    from set_package_version import apply_version  # noqa: E402

    changed = apply_version(tmp_path, "9.9.9")
    assert "python/pyproject.toml" in changed
    assert 'version = "9.9.9"' in pyproject.read_text(encoding="utf-8")


def test_trees_equal_is_byte_deep(tmp_path: Path) -> None:
    """Contracts short-circuit must not accept same size/mtime different bytes."""
    sys.path.insert(0, str(TOOLS))
    from prepare_python_package import _trees_equal  # noqa: E402

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    fa = a / "f.txt"
    fb = b / "f.txt"
    fa.write_bytes(b"AAAA")
    fb.write_bytes(b"BBBB")
    # Force identical mtime
    import os

    st = fa.stat()
    os.utime(fb, (st.st_atime, st.st_mtime))
    assert not _trees_equal(a, b)


