"""PY-10b-sources-ahp: claim-writer expands progressive sources with ahp.

Authority: docs/python-implementation-spec.md §5 claim-writer rule + §9
PY-10b-sources-ahp. Claim only when filtered verify is green for ahp under
the schema→op map for currently claimed normalize outputs.
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
CAPS_PATH = ROOT / "python" / "runtime-capabilities.json"
INTERIOR_CAPS = ROOT / "python" / "src" / "hypabolic_trajectory" / "runtime-capabilities.json"

# Tip source order (contracts/compatibility + spec §5 tip matrix).
_TIP_SOURCE_ORDER = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
)


def _filtered_verify(*sources: str) -> subprocess.CompletedProcess[str]:
    """Shared verify.py with explicit source + normalize-letta/canonical filters."""
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


def test_claim_writer_includes_ahp() -> None:
    """PY-10b-sources-ahp: ahp is present in progressive claimed sources."""
    data = json.loads(CAPS_PATH.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    sources = data["sources"]
    assert "ahp" in sources
    assert "pi" in sources  # baseline from PY-10a remains
    # Tip order: claimed sources appear in tip sequence (subset may grow).
    claimed_tip = [s for s in _TIP_SOURCE_ORDER if s in sources]
    assert sources[: len(claimed_tip)] == claimed_tip or sources == claimed_tip
    # Letta/canonical remain the progressive normalize outputs for this slice
    # (other claim-writers may add more outputs — do not forbid them here).
    assert "letta-trajectory-v1" in data["outputs"]
    assert "letta-canonical-v1" in data["outputs"]
    # Packaged interior copy must match authoritative path.
    interior = json.loads(INTERIOR_CAPS.read_text(encoding="utf-8"))
    assert interior == data


def test_filtered_verify_ahp_normalize_green() -> None:
    """Claim gate: filtered ahp normalize-letta/canonical is green."""
    completed = _filtered_verify("ahp")
    assert completed.returncode == 0, (
        f"verify failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    # ahp cases: tool-calls (letta+canonical), multi-turn (canonical),
    # cancelled-turn (canonical) → 3 cases / 4 ops.
    assert summary["cases"] == 3
    assert summary["operations"] == 4


def test_filtered_verify_pi_and_ahp_green() -> None:
    """Progressive honesty for this issue: pi + ahp under claimed normalize ops."""
    completed = _filtered_verify("pi", "ahp")
    assert completed.returncode == 0, (
        f"verify failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    assert summary["operations"] >= 5
