#!/usr/bin/env python3
"""Run declared conformance operations without modifying checked-in fixtures.

Supports:
- Batch normalize/list cases (conformance-case-v1)
- Multi-step stream cases (streaming-case-v1) via stream-sequence / stream-replay

Stream ``status=unsupported`` is a skip **only** for unclaimed *optional*
capabilities (``stream-file-io``, ``stream-hermes-provider``, …). When the
invoked runner advertises a required core ``stream-*`` capability (default:
``contracts/compatibility.json`` ``capabilities.required``, or
``--capabilities-file``), an unsupported response for a case that needs that
capability is a **failure**. Four core runners must report
``stream_unsupported_skips: 0`` on ``stream-sequence``.
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


def _read_utf8(path: Path) -> str:
    """Read a UTF-8 file without newline translation.

    ``Path.read_text`` uses universal newlines, which would rewrite CRLF into
    ``\\n`` before compare and can hide fixture corruption. Goldens and
    runner I/O are encoding-stable UTF-8 with LF only.
    """
    return path.read_bytes().decode("utf-8")


def _write_utf8(path: Path, text: str) -> None:
    """Write UTF-8 bytes without translating ``\\n`` to ``os.linesep``."""
    path.write_bytes(text.encode("utf-8"))


# Required core stream capabilities (compatibility.json required + four
# runtime-capabilities.json). Advertised ⇒ stream unsupported must FAIL.
CORE_STREAM_CAPABILITIES: frozenset[str] = frozenset(
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

# Optional package-only stream capabilities. Unsupported may skip when the
# runner did not advertise the name.
OPTIONAL_STREAM_CAPABILITIES: frozenset[str] = frozenset(
    {
        "stream-file-io",
        "stream-file-watch",
        "stream-ahp-client",
        "stream-ahp-list-sessions",
        "stream-hermes-provider",
        "stream-async-iterator",
    }
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
    parser.add_argument(
        "--capabilities-file",
        type=Path,
        dest="capabilities_file",
        help=(
            "JSON capability manifest for this invocation "
            "(runtime-capabilities.json with a capabilities array, or "
            "compatibility.json with capabilities.required). Default: "
            "contracts/compatibility.json required set — the four core "
            "runners advertise that set."
        ),
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
        _write_utf8(candidate, actual)
        print(
            f"CANDIDATE {label}: wrote unaccepted output {candidate}",
            file=sys.stderr,
        )
        return False
    expected = _read_utf8(expected_path)

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


def load_advertised_capabilities(
    repository_root: Path, capabilities_file: Path | None
) -> set[str]:
    """Capability set the invoked runner claims for this verify run.

    ``--capabilities-file`` should be that runtime's ``runtime-capabilities.json``
    (or a compatibility-shaped document). When omitted, use
    ``contracts/compatibility.json`` ``capabilities.required`` — the four core
    runners advertise that required set.
    """
    path = capabilities_file
    if path is None:
        path = repository_root / "contracts" / "compatibility.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    caps = payload.get("capabilities")
    if isinstance(caps, list):
        return {item for item in caps if isinstance(item, str)}
    if isinstance(caps, dict):
        required = caps.get("required") or []
        if not isinstance(required, list):
            raise AssertionError(f"{path}: capabilities.required must be an array")
        return {item for item in required if isinstance(item, str)}
    raise AssertionError(
        f"{path}: capabilities must be an array or an object with required[]"
    )


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


def scan_stream_case_inputs(
    *,
    label: str,
    case_directory: Path,
    manifest: dict[str, Any],
    sentinels: list[str],
) -> None:
    """Enforce fixture privacy on case materials and inline step inputs.

    Acceptance requires privacy rules on fixture content; materials and
    inline_utf8 are the primary inputs (not only runner outputs/goldens).
    case.json is scanned after stripping privacy.forbidden_substrings so the
    declaration of sentinels itself is not treated as a violation.
    """
    # Scan case manifest fields (description, ids, options, …) without the
    # privacy declaration list that intentionally names the sentinels.
    case_for_scan = json.loads(json.dumps(manifest))
    privacy_block = case_for_scan.get("privacy")
    if isinstance(privacy_block, dict):
        privacy_block.pop("forbidden_substrings", None)
        if not privacy_block:
            case_for_scan.pop("privacy", None)
    scan_privacy(
        f"{label}/case.json",
        json.dumps(case_for_scan, ensure_ascii=False),
        sentinels,
    )

    for step in manifest.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = step.get("id", "?")
        step_input = step.get("input") or {}
        if not isinstance(step_input, dict):
            continue
        _scan_step_input_materials(
            label=f"{label}/step:{step_id}",
            case_directory=case_directory,
            step_input=step_input,
            sentinels=sentinels,
        )


def _scan_step_input_materials(
    *,
    label: str,
    case_directory: Path,
    step_input: dict[str, Any],
    sentinels: list[str],
) -> None:
    material = step_input.get("material")
    if isinstance(material, str) and material:
        path = case_directory / material
        if path.is_file():
            raw = path.read_bytes()
            # Streaming materials are byte-identity fixtures. A CR means
            # Windows autocrlf (or a text-mode rewrite) corrupted LF JSONL /
            # utf8-byte-boundary tails — that shifts apply_append offsets.
            if b"\r" in raw:
                raise AssertionError(
                    f"{label}/material:{material}: fixture contains CR; "
                    "streaming materials must keep LF byte identity"
                )
            # Binary materials (utf8-byte-boundary) are scanned as latin-1 so
            # ASCII privacy sentinels still match without decode failures.
            scan_privacy(
                f"{label}/material:{material}",
                raw.decode("latin-1"),
                sentinels,
            )
    inline = step_input.get("inline_utf8")
    if isinstance(inline, str) and inline:
        scan_privacy(f"{label}/inline_utf8", inline, sentinels)

    reset = step_input.get("reset")
    if isinstance(reset, dict):
        reset_material = reset.get("material")
        if isinstance(reset_material, str) and reset_material:
            path = case_directory / reset_material
            if path.is_file():
                raw = path.read_bytes()
                if b"\r" in raw:
                    raise AssertionError(
                        f"{label}/reset.material:{reset_material}: fixture "
                        "contains CR; streaming materials must keep LF byte identity"
                    )
                scan_privacy(
                    f"{label}/reset.material:{reset_material}",
                    raw.decode("latin-1"),
                    sentinels,
                )
        reset_inline = reset.get("inline_utf8")
        if isinstance(reset_inline, str) and reset_inline:
            scan_privacy(f"{label}/reset.inline_utf8", reset_inline, sentinels)


def match_key(stream_record: dict[str, Any]) -> str | None:
    """Normative match key: provisional_id when set non-empty, else record.id.

    See contracts/spec/streaming.md §7.
    """
    if not isinstance(stream_record, dict):
        return None
    provisional = stream_record.get("provisional_id")
    if isinstance(provisional, str) and provisional:
        return provisional
    body = stream_record.get("record")
    if isinstance(body, dict):
        rid = body.get("id")
        if isinstance(rid, str) and rid:
            return rid
    return None


def encode_opt_int_for_diagnostic_key(value: Any) -> str:
    """Encode optional int for diagnostic_key: '-' when absent, else decimal."""
    if value is None:
        return "-"
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(
            f"stream-delta-apply: diagnostic key field must be int or absent, got {value!r}"
        )
    return str(value)


def diagnostic_key(diagnostic: dict[str, Any]) -> str:
    """Recompute diagnostic_key: code|input_line|record_index (see streaming.md §7).

    message and count do not participate. Omitted optional ints encode as '-'.
    """
    if not isinstance(diagnostic, dict):
        raise AssertionError("stream-delta-apply: diagnostic must be an object")
    code = diagnostic.get("code")
    if not isinstance(code, str) or not code:
        raise AssertionError("stream-delta-apply: diagnostic.code required for key")
    if "|" in code:
        raise AssertionError(
            "stream-delta-apply: diagnostic.code must not contain '|' "
            f"(got {code!r})"
        )
    # Property omitted vs present: only treat as present when key is set.
    line_part = (
        encode_opt_int_for_diagnostic_key(diagnostic["input_line"])
        if "input_line" in diagnostic
        else "-"
    )
    index_part = (
        encode_opt_int_for_diagnostic_key(diagnostic["record_index"])
        if "record_index" in diagnostic
        else "-"
    )
    return f"{code}|{line_part}|{index_part}"


def _upsert_record(records: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    key = match_key(entry)
    if key is None:
        raise AssertionError(
            "stream-delta-apply: upsert record missing match_key "
            "(provisional_id or record.id)"
        )
    for i, existing in enumerate(records):
        if match_key(existing) == key:
            records[i] = entry
            return
    records.append(entry)


def assert_delta_base_revision_chain(
    prior_snapshot: dict[str, Any] | None,
    delta: dict[str, Any],
    *,
    label: str = "stream-delta-apply",
) -> None:
    """Validate the delta-apply law precondition (streaming.md §7).

    D.base_revision_id must equal S0.revision.revision_id when the prior
    snapshot carries a revision id; both must be null for the first revision
    of a generation (empty / seed prior without revision.revision_id).
    """
    prior_revision_id: Any = None
    if isinstance(prior_snapshot, dict):
        revision = prior_snapshot.get("revision")
        if isinstance(revision, dict):
            prior_revision_id = revision.get("revision_id")

    base_revision_id = delta.get("base_revision_id")

    if prior_revision_id is not None:
        if base_revision_id != prior_revision_id:
            raise AssertionError(
                f"{label}: stream-delta-apply base_revision_id chain broken — "
                f"delta.base_revision_id={base_revision_id!r} does not equal "
                f"prior revision.revision_id={prior_revision_id!r}"
            )
    else:
        if base_revision_id is not None:
            raise AssertionError(
                f"{label}: stream-delta-apply base_revision_id chain broken — "
                f"first revision of a generation requires delta.base_revision_id "
                f"null, got {base_revision_id!r}"
            )


def apply_delta_to_snapshot(
    prior_snapshot: dict[str, Any] | None, delta: dict[str, Any]
) -> dict[str, Any]:
    """Apply stream delta operations to a prior snapshot (normative delta-apply law).

    Implements contracts/spec/streaming.md §7 / streaming-delta-v1:
    match_key, finalize remove-then-upsert, diagnostic_key de-dupe,
    no-op remove/state_change misses, revision+complete from delta.
    """
    if prior_snapshot is None:
        records: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        base: dict[str, Any] = {
            "schema_id": "trajectory-stream-v1",
            "records": records,
            "diagnostics": diagnostics,
        }
    else:
        base = json.loads(json.dumps(prior_snapshot))  # deep copy via JSON
        records = base.setdefault("records", [])
        diagnostics = base.setdefault("diagnostics", [])
        if not isinstance(records, list) or not isinstance(diagnostics, list):
            raise AssertionError(
                "stream-delta-apply: snapshot records/diagnostics malformed"
            )

    ops = delta.get("operations")
    if not isinstance(ops, list):
        raise AssertionError("stream-delta-apply: delta.operations must be an array")

    for op in ops:
        if not isinstance(op, dict) or "op" not in op:
            raise AssertionError("stream-delta-apply: each operation requires op")
        kind = op["op"]
        if kind == "upsert":
            entry = op.get("record")
            if not isinstance(entry, dict):
                raise AssertionError("stream-delta-apply: upsert requires record")
            _upsert_record(records, entry)

        elif kind == "remove":
            rid = op.get("record_id")
            if not isinstance(rid, str) or not rid:
                raise AssertionError("stream-delta-apply: remove requires record_id")
            # No-op if none match (stable relative order of survivors).
            records[:] = [r for r in records if match_key(r) != rid]

        elif kind == "finalize":
            provisional_id = op.get("provisional_id")
            entry = op.get("record")
            if not isinstance(provisional_id, str) or not provisional_id:
                raise AssertionError(
                    "stream-delta-apply: finalize requires provisional_id"
                )
            if not isinstance(entry, dict):
                raise AssertionError("stream-delta-apply: finalize requires record")
            # Remove every record keyed by provisional_id, then upsert replacement.
            records[:] = [
                r
                for r in records
                if not (
                    (isinstance(r.get("provisional_id"), str)
                     and r.get("provisional_id") == provisional_id)
                    or match_key(r) == provisional_id
                )
            ]
            _upsert_record(records, entry)

        elif kind == "state_change":
            rid = op.get("record_id")
            status = op.get("status")
            if not isinstance(rid, str) or not rid:
                raise AssertionError(
                    "stream-delta-apply: state_change requires record_id"
                )
            if not isinstance(status, str) or not status:
                raise AssertionError(
                    "stream-delta-apply: state_change requires status"
                )
            # No-op if none match; only status mutates when found.
            for i, existing in enumerate(records):
                if match_key(existing) == rid:
                    updated = json.loads(json.dumps(existing))
                    updated["status"] = status
                    records[i] = updated
                    break

        elif kind == "diagnostic_add":
            diag = op.get("diagnostic")
            if not isinstance(diag, dict):
                raise AssertionError(
                    "stream-delta-apply: diagnostic_add requires diagnostic"
                )
            key = diagnostic_key(diag)
            # De-dupe + refresh: drop existing same key, then append.
            diagnostics[:] = [
                d
                for d in diagnostics
                if not (isinstance(d, dict) and diagnostic_key(d) == key)
            ]
            diagnostics.append(diag)

        elif kind == "diagnostic_remove":
            dkey = op.get("diagnostic_key")
            if not isinstance(dkey, str) or not dkey:
                raise AssertionError(
                    "stream-delta-apply: diagnostic_remove requires diagnostic_key"
                )
            # No-op if none match.
            diagnostics[:] = [
                d
                for d in diagnostics
                if not (isinstance(d, dict) and diagnostic_key(d) == dkey)
            ]

        elif kind == "reset":
            reset_meta = op.get("reset")
            if not isinstance(reset_meta, dict):
                raise AssertionError("stream-delta-apply: reset requires reset object")
            records.clear()
            diagnostics.clear()
            # Install reset metadata on working snapshot for consumers that
            # surface generation transitions (not required by snapshot schema).
            base["reset"] = reset_meta

        else:
            raise AssertionError(
                f"stream-delta-apply: unknown delta op {kind!r} "
                "(comparison mode stub/error)"
            )

    # Set revision and complete from D.revision (normative step 3).
    revision = delta.get("revision")
    if isinstance(revision, dict):
        base["revision"] = revision
        if "complete" in revision:
            base["complete"] = revision["complete"]
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
            if "provisional_ids" in expected_meta:
                actual_ids = _envelope_provisional_ids(update)
                if actual_ids != expected_meta["provisional_ids"]:
                    raise AssertionError(
                        f"{step_label}: expected provisional_ids "
                        f"{expected_meta['provisional_ids']!r}, got {actual_ids!r}"
                    )
            if "finalized_ids" in expected_meta:
                actual_ids = _envelope_finalized_ids(update)
                if actual_ids != expected_meta["finalized_ids"]:
                    raise AssertionError(
                        f"{step_label}: expected finalized_ids "
                        f"{expected_meta['finalized_ids']!r}, got {actual_ids!r}"
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
                    _write_utf8(candidate, actual_json + "\n")
                    print(
                        f"CANDIDATE {step_label}: wrote unaccepted output {candidate}",
                        file=sys.stderr,
                    )
                    missing_goldens += 1
                else:
                    expected = json.loads(_read_utf8(golden_path))
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
                    expected = json.loads(_read_utf8(golden_path))
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
                # First revision: session identity (source/group_id) is not
                # carried on delta ops — seed empty prior from snapshot when
                # present so equality of those fields is meaningful.
                seed = prior_snapshot
                if seed is None and isinstance(snapshot, dict):
                    seed = {
                        "schema_id": snapshot.get("schema_id", "trajectory-stream-v1"),
                        "source": snapshot.get("source"),
                        "group_id": snapshot.get("group_id"),
                        "records": [],
                        "diagnostics": [],
                        "complete": False,
                    }
                # Normative precondition (streaming.md §7): base_revision_id
                # must chain to prior revision_id (or both null for first rev).
                assert_delta_base_revision_chain(seed, delta, label=step_label)
                reconstructed = apply_delta_to_snapshot(seed, delta)
                if isinstance(snapshot, dict):
                    # Normative equality: records, diagnostics, revision,
                    # source, group_id, complete (streaming.md §7).
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
                if (
                    not oracle.get("append_equals_prefix")
                    and not oracle.get("prefix_re_normalize")
                    and not oracle.get("action_equals_snapshot")
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
                if oracle.get("action_equals_snapshot") and not oracle_section.get(
                    "action_equals_snapshot"
                ):
                    raise AssertionError(
                        f"{label}: stream-oracle-parity action path diverged from "
                        "independent Shape A snapshot"
                    )

            else:
                raise AssertionError(
                    f"{step_label}: comparison mode {mode!r} is not implemented "
                    "(stub missing — fix verify.py)"
                )

    return missing_goldens


def _normalize_for_delta_eq(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Structural equality fields required by the delta-apply law (streaming.md §7)."""
    return {
        "records": snapshot.get("records") or [],
        "diagnostics": snapshot.get("diagnostics") or [],
        "revision": snapshot.get("revision"),
        "source": snapshot.get("source"),
        "group_id": snapshot.get("group_id"),
        "complete": snapshot.get("complete"),
    }


def _envelope_provisional_ids(update: dict[str, Any]) -> list[Any]:
    provisional = update.get("provisional")
    if isinstance(provisional, dict) and isinstance(
        provisional.get("provisional_ids"), list
    ):
        return provisional["provisional_ids"]
    return []


def _envelope_finalized_ids(update: dict[str, Any]) -> list[Any]:
    provisional = update.get("provisional")
    if isinstance(provisional, dict) and isinstance(
        provisional.get("finalized_ids"), list
    ):
        return provisional["finalized_ids"]
    return []


def _step_diagnostic_codes(update: dict[str, Any]) -> list[str]:
    """Codes for stream-diagnostics-by-step / expected.diagnostic_codes.

    Prefer ``update.diagnostics`` (StreamUpdate field) as the authoritative
    per-step source. Fall back to ``snapshot.diagnostics`` only when the update
    omits the field entirely. Never concatenate both — engines may populate
    both with the same codes (normative StreamUpdate and StreamSnapshot each
    carry diagnostics), which would double-count.
    """
    items = update.get("diagnostics")
    if items is None:
        snapshot = update.get("snapshot")
        if isinstance(snapshot, dict):
            items = snapshot.get("diagnostics")
    if not isinstance(items, list):
        return []
    codes: list[str] = []
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
                _read_utf8(manifest_path.parent / operation["expected"])
            )
            if first.get("fatal_error") != expected_error:
                raise AssertionError(f"{label}: fatal error differs")

        checked += 1
        print(f"PASS {label}", file=sys.stderr)
    return checked, candidates


def stream_unsupported_is_skip(
    *,
    required: set[str],
    advertised: set[str],
) -> bool:
    """True when unsupported may be skipped (unclaimed optional only).

    Fail (return False) when the case needs any advertised core ``stream-*``
    name, or any other advertised capability. Skip only when every required
    capability is either unadvertised-and-optional or not a stream name the
    runner claimed.
    """
    if not required:
        # No declared requirements: treat as core stream work once the runner
        # advertises any required core stream capability.
        return not bool(advertised & CORE_STREAM_CAPABILITIES)
    claimed_needed = required & advertised
    if claimed_needed:
        return False
    leftover = required - advertised
    return leftover <= OPTIONAL_STREAM_CAPABILITIES


def run_stream_case(
    *,
    args: argparse.Namespace,
    repository_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    advertised: set[str],
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
    case_directory = manifest_path.parent

    # Fixture inputs are primary privacy surface — scan before invoking runner.
    scan_stream_case_inputs(
        label=case_id,
        case_directory=case_directory,
        manifest=manifest,
        sentinels=sentinels,
    )

    for operation_name in operations:
        label = f"{case_id}/{operation_name}"
        first = invoke(args.runner, repository_root, case_id, operation_name)
        second = invoke(args.runner, repository_root, case_id, operation_name)
        if first != second:
            raise AssertionError(f"{label}: repeated runner responses differ")

        status = first.get("status")
        if status == "unsupported":
            fatal = first.get("fatal_error") or {}
            code = fatal.get("code", "")
            required_caps = {
                item
                for item in (manifest.get("required_capabilities") or [])
                if isinstance(item, str)
            }
            if not stream_unsupported_is_skip(
                required=required_caps, advertised=advertised
            ):
                claimed = sorted((required_caps & advertised) or (advertised & CORE_STREAM_CAPABILITIES))
                raise AssertionError(
                    f"{label}: runner advertised required core stream "
                    f"capabilities {claimed} but returned unsupported "
                    f"({code or 'no-code'}). Unsupported is a skip only for "
                    "unclaimed optional capabilities."
                )
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
                golden = case_directory / result_rel
                if golden.exists():
                    scan_privacy(
                        f"{label}/{result_rel}",
                        _read_utf8(golden),
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
    advertised = load_advertised_capabilities(
        repository_root, args.capabilities_file
    )

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
                advertised=advertised,
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
