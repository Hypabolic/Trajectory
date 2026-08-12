"""Programmable fake AHP host for CI (no real network)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from hypabolic_trajectory.ahp_client.protocol import (
    AHP_ROOT_CHANNEL,
    encode_error,
    encode_notification,
    encode_result,
    parse_message,
)
from hypabolic_trajectory.ahp_client.transport import MemoryAhpTransport

ScriptStepKind = Literal[
    "require-auth",
    "accept-auth",
    "reject-auth",
    "subscribe-snapshot",
    "subscribe-actions",
    "push-action",
    "push-actions",
    "push-snapshot",
    "resync-snapshot",
    "close",
]


@dataclass
class FakeAhpHostScript:
    """Declarative host behaviour for a single chat channel."""

    require_auth: bool = False
    accept_token: str | None = "test-token"
    initial_snapshot: dict[str, Any] | None = None
    initial_revision: str = "rev-1"
    initial_actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FakeAhpHost:
    """Responds to initialize/authenticate/subscribe/resync over a memory transport."""

    transport: MemoryAhpTransport
    script: FakeAhpHostScript
    chat_channel: str
    _closed: bool = False
    auth_attempts: int = 0
    subscribe_count: int = 0
    resync_count: int = 0
    received_methods: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.transport.set_handler(self._on_frame)

    def close(self) -> None:
        self._closed = True
        self.transport.close()

    def push_action(self, envelope: dict[str, Any]) -> None:
        self.transport.send(
            encode_notification(
                "action",
                {"channel": self.chat_channel, "envelope": envelope},
            )
        )

    def push_actions(self, envelopes: list[dict[str, Any]]) -> None:
        for env in envelopes:
            self.push_action(env)

    def push_snapshot(
        self, snapshot: dict[str, Any], *, revision: str = "rev-push"
    ) -> None:
        self.transport.send(
            encode_notification(
                "snapshot",
                {
                    "channel": self.chat_channel,
                    "revision": revision,
                    "snapshot": snapshot,
                },
            )
        )

    def _on_frame(self, raw: str) -> None:
        if self._closed:
            return
        try:
            msg = parse_message(raw)
        except Exception:
            return
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if not isinstance(method, str) or req_id is None:
            return
        self.received_methods.append(method)

        if method == "initialize":
            result: dict[str, Any] = {
                "channel": AHP_ROOT_CHANNEL,
                "protocolVersion": "0.7.0",
            }
            if self.script.require_auth:
                result["authRequired"] = True
            self.transport.send(encode_result(req_id, result))
            return

        if method == "authenticate":
            self.auth_attempts += 1
            token = params.get("token")
            if (
                self.script.accept_token is not None
                and token == self.script.accept_token
            ):
                self.transport.send(encode_result(req_id, {"ok": True}))
            else:
                self.transport.send(
                    encode_error(req_id, -32001, "authentication failed")
                )
            return

        if method == "subscribe":
            self.subscribe_count += 1
            channel = params.get("channel", self.chat_channel)
            result = {"channel": channel}
            if self.script.initial_snapshot is not None:
                result["revision"] = self.script.initial_revision
                result["snapshot"] = self.script.initial_snapshot
            if self.script.initial_actions:
                result["actions"] = list(self.script.initial_actions)
            self.transport.send(encode_result(req_id, result))
            return

        if method == "resync":
            self.resync_count += 1
            snap = self.script.initial_snapshot or {
                "ahpProtocolVersion": "0.7.0",
                "chat": {
                    "id": self.chat_channel,
                    "turns": [],
                    "activeTurn": None,
                },
            }
            self.transport.send(
                encode_result(
                    req_id,
                    {
                        "channel": self.chat_channel,
                        "revision": f"resync-{self.resync_count}",
                        "snapshot": snap,
                    },
                )
            )
            return

        self.transport.send(encode_error(req_id, -32601, "method not found"))
