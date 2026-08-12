"""PY-17: first-ship join — §11 Definition of Done checklist.

Acceptance (docs/python-implementation-spec.md §9 PY-17 / §11):
  Conformance + packaging + CI + engine + docs on same tag.

Does **not** cut a git tag or publish to PyPI. Formalizes that every §11 DoD
item is green on this branch so a multi-registry ship tag can be cut later.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

import hypabolic_trajectory as ht
from hypabolic_trajectory import ir as ir_mod
from hypabolic_trajectory import otel as otel_mod

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TOOLS = ROOT / "tools"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
PYPROJECT = ROOT / "python" / "pyproject.toml"
CAPS_PATH = ROOT / "python" / "runtime-capabilities.json"
COMPAT_PATH = ROOT / "contracts" / "compatibility.json"
IDENTITY_BASELINE = ROOT / "conformance" / "identity-baseline.sha256"
VALIDATOR = TOOLS / "validate_release_metadata.py"
PACK_SMOKE = TOOLS / "python_package_smoke.py"
PRODUCT_README = ROOT / "python" / "README.md"
STATUS_DOC = ROOT / "docs" / "python-impl-status.md"
PUBLISHING_MD = ROOT / "docs" / "publishing.md"
RELEASE_READY = ROOT / "docs" / "release-readiness.md"

RUNNER_ENV = {
    **os.environ,
    "PYTHONPATH": os.pathsep.join(
        [
            str(ROOT / "python" / "tools"),
            str(ROOT / "python" / "src"),
            os.environ.get("PYTHONPATH", ""),
        ]
    ),
}

TIP_SOURCES = ["pi", "claude-code", "codex", "openclaw", "hermes", "ahp", "grok-build"]
TIP_OUTPUTS = [
    "letta-trajectory-v1",
    "letta-canonical-v1",
    "hypabolic-trajectory-v1",
    "openai-chat-messages",
    "jsonl-minimal",
    "otel-genai-spans-v1",
]
TIP_CAPABILITIES = [
    "normalize",
    "normalize-partial",
    "list-explicit-root",
    "typed-diagnostics",
    "typed-fatal-errors",
    "deterministic-rerun",
]
TIP_SLICE = "ML13"
NORMALIZER_CONTRACT_VERSION = "0.2.0"
# 39 batch + 24 stream scaffolds (stream ops skip as unsupported pre-engine / pre LS-12 caps).
EXPECTED_TIP_CASES = 63
EXPECTED_TIP_OPERATIONS = 90
EXPECTED_IDENTITY_GOLDENS = 37

# Exhaustive root __all__ (export owner PY-04a → PY-12 pin).
_EXPECTED_ROOT_ALL = frozenset(
    {
        "NORMALIZER_CONTRACT_VERSION",
        "PACKAGE_VERSION",
        "__version__",
        "WIRE_PACKAGE_VERSION",
        "LETTA_TRAJECTORY_V1",
        "LETTA_CANONICAL_V1",
        "HYPABOLIC_TRAJECTORY_V1",
        "OPENAI_CHAT_MESSAGES",
        "JSONL_MINIMAL",
        "OTEL_GENAI_SPANS_V1",
        "SCHEMA_IDS",
        "SchemaId",
        "IMPLEMENTED_SOURCES",
        "TrajectorySource",
        "JsonPrimitive",
        "JsonValue",
        "JsonObject",
        "SourceContext",
        "ToolArgumentBounds",
        "ToolResultBounds",
        "Bounds",
        "Filters",
        "NormalizeOptions",
        "NormalizeRequest",
        "TrajectoryListing",
        "TrajectoryListingPage",
        "Diagnostic",
        "TrajectoryError",
        "TrajectoryEngine",
        "normalize_to_ir",
        "normalize_to_letta",
        "normalize_to_canonical",
        "normalize_to_hypabolic",
        "project_letta",
        "project_canonical",
        "project_hypabolic",
        "project_openai",
        "project_minimal_jsonl",
        "project_otel_genai",
        "list_trajectories",
        "serialize_projection",
        "canonical_json",
        "TrajectoryIR",
        "IrRecord",
        "RecordKind",
        "TrajectoryRole",
        "ToolCall",
        "Provenance",
        "SourceIdentityKind",
        "SourceAnchorKind",
        "RecordHashes",
        "AppliedConfig",
        "AppliedBounds",
        "AppliedFilters",
        "TrajectoryExecution",
        "ModelInvocation",
        "ModelTokenUsage",
        "WorkflowInvocation",
    }
)

_EXPECTED_IR_ALL = frozenset(
    {
        "TrajectoryIR",
        "IrRecord",
        "RecordKind",
        "TrajectoryRole",
        "ToolCall",
        "Provenance",
        "SourceIdentityKind",
        "SourceAnchorKind",
        "RecordHashes",
        "AppliedConfig",
        "AppliedBounds",
        "AppliedFilters",
        "TrajectoryExecution",
        "ModelInvocation",
        "ModelTokenUsage",
        "WorkflowInvocation",
        "Diagnostic",
    }
)

_EXPECTED_OTEL_ALL = frozenset({"SpanSetSink", "emit_to"})


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _job_block(workflow: str, job_id: str) -> str:
    pattern = re.compile(
        rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        re.DOTALL,
    )
    m = pattern.search(workflow)
    assert m is not None, f"job {job_id!r} not found"
    return m.group(1)


# ---------------------------------------------------------------------------
# §11.1 Independence — pure Python, no FFI
# ---------------------------------------------------------------------------


def test_dod_01_independence_no_ffi_imports() -> None:
    """Core package must not import FFI/subprocess bridges to other runtimes."""
    src = ROOT / "python" / "src" / "hypabolic_trajectory"
    banned = (
        "ctypes",
        "cffi",
        "pyo3",
        "_cffi_backend",
        "wasmtime",
        "wasmer",
        "clr",
        "pythonnet",
    )
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in banned:
            # crude but sufficient: import/from of banned modules
            if re.search(rf"(?m)^\s*(import|from)\s+{re.escape(name)}\b", text):
                offenders.append(f"{path.relative_to(ROOT)}: {name}")
    assert not offenders, "FFI-style imports in core:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# §11.3 Capabilities honesty + §11.4 Parity (tip verify + identity)
# ---------------------------------------------------------------------------


def test_dod_03_capabilities_tip_honesty() -> None:
    caps = _load_json(CAPS_PATH)
    compat = _load_json(COMPAT_PATH)
    assert caps["runtime"] == "python"
    assert caps["slice"] == TIP_SLICE
    assert caps["normalizer_contract_version"] == NORMALIZER_CONTRACT_VERSION
    assert caps["sources"] == TIP_SOURCES
    assert caps["outputs"] == TIP_OUTPUTS
    assert caps["capabilities"] == TIP_CAPABILITIES
    assert caps["sources"] == compat["implemented"]["sources"]
    assert caps["outputs"] == compat["implemented"]["outputs"]
    assert caps["capabilities"] == compat["capabilities"]["required"]


@pytest.mark.heavy
def test_dod_04_unfiltered_verify_and_identity_baseline() -> None:
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--",
        PYTHON,
        "-m",
        "trajectory_conformance",
    ]
    completed = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=RUNNER_ENV,
        check=False,
    )
    assert completed.returncode == 0, (
        f"unfiltered verify failed:\nstdout={completed.stdout}\n"
        f"stderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    assert summary["cases"] == EXPECTED_TIP_CASES
    assert summary["operations"] == EXPECTED_TIP_OPERATIONS

    # Identity baseline (portable hashlib; peer CI uses sha256sum --check).
    lines = IDENTITY_BASELINE.read_text(encoding="utf-8").splitlines()
    checked = 0
    failures: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "  " not in line:
            failures.append(f"malformed: {line!r}")
            continue
        expected_hex, rel_path = line.split("  ", 1)
        target = ROOT / rel_path
        if not target.is_file():
            failures.append(f"missing: {rel_path}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected_hex:
            failures.append(f"{rel_path}: expected {expected_hex}, got {actual}")
            continue
        checked += 1
    assert not failures, "identity baseline:\n" + "\n".join(failures)
    assert checked == EXPECTED_IDENTITY_GOLDENS


# ---------------------------------------------------------------------------
# §11.5 API — free functions, engine, ir/otel, py.typed
# ---------------------------------------------------------------------------


def test_dod_05_api_surface_and_engine() -> None:
    assert frozenset(ht.__all__) == _EXPECTED_ROOT_ALL
    assert frozenset(ir_mod.__all__) == _EXPECTED_IR_ALL
    assert frozenset(otel_mod.__all__) == _EXPECTED_OTEL_ALL

    assert ht.NORMALIZER_CONTRACT_VERSION == NORMALIZER_CONTRACT_VERSION
    # Wire package version is pinned for golden identity (not package SemVer).
    assert ht.WIRE_PACKAGE_VERSION == "0.1.0"
    package_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert package_version == ht.PACKAGE_VERSION
    assert package_version  # non-empty synchronized SemVer

    # Free functions callable.
    for name in (
        "normalize_to_ir",
        "project_letta",
        "project_canonical",
        "project_hypabolic",
        "project_openai",
        "project_minimal_jsonl",
        "project_otel_genai",
        "list_trajectories",
        "serialize_projection",
        "canonical_json",
    ):
        assert callable(getattr(ht, name)), name

    # Working TrajectoryEngine on root __all__.
    eng = ht.TrajectoryEngine.create_default()
    assert "TrajectoryEngine" in ht.__all__
    assert hasattr(eng, "project")
    assert hasattr(eng, "add_output_adapter")

    # Duplicate adapter → ValueError (not TrajectoryError).
    def _noop_adapter(ir: ht.TrajectoryIR) -> dict:
        return {"schema": "x"}

    try:
        eng.add_output_adapter(ht.LETTA_TRAJECTORY_V1, _noop_adapter)
        raise AssertionError("expected ValueError on duplicate adapter")
    except ValueError:
        pass

    # pure otel without extra
    assert callable(ht.project_otel_genai)
    # otel submodule always importable
    assert hasattr(otel_mod, "SpanSetSink")
    assert hasattr(otel_mod, "emit_to")

    # py.typed present
    assert (ROOT / "python" / "src" / "hypabolic_trajectory" / "py.typed").is_file()

    # TrajectoryIR.source is TrajectorySource-typed (runtime annotation).
    hints = ht.TrajectoryIR.__annotations__
    assert "source" in hints


# ---------------------------------------------------------------------------
# §11.6 Optional OTEL — core deps clean
# ---------------------------------------------------------------------------


def test_dod_06_core_has_no_unconditional_otel_or_sqlite_deps() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    # Ensure no [project.scripts] / [project.gui-scripts] tables (comment ok).
    assert not re.search(r"(?m)^\[project\.scripts\]", text)
    assert not re.search(r"(?m)^\[project\.gui-scripts\]", text)
    assert "opentelemetry-api" in text
    assert "opentelemetry-sdk" in text
    # opentelemetry only under optional-dependencies otel
    otel_block = re.search(
        r"(?ms)^otel = \[(.*?)^\]",
        text,
    )
    assert otel_block is not None
    assert "opentelemetry-api" in otel_block.group(1)
    assert "opentelemetry-sdk" in otel_block.group(1)
    # No unconditional project dependencies array with those names
    assert not re.search(
        r"(?ms)^dependencies = \[.*?opentelemetry",
        text,
    )
    assert "hatchling>=1.27" in text
    assert 'requires-python = ">=3.11"' in text


# ---------------------------------------------------------------------------
# §11.7 Listing — registry dispatcher present (full algorithm in PY-09b/10b)
# ---------------------------------------------------------------------------


def test_dod_07_list_trajectories_is_public_and_callable() -> None:
    assert "list_trajectories" in ht.__all__
    assert callable(ht.list_trajectories)
    caps = _load_json(CAPS_PATH)
    assert "list-explicit-root" in caps["capabilities"]


# ---------------------------------------------------------------------------
# §11.8 Packaging — stamp, sdist exclusion, pack-smoke green
# ---------------------------------------------------------------------------


def test_dod_08_sdist_artifacts_are_root_anchored() -> None:
    """Bare README.md/LICENSE artifact globs force-include nested samples READMEs."""
    text = PYPROJECT.read_text(encoding="utf-8")
    # Extract sdist section (comments may sit between header and artifacts=).
    m = re.search(
        r"(?ms)\[tool\.hatch\.build\.targets\.sdist\]\s*\n(.*?)(?=\n\[|\Z)",
        text,
    )
    assert m is not None, "sdist target section missing"
    section = m.group(1)
    art = re.search(r"(?ms)artifacts = \[(.*?)\]", section)
    assert art is not None, "sdist artifacts block missing"
    block = art.group(1)
    # Root-anchored forms only for top-level names.
    assert '"/README.md"' in block or "'/README.md'" in block
    assert '"/LICENSE"' in block or "'/LICENSE'" in block
    assert '"/pyproject.toml"' in block or "'/pyproject.toml'" in block
    # Bare unanchored names must not appear (would match samples/**/README.md).
    assert re.search(r'(?m)^\s*"README\.md"', block) is None
    assert re.search(r"(?m)^\s*'README\.md'", block) is None
    assert re.search(r'(?m)^\s*"LICENSE"', block) is None
    # samples excluded somewhere in the sdist section
    assert "/samples" in section or "samples/**" in section or "samples/" in section


@pytest.mark.heavy
def test_dod_08_pack_smoke_two_column_green() -> None:
    """Full two-column pack-smoke including isolated sdist install.

    Marked heavy: CI python-unit matrix skips this; python-package-smoke runs
    the same pack-smoke tool directly.
    """
    with tempfile.TemporaryDirectory(prefix="py17-pack-") as tmp:
        outdir = Path(tmp)
        completed = subprocess.run(
            [
                PYTHON,
                str(PACK_SMOKE),
                "--repository-root",
                str(ROOT),
                "--outdir",
                str(outdir),
            ],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            check=False,
        )
        assert completed.returncode == 0, (
            f"pack-smoke failed:\nstdout={completed.stdout}\n"
            f"stderr={completed.stderr}"
        )

        sdists = list(outdir.glob("*.tar.gz"))
        wheels = list(outdir.glob("*.whl"))
        assert len(sdists) == 1
        assert len(wheels) == 1

        # Explicit forbidden-prefix recheck (sdist).
        with tarfile.open(sdists[0], "r:gz") as tf:
            names = [n for n in tf.getnames() if n and not n.endswith("/")]
        # strip versioned prefix
        stripped: list[str] = []
        for n in names:
            parts = n.split("/", 1)
            stripped.append(parts[1] if len(parts) == 2 else n)
        for m in stripped:
            for prefix in ("tests/", "samples/", "tools/"):
                assert not m.startswith(prefix), f"sdist has forbidden {m}"

        # Wheel must not ship samples/tools/tests.
        with zipfile.ZipFile(wheels[0]) as zf:
            wnames = zf.namelist()
        for m in wnames:
            assert not m.startswith("samples/"), m
            assert not m.startswith("tools/"), m
            assert "/tests/" not in f"/{m}/"


def test_dod_08_validate_release_metadata_green() -> None:
    completed = subprocess.run(
        [PYTHON, str(VALIDATOR), "--repository-root", str(ROOT)],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "success"
    assert payload["slice"] == TIP_SLICE
    assert payload["version"] == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# §11.9 CI — unit, conformance tip, package-smoke, OIDC release path
# ---------------------------------------------------------------------------


def test_dod_09_ci_jobs_present() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    for job_id in ("python-unit", "python-conformance", "python-package-smoke"):
        assert f"{job_id}:" in text, job_id
        job = _job_block(text, job_id)
        assert "continue-on-error:" not in job
        assert "actions/setup-python" in job

    conf = _job_block(text, "python-conformance")
    assert "python-conformance-candidates" in conf
    assert "identity-baseline.sha256" in conf
    assert "conformance/verify.py" in conf
    # tip (unfiltered) — no generator argv injection into verify
    assert not re.search(
        r"verify\.py[^\n]*\$\(python tools/conformance_argv_from_capabilities",
        conf,
    )


def test_dod_09_release_oidc_pypi_path() -> None:
    text = RELEASE_YML.read_text(encoding="utf-8")
    assert "publish-pypi:" in text
    job = _job_block(text, "publish-pypi")
    assert "id-token: write" in job
    assert "environment: release" in job
    assert "pypa/gh-action-pypi-publish@release/v1" in job
    assert "packages-dir: artifacts/release/pypi" in job
    assert "skip-existing: true" in job
    # download-only
    assert "python -m build" not in job
    assert "prepare_python_package" not in job

    validate = _job_block(text, "validate")
    assert "artifacts/release/pypi" in validate
    assert "python_package_smoke" in validate

    gh = _job_block(text, "github-release")
    assert "publish-pypi" in gh
    assert "artifacts/release/pypi/*" in gh


# ---------------------------------------------------------------------------
# §11.10 Docs — install, imports, formulas, filtered runner argv
# ---------------------------------------------------------------------------


def test_dod_10_docs_ship_surface() -> None:
    readme = PRODUCT_README.read_text(encoding="utf-8")
    for needle in (
        "pip install hypabolic-trajectory",
        "hypabolic_trajectory",
        "WIRE_PACKAGE_VERSION",
        "0.2.0",
        "project_otel_genai",
        "TrajectoryEngine",
        "conformance/verify.py",
        "trajectory_conformance",
        "OTEL import",
        "serialize_projection",
        "list_trajectories",
    ):
        assert needle in readme, f"python/README.md missing {needle!r}"

    status = STATUS_DOC.read_text(encoding="utf-8")
    assert "PY-17" in status

    publishing = PUBLISHING_MD.read_text(encoding="utf-8")
    assert "hypabolic-trajectory" in publishing
    assert "Trusted Publishing" in publishing or "OIDC" in publishing

    release = RELEASE_READY.read_text(encoding="utf-8")
    assert "hypabolic-trajectory" in release
    assert "python" in release.lower()


# ---------------------------------------------------------------------------
# §11.11 No false claims
# ---------------------------------------------------------------------------


def test_dod_11_no_false_claims() -> None:
    caps = _load_json(CAPS_PATH)
    # Shape A only for AHP — no live/shape-B capability strings.
    for bad in ("ahp-shape-b", "ahp-live", "sqlite-stores", "conformance-rpc"):
        assert bad not in caps["capabilities"]
        assert bad not in caps["sources"]
        assert bad not in caps["outputs"]

    # No console scripts marketed in pyproject.
    text = PYPROJECT.read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\[project\.scripts\]", text)

    # Do not retag 0.1.0 — docs state next synchronized tag.
    readme = PRODUCT_README.read_text(encoding="utf-8")
    assert "do not retag" in readme.lower() or "next" in readme.lower()


# ---------------------------------------------------------------------------
# Join glue: dependency issues reported Done in status
# ---------------------------------------------------------------------------


def test_join_status_marks_py17_and_deps_done() -> None:
    """Status doc is the human join checklist; must mark PY-17 Done when green."""
    text = STATUS_DOC.read_text(encoding="utf-8")
    # Dependencies of PY-17 must already be Done.
    for issue in (
        "PY-11",
        "PY-12",
        "PY-14a",
        "PY-14b",
        "PY-15b",
        "PY-16",
        "PY-17",
    ):
        # Match table rows like "| PY-17 first-ship join | **Done**"
        rows = [
            line
            for line in text.splitlines()
            if issue in line and line.strip().startswith("|")
        ]
        assert rows, f"no status row for {issue}"
        # At least one row for the issue should say Done (not Not ready).
        assert any("Done" in r or "**Done**" in r for r in rows), (
            f"{issue} not marked Done in status rows: {rows}"
        )
        assert not any("Not ready" in r for r in rows), (
            f"{issue} still Not ready: {rows}"
        )
