"""PY-10b-openai-jsonl: runner ops + claim-writer for openai/jsonl-minimal.

Authority:
  - docs/python-implementation-spec.md §5 claim-writer rule + §9 PY-10b-openai-jsonl
  - schema→op map: openai-chat-messages → project-openai;
    jsonl-minimal → project-minimal-jsonl
  - Claim only when filtered verify is green
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

CLAIMED_OPENAI_JSONL_OPS = ("project-openai", "project-minimal-jsonl")


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


def test_public_free_function_imports_openai_jsonl() -> None:
    """PY-10b acceptance: project_openai / project_minimal_jsonl import from root."""
    from hypabolic_trajectory import (  # noqa: PLC0415
        project_minimal_jsonl,
        project_openai,
        serialize_projection,
    )

    assert callable(project_openai)
    assert callable(project_minimal_jsonl)
    assert callable(serialize_projection)


def test_success_project_openai_unicode_boundaries() -> None:
    completed = _invoke(_request("pi/unicode-boundaries", "project-openai"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["case"] == "pi/unicode-boundaries"
    assert response["operation"] == "project-openai"
    assert response["status"] == "success"
    assert response["fatal_error"] is None
    assert isinstance(response["diagnostics"], list)
    expected = json.loads(
        (
            ROOT / "conformance/cases/pi/unicode-boundaries/expected.openai.json"
        ).read_text(encoding="utf-8")
    )
    assert json.loads(response["output_text"]) == expected


def test_success_project_minimal_jsonl_unicode_boundaries() -> None:
    """jsonl-exact: output_text must match expected.minimal.jsonl byte-for-byte."""
    completed = _invoke(_request("pi/unicode-boundaries", "project-minimal-jsonl"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["case"] == "pi/unicode-boundaries"
    assert response["operation"] == "project-minimal-jsonl"
    assert response["status"] == "success"
    assert response["fatal_error"] is None
    expected = (
        ROOT / "conformance/cases/pi/unicode-boundaries/expected.minimal.jsonl"
    ).read_text(encoding="utf-8")
    assert response["output_text"] == expected


def test_claim_writer_openai_jsonl() -> None:
    """Claim-writer: openai-chat-messages + jsonl-minimal after filtered green."""
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    assert "pi" in data["sources"]
    outputs = data["outputs"]
    assert "openai-chat-messages" in outputs
    assert "jsonl-minimal" in outputs
    # Baseline normalize outputs remain claimed.
    assert "letta-trajectory-v1" in outputs
    assert "letta-canonical-v1" in outputs
    # otel-genai-spans-v1 is owned by PY-10b-otel (may be co-claimed).


def test_filtered_verify_pi_openai_jsonl_green() -> None:
    """Filtered verify for schema→op map: project-openai + project-minimal-jsonl."""
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--source",
        "pi",
    ]
    for operation in CLAIMED_OPENAI_JSONL_OPS:
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
    assert summary["operations"] == 2
    assert summary["cases"] == 1


def test_filtered_verify_full_claimed_surface_green() -> None:
    """Full progressive claim surface for this issue: normalize + openai + jsonl."""
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
        "project-openai",
        "--operation",
        "project-minimal-jsonl",
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
    assert summary["operations"] == 12
    assert summary["cases"] == 6
