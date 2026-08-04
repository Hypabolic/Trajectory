"""PY-10a: early protocol-v1 conformance runner (pi normalize ops)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def _invoke(
    request: dict[str, object],
    *,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        RUNNER_CMD + (extra_args or []),
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
    protocol_version: str = "1",
) -> dict[str, object]:
    return {
        "protocol_version": protocol_version,
        "case": case,
        "operation": operation,
        "repository_root": repository_root if repository_root is not None else str(ROOT),
    }


def test_public_free_function_imports() -> None:
    """PY-10a acceptance: free functions import from package root."""
    from hypabolic_trajectory import (  # noqa: PLC0415
        normalize_to_ir,
        project_canonical,
        project_letta,
        serialize_projection,
    )

    assert callable(normalize_to_ir)
    assert callable(project_letta)
    assert callable(project_canonical)
    assert callable(serialize_projection)


def test_success_normalize_letta_tool_calls() -> None:
    completed = _invoke(_request("pi/tool-calls", "normalize-letta"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["case"] == "pi/tool-calls"
    assert response["operation"] == "normalize-letta"
    assert response["status"] == "success"
    assert response["fatal_error"] is None
    assert isinstance(response["diagnostics"], list)
    expected = json.loads(
        (ROOT / "conformance/cases/pi/tool-calls/expected.letta.json").read_text(
            encoding="utf-8"
        )
    )
    assert json.loads(response["output_text"]) == expected


def test_success_normalize_canonical_tool_calls() -> None:
    completed = _invoke(_request("pi/tool-calls", "normalize-canonical"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["status"] == "success"
    expected = json.loads(
        (
            ROOT / "conformance/cases/pi/tool-calls/expected.canonical.json"
        ).read_text(encoding="utf-8")
    )
    assert json.loads(response["output_text"]) == expected


def test_domain_fatal_exit_zero_missing_assistant() -> None:
    """Domain TrajectoryError → fatal-error template, exit 0 (not 2)."""
    completed = _invoke(_request("pi/missing-assistant", "normalize-canonical"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["status"] == "fatal-error"
    assert response["output_text"] is None
    assert response["diagnostics"] == []
    expected = json.loads(
        (ROOT / "conformance/cases/pi/missing-assistant/expected.error.json").read_text(
            encoding="utf-8"
        )
    )
    assert response["fatal_error"] == expected


def test_protocol_error_exit_two_bad_json() -> None:
    completed = subprocess.run(
        RUNNER_CMD,
        input="not-json",
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=RUNNER_ENV,
        check=False,
    )
    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["status"] == "protocol-error"
    assert response["case"] == ""
    assert response["operation"] == ""
    assert response["output_text"] is None
    assert response["diagnostics"] == []
    assert response["fatal_error"]["code"] == "invalid_request"
    assert isinstance(response["fatal_error"]["message"], str)
    assert response["fatal_error"]["message"]


def test_protocol_error_exit_two_wrong_version() -> None:
    completed = _invoke(
        _request("pi/tool-calls", "normalize-letta", protocol_version="99")
    )
    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["status"] == "protocol-error"
    assert response["fatal_error"]["code"] == "invalid_request"


def test_protocol_error_exit_two_undeclared_operation() -> None:
    # project-openai is not declared on pi/missing-assistant
    completed = _invoke(_request("pi/missing-assistant", "project-openai"))
    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["status"] == "protocol-error"
    assert response["case"] == "pi/missing-assistant"
    assert response["operation"] == "project-openai"


def test_protocol_error_path_escape() -> None:
    completed = _invoke(_request("../secret", "normalize-letta"))
    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["status"] == "protocol-error"


def test_diagnostics_wire_casing_camel_case() -> None:
    """Protocol diagnostics use inputLine/recordIndex (not snake_case)."""
    completed = _invoke(_request("pi/tool-linking", "normalize-canonical"))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["status"] == "success"
    codes = [d["code"] for d in response["diagnostics"]]
    assert codes == [
        "duplicate_tool_call_id",
        "tool_call_id_synthesized",
        "tool_arguments_reshaped",
        "duplicate_tool_result",
        "orphan_tool_result",
    ]
    for item in response["diagnostics"]:
        assert "code" in item and "message" in item
        # Optional location keys must be camelCase when present.
        assert "input_line" not in item
        assert "record_index" not in item
        if "inputLine" in item:
            assert type(item["inputLine"]) is int
        if "recordIndex" in item:
            assert type(item["recordIndex"]) is int


def test_claim_writer_progressive_pi_normalize() -> None:
    """Claim-writer surface: pi + progressive outputs (grows via PY-10b-*).

    PY-10a claimed letta/canonical; PY-10b-openai-jsonl adds openai + jsonl.
    """
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    assert data["sources"] == ["pi"]
    # Baseline + PY-10b-openai-jsonl claim expansion.
    assert data["outputs"] == [
        "letta-trajectory-v1",
        "letta-canonical-v1",
        "openai-chat-messages",
        "jsonl-minimal",
    ]
    # Progressive capabilities covered by filtered pi normalize-letta/canonical.
    assert "normalize" in data["capabilities"]
    assert "normalize-partial" in data["capabilities"]
    assert "typed-diagnostics" in data["capabilities"]
    assert "typed-fatal-errors" in data["capabilities"]
    assert "deterministic-rerun" in data["capabilities"]
    # Listing is PY-10b-list — must not claim early.
    assert "list-explicit-root" not in data["capabilities"]
    # Hypabolic / otel remain later claim-writer issues.
    for forbidden in (
        "hypabolic-trajectory-v1",
        "otel-genai-spans-v1",
    ):
        assert forbidden not in data["outputs"]


def test_filtered_verify_pi_normalize_green() -> None:
    """Integration: shared verify.py with explicit --source and --operation filters.

    Bare ``--source pi`` is forbidden for progressive honesty (would pull
    unclaimed listing/hypabolic/otel ops). Filters match claims.
    """
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
    assert summary["operations"] >= 1
