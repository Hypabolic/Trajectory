"""PY-15b: CI tip gate — full suite + tip equality.

Acceptance (docs/python-implementation-spec.md §9 PY-15b):
  Full suite + tip equality | Tip honesty.

Covers:
  * .github/workflows/ci.yml ``python-conformance`` tip topology
    (jq tip equality, unfiltered verify, generator tip-equality check,
    identity-baseline, contracts freeze, artifact pin, no continue-on-error)
  * tools/validate_release_metadata.py ship rules (sources/outputs/
    capabilities/slice equality to tip ML13 matrix)
  * Generator check-only reports proper_subset_of_tip=false at tip
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TOOLS = ROOT / "tools"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
CAPS_PATH = ROOT / "python" / "runtime-capabilities.json"
COMPAT_PATH = ROOT / "contracts" / "compatibility.json"
VALIDATOR = TOOLS / "validate_release_metadata.py"
GENERATOR = TOOLS / "conformance_argv_from_capabilities.py"

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


def _job_block(workflow: str, job_id: str) -> str:
    pattern = re.compile(
        rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        re.DOTALL,
    )
    m = pattern.search(workflow)
    assert m is not None, f"job {job_id!r} not found in ci.yml"
    return m.group(1)


# ---------------------------------------------------------------------------
# CI job topology (ci.yml) — tip gate
# ---------------------------------------------------------------------------


def test_ci_yml_python_conformance_is_tip_gate() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    assert "python-conformance:" in text
    job = _job_block(text, "python-conformance")

    # Job named tip (not progressive-only).
    assert "Shared conformance / Python tip" in text

    # Single 3.11 via setup-python.
    assert "actions/setup-python" in job
    assert "python-version: '3.11'" in job or 'python-version: "3.11"' in job

    # Editable install.
    assert "pip install -e './python[dev]'" in job or 'pip install -e "./python[dev]"' in job

    # Tip equality jq on capabilities (sources + outputs + capabilities + slice).
    assert "Validate Python capability manifest" in job
    assert "jq -e" in job
    assert 'runtime == "python"' in job or ".runtime == \"python\"" in job
    assert "list-explicit-root" in job
    for source in TIP_SOURCES:
        assert source in job
    for schema in TIP_OUTPUTS:
        assert schema in job

    # Generator check-only still enforces §5 maps + tip equality flag.
    assert "conformance_argv_from_capabilities.py" in job
    assert "--check-only" in job
    assert "proper_subset_of_tip" in job

    # Unfiltered tip verify (no generator argv injection into verify).
    assert "conformance/verify.py" in job
    assert "trajectory_conformance" in job
    assert "PYTHONPATH=python/tools" in job
    assert "unfiltered tip" in job
    # Must not feed generator argv into verify (progressive-style injection).
    assert not re.search(
        r"verify\.py[^\n]*\$\(python tools/conformance_argv_from_capabilities",
        job,
    )
    # Generator may still appear for --check-only only.
    assert job.count("conformance_argv_from_capabilities.py") == 1

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


# ---------------------------------------------------------------------------
# validate_release_metadata ship tip equality
# ---------------------------------------------------------------------------


def test_validate_release_metadata_requires_tip_equality() -> None:
    """Ship validator enforces Python sources/outputs/capabilities/slice == tip."""
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

    # Authoritative caps already at tip (validator would fail otherwise).
    caps = json.loads(CAPS_PATH.read_text(encoding="utf-8"))
    compat = json.loads(COMPAT_PATH.read_text(encoding="utf-8"))
    assert caps["sources"] == TIP_SOURCES
    assert caps["outputs"] == TIP_OUTPUTS
    assert caps["capabilities"] == TIP_CAPABILITIES
    assert caps["slice"] == TIP_SLICE
    assert caps["sources"] == compat["implemented"]["sources"]
    assert caps["outputs"] == compat["implemented"]["outputs"]
    assert caps["capabilities"] == compat["capabilities"]["required"]


def test_validate_release_metadata_source_mentions_ship_equality() -> None:
    """Validator source documents PY-15b ship tip equality (not progressive-only)."""
    text = VALIDATOR.read_text(encoding="utf-8")
    assert "PY-15b" in text or "ship" in text.lower()
    assert "must equal tip" in text
    # Progressive-only subset wording must not be the sole gate.
    assert "not subset of tip" not in text


# ---------------------------------------------------------------------------
# Generator tip-equality signal (used by CI check-only step)
# ---------------------------------------------------------------------------


def test_generator_check_only_reports_not_proper_subset() -> None:
    completed = subprocess.run(
        [
            PYTHON,
            str(GENERATOR),
            "--repository-root",
            str(ROOT),
            "--check-only",
        ],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["status"] == "ok"
    assert plan["proper_subset_of_tip"] is False
    assert plan["matched_operations"] > 0
    assert set(plan["sources"]) == set(TIP_SOURCES)
