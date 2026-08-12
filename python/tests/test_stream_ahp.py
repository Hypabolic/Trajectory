"""LS-06 / LS-07: AHP snapshot streaming and Shape B action-log reducer."""

from __future__ import annotations

import json
from pathlib import Path

from hypabolic_trajectory import (
    StreamOptions,
    apply_ahp_actions,
    apply_ahp_snapshot,
    apply_delta_to_snapshot,
    create_stream,
)
from hypabolic_trajectory.streaming.types import (
    AhpServerSeqPosition,
    SnapshotRevisionPosition,
)

ROOT = Path(__file__).resolve().parents[2]
STREAM_CASES = ROOT / "conformance" / "cases" / "streaming"


def _read(case: str, name: str) -> bytes:
    return (STREAM_CASES / case / name).read_bytes()


def test_ahp_snapshot_provisional_active_turn() -> None:
    chat = "ahp-chat:/00000000-0000-4000-8000-0000000000b1"
    state = create_stream(StreamOptions(source="ahp", group_id=chat))
    state, u1 = apply_ahp_snapshot(
        state,
        _read("provisional-to-stable", "step-provisional.json"),
        source_revision="ahp-rev-1",
    )
    assert u1.kind == "updated"
    assert list(u1.provisional.provisional_ids) == ["prov-active-turn-1"]
    assert isinstance(u1.cursor.position, SnapshotRevisionPosition)
    assert u1.cursor.position.revision == "ahp-rev-1"
    assert any(r.status == "provisional" for r in (u1.snapshot.records if u1.snapshot else ()))

    # Idempotent duplicate revision
    _, u_dup = apply_ahp_snapshot(
        state,
        _read("provisional-to-stable", "step-provisional.json"),
        source_revision="ahp-rev-1",
    )
    assert u_dup.kind == "unchanged"

    state, u2 = apply_ahp_snapshot(
        state,
        _read("provisional-to-stable", "step-stable.json"),
        source_revision="ahp-rev-2",
    )
    assert u2.kind == "updated"
    assert list(u2.provisional.provisional_ids) == []
    assert "prov-active-turn-1" in u2.provisional.finalized_ids
    assert u1.snapshot is not None and u2.snapshot is not None and u2.delta is not None
    recon = apply_delta_to_snapshot(u1.snapshot.to_dict(), u2.delta.to_dict())
    assert recon["records"] == u2.snapshot.to_dict()["records"]


def test_ahp_action_turn_flow_and_gap() -> None:
    chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1"
    state = create_stream(StreamOptions(source="ahp", group_id=chat))
    state, u = apply_ahp_actions(state, _read("ahp-action-turn-flow", "step-actions.jsonl"))
    assert u.kind == "updated"
    assert isinstance(u.cursor.position, AhpServerSeqPosition)
    assert u.cursor.position.last_server_seq == 5
    assert u.cursor.position.next_server_seq == 6
    assert u.snapshot is not None
    roles = [r.record.get("role") for r in u.snapshot.records]
    assert "user" in roles and "assistant" in roles
    assert all(r.status == "stable" for r in u.snapshot.records if r.record.get("role") != "meta")

    # Sequence gap → reset-required, cursor unchanged
    prior_cursor = state.cursor
    _, ug = apply_ahp_actions(state, _read("ahp-action-sequence-gap", "step-gap.jsonl"))
    assert ug.kind == "reset-required"
    assert ug.reset is not None
    assert ug.reset.reason == "sequence-gap"
    assert ug.cursor.position == prior_cursor.position


def test_ahp_unknown_and_foreign_channel() -> None:
    chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1"
    state = create_stream(StreamOptions(source="ahp", group_id=chat))
    state, _ = apply_ahp_actions(
        state, _read("ahp-action-unknown-foreign", "step-baseline.jsonl")
    )
    state, u = apply_ahp_actions(
        state, _read("ahp-action-unknown-foreign", "step-mixed.jsonl")
    )
    assert u.kind == "updated"
    codes = {d.code for d in u.diagnostics}
    assert "ahp_unknown_action" in codes
    assert "ahp_foreign_channel" in codes
    # Diagnostics content-safe: no action type payload echoed beyond fixed message.
    for d in u.diagnostics:
        assert "notARealAction" not in d.message
        assert "SECRET" not in d.message


def test_ahp_action_equals_snapshot() -> None:
    chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1"
    actions = _read("ahp-action-equals-snapshot", "step-actions.jsonl")
    snapshot = _read("ahp-action-equals-snapshot", "step-snapshot.json")

    s_act = create_stream(StreamOptions(source="ahp", group_id=chat))
    s_act, u_act = apply_ahp_actions(s_act, actions)
    assert u_act.kind == "updated" and u_act.snapshot is not None

    s_snap = create_stream(StreamOptions(source="ahp", group_id=chat))
    s_snap, u_snap = apply_ahp_snapshot(
        s_snap, snapshot, source_revision="ahp-equiv-1"
    )
    assert u_snap.kind == "updated" and u_snap.snapshot is not None

    act_ids = [(r.record.get("id"), r.status) for r in u_act.snapshot.records]
    snap_ids = [(r.record.get("id"), r.status) for r in u_snap.snapshot.records]
    assert act_ids == snap_ids
    act_content = [
        (r.record.get("role"), r.record.get("content"))
        for r in u_act.snapshot.records
        if r.record.get("role") != "meta"
    ]
    snap_content = [
        (r.record.get("role"), r.record.get("content"))
        for r in u_snap.snapshot.records
        if r.record.get("role") != "meta"
    ]
    assert act_content == snap_content


def test_ahp_action_idempotent_replay() -> None:
    chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1"
    data = _read("ahp-action-turn-flow", "step-actions.jsonl")
    state = create_stream(StreamOptions(source="ahp", group_id=chat))
    pre = state.cursor
    state, u1 = apply_ahp_actions(state, data)
    assert u1.kind == "updated"
    # True replay: same bytes + pre-apply cursor
    _, u2 = apply_ahp_actions(state, data, cursor=pre)
    assert u2.kind == "unchanged"
