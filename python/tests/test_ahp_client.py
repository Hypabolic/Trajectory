"""LS-10: optional AHP client — fake-host tests (gap, replay, cancel, backpressure, auth)."""

from __future__ import annotations

import json
from pathlib import Path

from hypabolic_trajectory.ahp_client import (
    AhpClientEvent,
    AhpClientOptions,
    AhpStreamClient,
    FakeAhpHost,
    FakeAhpHostScript,
    InMemoryAhpTransportPair,
)
from hypabolic_trajectory.ahp_client.protocol import encode_notification
from hypabolic_trajectory.streaming.types import AhpServerSeqPosition

ROOT = Path(__file__).resolve().parents[2]
STREAM_CASES = ROOT / "conformance" / "cases" / "streaming"
CHAT = "ahp-chat:/00000000-0000-4000-8000-0000000000c1"


def _actions_from_case(case: str, name: str) -> list[dict]:
    lines = (STREAM_CASES / case / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _shape_a_empty() -> dict:
    return {
        "ahpProtocolVersion": "0.7.0",
        "chat": {"id": CHAT, "turns": [], "activeTurn": None},
    }


def _collect(events: list[AhpClientEvent], kinds: set[str] | None = None) -> list[AhpClientEvent]:
    if kinds is None:
        return list(events)
    return [e for e in events if e.kind in kinds]


def test_subscribe_actions_feed_core() -> None:
    pair = InMemoryAhpTransportPair()
    actions = _actions_from_case("ahp-action-turn-flow", "step-actions.jsonl")
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(initial_actions=actions),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT),
        on_event=events.append,
    )
    client.start()
    updates = [e for e in events if e.kind == "stream-update"]
    assert any(e.kind == "ready" for e in events)
    assert updates
    assert updates[-1].update is not None
    assert updates[-1].update.kind == "updated"
    assert isinstance(client.cursor.position, AhpServerSeqPosition)
    assert client.cursor.position.last_server_seq == 5
    # No secrets in stream envelope
    blob = json.dumps(updates[-1].update.to_dict())
    assert "token" not in blob.lower() or "inputTokens" in blob  # usage field ok
    host.close()
    client.cancel()


def test_auth_failure() -> None:
    pair = InMemoryAhpTransportPair()
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(require_auth=True, accept_token="good"),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(
            chat_channel=CHAT,
            auth=lambda _c: {"token": "bad"},
        ),
        on_event=events.append,
    )
    client.start()
    assert any(e.kind == "auth-required" for e in events)
    assert any(e.kind == "auth-failed" for e in events)
    assert host.auth_attempts == 1
    assert not any(e.kind == "ready" for e in events)
    client.cancel()


def test_auth_success_then_subscribe() -> None:
    pair = InMemoryAhpTransportPair()
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(
            require_auth=True,
            accept_token="secret-token-xyz",
            initial_snapshot=_shape_a_empty(),
        ),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(
            chat_channel=CHAT,
            auth=lambda _c: {"token": "secret-token-xyz"},
        ),
        on_event=events.append,
    )
    client.start()
    assert any(e.kind == "ready" for e in events)
    updates = [e for e in events if e.kind == "stream-update" and e.update]
    for u in updates:
        blob = json.dumps(u.update.to_dict())  # type: ignore[union-attr]
        assert "secret-token-xyz" not in blob
    client.cancel()


def test_held_token_in_update_payload_is_not_delivered() -> None:
    """Regression: secret check must suppress stream-update, not only emit error.

    If a held auth token appears in the serialized StreamUpdate, on_event must
    receive a protocol error only — never the original update envelope.
    """
    from hypabolic_trajectory.ahp_client.protocol import ERR_PROTOCOL
    from hypabolic_trajectory.streaming.types import (
        BytePosition,
        StreamConsumed,
        StreamCursor,
        StreamProvisionalInfo,
        StreamRevision,
        StreamUpdate,
    )

    pair = InMemoryAhpTransportPair()
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(initial_snapshot=_shape_a_empty()),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT),
        on_event=events.append,
    )
    client.start()
    events.clear()

    # Distinctive token that cannot appear as an incidental field name.
    leak_token = "TRJ-AUTH-LEAK-MARKER-9f3c2e1b7a44"
    client._auth_token_held = leak_token  # simulate mid-auth hold

    # Embed the token in a JSON-visible cursor field (group_id).
    leaky = StreamUpdate(
        kind="updated",
        revision=StreamRevision(
            revision=0,
            revision_id="r0",
            parent_revision_id=None,
            complete=True,
            generation=0,
        ),
        cursor=StreamCursor(
            source="ahp",
            group_id=f"ahp-chat:/{leak_token}",
            generation=0,
            position=BytePosition(next_byte_offset=0),
        ),
        provisional=StreamProvisionalInfo(include=True),
        consumed=StreamConsumed(complete_records=0, bytes=0),
    )
    assert leak_token in json.dumps(leaky.to_dict())

    delivered = client._emit_update(leaky)
    assert delivered is False
    assert client._auth_token_held is None  # scrubbed on detect

    stream_updates = [e for e in events if e.kind == "stream-update"]
    assert stream_updates == [], "leaky StreamUpdate must not reach on_event"
    errors = [e for e in events if e.kind == "error"]
    assert len(errors) == 1
    assert errors[0].code == ERR_PROTOCOL
    for e in events:
        blob = json.dumps(
            {
                "kind": e.kind,
                "code": e.code,
                "message": e.message,
                "update": e.update.to_dict() if e.update is not None else None,
            }
        )
        assert leak_token not in blob

    client.cancel()
    host.close()


def test_sequence_gap_triggers_resync() -> None:
    pair = InMemoryAhpTransportPair()
    actions = _actions_from_case("ahp-action-turn-flow", "step-actions.jsonl")
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(
            initial_actions=actions,
            initial_snapshot=_shape_a_empty(),
        ),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT, auto_resync=True),
        on_event=events.append,
    )
    client.start()
    gen_before = client.cursor.generation
    updates_before = sum(1 for e in events if e.kind == "stream-update")
    # Push gapped action (serverSeq 9 after cursor next=6)
    gap = _actions_from_case("ahp-action-sequence-gap", "step-gap.jsonl")
    host.push_actions(gap)
    assert any(e.kind == "resync-required" for e in events)
    assert host.resync_count >= 1
    # Auto-resync advances generation and installs a post-resync stream-update.
    assert client.cursor.generation > gen_before
    updates_after = [e for e in events if e.kind == "stream-update"]
    assert len(updates_after) > updates_before
    assert updates_after[-1].update is not None
    assert updates_after[-1].update.kind in {"updated", "unchanged"}
    # Cursor remains valid after cancel
    cursor_before = client.cursor
    client.cancel()
    assert client.cursor.source == cursor_before.source
    assert client.cancelled


def test_duplicate_action_replay_idempotent() -> None:
    pair = InMemoryAhpTransportPair()
    actions = _actions_from_case("ahp-action-turn-flow", "step-actions.jsonl")
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(initial_actions=actions),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT),
        on_event=events.append,
    )
    client.start()
    first_updates = [e for e in events if e.kind == "stream-update"]
    assert first_updates[-1].update and first_updates[-1].update.kind == "updated"
    # Replay same batch via push
    before = len(events)
    host.push_actions(actions)
    replay = [e for e in events[before:] if e.kind == "stream-update"]
    # Core may return unchanged or updated with same seq authority; no fatal error
    assert all(
        e.update is not None and e.update.kind in {"updated", "unchanged", "reset-required"}
        for e in replay
    )
    client.cancel()


def test_backpressure() -> None:
    pair = InMemoryAhpTransportPair()
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(initial_snapshot=_shape_a_empty()),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT, max_buffered_actions=2),
        on_event=events.append,
    )
    client.start()
    # Pause by flooding while client is mid-buffer: fill buffer without flush by
    # setting paused via exceeding limit on consecutive pushes when resync holds.
    # Directly exercise buffer limit: push many actions quickly after forcing pause.
    client.set_paused_for_test(True)
    for i in range(5):
        host.push_action(
            {
                "channel": CHAT,
                "serverSeq": 100 + i,
                "origin": {"kind": "server"},
                "action": {"type": "chat/activityChanged", "activity": "thinking"},
            }
        )
    assert any(e.kind == "backpressure" for e in events)
    client.cancel()


def test_resume_after_backpressure_flushes_buffer() -> None:
    pair = InMemoryAhpTransportPair()
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(initial_snapshot=_shape_a_empty()),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT, max_buffered_actions=8),
        on_event=events.append,
    )
    client.start()
    client.set_paused_for_test(True)
    for i in range(3):
        host.push_action(
            {
                "channel": CHAT,
                "serverSeq": 1 + i,
                "origin": {"kind": "server"},
                "action": {"type": "chat/activityChanged", "activity": "thinking"},
            }
        )
    updates_before = sum(1 for e in events if e.kind == "stream-update")
    client.resume()
    updates_after = sum(1 for e in events if e.kind == "stream-update")
    assert updates_after > updates_before
    host.push_action(
        {
            "channel": CHAT,
            "serverSeq": 4,
            "origin": {"kind": "server"},
            "action": {"type": "chat/activityChanged", "activity": "idle"},
        }
    )
    assert isinstance(client.cursor.position, AhpServerSeqPosition)
    assert client.cursor.position.last_server_seq >= 3
    client.cancel()


def test_foreign_channel_action_notification_ignored() -> None:
    pair = InMemoryAhpTransportPair()
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(initial_snapshot=_shape_a_empty()),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT),
        on_event=events.append,
    )
    client.start()
    updates_before = sum(1 for e in events if e.kind == "stream-update")
    gen_before = client.cursor.generation
    foreign = "ahp-chat:/ffffffff-ffff-4fff-8fff-ffffffffffff"
    host.transport.send(
        encode_notification(
            "action",
            {
                "channel": foreign,
                "envelope": {
                    "channel": foreign,
                    "serverSeq": 99,
                    "origin": {"kind": "server"},
                    "action": {
                        "type": "chat/activityChanged",
                        "activity": "foreign",
                    },
                },
            },
        )
    )
    updates_after = sum(1 for e in events if e.kind == "stream-update")
    assert updates_after == updates_before
    assert client.cursor.generation == gen_before
    client.cancel()


def test_cancel_keeps_cursor() -> None:
    pair = InMemoryAhpTransportPair()
    actions = _actions_from_case("ahp-action-turn-flow", "step-actions.jsonl")
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(initial_actions=actions),
        chat_channel=CHAT,
    )
    events: list[AhpClientEvent] = []
    client = AhpStreamClient(
        transport=pair.client,
        options=AhpClientOptions(chat_channel=CHAT),
        on_event=events.append,
    )
    client.start()
    cur = client.cursor
    assert isinstance(cur.position, AhpServerSeqPosition)
    client.cancel()
    assert client.cancelled
    assert client.cursor.generation == cur.generation
    assert isinstance(client.cursor.position, AhpServerSeqPosition)
    assert client.cursor.position.last_server_seq == cur.position.last_server_seq
    host.close()
