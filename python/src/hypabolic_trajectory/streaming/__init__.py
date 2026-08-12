"""Live session streaming core (LS-03 / LS-04).

Pure algorithm: no filesystem watchers, network, or SQLite. Callers own I/O
and scheduling. Public surface re-exported from the package root.
"""

from __future__ import annotations

from hypabolic_trajectory.streaming.apply import (
    TrajectoryStream,
    apply_ahp_actions,
    apply_ahp_snapshot,
    apply_append,
    apply_snapshot,
    apply_stream,
    create_stream,
    finish_stream,
    reset_stream,
)
from hypabolic_trajectory.streaming.delta import (
    apply_delta_to_snapshot,
    diagnostic_key,
    diff_snapshots,
    match_key,
)
from hypabolic_trajectory.streaming.framing import split_complete_lines
from hypabolic_trajectory.streaming.types import (
    STREAM_SCHEMA_ID,
    AhpServerSeqPosition,
    BytePosition,
    HermesRowPosition,
    SnapshotRevisionPosition,
    StreamConsumed,
    StreamCursor,
    StreamDelta,
    StreamDeltaOperation,
    StreamDiagnostic,
    StreamError,
    StreamInput,
    StreamOptions,
    StreamProvisionalInfo,
    StreamRecord,
    StreamReset,
    StreamResetRequest,
    StreamRevision,
    StreamSnapshot,
    StreamState,
    StreamUpdate,
)

__all__ = [
    "STREAM_SCHEMA_ID",
    "AhpServerSeqPosition",
    "BytePosition",
    "HermesRowPosition",
    "SnapshotRevisionPosition",
    "StreamConsumed",
    "StreamCursor",
    "StreamDelta",
    "StreamDeltaOperation",
    "StreamDiagnostic",
    "StreamError",
    "StreamInput",
    "StreamOptions",
    "StreamProvisionalInfo",
    "StreamRecord",
    "StreamReset",
    "StreamResetRequest",
    "StreamRevision",
    "StreamSnapshot",
    "StreamState",
    "StreamUpdate",
    "TrajectoryStream",
    "apply_ahp_actions",
    "apply_ahp_snapshot",
    "apply_append",
    "apply_delta_to_snapshot",
    "apply_snapshot",
    "apply_stream",
    "create_stream",
    "diagnostic_key",
    "diff_snapshots",
    "finish_stream",
    "match_key",
    "reset_stream",
    "split_complete_lines",
]
