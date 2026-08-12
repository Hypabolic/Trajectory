"""LS-02: pure-Python tests for normative stream delta-apply law.

Feeds contracts/vectors/streaming/valid delta instances through
conformance/verify.apply_delta_to_snapshot and asserts reconstruction.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "contracts" / "vectors" / "streaming" / "valid"
VERIFY_PATH = ROOT / "conformance" / "verify.py"


def _load_verify() -> Any:
    spec = importlib.util.spec_from_file_location("trajectory_verify", VERIFY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["trajectory_verify"] = module
    spec.loader.exec_module(module)
    return module


verify = _load_verify()


def _vector_instance(name: str) -> dict[str, Any]:
    payload = json.loads((VECTORS / name).read_text(encoding="utf-8"))
    instance = payload["instance"]
    assert isinstance(instance, dict)
    return instance


def _minimal_body(
    record_id: str,
    *,
    content: str = "x",
    order: int = 1,
    kind: str = "message",
    role: str = "assistant",
) -> dict[str, Any]:
    zeros = "0" * 64
    ones = "1" * 64
    return {
        "id": record_id,
        "kind": kind,
        "role": role,
        "order": order,
        "source_timestamp": "2026-01-01T00:00:00.000Z",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "content": content,
        "provenance": {
            "stable_source_record_id": "native-1",
            "source_identity_kind": "native",
            "source_order_id": "1|2026-01-01T00:00:00.000Z|00000000000000000001|native-1",
            "component_key": "message:0",
            "component_index": 0,
            "component_type_ordinal": 0,
            "native_record_id": "native-1",
        },
        "hashes": {
            "content_sha256": zeros,
            "record_sha256": ones,
        },
    }


def test_diagnostic_key_encoding_normative_examples() -> None:
    assert (
        verify.diagnostic_key({"code": "invalid_json_line", "message": "x"})
        == "invalid_json_line|-|-"
    )
    assert (
        verify.diagnostic_key(
            {"code": "invalid_json_line", "message": "x", "input_line": 3}
        )
        == "invalid_json_line|3|-"
    )
    assert (
        verify.diagnostic_key(
            {
                "code": "orphan_tool_result",
                "message": "x",
                "input_line": 10,
                "record_index": 2,
                "count": 1,
            }
        )
        == "orphan_tool_result|10|2"
    )


def test_step_diagnostic_codes_prefers_update_not_concat() -> None:
    """update.diagnostics is authoritative; do not double-count snapshot."""
    same = {"code": "invalid_json_line", "message": "bad"}
    update = {
        "kind": "updated",
        "diagnostics": [same],
        "snapshot": {"diagnostics": [same]},
    }
    assert verify._step_diagnostic_codes(update) == ["invalid_json_line"]

    # Explicit empty list on update is authoritative (no fall-through).
    cleared = {
        "kind": "updated",
        "diagnostics": [],
        "snapshot": {"diagnostics": [same]},
    }
    assert verify._step_diagnostic_codes(cleared) == []

    # Fall back to snapshot only when update omits the field.
    snap_only = {
        "kind": "updated",
        "snapshot": {"diagnostics": [same, {"code": "orphan_tool_result"}]},
    }
    assert verify._step_diagnostic_codes(snap_only) == [
        "invalid_json_line",
        "orphan_tool_result",
    ]


def test_match_key_prefers_provisional_id() -> None:
    provisional = {
        "status": "provisional",
        "provisional_id": "prov-1",
        "record": _minimal_body("a" * 64),
    }
    stable = {
        "status": "stable",
        "record": _minimal_body("b" * 64),
    }
    assert verify.match_key(provisional) == "prov-1"
    assert verify.match_key(stable) == "b" * 64


def test_schema_valid_finalize_and_diagnostic_remove_shapes_accepted() -> None:
    """Negative regression: valid wire shapes must not be rejected as missing fields.

    Old apply_delta expected finalize.record_id and diagnostic_remove.code;
    streaming-delta-v1 requires provisional_id+record and diagnostic_key.
    """
    finalize_delta = _vector_instance("delta-finalize.json")
    prior = {
        "schema_id": "trajectory-stream-v1",
        "source": "ahp",
        "group_id": "g",
        "revision": {
            "revision": 4,
            "revision_id": "rev-4",
            "parent_revision_id": "rev-3",
            "complete": False,
            "generation": 0,
        },
        "records": [
            {
                "status": "provisional",
                "provisional_id": "prov-active-turn-1",
                "record": _minimal_body(
                    "1111111111111111111111111111111111111111111111111111111111111111",
                    content="partial",
                ),
            }
        ],
        "diagnostics": [],
        "complete": False,
    }
    result = verify.apply_delta_to_snapshot(prior, finalize_delta)
    assert result["revision"]["revision_id"] == "rev-5"
    assert result["complete"] is False
    assert len(result["records"]) == 1
    rec = result["records"][0]
    assert rec["status"] == "final"
    assert rec.get("provisional_id") is None or "provisional_id" not in rec
    assert (
        rec["record"]["id"]
        == "2222222222222222222222222222222222222222222222222222222222222222"
    )
    assert rec.get("finalizes_provisional_id") == "prov-active-turn-1"

    diag_delta = _vector_instance("delta-diagnostic-add-remove.json")
    prior_diag = {
        "schema_id": "trajectory-stream-v1",
        "source": "pi",
        "group_id": "g",
        "revision": {
            "revision": 6,
            "revision_id": "rev-6",
            "parent_revision_id": "rev-5",
            "complete": False,
            "generation": 0,
        },
        "records": [],
        "diagnostics": [
            {
                "code": "invalid_json_line",
                "message": "old",
                "input_line": 3,
            }
        ],
        "complete": False,
    }
    result_diag = verify.apply_delta_to_snapshot(prior_diag, diag_delta)
    codes = [d["code"] for d in result_diag["diagnostics"]]
    # remove invalid_json_line|3|-, add invalid_json_line at line 12, add orphan
    assert codes == ["invalid_json_line", "orphan_tool_result"]
    assert result_diag["diagnostics"][0]["input_line"] == 12
    assert result_diag["diagnostics"][1]["record_index"] == 2


def test_upsert_matches_provisional_id_not_body_id() -> None:
    """Body id may change across provisional revisions without opening a second slot."""
    rid_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    rid_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    prior = {
        "schema_id": "trajectory-stream-v1",
        "source": "ahp",
        "group_id": "ahp-chat-1",
        "revision": {
            "revision": 1,
            "revision_id": "rev-1",
            "parent_revision_id": None,
            "complete": False,
            "generation": 0,
        },
        "records": [
            {
                "status": "provisional",
                "provisional_id": "prov-active-turn-1",
                "record": _minimal_body(rid_a, content="partial"),
            }
        ],
        "diagnostics": [],
        "complete": False,
    }
    # Use vector-shaped upsert from update-updated-provisional (same provisional_id).
    update = _vector_instance("update-updated-provisional.json")
    delta = update["delta"]
    # Override body id to prove match_key ignores it when provisional_id is set.
    delta = json.loads(json.dumps(delta))
    delta["operations"][0]["record"]["record"]["id"] = rid_b
    delta["operations"][0]["record"]["record"]["content"] = "partial+"

    result = verify.apply_delta_to_snapshot(prior, delta)
    assert len(result["records"]) == 1
    assert result["records"][0]["record"]["id"] == rid_b
    assert result["records"][0]["record"]["content"] == "partial+"
    assert result["records"][0]["provisional_id"] == "prov-active-turn-1"
    assert result["revision"]["revision_id"] == "rev-2"
    assert result["source"] == "ahp"
    assert result["group_id"] == "ahp-chat-1"
    assert result["complete"] is False


def test_remove_and_state_change_are_noop_when_missing() -> None:
    prior = {
        "schema_id": "trajectory-stream-v1",
        "source": "pi",
        "group_id": "g",
        "revision": {
            "revision": 1,
            "revision_id": "rev-1",
            "parent_revision_id": None,
            "complete": False,
            "generation": 0,
        },
        "records": [
            {
                "status": "stable",
                "record": _minimal_body(
                    "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
                ),
            }
        ],
        "diagnostics": [],
        "complete": False,
    }
    delta = {
        "schema_id": "trajectory-stream-v1",
        "base_revision_id": "rev-1",
        "revision": {
            "revision": 2,
            "revision_id": "rev-2",
            "parent_revision_id": "rev-1",
            "complete": False,
            "generation": 0,
        },
        "operations": [
            {
                "op": "remove",
                "record_id": "missing-key",
                "reason": "retracted",
            },
            {
                "op": "state_change",
                "record_id": "also-missing",
                "status": "final",
            },
        ],
    }
    result = verify.apply_delta_to_snapshot(prior, delta)
    assert len(result["records"]) == 1
    assert result["records"][0]["status"] == "stable"
    assert result["revision"]["revision_id"] == "rev-2"


def test_diagnostic_add_dedupes_by_key() -> None:
    prior = {
        "schema_id": "trajectory-stream-v1",
        "source": "pi",
        "group_id": "g",
        "revision": {
            "revision": 0,
            "revision_id": "rev-0",
            "parent_revision_id": None,
            "complete": False,
            "generation": 0,
        },
        "records": [],
        "diagnostics": [
            {
                "code": "invalid_json_line",
                "message": "first",
                "input_line": 3,
                "count": 1,
            }
        ],
        "complete": False,
    }
    delta = {
        "schema_id": "trajectory-stream-v1",
        "base_revision_id": "rev-0",
        "revision": {
            "revision": 1,
            "revision_id": "rev-1",
            "parent_revision_id": "rev-0",
            "complete": False,
            "generation": 0,
        },
        "operations": [
            {
                "op": "diagnostic_add",
                "diagnostic": {
                    "code": "invalid_json_line",
                    "message": "refreshed",
                    "input_line": 3,
                    "count": 2,
                },
            }
        ],
    }
    result = verify.apply_delta_to_snapshot(prior, delta)
    assert len(result["diagnostics"]) == 1
    assert result["diagnostics"][0]["message"] == "refreshed"
    assert result["diagnostics"][0]["count"] == 2


def test_normalize_for_delta_eq_includes_revision_source_group_complete() -> None:
    left = {
        "records": [],
        "diagnostics": [],
        "revision": {"revision_id": "a"},
        "source": "pi",
        "group_id": "g1",
        "complete": False,
    }
    right = dict(left)
    right["group_id"] = "g2"
    assert verify._normalize_for_delta_eq(left) != verify._normalize_for_delta_eq(right)
    right["group_id"] = "g1"
    right["complete"] = True
    assert verify._normalize_for_delta_eq(left) != verify._normalize_for_delta_eq(right)
    right["complete"] = False
    right["source"] = "ahp"
    assert verify._normalize_for_delta_eq(left) != verify._normalize_for_delta_eq(right)
    right["source"] = "pi"
    right["revision"] = {"revision_id": "b"}
    assert verify._normalize_for_delta_eq(left) != verify._normalize_for_delta_eq(right)
    right["revision"] = {"revision_id": "a"}
    assert verify._normalize_for_delta_eq(left) == verify._normalize_for_delta_eq(right)


def test_apply_delta_reconstruct_update_updated_both() -> None:
    """Snapshot+delta pair from valid vectors must satisfy the delta-apply law."""
    update = _vector_instance("update-updated-both.json")
    prior = {
        "schema_id": "trajectory-stream-v1",
        "source": update["snapshot"]["source"],
        "group_id": update["snapshot"]["group_id"],
        "revision": {
            "revision": 0,
            "revision_id": "rev-0",
            "parent_revision_id": None,
            "complete": False,
            "generation": 0,
        },
        "records": [],
        "diagnostics": [],
        "complete": False,
    }
    reconstructed = verify.apply_delta_to_snapshot(prior, update["delta"])
    # Preserve source/group_id from prior when delta does not carry them.
    assert verify._normalize_for_delta_eq(reconstructed) == verify._normalize_for_delta_eq(
        update["snapshot"]
    )


def test_reset_clears_records_and_diagnostics() -> None:
    prior = {
        "schema_id": "trajectory-stream-v1",
        "source": "pi",
        "group_id": "g",
        "revision": {
            "revision": 3,
            "revision_id": "rev-3",
            "parent_revision_id": "rev-2",
            "complete": False,
            "generation": 0,
        },
        "records": [
            {
                "status": "stable",
                "record": _minimal_body(
                    "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
                ),
            }
        ],
        "diagnostics": [{"code": "x", "message": "y"}],
        "complete": False,
    }
    delta = {
        "schema_id": "trajectory-stream-v1",
        "base_revision_id": "rev-3",
        "revision": {
            "revision": 0,
            "revision_id": "rev-gen1-0",
            "parent_revision_id": None,
            "complete": False,
            "generation": 1,
        },
        "operations": [
            {
                "op": "reset",
                "reset": {
                    "reason": "source-truncated",
                    "prior_cursor": None,
                    "requires_snapshot": True,
                    "dropped_record_ids": [
                        "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
                    ],
                },
            }
        ],
    }
    result = verify.apply_delta_to_snapshot(prior, delta)
    assert result["records"] == []
    assert result["diagnostics"] == []
    assert result["revision"]["generation"] == 1
    assert result["reset"]["reason"] == "source-truncated"
