"""Public stream types (cursor, options, update, state).

Wire-aligned with contracts/schemas/trajectory-stream-v1 and streaming-cursor-v1.
StreamState is runtime-local and is not a cross-language wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import NormalizeOptions

STREAM_SCHEMA_ID: str = "trajectory-stream-v1"
STREAM_CURSOR_VERSION: int = 1

StreamDelivery = Literal["both", "snapshot", "delta"]
StreamResetPolicy = Literal["return-reset-required", "auto-reset"]
StreamUpdateKind = Literal["updated", "unchanged", "reset-required", "error"]
StreamRecordStatus = Literal["provisional", "stable", "final"]
StreamResetReason = Literal[
    "source-truncated",
    "source-replaced",
    "source-compacted",
    "cursor-mismatch",
    "group-changed",
    "sequence-gap",
    "prefix-hash-mismatch",
    "manual",
]
StreamInputKind = Literal[
    "append-bytes",
    "snapshot-bytes",
    "ahp-actions",
    "ahp-snapshot",
    "hermes-export",
    "finish",
    "reset",
]
RemoveReason = Literal["retracted", "reset", "source-rewrite"]


@dataclass(frozen=True, slots=True, kw_only=True)
class BytePosition:
    kind: Literal["byte"] = "byte"
    next_byte_offset: int = 0
    pending_byte_length: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class AhpServerSeqPosition:
    kind: Literal["ahp-server-seq"] = "ahp-server-seq"
    next_server_seq: int
    last_server_seq: int
    next_byte_offset: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotRevisionPosition:
    kind: Literal["snapshot-revision"] = "snapshot-revision"
    revision: str
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class HermesRowPosition:
    kind: Literal["hermes-row"] = "hermes-row"
    database_generation: str
    last_row_id: int | None = None
    change_token: str | None = None


StreamPosition = (
    BytePosition | AhpServerSeqPosition | SnapshotRevisionPosition | HermesRowPosition
)


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamCursor:
    """Public serializable stream position checkpoint (cursor_version 1)."""

    source: str
    group_id: str
    generation: int = 0
    position: StreamPosition = field(default_factory=BytePosition)
    source_revision: str | None = None
    prefix_sha256: str | None = None
    cursor_version: int = STREAM_CURSOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        pos = self.position
        if isinstance(pos, BytePosition):
            position: dict[str, Any] = {
                "kind": "byte",
                "next_byte_offset": pos.next_byte_offset,
                "pending_byte_length": pos.pending_byte_length,
            }
        elif isinstance(pos, AhpServerSeqPosition):
            position = {
                "kind": "ahp-server-seq",
                "next_server_seq": pos.next_server_seq,
                "last_server_seq": pos.last_server_seq,
            }
            if pos.next_byte_offset is not None:
                position["next_byte_offset"] = pos.next_byte_offset
        elif isinstance(pos, SnapshotRevisionPosition):
            position = {"kind": "snapshot-revision", "revision": pos.revision}
            if pos.content_sha256 is not None:
                position["content_sha256"] = pos.content_sha256
        else:
            position = {
                "kind": "hermes-row",
                "database_generation": pos.database_generation,
            }
            if pos.last_row_id is not None:
                position["last_row_id"] = pos.last_row_id
            if pos.change_token is not None:
                position["change_token"] = pos.change_token
        return {
            "cursor_version": self.cursor_version,
            "source": self.source,
            "group_id": self.group_id,
            "generation": self.generation,
            "position": position,
            "source_revision": self.source_revision,
            "prefix_sha256": self.prefix_sha256,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamOptions:
    source: TrajectorySource | str
    group_id: str | None = None
    delivery: StreamDelivery = "both"
    include_provisional: bool = True
    require_complete_lines: bool = True
    finalize_on_close: bool = True
    reorder: Literal["reject"] = "reject"
    reset_policy: StreamResetPolicy = "return-reset-required"
    max_pending_bytes: int | None = None
    max_line_bytes: int | None = None
    normalize: NormalizeOptions = field(default_factory=NormalizeOptions)
    ahp_protocol_version: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamRevision:
    revision: int
    revision_id: str
    parent_revision_id: str | None
    complete: bool
    generation: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "revision_id": self.revision_id,
            "parent_revision_id": self.parent_revision_id,
            "complete": self.complete,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamDiagnostic:
    code: str
    message: str
    input_line: int | None = None
    record_index: int | None = None
    count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.input_line is not None:
            out["input_line"] = self.input_line
        if self.record_index is not None:
            out["record_index"] = self.record_index
        if self.count is not None:
            out["count"] = self.count
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamRecord:
    status: StreamRecordStatus
    record: dict[str, Any]
    provisional_id: str | None = None
    replaces_provisional_id: str | None = None
    finalizes_provisional_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "record": self.record,
        }
        if self.provisional_id is not None:
            out["provisional_id"] = self.provisional_id
        if self.replaces_provisional_id is not None:
            out["replaces_provisional_id"] = self.replaces_provisional_id
        if self.finalizes_provisional_id is not None:
            out["finalizes_provisional_id"] = self.finalizes_provisional_id
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamSnapshot:
    source: str
    group_id: str
    revision: StreamRevision
    records: tuple[StreamRecord, ...]
    diagnostics: tuple[StreamDiagnostic, ...]
    complete: bool
    schema_id: str = STREAM_SCHEMA_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "source": self.source,
            "group_id": self.group_id,
            "revision": self.revision.to_dict(),
            "records": [r.to_dict() for r in self.records],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamDeltaOperation:
    op: str
    # Payload fields vary by op; stored as a dict for wire fidelity.
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"op": self.op, **self.payload}
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamDelta:
    base_revision_id: str | None
    revision: StreamRevision
    operations: tuple[StreamDeltaOperation, ...]
    schema_id: str = STREAM_SCHEMA_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "base_revision_id": self.base_revision_id,
            "revision": self.revision.to_dict(),
            "operations": [op.to_dict() for op in self.operations],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamReset:
    reason: StreamResetReason
    prior_cursor: StreamCursor | None
    requires_snapshot: bool
    dropped_record_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "prior_cursor": (
                self.prior_cursor.to_dict() if self.prior_cursor is not None else None
            ),
            "requires_snapshot": self.requires_snapshot,
            "dropped_record_ids": list(self.dropped_record_ids),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamResetRequest:
    reason: StreamResetReason
    generation: int | None = None
    source_revision: str | None = None
    prior_cursor: StreamCursor | None = None
    material: bytes | None = None
    change_token: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamError:
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamProvisionalInfo:
    include: bool
    provisional_ids: tuple[str, ...] = ()
    finalized_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "include": self.include,
            "provisional_ids": list(self.provisional_ids),
            "finalized_ids": list(self.finalized_ids),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamConsumed:
    complete_records: int
    bytes: int
    first_source_position: int | None = None
    last_source_position: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "complete_records": self.complete_records,
            "bytes": self.bytes,
        }
        if self.first_source_position is not None:
            out["first_source_position"] = self.first_source_position
        if self.last_source_position is not None:
            out["last_source_position"] = self.last_source_position
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamUpdate:
    kind: StreamUpdateKind
    revision: StreamRevision
    cursor: StreamCursor
    snapshot: StreamSnapshot | None = None
    delta: StreamDelta | None = None
    diagnostics: tuple[StreamDiagnostic, ...] = ()
    provisional: StreamProvisionalInfo = field(
        default_factory=lambda: StreamProvisionalInfo(include=True)
    )
    consumed: StreamConsumed = field(
        default_factory=lambda: StreamConsumed(complete_records=0, bytes=0)
    )
    reset: StreamReset | None = None
    error: StreamError | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "revision": self.revision.to_dict(),
            "cursor": self.cursor.to_dict(),
            "snapshot": self.snapshot.to_dict() if self.snapshot is not None else None,
            "delta": self.delta.to_dict() if self.delta is not None else None,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "provisional": self.provisional.to_dict(),
            "consumed": self.consumed.to_dict(),
        }
        if self.reset is not None:
            out["reset"] = self.reset.to_dict()
        if self.error is not None:
            out["error"] = self.error.to_dict()
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamInput:
    kind: StreamInputKind
    data: bytes = b""
    source_revision: str | None = None
    cursor: StreamCursor | None = None
    reset: StreamResetRequest | None = None
    # Hermes provider: opaque change token for hermes-export apply.
    change_token: str | None = None
    database_generation: str | None = None


@dataclass(slots=True, kw_only=True)
class StreamState:
    """Runtime-local stream algorithm state (not a wire format)."""

    options: StreamOptions
    cursor: StreamCursor
    pending_bytes: bytearray = field(default_factory=bytearray)
    committed_prefix: bytearray = field(default_factory=bytearray)
    snapshot: StreamSnapshot | None = None
    generation: int = 0
    next_revision: int = 0
    finished: bool = False
    group_locked: bool = False
    # Last accepted append-bytes segment + pre-apply next_byte_offset.
    # True replay requires re-supply with that pre-apply cursor (not content alone).
    last_append_segment: bytes | None = None
    last_append_pre_offset: int | None = None
    # AHP stream state (LS-06 / LS-07). Not a wire format.
    ahp_chat_state: dict[str, Any] | None = None
    ahp_session: dict[str, Any] | None = None
    ahp_protocol_version: str | None = None
    ahp_last_server_seq: int | None = None
    ahp_target_channel: str | None = None
    ahp_last_snapshot_revision: str | None = None
    ahp_last_content_sha256: str | None = None
    # Last accepted action batch fingerprint for idempotent replay.
    last_ahp_actions_sha256: str | None = None
    last_ahp_actions_pre_seq: int | None = None
    # Hermes export stream state (LS-07h). Ordered row fingerprints of last export.
    hermes_row_fingerprints: tuple[str, ...] | None = None
    hermes_last_export_sha: str | None = None
