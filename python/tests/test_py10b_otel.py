"""PY-10b-otel: project-otel runner op + otel-genai-spans-v1 claim-writer.

Acceptance (spec §9):
  - Runner implements project-otel via free-function project_otel_genai
  - Claim otel-genai-spans-v1 only when filtered verify is green
  - Schema→op map: otel-genai-spans-v1 → project-otel
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


def test_public_project_otel_genai_import() -> None:
    """Free-function project_otel_genai remains importable from package root."""
    from hypabolic_trajectory import (  # noqa: PLC0415
        project_otel_genai,
        serialize_projection,
    )

    assert callable(project_otel_genai)
    assert callable(serialize_projection)


def test_success_project_otel_unicode_boundaries() -> None:
    """Runner project-otel matches unicode-boundaries golden (byte-exact product)."""
    completed = _invoke(_request("pi/unicode-boundaries", "project-otel"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["case"] == "pi/unicode-boundaries"
    assert response["operation"] == "project-otel"
    assert response["status"] == "success"
    assert response["fatal_error"] is None
    assert isinstance(response["diagnostics"], list)
    expected = (
        ROOT / "conformance/cases/pi/unicode-boundaries/expected.otel.json"
    ).read_text(encoding="utf-8")
    # Golden is single-line compact JSON; product serializer matches byte-exact.
    assert response["output_text"] == expected.rstrip("\n")


def test_claim_writer_includes_otel_genai_spans_v1() -> None:
    """Claim-writer: otel-genai-spans-v1 present after filtered verify green."""
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    assert "pi" in data["sources"]
    assert "otel-genai-spans-v1" in data["outputs"]
    # Tip-order: otel is the last tip output schema.
    tip_order = [
        "letta-trajectory-v1",
        "letta-canonical-v1",
        "openai-chat-messages",
        "jsonl-minimal",
        "otel-genai-spans-v1",
    ]
    claimed = data["outputs"]
    claimed_tip = [name for name in claimed if name in tip_order]
    assert claimed_tip == sorted(
        claimed_tip, key=lambda n: tip_order.index(n)
    )
    # Coverage capabilities remain (from PY-10a); listing is a separate issue.
    for cap in (
        "normalize",
        "normalize-partial",
        "typed-diagnostics",
        "typed-fatal-errors",
        "deterministic-rerun",
    ):
        assert cap in data["capabilities"]


def test_filtered_verify_project_otel_green() -> None:
    """Schema→op honesty gate: --operation project-otel filtered verify green."""
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--source",
        "pi",
        "--operation",
        "project-otel",
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
    assert summary["operations"] == 1
    assert summary["cases"] == 1


def test_filtered_verify_claimed_normalize_plus_otel_green() -> None:
    """Progressive claimed surface: normalize-letta/canonical + project-otel."""
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
        "project-otel",
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
    assert summary["operations"] >= 11  # 10 normalize + 1 otel (pi)
