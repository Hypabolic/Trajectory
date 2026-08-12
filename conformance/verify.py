#!/usr/bin/env python3
"""Run declared conformance operations without modifying checked-in fixtures.

Supports:
- Batch normalize/list cases (conformance-case-v1)
- Multi-step stream cases (streaming-case-v1) via stream-sequence / stream-replay

Stream engines may return status=unsupported until LS-04+ lands; that is a
valid protocol outcome and does not fail the suite. When engines return
success, comparison modes from contracts/spec/streaming.md are applied.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


# Protocol operations that execute ordered multi-step stream cases.
STREAM_SEQUENCE_OPS: frozenset[str] = frozenset({"stream-sequence", "stream-replay"})

# All stream protocol ops (sequence + reserved per-step apply ops).
STREAM_OPS: frozenset[str] = frozenset(
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

# Comparison modes declared in streaming-case-v1 / streaming.md.
STREAM_COMPARISON_MODES: frozenset[str] = frozenset(
    {
        "stream-json-exact",
        "stream-cursor-exact",
        "stream-delta-apply",
        "stream-diagnostics-by-step",
        "stream-idempotence",
        "stream-oracle-parity",
    }
)

# Default privacy sentinels scanned even when case.privacy is omitted.
DEFAULT_PRIVACY_SENTINELS: tuple[str, ...] = (
    "SECRET_TOKEN_xyz",
    "/Users/real-user/",
    "sk-live-",
    "auth.json",
)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="only run cases for this source (repeatable)",
    )
    parser.add_argument(
        "--operation",
        action="append",
        dest="operations",
        help="only run declared operations with this name (repeatable)",
    )
    parser.add_argument("runner", nargs=argparse.REMAINDER)
    result = parser.parse_args()
    if result.runner[:1] == ["--"]:
        result.runner = result.runner[1:]
    if not result.runner:
        parser.error("runner command is required after --")
    return result


def invoke(
    runner: list[str], repository_root: Path, case_id: str, operation: str
) -> dict[str, Any]:
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
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=None,
        check=False,
    )
    if completed.returncode != 0:
        # Stream unsupported may still exit 0; non-zero is always a failure.
        # Protocol-error uses exit 2 with a JSON body — try to surface it.
        try:
            body = json.loads(completed.stdout) if completed.stdout else None
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict) and body.get("status") == "protocol-error":
            raise RuntimeError(
                f"{case_id}/{operation}: protocol-error: "
                f"{(body.get('fatal_error') or {}).get('message', completed.stdout)}"
            )
        raise RuntimeError(
            f"{case_id}/{operation}: runner exited {completed.returncode}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"{case_id}/{operation}: stdout was not one JSON response"
        ) from error


def is_stream_case(manifest: dict[str, Any]) -> bool:
    """Streaming cases declare ordered steps; batch cases declare operation map."""
    steps = manifest.get("steps")
    return isinstance(steps, list) and len(steps) >= 1


def compare_output(
    repository_root: Path,
    case_directory: Path,
    operation: dict[str, Any],
    response: dict[str, Any],
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
        candidate.write_text(actual, encoding="utf-8")
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


def implemented_sources(repository_root: Path) -> set[str]:
    """Sources advertised in contracts/compatibility.json implemented.sources."""
    manifest_path = repository_root / "contracts" / "compatibility.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return set(payload.get("implemented", {}).get("sources", []))


def privacy_sentinels(manifest: dict[str, Any]) -> list[str]:
    privacy = manifest.get("privacy") or {}
    custom = privacy.get("forbidden_substrings") or []
    if not isinstance(custom, list):
        custom = []
    merged: list[str] = []
    for item in list(DEFAULT_PRIVACY_SENTINELS) + list(custom):
        if isinstance(item, str) and item and item not in merged:
            merged.append(item)
    return merged


def scan_privacy(label: str, text: str | None, sentinels: list[str]) -> None:
    if not text:
        return
    for sentinel in sentinels:
        if sentinel in text:
            raise AssertionError(
                f"{label}: privacy violation — forbidden substring {sentinel!r} "
                "appears in runner output/diagnostics"
            )


def apply_delta_to_snapshot(
    prior_snapshot: dict[str, Any] | None, delta: dict[str, Any]
) -> dict[str, Any]:
    """Apply stream delta operations to a prior snapshot (delta-apply law).

    Implements the public ordered op set from streaming-delta-v1 sufficiently
    for conformance checks. Raises AssertionError on unknown ops or shape errors.
    """
    if prior_snapshot is None:
        records: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        base = {
            "schema_id": "trajectory-stream-v1",
            "records": records,
            "diagnostics": diagnostics,
        }
    else:
        base = json.loads(json.dumps(prior_snapshot))  # deep copy via JSON
        records = base.setdefault("records", [])
        diagnostics = base.setdefault("diagnostics", [])
        if not isinstance(records, list) or not isinstance(diagnostics, list):
            raise AssertionError("stream-delta-apply: snapshot records/diagnostics malformed")

    ops = delta.get("operations")
    if not isinstance(ops, list):
        raise AssertionError("stream-delta-apply: delta.operations must be an array")

    def record_id(entry: dict[str, Any]) -> str | None:
        rec = entry.get("record") if isinstance(entry.get("record"), dict) else entry
        if not isinstance(rec, dict):
            return None
        rid = rec.get("id")
        return rid if isinstance(rid, str) else None

    for op in ops:
        if not isinstance(op, dict) or "op" not in op:
            raise AssertionError("stream-delta-apply: each operation requires op")
        kind = op["op"]
        if kind == "upsert":
            entry = op.get("record")
            if not isinstance(entry, dict):
                raise AssertionError("stream-delta-apply: upsert requires record")
            rid = record_id(entry)
            if rid is None:
                raise AssertionError("stream-delta-apply: upsert record missing id")
            replaced = False
            for i, existing in enumerate(records):
                if record_id(existing) == rid:
                    records[i] = entry
                    replaced = True
                    break
            if not replaced:
                records.append(entry)
        elif kind == "remove":
            rid = op.get("record_id")
            if not isinstance(rid, str):
                raise AssertionError("stream-delta-apply: remove requires record_id")
            records[:] = [r for r in records if record_id(r) != rid]
        elif kind == "finalize":
            rid = op.get("record_id")
            if not isinstance(rid, str):
                raise AssertionError("stream-delta-apply: finalize requires record_id")
            found = False
            for i, existing in enumerate(records):
                if record_id(existing) == rid:
                    updated = json.loads(json.dumps(existing))
                    updated["status"] = "final"
                    if "finalizes_provisional_id" in op:
                        updated["finalizes_provisional_id"] = op["finalizes_provisional_id"]
                    records[i] = updated
                    found = True
                    break
            if not found:
                raise AssertionError(
                    f"stream-delta-apply: finalize target {rid!r} not found"
                )
        elif kind == "state_change":
            rid = op.get("record_id")
            status = op.get("status")
            if not isinstance(rid, str) or not isinstance(status, str):
                raise AssertionError(
                    "stream-delta-apply: state_change requires record_id and status"
                )
            found = False
            for i, existing in enumerate(records):
                if record_id(existing) == rid:
                    updated = json.loads(json.dumps(existing))
                    updated["status"] = status
                    records[i] = updated
                    found = True
                    break
            if not found:
                raise AssertionError(
                    f"stream-delta-apply: state_change target {rid!r} not found"
                )
        elif kind == "diagnostic_add":
            diag = op.get("diagnostic")
            if not isinstance(diag, dict):
                raise AssertionError(
                    "stream-delta-apply: diagnostic_add requires diagnostic"
                )
            diagnostics.append(diag)
        elif kind == "diagnostic_remove":
            # Match by code (+ optional message) when full diagnostic not given.
            code = op.get("code") or (op.get("diagnostic") or {}).get("code")
            if not isinstance(code, str):
                raise AssertionError(
                    "stream-delta-apply: diagnostic_remove requires code"
                )
            message = op.get("message")
            if message is None and isinstance(op.get("diagnostic"), dict):
                message = op["diagnostic"].get("message")
            kept: list[dict[str, Any]] = []
            removed = False
            for diag in diagnostics:
                if diag.get("code") == code and (
                    message is None or diag.get("message") == message
                ):
                    if not removed:
                        removed = True
                        continue
                kept.append(diag)
            diagnostics[:] = kept
        elif kind == "reset":
            records.clear()
            diagnostics.clear()
            if "revision" in delta:
                base["revision"] = delta["revision"]
        else:
            raise AssertionError(
                f"stream-delta-apply: unknown delta op {kind!r} "
                "(comparison mode stub/error)"
            )

    if "revision" in delta:
        base["revision"] = delta["revision"]
    return base


def compare_stream_modes(
    *,
    label: str,
    case_directory: Path,
    manifest: dict[str, Any],
    response: dict[str, Any],
    repository_root: Path,
) -> int:
    """Apply declared stream comparison modes. Returns number of missing goldens.

    Missing step goldens write candidates and count as unaccepted (not hard-fail
    until engines land goldens). Hard failures raise AssertionError.
    """
    modes = manifest.get("comparison") or []
    if not isinstance(modes, list) or not modes:
        raise AssertionError(f"{label}: stream case must declare comparison modes")

    unknown = [m for m in modes if m not in STREAM_COMPARISON_MODES]
    if unknown:
        raise AssertionError(f"{label}: unknown stream comparison modes {unknown}")

    output_text = response.get("output_text")
    if not isinstance(output_text, str) or not output_text:
        raise AssertionError(f"{label}: success response missing output_text")

    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"{label}: output_text is not JSON for stream comparison"
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
        raise AssertionError(
            f"{label}: stream output_text must be an object with steps[]"
        )

    actual_steps: list[dict[str, Any]] = payload["steps"]
    declared_steps: list[dict[str, Any]] = manifest["steps"]
    if len(actual_steps) != len(declared_steps):
        raise AssertionError(
            f"{label}: step count mismatch: actual {len(actual_steps)} "
            f"!= declared {len(declared_steps)}"
        )

    missing_goldens = 0
    prior_snapshot: dict[str, Any] | None = None

    for index, (declared, actual) in enumerate(zip(declared_steps, actual_steps)):
        step_id = declared.get("id", f"step-{index}")
        step_label = f"{label}/step:{step_id}"
        expected_meta = declared.get("expected") or {}
        update = actual.get("update")
        if update is not None and not isinstance(update, dict):
            raise AssertionError(f"{step_label}: update must be an object when present")

        # Structural expectations from case (always checked when update present).
        if isinstance(update, dict):
            kind = update.get("kind")
            expected_kind = expected_meta.get("update_kind")
            if expected_kind is not None and kind != expected_kind:
                raise AssertionError(
                    f"{step_label}: expected update_kind {expected_kind!r}, got {kind!r}"
                )
            if "record_count" in expected_meta:
                snapshot = update.get("snapshot") or {}
                records = snapshot.get("records") if isinstance(snapshot, dict) else None
                if not isinstance(records, list):
                    # Some update kinds have null snapshot.
                    if expected_meta["record_count"] != 0 or update.get("snapshot") is not None:
                        raise AssertionError(
                            f"{step_label}: expected record_count but snapshot.records missing"
                        )
                elif len(records) != expected_meta["record_count"]:
                    raise AssertionError(
                        f"{step_label}: expected record_count "
                        f"{expected_meta['record_count']}, got {len(records)}"
                    )
            if "complete" in expected_meta:
                revision = None
                if isinstance(update.get("snapshot"), dict):
                    revision = update["snapshot"].get("revision")
                if revision is None and isinstance(update.get("revision"), dict):
                    revision = update["revision"]
                if isinstance(revision, dict) and "complete" in revision:
                    if revision["complete"] is not expected_meta["complete"]:
                        raise AssertionError(
                            f"{step_label}: expected complete="
                            f"{expected_meta['complete']!r}"
                        )
            if "reset_reason" in expected_meta:
                reset = update.get("reset") or {}
                reason = reset.get("reason") if isinstance(reset, dict) else None
                if reason != expected_meta["reset_reason"]:
                    raise AssertionError(
                        f"{step_label}: expected reset_reason "
                        f"{expected_meta['reset_reason']!r}, got {reason!r}"
                    )
            if "cursor_position_kind" in expected_meta:
                cursor = update.get("cursor") or {}
                position = cursor.get("position") if isinstance(cursor, dict) else None
                pos_kind = position.get("kind") if isinstance(position, dict) else None
                if pos_kind != expected_meta["cursor_position_kind"]:
                    raise AssertionError(
                        f"{step_label}: expected cursor_position_kind "
                        f"{expected_meta['cursor_position_kind']!r}, got {pos_kind!r}"
                    )
            if "diagnostic_codes" in expected_meta:
                codes = _step_diagnostic_codes(update)
                if codes != expected_meta["diagnostic_codes"]:
                    raise AssertionError(
                        f"{step_label}: expected diagnostic_codes "
                        f"{expected_meta['diagnostic_codes']}, got {codes}"
                    )
            if "fatal_error" in expected_meta and expected_meta["fatal_error"] is not None:
                fatal = update.get("fatal_error") or actual.get("fatal_error")
                code = fatal.get("code") if isinstance(fatal, dict) else None
                if code != expected_meta["fatal_error"]:
                    raise AssertionError(
                        f"{step_label}: expected fatal_error "
                        f"{expected_meta['fatal_error']!r}, got {code!r}"
                    )

        # Per-mode checks.
        for mode in modes:
            if mode == "stream-json-exact":
                golden_rel = expected_meta.get("result")
                if not isinstance(golden_rel, str) or not golden_rel:
                    # Scaffold phase: no golden yet — skip exact compare.
                    continue
                golden_path = case_directory / golden_rel
                if update is None:
                    raise AssertionError(f"{step_label}: missing update for json-exact")
                actual_json = json.dumps(update, separators=(",", ":"), ensure_ascii=False)
                if not golden_path.exists():
                    candidate = (
                        repository_root
                        / "artifacts"
                        / "conformance-candidates"
                        / "streaming"
                        / manifest["id"].split("/", 1)[-1]
                        / golden_rel
                    )
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    candidate.write_text(actual_json + "\n", encoding="utf-8")
                    print(
                        f"CANDIDATE {step_label}: wrote unaccepted output {candidate}",
                        file=sys.stderr,
                    )
                    missing_goldens += 1
                else:
                    expected = json.loads(golden_path.read_text(encoding="utf-8"))
                    if update != expected:
                        raise AssertionError(
                            f"{step_label}: stream-json-exact differs from {golden_path}"
                        )

            elif mode == "stream-cursor-exact":
                if not isinstance(update, dict):
                    continue
                cursor = update.get("cursor")
                golden_rel = expected_meta.get("result")
                if cursor is None or not isinstance(golden_rel, str):
                    continue
                golden_path = case_directory / golden_rel
                if golden_path.exists():
                    expected = json.loads(golden_path.read_text(encoding="utf-8"))
                    expected_cursor = expected.get("cursor")
                    if expected_cursor is not None and cursor != expected_cursor:
                        raise AssertionError(
                            f"{step_label}: stream-cursor-exact differs"
                        )

            elif mode == "stream-delta-apply":
                if not isinstance(update, dict) or update.get("kind") != "updated":
                    continue
                snapshot = update.get("snapshot")
                delta = update.get("delta")
                if snapshot is None and delta is None:
                    continue
                if delta is None:
                    # Snapshot-only delivery: nothing to apply.
                    prior_snapshot = snapshot if isinstance(snapshot, dict) else prior_snapshot
                    continue
                if not isinstance(delta, dict):
                    raise AssertionError(f"{step_label}: delta must be an object")
                reconstructed = apply_delta_to_snapshot(prior_snapshot, delta)
                if isinstance(snapshot, dict):
                    # Compare record/diagnostic identity sets; revision may be
                    # carried on delta only.
                    if _normalize_for_delta_eq(reconstructed) != _normalize_for_delta_eq(
                        snapshot
                    ):
                        raise AssertionError(
                            f"{step_label}: stream-delta-apply — applying delta to "
                            "prior snapshot does not yield new snapshot"
                        )
                    prior_snapshot = snapshot
                else:
                    prior_snapshot = reconstructed

            elif mode == "stream-diagnostics-by-step":
                if "diagnostic_codes" not in expected_meta:
                    continue
                if not isinstance(update, dict):
                    raise AssertionError(
                        f"{step_label}: stream-diagnostics-by-step requires update"
                    )
                codes = _step_diagnostic_codes(update)
                if codes != expected_meta["diagnostic_codes"]:
                    raise AssertionError(
                        f"{step_label}: diagnostics {codes} != "
                        f"{expected_meta['diagnostic_codes']}"
                    )

            elif mode == "stream-idempotence":
                # Runner must report double-invoke parity when double_invoke is set.
                double = declared.get("double_invoke", True)
                if double is False:
                    continue
                if actual.get("idempotent") is not True:
                    raise AssertionError(
                        f"{step_label}: stream-idempotence — runner did not report "
                        "idempotent=true after double-invoke"
                    )

            elif mode == "stream-oracle-parity":
                oracle = manifest.get("oracle") or {}
                if not oracle.get("append_equals_prefix") and not oracle.get(
                    "prefix_re_normalize"
                ):
                    continue
                # Engines must embed oracle section when they claim success.
                oracle_section = payload.get("oracle")
                if oracle_section is None:
                    raise AssertionError(
                        f"{label}: stream-oracle-parity requires output_text.oracle "
                        "when case.oracle is set (engine hook not populated)"
                    )
                if oracle.get("append_equals_prefix") and not oracle_section.get(
                    "append_equals_prefix"
                ):
                    raise AssertionError(
                        f"{label}: stream-oracle-parity append path diverged from prefix"
                    )
                if oracle.get("prefix_re_normalize") and not oracle_section.get(
                    "prefix_re_normalize"
                ):
                    raise AssertionError(
                        f"{label}: stream-oracle-parity prefix re-normalize mismatch"
                    )

            else:
                raise AssertionError(
                    f"{step_label}: comparison mode {mode!r} is not implemented "
                    "(stub missing — fix verify.py)"
                )

    return missing_goldens


def _normalize_for_delta_eq(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compare snapshots for delta-apply equality (records + diagnostics)."""
    return {
        "records": snapshot.get("records") or [],
        "diagnostics": snapshot.get("diagnostics") or [],
    }


def _step_diagnostic_codes(update: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("diagnostics",):
        items = update.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("code"), str):
                    codes.append(item["code"])
    snapshot = update.get("snapshot")
    if isinstance(snapshot, dict):
        items = snapshot.get("diagnostics")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("code"), str):
                    codes.append(item["code"])
    return codes


def run_batch_case(
    *,
    args: argparse.Namespace,
    repository_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> tuple[int, int]:
    """Returns (operations_checked, candidates)."""
    case_id = manifest["id"]
    expected_result = manifest["expected"]["result"]
    expected_codes = manifest["expected"].get("diagnostic_codes", [])
    operations = [
        (name, operation)
        for name, operation in manifest["operation"].items()
        if not args.operations or name in args.operations
    ]
    if not operations:
        return 0, 0

    checked = 0
    candidates = 0
    for operation_name, operation in operations:
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
    return checked, candidates


def run_stream_case(
    *,
    args: argparse.Namespace,
    repository_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> tuple[int, int, int]:
    """Returns (operations_checked, candidates, skipped_unsupported)."""
    case_id = manifest["id"]

    # Operation selection: default stream-sequence; honor explicit stream filters.
    if args.operations:
        selected = [op for op in args.operations if op in STREAM_OPS]
        if not selected:
            return 0, 0, 0
        # Prefer sequence ops when present; otherwise run first selected stream op
        # (individual apply ops may return unsupported until engines land).
        sequence = [op for op in selected if op in STREAM_SEQUENCE_OPS]
        operations = sequence if sequence else selected
    else:
        operations = ["stream-sequence"]

    checked = 0
    candidates = 0
    skipped = 0
    sentinels = privacy_sentinels(manifest)

    for operation_name in operations:
        label = f"{case_id}/{operation_name}"
        first = invoke(args.runner, repository_root, case_id, operation_name)
        second = invoke(args.runner, repository_root, case_id, operation_name)
        if first != second:
            raise AssertionError(f"{label}: repeated runner responses differ")

        status = first.get("status")
        if status == "unsupported":
            # Pre-engine: protocol works; capabilities not claimed yet.
            fatal = first.get("fatal_error") or {}
            code = fatal.get("code", "")
            if code not in {
                "capability_unsupported",
                "stream_engine_unavailable",
                "unsupported",
            }:
                # Accept any content-safe unsupported code; warn only.
                pass
            scan_privacy(label, json.dumps(first, ensure_ascii=False), sentinels)
            print(f"SKIP {label}: unsupported ({code or 'no-code'})", file=sys.stderr)
            skipped += 1
            checked += 1
            continue

        if status == "protocol-error":
            raise AssertionError(
                f"{label}: protocol-error: "
                f"{(first.get('fatal_error') or {}).get('message')}"
            )

        if status not in {"success", "fatal-error"}:
            raise AssertionError(f"{label}: unexpected status {status!r}")

        scan_privacy(label, first.get("output_text"), sentinels)
        scan_privacy(
            label,
            json.dumps(first.get("diagnostics") or [], ensure_ascii=False),
            sentinels,
        )
        scan_privacy(
            label,
            json.dumps(first.get("fatal_error") or {}, ensure_ascii=False),
            sentinels,
        )

        # Scan checked-in goldens for privacy.
        for step in manifest.get("steps") or []:
            result_rel = (step.get("expected") or {}).get("result")
            if isinstance(result_rel, str):
                golden = manifest_path.parent / result_rel
                if golden.exists():
                    scan_privacy(
                        f"{label}/{result_rel}",
                        golden.read_text(encoding="utf-8"),
                        sentinels,
                    )

        if status == "fatal-error":
            # Stream cases may declare a step that ends in error; without goldens
            # we only require a typed fatal_error object.
            fatal = first.get("fatal_error")
            if not isinstance(fatal, dict) or "code" not in fatal:
                raise AssertionError(f"{label}: fatal-error missing typed fatal_error")
            print(f"PASS {label} (fatal-error)", file=sys.stderr)
            checked += 1
            continue

        missing = compare_stream_modes(
            label=label,
            case_directory=manifest_path.parent,
            manifest=manifest,
            response=first,
            repository_root=repository_root,
        )
        if missing:
            candidates += missing
        print(f"PASS {label}", file=sys.stderr)
        checked += 1

    return checked, candidates, skipped


def main() -> int:
    args = parse_args()
    repository_root = args.repository_root.resolve()
    cases_root = repository_root / "conformance" / "cases"
    manifests = sorted(cases_root.glob("**/case.json"))
    if args.sources:
        manifests = [
            path
            for path in manifests
            if json.loads(path.read_text(encoding="utf-8"))["source"]
            in args.sources
        ]
    else:
        # Phase-0+ contract fixtures may land before runtimes implement a
        # source. Skip unadvertised sources unless the caller filters with
        # --source (explicit development / Phase-1 runs).
        allowed = implemented_sources(repository_root)
        manifests = [
            path
            for path in manifests
            if json.loads(path.read_text(encoding="utf-8"))["source"] in allowed
        ]
    checked = 0
    candidates = 0
    checked_manifests = 0
    skipped_stream = 0

    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if is_stream_case(manifest):
            ops_checked, case_candidates, case_skipped = run_stream_case(
                args=args,
                repository_root=repository_root,
                manifest_path=manifest_path,
                manifest=manifest,
            )
            if ops_checked == 0:
                continue
            checked_manifests += 1
            checked += ops_checked
            candidates += case_candidates
            skipped_stream += case_skipped
            continue

        # Batch path (requires operation map).
        if "operation" not in manifest:
            raise AssertionError(
                f"{manifest_path}: neither steps[] (stream) nor operation{{}} (batch)"
            )
        ops_checked, case_candidates = run_batch_case(
            args=args,
            repository_root=repository_root,
            manifest_path=manifest_path,
            manifest=manifest,
        )
        if ops_checked == 0:
            continue
        checked_manifests += 1
        checked += ops_checked
        candidates += case_candidates

    if candidates:
        raise AssertionError(
            f"{candidates} candidate outputs require review and check-in"
        )

    print(
        json.dumps(
            {
                "protocol_version": "1",
                "status": "success",
                "cases": checked_manifests,
                "operations": checked,
                "stream_unsupported_skips": skipped_stream,
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
