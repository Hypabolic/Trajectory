"""PY-11: full shared conformance green — tip matrix, identity baseline, tip equality.

Acceptance (docs/python-implementation-spec.md §9 PY-11):
  Full verify green; tip capabilities equality.

Formalizes what progressive claim-writers (PY-10a / PY-10b-*) already claimed
at tip surface:
  - Unfiltered ``conformance/verify.py`` (defaults to compatibility tip sources)
    is green for the Python runner (double-run, all declared ops).
  - ``python/runtime-capabilities.json`` equals the ML13 tip matrix (sources,
    outputs, required capabilities, slice) and matches peer TS/Rust manifests
    on those equality peers.
  - ``conformance/identity-baseline.sha256`` still matches checked-in identity
    goldens (normalizer 0.2.0).

CI tip wiring is PY-15b (``test_py15b_tip_gate.py``). OIDC/docs are PY-14b / PY-16.
"""

from __future__ import annotations

import hashlib
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

CAPS_PATH = ROOT / "python" / "runtime-capabilities.json"
INTERIOR_CAPS = (
    ROOT / "python" / "src" / "hypabolic_trajectory" / "runtime-capabilities.json"
)
COMPAT_PATH = ROOT / "contracts" / "compatibility.json"
TS_CAPS = (
    ROOT / "typescript" / "packages" / "trajectory" / "runtime-capabilities.json"
)
RUST_CAPS = (
    ROOT
    / "rust"
    / "crates"
    / "hypabolic-trajectory"
    / "runtime-capabilities.json"
)
IDENTITY_BASELINE = ROOT / "conformance" / "identity-baseline.sha256"

# Tip ML13 matrix (contracts/compatibility.json + peer runtime-capabilities).
TIP_SOURCES: list[str] = [
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
    "grok-build",
]
TIP_OUTPUTS: list[str] = [
    "letta-trajectory-v1",
    "letta-canonical-v1",
    "hypabolic-trajectory-v1",
    "openai-chat-messages",
    "jsonl-minimal",
    "otel-genai-spans-v1",
]
TIP_CAPABILITIES: list[str] = [
    "normalize",
    "normalize-partial",
    "list-explicit-root",
    "typed-diagnostics",
    "typed-fatal-errors",
    "deterministic-rerun",
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
]
TIP_SLICE = "ML13"
NORMALIZER_CONTRACT_VERSION = "0.2.0"

# Current tip case inventory under unfiltered verify (all declared pairs).
# 39 batch + 38 stream cases (LS-08 matrix + H1/H3 cases; LS-12 advertises core stream-* caps).
EXPECTED_TIP_CASES = 77
EXPECTED_TIP_OPERATIONS = 104


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_tip_capabilities_equality_claim() -> None:
    """Claim ceremony: Python runtime-capabilities equals tip ML13 matrix.

    Order-sensitive equality matches peer TS/Rust manifests and
    contracts/compatibility.json implemented sources/outputs + required caps.
    """
    caps = _load_json(CAPS_PATH)
    compat = _load_json(COMPAT_PATH)

    assert caps["runtime"] == "python"
    assert caps["slice"] == TIP_SLICE
    assert caps["normalizer_contract_version"] == NORMALIZER_CONTRACT_VERSION
    assert caps["sources"] == TIP_SOURCES
    assert caps["outputs"] == TIP_OUTPUTS
    assert caps["capabilities"] == TIP_CAPABILITIES

    # Compatibility tip peers.
    assert caps["sources"] == compat["implemented"]["sources"]
    assert caps["outputs"] == compat["implemented"]["outputs"]
    assert caps["capabilities"] == compat["capabilities"]["required"]
    assert compat["contracts"]["normalizer"] == NORMALIZER_CONTRACT_VERSION

    # Peer runtimes already enforce the same equality (CI jq gates).
    for peer_path in (TS_CAPS, RUST_CAPS):
        peer = _load_json(peer_path)
        assert peer["slice"] == TIP_SLICE
        assert peer["normalizer_contract_version"] == NORMALIZER_CONTRACT_VERSION
        assert peer["sources"] == caps["sources"]
        assert peer["outputs"] == caps["outputs"]
        assert peer["capabilities"] == caps["capabilities"]

    # Packaged interior copy must match authoritative path (prepare stamp).
    interior = _load_json(INTERIOR_CAPS)
    assert interior == caps


@pytest.mark.heavy
def test_unfiltered_verify_full_tip_matrix_green() -> None:
    """Bare unfiltered verify.py is green for the full tip surface including ahp.

    No --source/--operation filters: verify defaults to compatibility
    implemented.sources (tip matrix). Double-run equality is enforced inside
    verify.py.

    Marked heavy: CI python-unit matrix skips this; python-conformance runs the
    same unfiltered tip verify directly.
    """
    cmd: list[str] = [
        PYTHON,
        str(ROOT / "conformance" / "verify.py"),
        "--repository-root",
        str(ROOT),
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
        f"unfiltered verify failed:\nstdout={completed.stdout}\n"
        f"stderr={completed.stderr}"
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "success"
    assert summary["cases"] == EXPECTED_TIP_CASES
    assert summary["operations"] == EXPECTED_TIP_OPERATIONS


def test_identity_baseline_goldens_unchanged() -> None:
    """Checked-in identity-bearing goldens match conformance/identity-baseline.sha256.

    Same gate peer CI jobs run via ``sha256sum --check``; implemented with
    hashlib so the test is portable (macOS has shasum, Linux has sha256sum).
    """
    assert IDENTITY_BASELINE.is_file(), f"missing {IDENTITY_BASELINE}"
    lines = IDENTITY_BASELINE.read_text(encoding="utf-8").splitlines()
    assert lines, "identity-baseline.sha256 is empty"

    failures: list[str] = []
    checked = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Format: "<hex>  <path>" (two spaces, GNU sha256sum style).
        if "  " not in line:
            failures.append(f"malformed baseline line: {line!r}")
            continue
        expected_hex, rel_path = line.split("  ", 1)
        target = ROOT / rel_path
        if not target.is_file():
            failures.append(f"missing golden: {rel_path}")
            continue
        actual_hex = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_hex != expected_hex:
            failures.append(
                f"{rel_path}: expected {expected_hex}, got {actual_hex}"
            )
            continue
        checked += 1

    assert not failures, "identity baseline mismatches:\n" + "\n".join(failures)
    assert checked >= 1, "identity baseline checked zero files"
    # Tip identity set currently pins 37 identity-bearing goldens.
    assert checked == 37, f"expected 37 identity goldens, checked {checked}"
