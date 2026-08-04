"""PY-15a: progressive python-conformance CI + argv generator/checker.

Acceptance (docs/python-implementation-spec.md §9 PY-15a):
  claimed⊆verified enforced; no continue-on-error; filtered when ⊂ tip.

Covers:
  * tools/conformance_argv_from_capabilities.py (§5 schema→op + capability coverage)
  * .github/workflows/ci.yml job ``python-conformance`` topology + artifact pin
  * fail-closed empty / unmet-coverage paths
  * progressive filters from tip capabilities equal full claimed surface
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TOOLS = ROOT / "tools"
GENERATOR = TOOLS / "conformance_argv_from_capabilities.py"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
CAPS_PATH = ROOT / "python" / "runtime-capabilities.json"
COMPAT_PATH = ROOT / "contracts" / "compatibility.json"

sys.path.insert(0, str(TOOLS))
from conformance_argv_from_capabilities import (  # noqa: E402
    SCHEMA_TO_OPERATION,
    GeneratorError,
    generate,
    operations_from_claims,
)


def _run_generator(*extra: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [PYTHON, str(GENERATOR), "--repository-root", str(ROOT), *extra]
    return subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=check,
    )


def _write_caps(
    path: Path,
    *,
    sources: list[str],
    outputs: list[str],
    capabilities: list[str],
    runtime: str = "python",
    ncv: str = "0.2.0",
) -> None:
    path.write_text(
        json.dumps(
            {
                "runtime": runtime,
                "slice": "ML13",
                "normalizer_contract_version": ncv,
                "sources": sources,
                "outputs": outputs,
                "capabilities": capabilities,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _job_block(workflow: str, job_id: str) -> str:
    pattern = re.compile(
        rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        re.DOTALL,
    )
    m = pattern.search(workflow)
    assert m is not None, f"job {job_id!r} not found in ci.yml"
    return m.group(1)


# ---------------------------------------------------------------------------
# Generator against repository tip capabilities
# ---------------------------------------------------------------------------


def test_generator_emits_filters_for_tip_capabilities() -> None:
    plan = generate(ROOT)
    caps = json.loads(CAPS_PATH.read_text(encoding="utf-8"))
    compat = json.loads(COMPAT_PATH.read_text(encoding="utf-8"))

    assert plan["sources"] == caps["sources"]
    assert plan["outputs"] == caps["outputs"]
    assert plan["capabilities"] == caps["capabilities"]

    expected_ops = operations_from_claims(caps["outputs"], caps["capabilities"])
    assert plan["operations"] == expected_ops
    assert "list-trajectories" in plan["operations"]
    for schema_id in caps["outputs"]:
        assert SCHEMA_TO_OPERATION[schema_id] in plan["operations"]

    argv = plan["argv"]
    assert argv  # never empty
    assert "--source" in argv
    assert "--operation" in argv
    # Every claimed source appears as a --source value.
    for source in caps["sources"]:
        assert source in argv
    for op in expected_ops:
        assert op in argv

    # Tip equality (PY-11 already claimed): not a proper subset.
    assert plan["proper_subset_of_tip"] is False
    assert set(caps["sources"]) == set(compat["implemented"]["sources"])
    assert set(caps["outputs"]) == set(compat["implemented"]["outputs"])
    assert plan["matched_operations"] > 0


def test_generator_cli_check_only_and_argv_formats() -> None:
    check = _run_generator("--check-only")
    assert check.returncode == 0, check.stderr
    payload = json.loads(check.stdout)
    assert payload["status"] == "ok"
    assert payload["matched_operations"] > 0
    assert isinstance(payload["sources"], list)
    assert isinstance(payload["operations"], list)

    argv_out = _run_generator("--format", "argv")
    assert argv_out.returncode == 0, argv_out.stderr
    tokens = argv_out.stdout.strip().split()
    assert tokens[0] == "--source"
    assert "--operation" in tokens

    lines_out = _run_generator("--format", "lines")
    assert lines_out.returncode == 0, lines_out.stderr
    line_tokens = [ln for ln in lines_out.stdout.splitlines() if ln]
    assert line_tokens == tokens

    json_out = _run_generator("--format", "json")
    assert json_out.returncode == 0, json_out.stderr
    plan = json.loads(json_out.stdout)
    assert plan["argv"] == tokens


def test_generator_proper_subset_emits_explicit_filters(tmp_path: Path) -> None:
    """When claimed ⊂ tip, argv must still carry --source/--operation filters."""
    caps_path = tmp_path / "runtime-capabilities.json"
    _write_caps(
        caps_path,
        sources=["pi"],
        outputs=["letta-trajectory-v1", "letta-canonical-v1"],
        capabilities=[
            "normalize",
            "normalize-partial",
            "typed-diagnostics",
            "typed-fatal-errors",
            "deterministic-rerun",
        ],
    )
    plan = generate(ROOT, capabilities_path=caps_path)
    assert plan["proper_subset_of_tip"] is True
    assert plan["sources"] == ["pi"]
    assert plan["operations"] == ["normalize-letta", "normalize-canonical"]
    assert "list-trajectories" not in plan["operations"]
    assert plan["argv"] == [
        "--source",
        "pi",
        "--operation",
        "normalize-letta",
        "--operation",
        "normalize-canonical",
    ]
    assert plan["matched_operations"] > 0


def test_generator_fail_closed_empty_sources(tmp_path: Path) -> None:
    caps_path = tmp_path / "runtime-capabilities.json"
    _write_caps(
        caps_path,
        sources=[],
        outputs=["letta-trajectory-v1"],
        capabilities=["normalize", "deterministic-rerun"],
    )
    with pytest.raises(GeneratorError, match="sources is empty"):
        generate(ROOT, capabilities_path=caps_path)


def test_generator_fail_closed_unknown_output(tmp_path: Path) -> None:
    caps_path = tmp_path / "runtime-capabilities.json"
    _write_caps(
        caps_path,
        sources=["pi"],
        outputs=["not-a-real-schema"],
        capabilities=["normalize", "deterministic-rerun"],
    )
    # Unknown schemas fail the tip-subset check first (same fail-closed path).
    with pytest.raises(GeneratorError, match="not subset of tip|schema"):
        generate(ROOT, capabilities_path=caps_path)


def test_generator_fail_closed_extra_source_beyond_tip(tmp_path: Path) -> None:
    caps_path = tmp_path / "runtime-capabilities.json"
    _write_caps(
        caps_path,
        sources=["pi", "made-up-source"],
        outputs=["letta-canonical-v1"],
        capabilities=["normalize", "typed-diagnostics", "deterministic-rerun"],
    )
    with pytest.raises(GeneratorError, match="not subset of tip"):
        generate(ROOT, capabilities_path=caps_path)


def test_generator_fail_closed_list_without_listing_source(tmp_path: Path) -> None:
    """ahp has no listing cases — list-explicit-root alone under ahp fails closed."""
    caps_path = tmp_path / "runtime-capabilities.json"
    _write_caps(
        caps_path,
        sources=["ahp"],
        outputs=[],
        capabilities=["list-explicit-root", "deterministic-rerun"],
    )
    with pytest.raises(GeneratorError, match="zero operations|list-trajectories"):
        generate(ROOT, capabilities_path=caps_path)


def test_generator_fail_closed_wrong_runtime(tmp_path: Path) -> None:
    caps_path = tmp_path / "runtime-capabilities.json"
    _write_caps(
        caps_path,
        sources=["pi"],
        outputs=["letta-canonical-v1"],
        capabilities=["normalize", "typed-diagnostics", "deterministic-rerun"],
        runtime="typescript",
    )
    with pytest.raises(GeneratorError, match="runtime must be 'python'"):
        generate(ROOT, capabilities_path=caps_path)


def test_generator_cli_exits_nonzero_on_bad_caps(tmp_path: Path) -> None:
    caps_path = tmp_path / "runtime-capabilities.json"
    _write_caps(
        caps_path,
        sources=[],
        outputs=["letta-trajectory-v1"],
        capabilities=["normalize"],
    )
    completed = subprocess.run(
        [
            PYTHON,
            str(GENERATOR),
            "--repository-root",
            str(ROOT),
            "--capabilities",
            str(caps_path),
        ],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=False,
    )
    assert completed.returncode == 1
    assert "FAIL" in completed.stderr


# ---------------------------------------------------------------------------
# CI job topology (ci.yml)
# ---------------------------------------------------------------------------


def test_ci_yml_python_conformance_job_topology() -> None:
    """PY-15a maps still enforced on the (now tip-mode) python-conformance job.

    PY-15b upgraded the job to unfiltered tip verify + jq tip equality; the
    progressive generator/checker remains for §5 coverage maps and tip-equality
    flag (proper_subset_of_tip == false). Full tip topology pins live in
    test_py15b_tip_gate.py.
    """
    text = CI_YML.read_text(encoding="utf-8")
    assert "python-conformance:" in text
    job = _job_block(text, "python-conformance")

    # Single 3.11 via setup-python (not bare system python3 for matrix claims).
    assert "actions/setup-python" in job
    assert "python-version: '3.11'" in job or 'python-version: "3.11"' in job

    # Editable install + progressive generator still present (check-only).
    assert "pip install -e './python[dev]'" in job or 'pip install -e "./python[dev]"' in job
    assert "conformance_argv_from_capabilities.py" in job
    assert "--check-only" in job
    assert "conformance/verify.py" in job
    assert "trajectory_conformance" in job
    assert "PYTHONPATH=python/tools" in job

    # Artifact name pin.
    assert "python-conformance-candidates" in job
    assert "artifacts/conformance-candidates" in job
    assert "if: failure()" in job

    # Identity baseline + contracts freeze.
    assert "identity-baseline.sha256" in job
    assert "git diff --exit-code -- contracts conformance" in job

    # Forbid soft-fail key on this job.
    assert "continue-on-error:" not in job


def test_ci_yml_no_continue_on_error_for_python_jobs() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    for job_id in ("python-unit", "python-conformance", "python-package-smoke"):
        job = _job_block(text, job_id)
        assert "continue-on-error:" not in job, f"{job_id} must not use continue-on-error"


def test_schema_to_op_map_covers_tip_outputs() -> None:
    compat = json.loads(COMPAT_PATH.read_text(encoding="utf-8"))
    for schema_id in compat["implemented"]["outputs"]:
        assert schema_id in SCHEMA_TO_OPERATION, schema_id
