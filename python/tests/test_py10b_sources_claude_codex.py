"""PY-10b-sources-claude-codex: claim-writer expands sources when verify-green.

Authority:
  - docs/python-implementation-spec.md §5 claim-writer rule + §9 issue table
  - Claimed ⊆ verified under schema→op map for letta/canonical normalize ops

This issue owns the claude-code + codex source claims. Other PY-10b claim-writers
may expand sources/outputs further; tests assert membership for this issue's
sources rather than exclusive list equality.
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

# Sources owned by this claim-writer issue (must be present after green).
OWNED_SOURCES = ("claude-code", "codex")
# Minimum progressive set used for filtered verify (always include pi).
VERIFY_SOURCES = ("pi", "claude-code", "codex")
CLAIMED_OPS = ("normalize-letta", "normalize-canonical")
TIP_SOURCE_ORDER = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
    "grok-build",
    "cursor",
)


def test_claim_writer_sources_claude_code_and_codex() -> None:
    """Authoritative capabilities claim claude-code + codex after filtered green."""
    caps_path = ROOT / "python" / "runtime-capabilities.json"
    data = json.loads(caps_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "python"
    assert data["normalizer_contract_version"] == "0.2.0"
    sources = data["sources"]
    assert "pi" in sources
    for name in OWNED_SOURCES:
        assert name in sources, f"missing claimed source: {name}"
    # Tip order for claimed sources only (subset may include later PY-10b sources).
    claimed_tip = [s for s in TIP_SOURCE_ORDER if s in sources]
    assert sources == claimed_tip
    # letta/canonical remain claimed (schema→op coverage for normalize ops).
    outputs = data["outputs"]
    assert "letta-trajectory-v1" in outputs
    assert "letta-canonical-v1" in outputs
    # Listing capability may be claimed by PY-10b-list in parallel; not asserted here.
    # Packaged interior is a staged prepare-copy (gitignored). When present it
    # should include this issue's sources; full equality is prepare's job and may
    # lag concurrent claim-writers mid-wave.
    interior = (
        ROOT / "python" / "src" / "hypabolic_trajectory" / "runtime-capabilities.json"
    )
    if interior.is_file():
        interior_data = json.loads(interior.read_text(encoding="utf-8"))
        for name in OWNED_SOURCES:
            assert name in interior_data.get("sources", []), name


def test_filtered_verify_pi_claude_codex_normalize_green() -> None:
    """Integration: owned sources ⊆ verified under normalize-letta/canonical.

    Filters must match claimed ops. Bare ``--source`` without ``--operation`` is
    forbidden for progressive honesty (would pull unclaimed listing/etc.).
    """
    cmd = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
    ]
    for source in VERIFY_SOURCES:
        cmd.extend(["--source", source])
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
    # pi (10 ops) + claude-code (4) + codex (7) = 21 normalize ops under filter.
    assert summary["operations"] >= 21
    assert summary["cases"] >= 14


def test_runner_claude_code_tool_call_letta() -> None:
    """Smoke: protocol runner succeeds for a claude-code normalize case."""
    request = {
        "protocol_version": "1",
        "case": "claude-code/tool-call",
        "operation": "normalize-letta",
        "repository_root": str(ROOT),
    }
    completed = subprocess.run(
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
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["status"] == "success"
    expected = json.loads(
        (ROOT / "conformance/cases/claude-code/tool-call/expected.letta.json").read_text(
            encoding="utf-8"
        )
    )
    assert json.loads(response["output_text"]) == expected


def test_runner_codex_missing_group_canonical_fatal() -> None:
    """Domain fatal for codex missing-group (canonical) stays exit 0."""
    request = {
        "protocol_version": "1",
        "case": "codex/missing-group",
        "operation": "normalize-canonical",
        "repository_root": str(ROOT),
    }
    completed = subprocess.run(
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
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["status"] == "fatal-error"
    expected = json.loads(
        (ROOT / "conformance/cases/codex/missing-group/expected.error.json").read_text(
            encoding="utf-8"
        )
    )
    assert response["fatal_error"] == expected
