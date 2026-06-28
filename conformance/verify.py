#!/usr/bin/env python3
"""Run declared conformance operations without modifying checked-in fixtures."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("runner", nargs=argparse.REMAINDER)
    result = parser.parse_args()
    if result.runner[:1] == ["--"]:
        result.runner = result.runner[1:]
    if not result.runner:
        parser.error("runner command is required after --")
    return result


def invoke(
    runner: list[str], repository_root: Path, case_id: str, operation: str
) -> dict:
    request = {
        "protocol_version": "1",
        "case": case_id,
        "operation": operation,
        "repository_root": str(repository_root),
    }
    completed = subprocess.run(
        runner,
        input=json.dumps(request, separators=(",", ":")),
        text=True,
        stdout=subprocess.PIPE,
        stderr=None,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{case_id}/{operation}: runner exited {completed.returncode}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"{case_id}/{operation}: stdout was not one JSON response"
        ) from error


def compare_output(
    repository_root: Path,
    case_directory: Path,
    operation: dict,
    response: dict,
    label: str,
) -> bool:
    expected_path = case_directory / operation["expected"]
    actual = response.get("output_text")
    if actual is None:
        raise AssertionError(f"{label}: expected output, got none")
    if not expected_path.exists():
        candidate = (
            repository_root
            / "artifacts"
            / "conformance-candidates"
            / expected_path.relative_to(repository_root / "conformance" / "cases")
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(actual, encoding="utf-8", newline="")
        print(
            f"CANDIDATE {label}: wrote unaccepted output {candidate}",
            file=sys.stderr,
        )
        return False
    expected = expected_path.read_text(encoding="utf-8")

    mode = operation.get("comparison", "json-exact")
    if mode == "json-exact":
        if json.loads(actual) != json.loads(expected):
            raise AssertionError(f"{label}: JSON differs from {expected_path}")
    elif mode in {"byte-exact", "jsonl-exact"}:
        if actual != expected:
            raise AssertionError(f"{label}: bytes differ from {expected_path}")
    else:
        raise AssertionError(f"{label}: unknown comparison mode {mode}")
    return True


def main() -> int:
    args = parse_args()
    repository_root = args.repository_root.resolve()
    cases_root = repository_root / "conformance" / "cases"
    manifests = sorted(cases_root.glob("**/case.json"))
    checked = 0
    candidates = 0

    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        case_id = manifest["id"]
        expected_result = manifest["expected"]["result"]
        expected_codes = manifest["expected"].get("diagnostic_codes", [])
        for operation_name, operation in manifest["operation"].items():
            label = f"{case_id}/{operation_name}"
            first = invoke(args.runner, repository_root, case_id, operation_name)
            second = invoke(args.runner, repository_root, case_id, operation_name)
            if first != second:
                raise AssertionError(f"{label}: repeated runner responses differ")

            expected_status = (
                "fatal-error" if expected_result == "fatal-error" else "success"
            )
            if first.get("status") != expected_status:
                raise AssertionError(
                    f"{label}: expected status {expected_status}, "
                    f"got {first.get('status')}"
                )

            actual_codes = [item["code"] for item in first.get("diagnostics", [])]
            if actual_codes != expected_codes:
                raise AssertionError(
                    f"{label}: expected diagnostics {expected_codes}, "
                    f"got {actual_codes}"
                )

            if expected_status == "success":
                accepted = compare_output(
                    repository_root,
                    manifest_path.parent,
                    operation,
                    first,
                    label,
                )
                if not accepted:
                    candidates += 1
            else:
                expected_error = json.loads(
                    (manifest_path.parent / operation["expected"]).read_text(
                        encoding="utf-8"
                    )
                )
                if first.get("fatal_error") != expected_error:
                    raise AssertionError(f"{label}: fatal error differs")

            checked += 1
            print(f"PASS {label}", file=sys.stderr)

    if candidates:
        raise AssertionError(
            f"{candidates} candidate outputs require review and check-in"
        )

    print(
        json.dumps(
            {
                "protocol_version": "1",
                "status": "success",
                "cases": len(manifests),
                "operations": checked,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, OSError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
