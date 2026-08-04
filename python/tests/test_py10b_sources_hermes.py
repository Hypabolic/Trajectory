"""PY-10b-sources-hermes: claim-writer expands sources with hermes when verify-green.

Authority:
  - docs/python-implementation-spec.md §5 claim-writer rule + §9 issue table
  - Claimed ⊆ verified under schema→op map for letta/canonical normalize ops
  - This issue only requires hermes membership; peer claim-writers may add
    other tip sources in parallel (tip order must still hold).
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

TIP_SOURCE_ORDER = [
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
]
CLAIMED_OPS = ["normalize-letta", "normalize-canonical"]


def _load_caps() -> dict:
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    return json.loads(caps_path.read_text(encoding="utf-8"))


def test_claim_writer_sources_hermes() -> None:
    """Authoritative capabilities claim hermes after filtered green."""
    data = _load_caps()
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    sources = data["sources"]
    assert "hermes" in sources
    assert "pi" in sources  # PY-10a baseline retained
    # Tip order: claimed sources appear in compatibility tip sequence.
    tip_rank = {name: i for i, name in enumerate(TIP_SOURCE_ORDER)}
    ranks = [tip_rank[s] for s in sources]
    assert ranks == sorted(ranks), f"sources not tip-ordered: {sources}"
    # Listing claim is owned by PY-10b-list (may be present).
    # letta/canonical remain claimed normalize outputs (PY-10a).
    outputs = data["outputs"]
    assert "letta-trajectory-v1" in outputs
    assert "letta-canonical-v1" in outputs
    # Interior packaged copy must stay lockstep with authoritative path.
    interior = (
        ROOT / "python" / "src" / "hypabolic_trajectory" / "runtime-capabilities.json"
    )
    interior_data = json.loads(interior.read_text(encoding="utf-8"))
    assert interior_data == data


def test_filtered_verify_hermes_normalize_green() -> None:
    """Integration: hermes alone is green under normalize-letta/canonical."""
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--source",
        "hermes",
        "--operation",
        "normalize-letta",
        "--operation",
        "normalize-canonical",
        "--",
        *RUNNER_CMD,
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
        f"verify failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    # hermes cases: array-envelope-parity, cleanup, missing-assistant, tool-calls
    assert summary["cases"] == 4
    assert summary["operations"] == 7


def test_filtered_verify_pi_and_hermes_normalize_green() -> None:
    """Progressive honesty: pi + hermes under claimed normalize ops are green.

    Bare ``--source`` without ``--operation`` is forbidden for progressive
    honesty (would pull unclaimed listing/other outputs).
    """
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--source",
        "pi",
        "--source",
        "hermes",
    ]
    for operation in CLAIMED_OPS:
        cmd.extend(["--operation", operation])
    cmd.append("--")
    cmd.extend(RUNNER_CMD)

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
        f"verify failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    # pi (6 cases / 10 ops) + hermes (4 cases / 7 ops)
    assert summary["cases"] == 10
    assert summary["operations"] == 17
