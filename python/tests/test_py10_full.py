"""PY-10-full: protocol v1 runner complete — all ops wired.

Acceptance (spec §9): All protocol v1 operations implemented.

Protocol v1 batch ops (conformance/protocol/request-v1.schema.json):
  normalize-letta | normalize-canonical | normalize-hypabolic |
  project-openai | project-minimal-jsonl | project-otel |
  list-trajectories

LS-02 stream ops are also in the request enum; they return status=unsupported
until stream engines land (LS-04+).

This join issue does not expand claims (owned by PY-10a / PY-10b-*). It pins
that the runner accepts and executes the full operation enum, and that the
filtered tip-matrix verify over claimed sources×ops is green.
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

# Batch normalize/list ops (tip-matrix honesty gate).
PROTOCOL_BATCH_OPS: frozenset[str] = frozenset(
    {
        "normalize-letta",
        "normalize-canonical",
        "normalize-hypabolic",
        "project-openai",
        "project-minimal-jsonl",
        "project-otel",
        "list-trajectories",
    }
)

# LS-02 stream protocol ops (unsupported until engines land).
PROTOCOL_STREAM_OPS: frozenset[str] = frozenset(
    {
        "stream-sequence",
        "stream-replay",
        "stream-apply-append",
        "stream-apply-snapshot",
        "stream-apply-ahp-actions",
        "stream-apply-ahp-snapshot",
        "stream-finish",
        "stream-reset",
    }
)

# Full normative protocol-v1 enum (request-v1.schema.json).
PROTOCOL_V1_OPS: frozenset[str] = PROTOCOL_BATCH_OPS | PROTOCOL_STREAM_OPS

# LS-12 tip core stream capabilities (runtime-capabilities / compatibility required).
# Matches tools/validate_release_metadata.py TIP_CAPABILITIES stream-* entries.
_CORE_STREAM_CAPABILITIES: frozenset[str] = frozenset(
    {
        "stream-core",
        "stream-cursor-v1",
        "stream-jsonl-framing",
        "stream-apply-snapshot",
        "stream-apply-append",
        "stream-full-snapshot",
        "stream-record-delta",
        "stream-reset",
        "stream-provisional-records",
        "stream-deterministic-replay",
        "stream-file-jsonl",
        "stream-ahp-snapshot",
        "stream-ahp-action-log",
    }
)

# One representative case per batch op (declared on that case's case.json).
_OP_SMOKE_CASES: dict[str, str] = {
    "normalize-letta": "pi/tool-calls",
    "normalize-canonical": "pi/tool-calls",
    "normalize-hypabolic": "pi/unicode-boundaries",
    "project-openai": "pi/unicode-boundaries",
    "project-minimal-jsonl": "pi/unicode-boundaries",
    "project-otel": "pi/unicode-boundaries",
    "list-trajectories": "pi/listing",
}

# Tip sources from contracts/compatibility.json implemented.sources.
_TIP_SOURCES: tuple[str, ...] = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
    "grok-build",
)


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


def test_runner_exports_full_protocol_v1_operation_set() -> None:
    """Runner PROTOCOL_V1_OPERATIONS equals the request schema enum."""
    # Import via tools path (same as -m trajectory_conformance).
    sys.path.insert(0, str(TOOLS))
    try:
        from trajectory_conformance.runner import (  # noqa: PLC0415
            PROTOCOL_BATCH_OPERATIONS,
            PROTOCOL_STREAM_OPERATIONS,
            PROTOCOL_V1_OPERATIONS,
            _KNOWN_OPS,
            _NORMALIZE_OPS,
        )
    finally:
        if str(TOOLS) in sys.path:
            sys.path.remove(str(TOOLS))

    assert PROTOCOL_V1_OPERATIONS == PROTOCOL_V1_OPS
    assert PROTOCOL_BATCH_OPERATIONS == PROTOCOL_BATCH_OPS
    assert PROTOCOL_STREAM_OPERATIONS == PROTOCOL_STREAM_OPS
    assert _KNOWN_OPS == PROTOCOL_V1_OPS
    assert _NORMALIZE_OPS == PROTOCOL_BATCH_OPS - {"list-trajectories"}
    assert "list-trajectories" in PROTOCOL_V1_OPERATIONS
    assert "stream-sequence" in PROTOCOL_V1_OPERATIONS


def test_request_schema_enum_matches_protocol_v1_ops() -> None:
    """Pin request-v1.schema.json operation enum to batch + stream ops."""
    schema = json.loads(
        (ROOT / "conformance/protocol/request-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    enum = set(schema["properties"]["operation"]["enum"])
    assert enum == PROTOCOL_V1_OPS


def test_each_protocol_v1_batch_op_executes_successfully() -> None:
    """Smoke: every batch protocol-v1 op runs to success on a declared case."""
    assert set(_OP_SMOKE_CASES) == PROTOCOL_BATCH_OPS
    for operation, case_id in _OP_SMOKE_CASES.items():
        completed = _invoke(_request(case_id, operation))
        assert completed.returncode == 0, (
            f"{operation} on {case_id} failed:\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
        response = json.loads(completed.stdout)
        assert response["protocol_version"] == "1"
        assert response["case"] == case_id
        assert response["operation"] == operation
        assert response["status"] == "success", (
            f"{operation}: expected success, got {response!r}"
        )
        assert response["fatal_error"] is None
        assert isinstance(response["output_text"], str)
        assert response["output_text"] != ""


def test_stream_sequence_runs_engine_for_jsonl_cases() -> None:
    """LS-05: stream-sequence runs core apply and returns success for JSONL cases."""
    completed = _invoke(_request("streaming/empty-prefix", "stream-sequence"))
    assert completed.returncode == 0, (
        f"stream-sequence failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    response = json.loads(completed.stdout)
    assert response["protocol_version"] == "1"
    assert response["case"] == "streaming/empty-prefix"
    assert response["operation"] == "stream-sequence"
    assert response["status"] == "success", response
    assert response["fatal_error"] is None
    payload = json.loads(response["output_text"])
    assert isinstance(payload.get("steps"), list)
    assert len(payload["steps"]) == 1
    assert payload["steps"][0]["update"]["kind"] == "updated"


def test_unknown_operation_is_protocol_error() -> None:
    """Ops outside the v1 enum are rejected with protocol-error exit 2."""
    completed = _invoke(_request("pi/tool-calls", "normalize-unknown"))
    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["status"] == "protocol-error"
    assert response["fatal_error"] is not None
    assert "normalize-unknown" in response["fatal_error"]["message"]


def test_filtered_verify_full_protocol_v1_tip_surface_green() -> None:
    """Honesty gate: filtered tip sources × batch protocol-v1 ops is verify-green.

    This is the PY-10-full join check. Tip *capabilities equality claim* and
    identity-baseline are formalized in test_py11_full_conformance.py (PY-11).
    Stream cases are excluded by the batch-op filter (they use stream-sequence).
    """
    cmd: list[str] = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
    ]
    for source in _TIP_SOURCES:
        cmd.extend(["--source", source])
    for operation in sorted(PROTOCOL_BATCH_OPS):
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
    # Current tip case inventory: 39 batch cases / 66 operations (all declared pairs).
    assert summary["cases"] == 39
    assert summary["operations"] == 66


def test_stream_verify_matrix_green() -> None:
    """LS-08: full stream corpus green (engines landed; no capability claims yet)."""
    cmd: list[str] = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
        "--operation",
        "stream-sequence",
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
        f"stream verify failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    # 34 streaming cases × stream-sequence; Hermes export path not in corpus yet.
    assert summary["cases"] == 34
    assert summary["operations"] == 34
    assert summary["stream_unsupported_skips"] == 0


def test_progressive_capabilities_cover_tip_ops_surface() -> None:
    """Claimed sources/outputs cover schema→op map for every batch protocol-v1 op.

    Claim membership is owned by PY-10a/PY-10b-*; this only asserts the join
    floor needed so all ops have a claim path (not tip-equality formalization).
    """
    caps = json.loads(
        (ROOT / "python" / "runtime-capabilities.json").read_text(encoding="utf-8")
    )
    # All tip sources claimed (ops run against every source with fixtures).
    for source in _TIP_SOURCES:
        assert source in caps["sources"], f"missing claimed source: {source}"

    # Schema→op map (spec §5): every normalize/project op has its output claimed.
    schema_to_op = {
        "letta-trajectory-v1": "normalize-letta",
        "letta-canonical-v1": "normalize-canonical",
        "hypabolic-trajectory-v1": "normalize-hypabolic",
        "openai-chat-messages": "project-openai",
        "jsonl-minimal": "project-minimal-jsonl",
        "otel-genai-spans-v1": "project-otel",
    }
    for schema_id, op in schema_to_op.items():
        assert schema_id in caps["outputs"], f"missing claimed output for {op}"
        assert op in PROTOCOL_BATCH_OPS

    # list-trajectories coverage capability.
    assert "list-explicit-root" in caps["capabilities"]
    assert "list-trajectories" in PROTOCOL_BATCH_OPS

    # LS-12: core stream-* set is required on tip (matches validate_release_metadata
    # / test_py15b_tip_gate TIP_CAPABILITIES). Optional/unimplemented names must not
    # appear on the core runtime-capabilities manifest.
    claimed = [str(c) for c in caps.get("capabilities", [])]
    for cap in _CORE_STREAM_CAPABILITIES:
        assert cap in claimed, f"missing required core stream capability: {cap}"
    forbidden = {
        "stream-file-io",
        "stream-ahp-client",
        "stream-hermes-provider",
        "stream-async-iterator",
        "stream-file-watch",
        "stream-ahp-list-sessions",
    }
    for cap in claimed:
        if cap.startswith("stream-"):
            assert cap in _CORE_STREAM_CAPABILITIES, (
                f"stream capability {cap!r} must not be claimed on core "
                f"(optional package or unimplemented)"
            )
            assert cap not in forbidden
