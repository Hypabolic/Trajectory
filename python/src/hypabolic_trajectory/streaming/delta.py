"""Stable-id stream diff and normative delta-apply law helpers."""

from __future__ import annotations

import copy
from typing import Any

from hypabolic_trajectory.streaming.types import (
    StreamDelta,
    StreamDeltaOperation,
    StreamDiagnostic,
    StreamRecord,
    StreamRevision,
    StreamSnapshot,
)


def match_key(stream_record: StreamRecord | dict[str, Any]) -> str:
    """Normative match key: provisional_id when set non-empty, else record.id."""
    if isinstance(stream_record, StreamRecord):
        if stream_record.provisional_id:
            return stream_record.provisional_id
        rid = stream_record.record.get("id")
        if isinstance(rid, str) and rid:
            return rid
        raise ValueError("stream record missing match key")
    provisional = stream_record.get("provisional_id")
    if isinstance(provisional, str) and provisional:
        return provisional
    body = stream_record.get("record")
    if isinstance(body, dict):
        rid = body.get("id")
        if isinstance(rid, str) and rid:
            return rid
    raise ValueError("stream record missing match key")


def diagnostic_key(diagnostic: StreamDiagnostic | dict[str, Any]) -> str:
    """Recompute diagnostic_key: code|input_line|record_index."""
    if isinstance(diagnostic, StreamDiagnostic):
        code = diagnostic.code
        line = diagnostic.input_line
        index = diagnostic.record_index
        line_part = "-" if line is None else str(line)
        index_part = "-" if index is None else str(index)
        return f"{code}|{line_part}|{index_part}"
    code = diagnostic.get("code")
    if not isinstance(code, str) or not code:
        raise ValueError("diagnostic.code required")
    # Treat missing and JSON null the same: normative '-' sentinel.
    line = diagnostic.get("input_line")
    index = diagnostic.get("record_index")
    line_part = "-" if line is None else str(line)
    index_part = "-" if index is None else str(index)
    return f"{code}|{line_part}|{index_part}"


def _record_body_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a == b


def _status_of(rec: StreamRecord) -> str:
    return rec.status


def diff_snapshots(
    prior: StreamSnapshot | None,
    current: StreamSnapshot,
    *,
    revision: StreamRevision,
) -> StreamDelta:
    """Produce ordered delta ops such that apply(prior, delta) == current."""
    base_revision_id = (
        prior.revision.revision_id if prior is not None else None
    )
    prior_records = list(prior.records) if prior is not None else []
    prior_diags = list(prior.diagnostics) if prior is not None else []
    curr_records = list(current.records)
    curr_diags = list(current.diagnostics)

    prior_by_key = {match_key(r): r for r in prior_records}
    curr_by_key = {match_key(r): r for r in curr_records}

    ops: list[StreamDeltaOperation] = []

    # 1. removals (match_key ascending)
    removed_keys = sorted(set(prior_by_key) - set(curr_by_key))
    for key in removed_keys:
        ops.append(
            StreamDeltaOperation(
                op="remove",
                payload={"record_id": key, "reason": "source-rewrite"},
            )
        )

    # 2. finalizations — not emitted by plain stable-id snapshot diff (no
    # provisional tracking in LS-04 snapshot path unless records carry
    # provisional_id). When a prior provisional becomes non-provisional with
    # same provisional_id linkage, emit finalize; otherwise upsert handles it.

    # 3. upserts (snapshot record order)
    for rec in curr_records:
        key = match_key(rec)
        prev = prior_by_key.get(key)
        if prev is None or not _record_body_equal(prev.record, rec.record) or (
            _status_of(prev) != _status_of(rec)
            and prev.provisional_id
            and not rec.provisional_id
        ):
            if (
                prev is not None
                and prev.provisional_id
                and rec.finalizes_provisional_id == prev.provisional_id
            ):
                ops.append(
                    StreamDeltaOperation(
                        op="finalize",
                        payload={
                            "provisional_id": prev.provisional_id,
                            "record": rec.to_dict(),
                        },
                    )
                )
            elif prev is not None and (
                _record_body_equal(prev.record, rec.record)
                and _status_of(prev) != _status_of(rec)
            ):
                ops.append(
                    StreamDeltaOperation(
                        op="state_change",
                        payload={"record_id": key, "status": rec.status},
                    )
                )
            else:
                ops.append(
                    StreamDeltaOperation(
                        op="upsert",
                        payload={"record": rec.to_dict()},
                    )
                )
        elif _status_of(prev) != _status_of(rec):
            ops.append(
                StreamDeltaOperation(
                    op="state_change",
                    payload={"record_id": key, "status": rec.status},
                )
            )

    # 5. diagnostic removes then adds (key ascending)
    prior_diag_keys = {diagnostic_key(d): d for d in prior_diags}
    curr_diag_keys = {diagnostic_key(d): d for d in curr_diags}

    for key in sorted(set(prior_diag_keys) - set(curr_diag_keys)):
        ops.append(
            StreamDeltaOperation(
                op="diagnostic_remove",
                payload={"diagnostic_key": key},
            )
        )
    for key in sorted(curr_diag_keys):
        d = curr_diag_keys[key]
        prev_d = prior_diag_keys.get(key)
        if prev_d is None or prev_d.to_dict() != d.to_dict():
            ops.append(
                StreamDeltaOperation(
                    op="diagnostic_add",
                    payload={"diagnostic": d.to_dict()},
                )
            )

    return StreamDelta(
        base_revision_id=base_revision_id,
        revision=revision,
        operations=tuple(ops),
    )


def apply_delta_to_snapshot(
    prior_snapshot: dict[str, Any] | None,
    delta: dict[str, Any],
) -> dict[str, Any]:
    """Apply stream delta operations to a prior snapshot (delta-apply law).

    Dict form for wire/conformance parity with ``conformance/verify.py``.
    """
    if prior_snapshot is None:
        base: dict[str, Any] = {
            "schema_id": "trajectory-stream-v1",
            "records": [],
            "diagnostics": [],
        }
    else:
        base = copy.deepcopy(prior_snapshot)

    records: list[dict[str, Any]] = list(base.get("records") or [])
    diagnostics: list[dict[str, Any]] = list(base.get("diagnostics") or [])

    for op in delta.get("operations") or []:
        kind = op["op"]
        if kind == "upsert":
            entry = op["record"]
            key = match_key(entry)
            replaced = False
            for i, existing in enumerate(records):
                if match_key(existing) == key:
                    records[i] = entry
                    replaced = True
                    break
            if not replaced:
                records.append(entry)
        elif kind == "remove":
            rid = op["record_id"]
            records = [r for r in records if match_key(r) != rid]
        elif kind == "finalize":
            pid = op["provisional_id"]
            records = [
                r
                for r in records
                if not (
                    isinstance(r.get("provisional_id"), str)
                    and r.get("provisional_id") == pid
                )
                and match_key(r) != pid
            ]
            entry = op["record"]
            key = match_key(entry)
            replaced = False
            for i, existing in enumerate(records):
                if match_key(existing) == key:
                    records[i] = entry
                    replaced = True
                    break
            if not replaced:
                records.append(entry)
        elif kind == "state_change":
            rid = op["record_id"]
            status = op["status"]
            for i, existing in enumerate(records):
                if match_key(existing) == rid:
                    updated = copy.deepcopy(existing)
                    updated["status"] = status
                    records[i] = updated
                    break
        elif kind == "diagnostic_add":
            d = op["diagnostic"]
            key = diagnostic_key(d)
            diagnostics = [
                x for x in diagnostics if diagnostic_key(x) != key
            ]
            diagnostics.append(d)
        elif kind == "diagnostic_remove":
            key = op["diagnostic_key"]
            diagnostics = [
                x for x in diagnostics if diagnostic_key(x) != key
            ]
        elif kind == "reset":
            records = []
            diagnostics = []
        else:
            raise ValueError(f"unknown delta op: {kind}")

    revision = delta.get("revision") or {}
    base["records"] = records
    base["diagnostics"] = diagnostics
    if isinstance(revision, dict):
        base["revision"] = copy.deepcopy(revision)
        if "complete" in revision:
            base["complete"] = revision["complete"]
    return base
