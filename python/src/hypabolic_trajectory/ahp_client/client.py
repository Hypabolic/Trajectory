"""AHP stream client: auth callback + subscribe + feed core apply_ahp_*."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from hypabolic_trajectory.ahp_client.protocol import (
    ERR_AUTH_FAILED,
    ERR_AUTH_REQUIRED,
    ERR_BACKPRESSURE,
    ERR_CANCELLED,
    ERR_PROTOCOL,
    ERR_RESYNC_REQUIRED,
    ERR_TRANSPORT,
    authenticate_params,
    encode_request,
    initialize_params,
    parse_message,
    resync_params,
    safe_error_message,
    subscribe_params,
)
from hypabolic_trajectory.ahp_client.transport import AhpTransport
from hypabolic_trajectory.streaming.apply import (
    apply_ahp_actions,
    apply_ahp_snapshot,
    create_stream,
    reset_stream,
)
from hypabolic_trajectory.streaming.types import (
    StreamCursor,
    StreamOptions,
    StreamResetRequest,
    StreamState,
    StreamUpdate,
)

AhpAuthCredentials = dict[str, str]  # {"token": "..."} only; never stored on stream
AhpAuthCallback = Callable[[dict[str, Any] | None], AhpAuthCredentials | None]

AhpClientEventKind = Literal[
    "stream-update",
    "auth-required",
    "auth-failed",
    "resync-required",
    "backpressure",
    "disconnected",
    "error",
    "ready",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class AhpClientEvent:
    kind: AhpClientEventKind
    update: StreamUpdate | None = None
    code: str | None = None
    message: str | None = None
    buffered: int | None = None


@dataclass(slots=True, kw_only=True)
class AhpClientOptions:
    chat_channel: str
    auth: AhpAuthCallback | None = None
    stream_options: StreamOptions | None = None
    auto_resync: bool = True
    max_buffered_actions: int = 256
    from_server_seq: int | None = None
    protocol_version: str = "0.7.0"


@dataclass
class AhpStreamClient:
    """Connect → auth (callback) → subscribe → feed core AHP stream apply.

    Cancellation leaves the last committed stream cursor valid. Auth material
    is never written into :class:`StreamUpdate` envelopes.
    """

    transport: AhpTransport
    options: AhpClientOptions
    on_event: Callable[[AhpClientEvent], None] = field(default=lambda _e: None)

    _state: StreamState = field(init=False, repr=False)
    _next_id: int = field(default=1, init=False, repr=False)
    _pending: dict[int, str] = field(default_factory=dict, init=False, repr=False)
    _action_buffer: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _paused: bool = field(default=False, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)
    _ready: bool = field(default=False, init=False, repr=False)
    _resync_inflight: bool = field(default=False, init=False, repr=False)
    _auth_token_held: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        opts = self.options.stream_options
        if opts is None:
            opts = StreamOptions(source="ahp", group_id=self.options.chat_channel)
        else:
            # Force AHP source; lock group to subscribed chat when unset.
            if opts.group_id is None:
                opts = StreamOptions(
                    source="ahp",
                    group_id=self.options.chat_channel,
                    delivery=opts.delivery,
                    include_provisional=opts.include_provisional,
                    require_complete_lines=opts.require_complete_lines,
                    finalize_on_close=opts.finalize_on_close,
                    reorder=opts.reorder,
                    reset_policy=opts.reset_policy,
                    max_pending_bytes=opts.max_pending_bytes,
                    max_line_bytes=opts.max_line_bytes,
                    normalize=opts.normalize,
                    ahp_protocol_version=opts.ahp_protocol_version
                    or self.options.protocol_version,
                )
            else:
                # Rebuild with source=ahp if needed
                source = opts.source
                if str(source) != "ahp" and getattr(source, "value", None) != "ahp":
                    opts = StreamOptions(
                        source="ahp",
                        group_id=opts.group_id,
                        delivery=opts.delivery,
                        include_provisional=opts.include_provisional,
                        require_complete_lines=opts.require_complete_lines,
                        finalize_on_close=opts.finalize_on_close,
                        reorder=opts.reorder,
                        reset_policy=opts.reset_policy,
                        max_pending_bytes=opts.max_pending_bytes,
                        max_line_bytes=opts.max_line_bytes,
                        normalize=opts.normalize,
                        ahp_protocol_version=opts.ahp_protocol_version
                        or self.options.protocol_version,
                    )
        self._state = create_stream(opts)
        self.transport.set_handler(self._on_frame)

    @property
    def cursor(self) -> StreamCursor:
        return self._state.cursor

    @property
    def state(self) -> StreamState:
        return self._state

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def set_paused_for_test(self, paused: bool) -> None:
        """Test helper: force backpressure pause without filling the buffer."""
        self._paused = paused

    def start(self) -> None:
        """Send initialize and begin the subscribe handshake."""
        if self._cancelled:
            self._emit_error(ERR_CANCELLED)
            return
        self._request("initialize", initialize_params(protocol_version=self.options.protocol_version))

    def cancel(self) -> None:
        """Stop receiving; last committed cursor remains valid."""
        self._cancelled = True
        self._action_buffer.clear()
        self._auth_token_held = None  # scrub transport auth
        try:
            self.transport.close()
        except Exception:
            pass
        self.on_event(
            AhpClientEvent(
                kind="disconnected",
                code=ERR_CANCELLED,
                message=safe_error_message(ERR_CANCELLED),
            )
        )

    def resume(self) -> None:
        """Clear backpressure pause and flush buffered actions."""
        self._paused = False
        self._flush_actions()

    def _request(self, method: str, params: dict[str, Any]) -> int:
        req_id = self._next_id
        self._next_id += 1
        self._pending[req_id] = method
        try:
            self.transport.send(encode_request(req_id, method, params))
        except Exception:
            self._emit_error(ERR_TRANSPORT)
        return req_id

    def _on_frame(self, raw: str) -> None:
        if self._cancelled:
            return
        try:
            msg = parse_message(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            self._emit_error(ERR_PROTOCOL)
            return

        if "method" in msg and "id" not in msg:
            self._handle_notification(msg)
            return
        if "id" in msg:
            self._handle_response(msg)
            return
        self._emit_error(ERR_PROTOCOL)

    def _handle_response(self, msg: dict[str, Any]) -> None:
        raw_id = msg.get("id")
        if not isinstance(raw_id, int):
            # Accept string numeric ids from hosts
            try:
                raw_id = int(raw_id)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                self._emit_error(ERR_PROTOCOL)
                return
        method = self._pending.pop(raw_id, None)
        if method is None:
            return

        if "error" in msg:
            err = msg.get("error") or {}
            err_msg = str(err.get("message", "")) if isinstance(err, dict) else ""
            lower = err_msg.lower()
            if method == "authenticate" or "auth" in lower:
                self._auth_token_held = None
                self.on_event(
                    AhpClientEvent(
                        kind="auth-failed",
                        code=ERR_AUTH_FAILED,
                        message=safe_error_message(ERR_AUTH_FAILED),
                    )
                )
                return
            if method == "initialize" and ("auth" in lower or "unauthor" in lower):
                self._begin_auth(challenge=None)
                return
            self._emit_error(ERR_PROTOCOL)
            return

        result = msg.get("result")
        if method == "initialize":
            # Host may require auth before subscribe.
            if isinstance(result, dict) and result.get("authRequired") is True:
                self._begin_auth(challenge=result.get("authChallenge") if isinstance(result.get("authChallenge"), dict) else None)
                return
            self._send_subscribe()
            return
        if method == "authenticate":
            self._auth_token_held = None  # scrub after use
            self._send_subscribe()
            return
        if method == "subscribe":
            self._ready = True
            self.on_event(AhpClientEvent(kind="ready"))
            if isinstance(result, dict):
                self._ingest_subscribe_result(result)
            return
        if method == "resync":
            # Keep resync_inflight true until reset + snapshot apply finish so
            # re-entrant action notifications drop mid-resync.
            if isinstance(result, dict):
                self._apply_resync_snapshot(result)
            else:
                self._resync_inflight = False
            return

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        if method in {"auth/required", "authRequired"}:
            self._begin_auth(challenge=params if params else None)
            return
        if method in {"action", "channel/action"}:
            if not self._notification_channel_ok(params):
                return
            envelope = params.get("envelope") if "envelope" in params else params
            if isinstance(envelope, dict) and "action" in envelope:
                self._buffer_action(envelope)
            return
        if method in {"snapshot", "channel/snapshot"}:
            if not self._notification_channel_ok(params):
                return
            self._apply_host_snapshot(params)
            return

    def _notification_channel_ok(self, params: dict[str, Any]) -> bool:
        """Ignore action/snapshot noise for a channel we did not subscribe to."""
        channel = params.get("channel")
        if not isinstance(channel, str):
            # Protocol requires channel on notifications; treat missing as foreign noise.
            return False
        return channel == self.options.chat_channel

    def _begin_auth(self, challenge: dict[str, Any] | None) -> None:
        self.on_event(
            AhpClientEvent(
                kind="auth-required",
                code=ERR_AUTH_REQUIRED,
                message=safe_error_message(ERR_AUTH_REQUIRED),
            )
        )
        if self.options.auth is None:
            self.on_event(
                AhpClientEvent(
                    kind="auth-failed",
                    code=ERR_AUTH_FAILED,
                    message=safe_error_message(ERR_AUTH_FAILED),
                )
            )
            return
        try:
            creds = self.options.auth(challenge)
        except Exception:
            self.on_event(
                AhpClientEvent(
                    kind="auth-failed",
                    code=ERR_AUTH_FAILED,
                    message=safe_error_message(ERR_AUTH_FAILED),
                )
            )
            return
        if not creds or not isinstance(creds.get("token"), str) or not creds["token"]:
            self.on_event(
                AhpClientEvent(
                    kind="auth-failed",
                    code=ERR_AUTH_FAILED,
                    message=safe_error_message(ERR_AUTH_FAILED),
                )
            )
            return
        token = creds["token"]
        self._auth_token_held = token  # ephemeral; scrubbed after authenticate response
        self._request("authenticate", authenticate_params(token))

    def _send_subscribe(self) -> None:
        self._request(
            "subscribe",
            subscribe_params(
                self.options.chat_channel,
                from_seq=self.options.from_server_seq,
            ),
        )

    def _ingest_subscribe_result(self, result: dict[str, Any]) -> None:
        # Prefer snapshot when present; else action batch.
        if "snapshot" in result:
            self._apply_host_snapshot(result)
        actions = result.get("actions")
        if isinstance(actions, list):
            for item in actions:
                if isinstance(item, dict):
                    self._buffer_action(item)
            self._flush_actions()

    def _buffer_action(self, envelope: dict[str, Any]) -> None:
        if self._paused or self._resync_inflight:
            # Drop while resyncing; host will redeliver after snapshot.
            if self._resync_inflight:
                return
        if len(self._action_buffer) >= self.options.max_buffered_actions:
            self._paused = True
            self.on_event(
                AhpClientEvent(
                    kind="backpressure",
                    code=ERR_BACKPRESSURE,
                    message=safe_error_message(ERR_BACKPRESSURE),
                    buffered=len(self._action_buffer),
                )
            )
            return
        self._action_buffer.append(envelope)
        if not self._paused:
            self._flush_actions()

    def _flush_actions(self) -> None:
        if self._cancelled or self._resync_inflight or not self._action_buffer:
            return
        batch = self._action_buffer
        self._action_buffer = []
        # JSONL batch of envelopes
        lines = [json.dumps(env, separators=(",", ":"), ensure_ascii=False) for env in batch]
        data = ("\n".join(lines) + "\n").encode("utf-8")
        self._state, update = apply_ahp_actions(self._state, data, cursor=None)
        self._emit_update(update)
        if update.kind == "reset-required" and (
            update.reset is not None and update.reset.reason == "sequence-gap"
        ):
            self._handle_sequence_gap(update)

    def _apply_host_snapshot(self, params: dict[str, Any]) -> None:
        snapshot_obj = params.get("snapshot", params.get("chat"))
        if snapshot_obj is None and "ahpProtocolVersion" in params:
            snapshot_obj = params
        if snapshot_obj is None:
            return
        if isinstance(snapshot_obj, dict) and "chat" not in snapshot_obj and "turns" in snapshot_obj:
            # bare ChatState
            material_obj = {
                "ahpProtocolVersion": params.get("ahpProtocolVersion")
                or self.options.protocol_version,
                "chat": snapshot_obj,
            }
        elif isinstance(snapshot_obj, dict):
            material_obj = snapshot_obj
            if "ahpProtocolVersion" not in material_obj:
                material_obj = {
                    **material_obj,
                    "ahpProtocolVersion": params.get("ahpProtocolVersion")
                    or self.options.protocol_version,
                }
        else:
            return
        revision = params.get("revision") or params.get("sourceRevision") or "host-snapshot"
        if not isinstance(revision, str):
            revision = str(revision)
        material = json.dumps(material_obj, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self._state, update = apply_ahp_snapshot(
            self._state, material, source_revision=revision, cursor=None
        )
        self._emit_update(update)

    def _handle_sequence_gap(self, update: StreamUpdate) -> None:
        self.on_event(
            AhpClientEvent(
                kind="resync-required",
                update=update,
                code=ERR_RESYNC_REQUIRED,
                message=safe_error_message(ERR_RESYNC_REQUIRED),
            )
        )
        if not self.options.auto_resync:
            return
        self._resync_inflight = True
        self._action_buffer.clear()
        self._request("resync", resync_params(self.options.chat_channel))

    def _apply_resync_snapshot(self, result: dict[str, Any]) -> None:
        prior = self._state.cursor
        self._state, _ = reset_stream(
            self._state,
            StreamResetRequest(
                reason="sequence-gap",
                prior_cursor=prior,
                source_revision=str(result.get("revision") or "resync"),
            ),
        )
        self._apply_host_snapshot(result)
        self._resync_inflight = False

    def _emit_update(self, update: StreamUpdate) -> None:
        # Privacy: never attach auth token to events.
        assert self._auth_token_held is None or update.kind in {
            "updated",
            "unchanged",
            "reset-required",
            "error",
        }
        self._assert_no_secrets_in_update(update)
        self.on_event(AhpClientEvent(kind="stream-update", update=update))

    def _assert_no_secrets_in_update(self, update: StreamUpdate) -> None:
        # Defensive: ensure auth token is not stringified into diagnostics.
        token = self._auth_token_held
        if not token:
            return
        blob = json.dumps(update.to_dict(), ensure_ascii=False)
        if token in blob:
            # Scrub and emit protocol error rather than leak.
            self._auth_token_held = None
            self._emit_error(ERR_PROTOCOL)

    def _emit_error(self, code: str) -> None:
        self.on_event(
            AhpClientEvent(
                kind="error",
                code=code,
                message=safe_error_message(code),
            )
        )
