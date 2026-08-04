"""PY-10b-hypabolic: normalize-hypabolic op + hypabolic-trajectory-v1 claim."""

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


def _request(case: str, operation: str) -> dict[str, object]:
    return {
        "protocol_version": "1",
        "case": case,
        "operation": operation,
        "repository_root": str(ROOT),
    }


def test_public_project_hypabolic_import() -> None:
    from hypabolic_trajectory import (  # noqa: PLC0415
        normalize_to_hypabolic,
        project_hypabolic,
        serialize_projection,
    )

    assert callable(project_hypabolic)
    assert callable(normalize_to_hypabolic)
    assert callable(serialize_projection)


def test_success_normalize_hypabolic_unicode_boundaries() -> None:
    completed = _invoke(_request("pi/unicode-boundaries", "normalize-hypabolic"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["case"] == "pi/unicode-boundaries"
    assert response["operation"] == "normalize-hypabolic"
    assert response["status"] == "success"
    assert response["fatal_error"] is None
    expected = json.loads(
        (
            ROOT / "conformance/cases/pi/unicode-boundaries/expected.hypabolic.json"
        ).read_text(encoding="utf-8")
    )
    assert json.loads(response["output_text"]) == expected


def test_success_normalize_hypabolic_partial_chunk() -> None:
    completed = _invoke(_request("pi/partial-chunk", "normalize-hypabolic"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["status"] == "success"
    expected = json.loads(
        (
            ROOT / "conformance/cases/pi/partial-chunk/expected.hypabolic.json"
        ).read_text(encoding="utf-8")
    )
    assert json.loads(response["output_text"]) == expected


def test_claim_writer_includes_hypabolic_trajectory_v1() -> None:
    """Claim-writer: hypabolic-trajectory-v1 present after filtered green."""
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    assert "pi" in data["sources"]
    assert "hypabolic-trajectory-v1" in data["outputs"]
    # Schema→op honesty requires letta/canonical still claimed for coverage caps.
    assert "letta-trajectory-v1" in data["outputs"]
    assert "letta-canonical-v1" in data["outputs"]
    # Tip order: hypabolic after canonical, before later outputs if present.
    outputs = data["outputs"]
    assert outputs.index("letta-canonical-v1") < outputs.index("hypabolic-trajectory-v1")
    # otel may be claimed by PY-10b-otel (membership, not exclusive outputs list).
    # Packaged interior must match authoritative path.
    packaged = (
        ROOT / "python" / "src" / "hypabolic_trajectory" / "runtime-capabilities.json"
    )
    assert json.loads(packaged.read_text(encoding="utf-8")) == data


def test_filtered_verify_normalize_hypabolic_green() -> None:
    """Claim gate: filtered hypabolic ops green under shared verify.py."""
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--source",
        "pi",
        "--operation",
        "normalize-hypabolic",
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
    assert summary["operations"] == 2
    assert summary["cases"] == 2


def test_filtered_verify_claimed_normalize_ops_green() -> None:
    """Progressive honesty: claimed normalize ops for pi stay green together."""
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
        "normalize-hypabolic",
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
    assert summary["operations"] >= 12
