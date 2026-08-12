"""AHP Shape B action-log reducer (minimal complete subset for LS-07).

Pure function: reduce ActionEnvelope batches into ChatState-like dicts, then
decode via existing Shape A path. No network. Protocol pin: 0.7.x.

Authority:
- docs/live-session-streaming.md §5 AHP / §4.3 ahp-server-seq
- docs/ahp-source-spec.md §5.7 action subset
- contracts/spec/sources/ahp.md Shape B
- Microsoft AHP chat reducer semantics (minimal port)
"""

from __future__ import annotations

import copy
import json
from typing import Any

from hypabolic_trajectory.diagnostics import (
    DIAG_AHP_FOREIGN_CHANNEL,
    DIAG_AHP_UNKNOWN_ACTION,
)

# Content-safe fixed messages (no action bodies, channels, or payloads).
MSG_UNKNOWN_ACTION = "Ignored an unknown AHP action type."
MSG_FOREIGN_CHANNEL = "Ignored an AHP action for a non-target channel."
MSG_INVALID_ACTIONS = "AHP action batch must be JSONL envelopes or a JSON array."
MSG_MISSING_SEQ = "AHP action envelope is missing a valid serverSeq."

# Chat actions the reducer understands (AHP 0.7.x names).
_KNOWN_CHAT_ACTIONS: frozenset[str] = frozenset(
    {
        "chat/turnStarted",
        "chat/responsePart",
        "chat/delta",
        "chat/reasoning",
        "chat/toolCallStart",
        "chat/toolCallDelta",
        "chat/toolCallReady",
        "chat/toolCallConfirmed",
        "chat/toolCallComplete",
        "chat/toolCallResultConfirmed",
        "chat/toolCallContentChanged",
        "chat/toolCallAuthRequired",
        "chat/toolCallAuthResolved",
        "chat/usage",
        "chat/turnComplete",
        "chat/turnCancelled",
        "chat/error",
        "chat/truncated",
        "chat/activityChanged",
        "chat/workingDirectorySet",
        "chat/workingDirectoryRemoved",
        "chat/inputRequested",
        "chat/inputAnswerChanged",
        "chat/inputCompleted",
    }
)


def empty_chat_state(*, resource: str | None = None) -> dict[str, Any]:
    """Create a minimal empty ChatState-like dict."""
    return {
        "resource": resource,
        "title": None,
        "status": 1,
        "activity": "",
        "modifiedAt": None,
        "origin": {"kind": "user"},
        "workingDirectories": [],
        "turns": [],
        "activeTurn": None,
    }


def parse_action_batch(data: bytes) -> list[dict[str, Any]]:
    """Parse Shape B bytes: JSONL envelopes, JSON array, or single envelope object."""
    if type(data) is not bytes:
        raise TypeError("AHP action batch must be bytes")
    text = data.decode("utf-8")
    stripped = text.strip()
    if not stripped:
        return []

    # Prefer JSONL when multiple non-empty lines are present (Shape B default).
    non_empty_lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(non_empty_lines) > 1:
        envelopes: list[dict[str, Any]] = []
        for line in non_empty_lines:
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                raise ValueError(MSG_INVALID_ACTIONS) from exc
            if not isinstance(obj, dict):
                raise ValueError(MSG_INVALID_ACTIONS)
            envelopes.append(obj)
        return envelopes

    # Single payload: JSON array, single envelope object, or one JSONL line.
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(MSG_INVALID_ACTIONS) from exc
    if isinstance(parsed, list):
        out: list[dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict):
                out.append(item)
        return out
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError(MSG_INVALID_ACTIONS)


def normalize_envelope(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize bare action or full envelope into {channel?, serverSeq, action}."""
    if "action" in raw and isinstance(raw.get("action"), dict):
        action = raw["action"]
        channel = raw.get("channel")
        seq = raw.get("serverSeq")
        return {
            "channel": channel if isinstance(channel, str) else None,
            "serverSeq": seq if isinstance(seq, (int, float)) and not isinstance(seq, bool) else None,
            "action": action,
            "origin": raw.get("origin"),
        }
    # Bare action: type field present
    action_type = raw.get("type")
    if isinstance(action_type, str):
        seq = raw.get("serverSeq")
        channel = raw.get("channel")
        action = {k: v for k, v in raw.items() if k not in {"channel", "serverSeq", "origin"}}
        return {
            "channel": channel if isinstance(channel, str) else None,
            "serverSeq": seq if isinstance(seq, (int, float)) and not isinstance(seq, bool) else None,
            "action": action,
            "origin": raw.get("origin"),
        }
    return None


def reduce_ahp_actions(
    chat: dict[str, Any] | None,
    envelopes: list[dict[str, Any]],
    *,
    target_channel: str | None,
    last_server_seq: int | None,
) -> tuple[dict[str, Any], int | None, list[dict[str, str]], list[int]]:
    """Reduce ordered envelopes into chat state.

    Returns (chat_state, new_last_server_seq, diagnostics, applied_seqs).

    Gaps are NOT applied: caller detects sequence-gap when next expected is
    skipped. This function assumes envelopes are contiguous or first-time.
    """
    state = copy.deepcopy(chat) if chat is not None else empty_chat_state(resource=target_channel)
    diagnostics: list[dict[str, str]] = []
    applied: list[int] = []
    last = last_server_seq
    channel = target_channel or state.get("resource")
    if isinstance(channel, str) and state.get("resource") is None:
        state["resource"] = channel

    # Sort by serverSeq ascending (stable for equal seq).
    normalized: list[dict[str, Any]] = []
    for raw in envelopes:
        env = normalize_envelope(raw)
        if env is None:
            diagnostics.append(
                {"code": DIAG_AHP_UNKNOWN_ACTION, "message": MSG_UNKNOWN_ACTION}
            )
            continue
        normalized.append(env)

    def _seq_key(e: dict[str, Any]) -> tuple[int, int]:
        seq = e.get("serverSeq")
        if seq is None:
            return (1, 0)
        return (0, int(seq))

    normalized.sort(key=_seq_key)

    for env in normalized:
        seq_raw = env.get("serverSeq")
        action = env.get("action")
        if not isinstance(action, dict):
            diagnostics.append(
                {"code": DIAG_AHP_UNKNOWN_ACTION, "message": MSG_UNKNOWN_ACTION}
            )
            continue
        action_type = action.get("type")
        if not isinstance(action_type, str):
            diagnostics.append(
                {"code": DIAG_AHP_UNKNOWN_ACTION, "message": MSG_UNKNOWN_ACTION}
            )
            continue

        env_channel = env.get("channel")
        # Lock channel from first chat-scoped action when unset.
        if channel is None and isinstance(env_channel, str) and env_channel.startswith(
            "ahp-chat:"
        ):
            channel = env_channel
            state["resource"] = channel

        # Foreign channel: ignore (non-chat or different chat URI).
        if isinstance(env_channel, str) and channel is not None and env_channel != channel:
            diagnostics.append(
                {"code": DIAG_AHP_FOREIGN_CHANNEL, "message": MSG_FOREIGN_CHANNEL}
            )
            continue
        if isinstance(env_channel, str) and not env_channel.startswith("ahp-chat:"):
            # session/root/terminal/etc.
            diagnostics.append(
                {"code": DIAG_AHP_FOREIGN_CHANNEL, "message": MSG_FOREIGN_CHANNEL}
            )
            continue

        if seq_raw is None:
            # Bare actions without seq: still reduce (offline convenience) but
            # do not advance serverSeq cursor authority.
            if action_type not in _KNOWN_CHAT_ACTIONS:
                diagnostics.append(
                    {"code": DIAG_AHP_UNKNOWN_ACTION, "message": MSG_UNKNOWN_ACTION}
                )
                continue
            state = _apply_chat_action(state, action)
            continue

        seq = int(seq_raw)
        # Already applied (idempotent replay of prefix)
        if last is not None and seq <= last:
            continue

        if action_type not in _KNOWN_CHAT_ACTIONS:
            diagnostics.append(
                {"code": DIAG_AHP_UNKNOWN_ACTION, "message": MSG_UNKNOWN_ACTION}
            )
            # Still advance seq so gaps are about missing numbers, not unknowns.
            last = seq
            applied.append(seq)
            continue

        state = _apply_chat_action(state, action)
        last = seq
        applied.append(seq)

    if channel is not None:
        state["resource"] = channel
    return state, last, diagnostics, applied


def shape_a_bytes(
    chat: dict[str, Any],
    *,
    protocol_version: str = "0.7.0",
    session: dict[str, Any] | None = None,
) -> bytes:
    """Serialize reduced ChatState as Shape A export bytes."""
    envelope: dict[str, Any] = {
        "ahpProtocolVersion": protocol_version,
        "chat": chat,
    }
    if session is not None:
        envelope["session"] = session
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def expected_next_seq(last_server_seq: int | None) -> int:
    """Next expected serverSeq (1-based host sequences typically start at 1)."""
    if last_server_seq is None:
        return 1
    return last_server_seq + 1


def detect_sequence_gap(
    envelopes: list[dict[str, Any]],
    *,
    last_server_seq: int | None,
    target_channel: str | None,
) -> int | None:
    """Return the first gap serverSeq, or None if contiguous / empty / all replay.

    A gap is a target-channel envelope whose serverSeq is strictly greater than
    expected next, after filtering already-applied seqs.
    """
    expected = expected_next_seq(last_server_seq)
    seqs: list[int] = []
    for raw in envelopes:
        env = normalize_envelope(raw)
        if env is None:
            continue
        seq_raw = env.get("serverSeq")
        if seq_raw is None:
            continue
        seq = int(seq_raw)
        env_channel = env.get("channel")
        if (
            isinstance(env_channel, str)
            and target_channel is not None
            and env_channel != target_channel
        ):
            continue
        if isinstance(env_channel, str) and not env_channel.startswith("ahp-chat:"):
            continue
        if last_server_seq is not None and seq <= last_server_seq:
            continue
        seqs.append(seq)
    if not seqs:
        return None
    seqs.sort()
    # First new seq must equal expected (when we already have a baseline).
    if last_server_seq is not None and seqs[0] > expected:
        return seqs[0]
    # Internal holes in the batch
    prev = seqs[0]
    for s in seqs[1:]:
        if s > prev + 1:
            return prev + 1
        prev = s
    return None


# ---------------------------------------------------------------------------
# Chat action application (minimal complete reducer)
# ---------------------------------------------------------------------------


def _apply_chat_action(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    action_type = action.get("type")
    if action_type == "chat/turnStarted":
        return _turn_started(state, action)
    if action_type == "chat/responsePart":
        return _response_part(state, action)
    if action_type == "chat/delta":
        return _delta(state, action, content_key="content", part_kinds=("markdown",))
    if action_type == "chat/reasoning":
        return _delta(state, action, content_key="content", part_kinds=("reasoning",))
    if action_type == "chat/toolCallStart":
        return _tool_call_start(state, action)
    if action_type == "chat/toolCallDelta":
        return _tool_call_delta(state, action)
    if action_type == "chat/toolCallReady":
        return _tool_call_ready(state, action)
    if action_type == "chat/toolCallConfirmed":
        return _tool_call_confirmed(state, action)
    if action_type == "chat/toolCallComplete":
        return _tool_call_complete(state, action)
    if action_type == "chat/toolCallResultConfirmed":
        return _tool_call_result_confirmed(state, action)
    if action_type == "chat/toolCallContentChanged":
        return _tool_call_content_changed(state, action)
    if action_type == "chat/toolCallAuthRequired":
        return _tool_call_auth_required(state, action)
    if action_type == "chat/toolCallAuthResolved":
        return _tool_call_auth_resolved(state, action)
    if action_type == "chat/usage":
        return _usage(state, action)
    if action_type == "chat/turnComplete":
        return _end_turn(state, action, turn_state="complete")
    if action_type == "chat/turnCancelled":
        return _end_turn(state, action, turn_state="cancelled")
    if action_type == "chat/error":
        return _end_turn(state, action, turn_state="error")
    if action_type == "chat/truncated":
        return _truncated(state, action)
    if action_type == "chat/activityChanged":
        next_state = copy.deepcopy(state)
        next_state["activity"] = action.get("activity") or ""
        return next_state
    if action_type == "chat/workingDirectorySet":
        return _working_dir_set(state, action)
    if action_type == "chat/workingDirectoryRemoved":
        return _working_dir_removed(state, action)
    # input request family: ignore content for trajectory; leave state unchanged
    if action_type in {
        "chat/inputRequested",
        "chat/inputAnswerChanged",
        "chat/inputCompleted",
    }:
        return state
    return state


def _active_turn(state: dict[str, Any]) -> dict[str, Any] | None:
    active = state.get("activeTurn")
    return active if isinstance(active, dict) else None


def _turn_started(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    turn_id = action.get("turnId")
    if not isinstance(turn_id, str):
        return state
    started = action.get("startedAt")
    message = action.get("message")
    if not isinstance(message, dict):
        message = {"text": "", "origin": {"kind": "user"}}
    next_state["activeTurn"] = {
        "id": turn_id,
        "startedAt": started if isinstance(started, str) else None,
        "duration": None,
        "message": message,
        "responseParts": [],
        "usage": None,
        "state": "in-progress",
        "error": None,
    }
    next_state["activity"] = next_state.get("activity") or "generating"
    return next_state


def _response_part(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    active = _active_turn(next_state)
    turn_id = action.get("turnId")
    part = action.get("part")
    if active is None or active.get("id") != turn_id or not isinstance(part, dict):
        return state
    parts = active.get("responseParts")
    if not isinstance(parts, list):
        parts = []
    parts = list(parts)
    parts.append(copy.deepcopy(part))
    active["responseParts"] = parts
    next_state["activeTurn"] = active
    return next_state


def _delta(
    state: dict[str, Any],
    action: dict[str, Any],
    *,
    content_key: str,
    part_kinds: tuple[str, ...],
) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    active = _active_turn(next_state)
    turn_id = action.get("turnId")
    part_id = action.get("partId")
    chunk = action.get(content_key)
    if (
        active is None
        or active.get("id") != turn_id
        or not isinstance(part_id, str)
        or not isinstance(chunk, str)
    ):
        return state
    parts = active.get("responseParts")
    if not isinstance(parts, list):
        return state
    updated = False
    new_parts: list[Any] = []
    for part in parts:
        if (
            not updated
            and isinstance(part, dict)
            and part.get("kind") in part_kinds
            and part.get("id") == part_id
        ):
            p = copy.deepcopy(part)
            prev = p.get("content")
            p["content"] = (prev if isinstance(prev, str) else "") + chunk
            new_parts.append(p)
            updated = True
        else:
            new_parts.append(part)
    if not updated:
        return state
    active["responseParts"] = new_parts
    next_state["activeTurn"] = active
    return next_state


def _tool_call_start(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    active = _active_turn(next_state)
    turn_id = action.get("turnId")
    tool_call_id = action.get("toolCallId")
    if active is None or active.get("id") != turn_id or not isinstance(tool_call_id, str):
        return state
    parts = list(active.get("responseParts") or [])
    parts.append(
        {
            "kind": "toolCall",
            "toolCall": {
                "toolCallId": tool_call_id,
                "toolName": action.get("toolName") or "unknown",
                "displayName": action.get("displayName"),
                "intention": action.get("intention"),
                "contributor": action.get("contributor"),
                "status": "streaming",
                "success": None,
                "confirmed": None,
                "content": None,
                "toolInput": None,
                "invocationMessage": None,
                "pastTenseMessage": None,
            },
        }
    )
    active["responseParts"] = parts
    next_state["activeTurn"] = active
    return next_state


def _update_tool_call(
    state: dict[str, Any],
    turn_id: Any,
    tool_call_id: Any,
    updater: Any,
) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    active = _active_turn(next_state)
    if (
        active is None
        or active.get("id") != turn_id
        or not isinstance(tool_call_id, str)
    ):
        return state
    parts = active.get("responseParts")
    if not isinstance(parts, list):
        return state
    found = False
    new_parts: list[Any] = []
    for part in parts:
        if (
            isinstance(part, dict)
            and part.get("kind") == "toolCall"
            and isinstance(part.get("toolCall"), dict)
            and part["toolCall"].get("toolCallId") == tool_call_id
        ):
            tc = copy.deepcopy(part["toolCall"])
            updated = updater(tc)
            if updated is tc or updated == tc:
                # may still be mutated in place by updater returning same dict
                pass
            new_parts.append({"kind": "toolCall", "toolCall": updated})
            found = True
        else:
            new_parts.append(part)
    if not found:
        return state
    active["responseParts"] = new_parts
    next_state["activeTurn"] = active
    return next_state


def _tool_call_delta(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        if tc.get("status") != "streaming":
            return tc
        content = action.get("content")
        if isinstance(content, str):
            prev = tc.get("partialInput")
            tc["partialInput"] = (prev if isinstance(prev, str) else "") + content
        inv = action.get("invocationMessage")
        if inv is not None:
            tc["invocationMessage"] = inv
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _tool_call_ready(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        status = tc.get("status")
        if status not in ("streaming", "running", "pending-confirmation"):
            return tc
        if action.get("intention") is not None:
            tc["intention"] = action.get("intention")
        if action.get("invocationMessage") is not None:
            tc["invocationMessage"] = action.get("invocationMessage")
        if action.get("toolInput") is not None:
            tc["toolInput"] = action.get("toolInput")
        if action.get("contributor") is not None:
            tc["contributor"] = action.get("contributor")
        confirmed = action.get("confirmed")
        if confirmed:
            tc["status"] = "running"
            tc["confirmed"] = confirmed
        else:
            tc["status"] = "pending-confirmation"
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _tool_call_confirmed(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        if tc.get("status") != "pending-confirmation":
            return tc
        if action.get("approved"):
            tc["status"] = "running"
            tc["confirmed"] = action.get("confirmed") or "user-action"
            edited = action.get("editedToolInput")
            if isinstance(edited, str) and isinstance(tc.get("toolInput"), str):
                tc["toolInput"] = edited
        else:
            tc["status"] = "cancelled"
            tc["success"] = False
            tc["reason"] = action.get("reason") or "denied"
            if action.get("reasonMessage") is not None:
                tc["reasonMessage"] = action.get("reasonMessage")
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _tool_call_complete(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        status = tc.get("status")
        if status not in ("running", "pending-confirmation", "auth-required"):
            return tc
        result = action.get("result")
        if not isinstance(result, dict):
            result = {}
        if status == "auth-required" and result.get("success") is True:
            return tc
        requires_confirm = bool(action.get("requiresResultConfirmation")) and status != "auth-required"
        for key in (
            "success",
            "pastTenseMessage",
            "content",
            "structuredContent",
            "error",
            "reasonMessage",
        ):
            if key in result:
                tc[key] = result[key]
        if requires_confirm:
            tc["status"] = "pending-result-confirmation"
        else:
            tc["status"] = "completed"
        if tc.get("confirmed") is None and status == "pending-confirmation":
            tc["confirmed"] = "not-needed"
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _tool_call_result_confirmed(
    state: dict[str, Any], action: dict[str, Any]
) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        if tc.get("status") != "pending-result-confirmation":
            return tc
        if action.get("approved"):
            tc["status"] = "completed"
        else:
            tc["status"] = "cancelled"
            tc["success"] = False
            tc["reason"] = "result-denied"
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _tool_call_content_changed(
    state: dict[str, Any], action: dict[str, Any]
) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        if tc.get("status") != "running":
            return tc
        if "content" in action:
            tc["content"] = action.get("content")
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _tool_call_auth_required(
    state: dict[str, Any], action: dict[str, Any]
) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        if tc.get("status") != "running":
            return tc
        contributor = tc.get("contributor")
        if not isinstance(contributor, dict) or contributor.get("kind") != "mcp":
            return tc
        tc["status"] = "auth-required"
        if "auth" in action:
            tc["auth"] = action.get("auth")
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _tool_call_auth_resolved(
    state: dict[str, Any], action: dict[str, Any]
) -> dict[str, Any]:
    def upd(tc: dict[str, Any]) -> dict[str, Any]:
        if tc.get("status") != "auth-required":
            return tc
        tc["status"] = "running"
        tc.pop("auth", None)
        return tc

    return _update_tool_call(state, action.get("turnId"), action.get("toolCallId"), upd)


def _usage(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    active = _active_turn(next_state)
    turn_id = action.get("turnId")
    usage = action.get("usage")
    if active is None or active.get("id") != turn_id or not isinstance(usage, dict):
        return state
    active["usage"] = copy.deepcopy(usage)
    next_state["activeTurn"] = active
    return next_state


def _end_turn(
    state: dict[str, Any], action: dict[str, Any], *, turn_state: str
) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    active = _active_turn(next_state)
    turn_id = action.get("turnId")
    if active is None or active.get("id") != turn_id:
        return state
    duration = action.get("duration")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        duration_val: int | float | None = max(0, duration)
    else:
        duration_val = 0

    # Force non-terminal tool calls to cancelled.
    parts = active.get("responseParts")
    new_parts: list[Any] = []
    if isinstance(parts, list):
        for part in parts:
            if (
                isinstance(part, dict)
                and part.get("kind") == "toolCall"
                and isinstance(part.get("toolCall"), dict)
            ):
                tc = copy.deepcopy(part["toolCall"])
                st = tc.get("status")
                if st not in ("completed", "cancelled"):
                    tc["status"] = "cancelled"
                    tc["success"] = False
                    tc["reason"] = "skipped"
                new_parts.append({"kind": "toolCall", "toolCall": tc})
            else:
                new_parts.append(part)

    turn = {
        "id": active.get("id"),
        "startedAt": active.get("startedAt"),
        "duration": duration_val,
        "message": active.get("message"),
        "responseParts": new_parts,
        "usage": active.get("usage"),
        "state": turn_state,
        "error": action.get("error") if turn_state == "error" else None,
    }
    turns = list(next_state.get("turns") or [])
    turns.append(turn)
    next_state["turns"] = turns
    next_state["activeTurn"] = None
    next_state["activity"] = ""
    return next_state


def _truncated(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    turn_id = action.get("turnId")
    turns = list(next_state.get("turns") or [])
    if turn_id is None:
        next_state["turns"] = []
    else:
        if not isinstance(turn_id, str):
            return state
        idx = next((i for i, t in enumerate(turns) if isinstance(t, dict) and t.get("id") == turn_id), -1)
        if idx < 0:
            return state
        next_state["turns"] = turns[: idx + 1]
    next_state["activeTurn"] = None
    next_state["activity"] = ""
    return next_state


def _working_dir_set(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    directory = action.get("directory")
    if not isinstance(directory, str):
        return state
    dirs = list(next_state.get("workingDirectories") or [])
    if directory not in dirs:
        dirs.append(directory)
    next_state["workingDirectories"] = dirs
    return next_state


def _working_dir_removed(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    next_state = copy.deepcopy(state)
    directory = action.get("directory")
    if not isinstance(directory, str):
        return state
    dirs = list(next_state.get("workingDirectories") or [])
    next_state["workingDirectories"] = [d for d in dirs if d != directory]
    return next_state
