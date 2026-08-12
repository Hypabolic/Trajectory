"""Minimal AHP JSON-RPC framing used by the optional client (protocol pin 0.7.x).

This is a transport subset for Trajectory live-session streaming — not a full
AHP SDK. Wire shapes follow AHP channel routing conventions:
every command/notification params carry ``channel``.
"""

from __future__ import annotations

import json
from typing import Any

AHP_ROOT_CHANNEL = "ahp-root://"
PROTOCOL_VERSION = "0.7.0"
CLIENT_NAME = "hypabolic-trajectory-ahp"

# Fixed content-safe host/client error codes (never include tokens/paths).
ERR_AUTH_FAILED = "ahp_auth_failed"
ERR_AUTH_REQUIRED = "ahp_auth_required"
ERR_TRANSPORT = "ahp_transport_error"
ERR_PROTOCOL = "ahp_protocol_error"
ERR_BACKPRESSURE = "ahp_backpressure"
ERR_CANCELLED = "ahp_cancelled"
ERR_RESYNC_REQUIRED = "ahp_resync_required"


def encode_request(id_: int | str, method: str, params: dict[str, Any]) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": id_, "method": method, "params": params},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def encode_notification(method: str, params: dict[str, Any]) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def encode_result(id_: int | str, result: Any) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": id_, "result": result},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def encode_error(id_: int | str | None, code: int, message: str) -> str:
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
    }
    if id_ is not None:
        body["id"] = id_
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def parse_message(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(ERR_PROTOCOL)
    return value


def initialize_params(*, protocol_version: str = PROTOCOL_VERSION) -> dict[str, Any]:
    return {
        "channel": AHP_ROOT_CHANNEL,
        "protocolVersion": protocol_version,
        "clientInfo": {"name": CLIENT_NAME, "version": "0.1.2"},
    }


def authenticate_params(token: str) -> dict[str, Any]:
    # Token is transport-only; never copy into stream diagnostics.
    return {"channel": AHP_ROOT_CHANNEL, "token": token}


def subscribe_params(
    channel: str, *, from_seq: int | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"channel": channel}
    if from_seq is not None:
        params["fromSeq"] = from_seq
    return params


def resync_params(channel: str) -> dict[str, Any]:
    return {"channel": channel}


def safe_error_message(code: str) -> str:
    """Fixed messages with no secrets, paths, or transcript prose."""
    return {
        ERR_AUTH_FAILED: "AHP authentication failed.",
        ERR_AUTH_REQUIRED: "AHP authentication is required.",
        ERR_TRANSPORT: "AHP transport error.",
        ERR_PROTOCOL: "AHP protocol error.",
        ERR_BACKPRESSURE: "AHP client backpressure limit reached.",
        ERR_CANCELLED: "AHP client cancelled.",
        ERR_RESYNC_REQUIRED: "AHP sequence gap requires resync.",
    }.get(code, "AHP client error.")
