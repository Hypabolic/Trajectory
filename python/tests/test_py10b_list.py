"""PY-10b-list: list-trajectories runner algorithm + $ROOT claim-writer."""

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


def _invoke(request: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        RUNNER_CMD,
        input=json.dumps(request, separators=(",", ":")),
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=RUNNER_ENV,
        check=False,
    )


def _request(
    case: str,
    operation: str,
    *,
    repository_root: str | None = None,
) -> dict[str, object]:
    return {
        "protocol_version": "1",
        "case": case,
        "operation": operation,
        "repository_root": repository_root if repository_root is not None else str(ROOT),
    }


def test_list_trajectories_pi_listing_all_pages_matches_golden() -> None:
    """pi/listing all_pages array: two pages, next_cursor null on last, $ROOT paths."""
    completed = _invoke(_request("pi/listing", "list-trajectories"))
    assert completed.returncode == 0, (
        f"runner failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["case"] == "pi/listing"
    assert response["operation"] == "list-trajectories"
    assert response["status"] == "success"
    assert response["fatal_error"] is None
    assert response["diagnostics"] == []

    expected = json.loads(
        (ROOT / "conformance/cases/pi/listing/expected.listing.json").read_text(
            encoding="utf-8"
        )
    )
    actual = json.loads(response["output_text"])
    assert actual == expected
    # Structural pins from acceptance criteria.
    assert isinstance(actual, list)
    assert len(actual) == 2
    assert actual[0]["next_cursor"] == "MQowCm5ld2Vy"
    assert actual[1]["next_cursor"] is None
    assert actual[0]["items"][0]["path"].startswith("$ROOT/")
    assert actual[1]["items"][0]["path"].startswith("$ROOT/")
    # Developer machine paths must never appear.
    assert "/var/" not in response["output_text"]
    assert "/tmp/" not in response["output_text"]
    assert "trajectory-conformance-" not in response["output_text"]


def test_list_trajectories_missing_store_protocol_error() -> None:
    """Cases that do not declare list-trajectories → protocol-error exit 2."""
    # openclaw/tool-calls does not declare list-trajectories.
    completed = _invoke(_request("openclaw/tool-calls", "list-trajectories"))
    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["status"] == "protocol-error"


def test_claim_writer_list_explicit_root() -> None:
    """Claim-writer: list-explicit-root present after filtered list verify green.

    Additive only — other PY-10b-* claim-writers may expand sources/outputs.
    """
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    assert "pi" in data["sources"]
    assert "list-explicit-root" in data["capabilities"]
    # Schema→op honesty still requires base normalize outputs for coverage caps.
    assert "letta-trajectory-v1" in data["outputs"]
    assert "letta-canonical-v1" in data["outputs"]
    # Packaged interior must match authoritative path.
    packaged = (
        ROOT / "python" / "src" / "hypabolic_trajectory" / "runtime-capabilities.json"
    )
    assert json.loads(packaged.read_text(encoding="utf-8")) == data


def test_filtered_verify_pi_list_and_normalize_green() -> None:
    """Integration: list-trajectories + baseline normalize ops are verify-green."""
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--source",
        "pi",
        "--operation",
        "normalize-letta",
        "--operation",
        "normalize-canonical",
        "--operation",
        "list-trajectories",
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
    assert summary["operations"] >= 1
