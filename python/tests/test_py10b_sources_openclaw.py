"""PY-10b-sources-openclaw: claim openclaw when filtered normalize is green.

Claim-writer only. Does not implement adapters (PY-06-openclaw) or listing
runner ops (PY-10b-list). Asserts membership of ``openclaw`` in progressive
``runtime-capabilities.json`` and filtered verify green for claimed normalize
outputs (letta + canonical).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TOOLS = ROOT / "python" / "tools"
RUNNER_ENV = {
    **os.environ,
    "PYTHONPATH": os.pathsep.join(
        [
            str(TOOLS),
            str(ROOT / "python" / "src"),
            os.environ.get("PYTHONPATH", ""),
        ]
    ),
}
RUNNER_CMD = [PYTHON, "-m", "trajectory_conformance"]


def _filtered_verify(*sources: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
    ]
    for source in sources:
        cmd.extend(["--source", source])
    cmd.extend(
        [
            "--operation",
            "normalize-letta",
            "--operation",
            "normalize-canonical",
            "--",
            *RUNNER_CMD,
        ]
    )
    return subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=RUNNER_ENV,
        check=False,
    )


def test_claim_writer_openclaw_when_green() -> None:
    """openclaw is claimed only after filtered normalize-letta/canonical green."""
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    assert "openclaw" in data["sources"]
    # Tip-relative order: openclaw after codex when both claimed.
    if "codex" in data["sources"]:
        assert data["sources"].index("codex") < data["sources"].index("openclaw")
    if "pi" in data["sources"]:
        assert data["sources"].index("pi") < data["sources"].index("openclaw")
    # Claimed normalize outputs must include letta + canonical (openclaw cases).
    assert "letta-trajectory-v1" in data["outputs"]
    assert "letta-canonical-v1" in data["outputs"]
    # Listing is PY-10b-list — must not claim via this source expansion.
    assert "list-explicit-root" not in data["capabilities"]


def test_filtered_verify_openclaw_normalize_green() -> None:
    """Filtered openclaw normalize-letta/canonical is green (claim precondition)."""
    completed = _filtered_verify("openclaw")
    assert completed.returncode == 0, (
        f"verify failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    # openclaw/cleanup + openclaw/tool-calls × 2 ops each.
    assert summary["cases"] == 2
    assert summary["operations"] == 4


def test_filtered_verify_openclaw_with_pi_capability_coverage() -> None:
    """With pi retained, progressive capability coverage stays executable.

    normalize-partial and typed-fatal-errors are covered by pi cases while
    openclaw contributes additional normalize success/diagnostics cases.
    """
    completed = _filtered_verify("pi", "openclaw")
    assert completed.returncode == 0, (
        f"verify failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    # pi ≥10 ops historically + openclaw 4 ops.
    assert summary["operations"] >= 14
