"""Stream create / apply_snapshot / apply shell (LS-03 / LS-04).

Pure library: callers supply bytes; core owns framing, normalize, diff, cursor.
"""

from __future__ import annotations

from typing import Any

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import NormalizeRequest, SourceContext
from hypabolic_trajectory.errors import (
    FATAL_SOURCE_GROUP_CONFLICT,
    TrajectoryError,
)
from hypabolic_trajectory.identity import sha256_hex
from hypabolic_trajectory.normalize.core import normalize_to_ir, resolve_source
from hypabolic_trajectory.project.core import project_hypabolic
from hypabolic_trajectory.streaming.delta import diff_snapshots
from hypabolic_trajectory.streaming.framing import append_framed, split_complete_lines
from hypabolic_trajectory.streaming.types import (
    STREAM_SCHEMA_ID,
    BytePosition,
    StreamConsumed,
    StreamCursor,
    StreamDelta,
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

# Content-safe fixed messages (no paths, secrets, group ids, raw lines).
_MSG_BUFFER_LIMIT = "Stream buffer limit exceeded."
_MSG_CURSOR_CONFLICT = "Supplied stream cursor does not match stream state."
_MSG_GROUP_CHANGED = "Source group changed relative to the active stream."
_MSG_SOURCE_TRUNCATED = "Source material is shorter than the committed cursor."
_MSG_PREFIX_MISMATCH = "Committed prefix hash does not match supplied material."
_MSG_UNSUPPORTED_INPUT = "Stream input kind is not supported for this source."
_MSG_AHP_UNSUPPORTED = "AHP stream apply is not available in this slice."
_MSG_HERMES_UNSUPPORTED = "Hermes export stream apply requires an optional provider."
_MSG_FINISHED = "Stream is already finished."
_MSG_INVALID_SOURCE = "Unknown or invalid stream source."

_STREAM_BUFFER_LIMIT = "stream_buffer_limit"
_STREAM_CURSOR_CONFLICT = "stream_cursor_conflict"
_STREAM_SOURCE_RESET = "stream_source_reset"
_STREAM_RESYNC_REQUIRED = "stream_resync_required"


def create_stream(options: StreamOptions) -> StreamState:
    """Create a new pure stream state (generation 0, empty cursor)."""
    source = resolve_source(options.source)
    group_id = options.group_id if options.group_id else "default"
    cursor = StreamCursor(
        source=source.value,
        group_id=group_id,
        generation=0,
        position=BytePosition(next_byte_offset=0, pending_byte_length=0),
        source_revision=None,
        prefix_sha256=None,
    )
    return StreamState(options=options, cursor=cursor)


def apply_stream(state: StreamState, input: StreamInput) -> tuple[StreamState, StreamUpdate]:
    """Pure apply(state, input) → (state, update). Failed apply leaves state unchanged."""
    if input.kind == "snapshot-bytes":
        return apply_snapshot(
            state,
            input.data,
            source_revision=input.source_revision or "",
            cursor=input.cursor,
        )
    if input.kind == "append-bytes":
        return apply_append(
            state,
            input.data,
            cursor=input.cursor,
            source_revision=input.source_revision,
        )
    if input.kind == "finish":
        return finish_stream(state)
    if input.kind == "reset":
        if input.reset is None:
            return state, _error_update(
                state,
                code="invalid_input",
                message="reset input requires a StreamResetRequest.",
            )
        return reset_stream(state, input.reset)
    if input.kind in {"ahp-actions", "ahp-snapshot"}:
        return state, _error_update(
            state, code=_STREAM_RESYNC_REQUIRED, message=_MSG_AHP_UNSUPPORTED
        )
    if input.kind == "hermes-export":
        return state, _error_update(
            state, code=_STREAM_RESYNC_REQUIRED, message=_MSG_HERMES_UNSUPPORTED
        )
    return state, _error_update(
        state, code="invalid_input", message=_MSG_UNSUPPORTED_INPUT
    )


def apply_snapshot(
    state: StreamState,
    material: bytes,
    *,
    source_revision: str,
    cursor: StreamCursor | None = None,
) -> tuple[StreamState, StreamUpdate]:
    """Full re-normalize of supplied snapshot material → StreamUpdate."""
    if type(material) is not bytes:
        raise TypeError("material must be bytes")
    if state.finished:
        return state, _error_update(state, code="invalid_input", message=_MSG_FINISHED)

    conflict = _cursor_conflict(state, cursor)
    if conflict is not None:
        return state, conflict

    opts = state.options
    limit_err = _validate_buffer_limits(opts)
    if limit_err is not None:
        return state, _error_update(state, code="invalid_input", message=limit_err)

    # Framing: only complete lines are committed for ordinary snapshot apply.
    if opts.require_complete_lines:
        committed, pending = split_complete_lines(material)
    else:
        committed, pending = material, b""

    if len(committed) > _INT64_MAX or len(pending) > _INT64_MAX:
        return state, _error_update(
            state,
            code="invalid_input",
            message="Stream material length exceeds non-negative int64 domain.",
        )

    if opts.max_pending_bytes is not None and len(pending) > opts.max_pending_bytes:
        return state, _error_update(
            state, code=_STREAM_BUFFER_LIMIT, message=_MSG_BUFFER_LIMIT
        )
    if opts.max_line_bytes is not None and (
        _any_line_too_long(committed, opts.max_line_bytes)
        or len(pending) > opts.max_line_bytes
    ):
        return state, _error_update(
            state, code=_STREAM_BUFFER_LIMIT, message=_MSG_BUFFER_LIMIT
        )

    # Normalize first so group-changed is reported ahead of prefix-hash checks
    # (a foreign-session snapshot typically fails both).
    built = _build_records_from_prefix(state, committed)
    if isinstance(built, StreamUpdate):
        return state, built
    records, diagnostics, group_id = built

    # Truncation against prior byte cursor (same group). Mid-file rewrites of
    # equal-or-longer snapshots are valid full re-normalizes (delta shows
    # upserts/removes). Prefix-hash divergence on append is handled in LS-05.
    prior_pos = state.cursor.position
    if (
        isinstance(prior_pos, BytePosition)
        and state.snapshot is not None
        and len(committed) < prior_pos.next_byte_offset
    ):
        return state, _reset_required(
            state, reason="source-truncated", diagnostic_code=_STREAM_SOURCE_RESET
        )

    empty_sha = sha256_hex(b"")
    effective_prefix_sha = sha256_hex(committed) if committed else empty_sha

    # Idempotent replay of same source_revision + same prefix.
    if (
        state.snapshot is not None
        and state.cursor.source_revision == source_revision
        and state.cursor.prefix_sha256 == effective_prefix_sha
        and bytes(state.pending_bytes) == pending
    ):
        return state, _unchanged_update(state)

    if not opts.include_provisional:
        records = tuple(r for r in records if r.status != "provisional")

    new_state = _clone_state(state)
    new_state.group_locked = True
    generation = new_state.generation
    parent_revision_id = (
        new_state.snapshot.revision.revision_id
        if new_state.snapshot is not None
        else None
    )
    revision_num = new_state.next_revision
    revision_id = _revision_id(
        generation=generation,
        revision=revision_num,
        source=new_state.cursor.source,
        group_id=group_id,
        prefix_sha=effective_prefix_sha,
        record_ids=tuple(r.record["id"] for r in records),
    )
    revision = StreamRevision(
        revision=revision_num,
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        complete=False,
        generation=generation,
    )
    snapshot = StreamSnapshot(
        source=new_state.cursor.source,
        group_id=group_id,
        revision=revision,
        records=records,
        diagnostics=diagnostics,
        complete=False,
    )
    delta = diff_snapshots(new_state.snapshot, snapshot, revision=revision)
    delta = _filter_delivery_delta(delta, opts.delivery)
    out_snapshot, out_delta = _apply_delivery(
        snapshot, delta, opts.delivery
    )

    cursor = StreamCursor(
        source=new_state.cursor.source,
        group_id=group_id,
        generation=generation,
        position=BytePosition(
            next_byte_offset=len(committed),
            pending_byte_length=len(pending),
        ),
        source_revision=source_revision,
        prefix_sha256=effective_prefix_sha,
    )
    consumed = StreamConsumed(
        complete_records=len(records),
        bytes=len(committed),
        first_source_position=0 if committed else None,
        last_source_position=(len(committed) - 1) if committed else None,
    )
    provisional_ids = tuple(
        r.provisional_id for r in records if r.provisional_id is not None
    )
    update = StreamUpdate(
        kind="updated",
        revision=revision,
        cursor=cursor,
        snapshot=out_snapshot,
        delta=out_delta,
        diagnostics=diagnostics,
        provisional=StreamProvisionalInfo(
            include=opts.include_provisional,
            provisional_ids=provisional_ids,
            finalized_ids=(),
        ),
        consumed=consumed,
    )

    new_state.cursor = cursor
    new_state.snapshot = snapshot
    new_state.pending_bytes = bytearray(pending)
    new_state.committed_prefix = bytearray(committed)
    new_state.next_revision = revision_num + 1
    return new_state, update


def apply_append(
    state: StreamState,
    segment: bytes,
    *,
    cursor: StreamCursor | None = None,
    source_revision: str | None = None,
) -> tuple[StreamState, StreamUpdate]:
    """Append complete-line segment; re-normalize full committed prefix (oracle path)."""
    if type(segment) is not bytes:
        raise TypeError("segment must be bytes")
    if state.finished:
        return state, _error_update(state, code="invalid_input", message=_MSG_FINISHED)

    conflict = _cursor_conflict(state, cursor)
    if conflict is not None:
        return state, conflict

    opts = state.options
    limit_err = _validate_buffer_limits(opts)
    if limit_err is not None:
        return state, _error_update(state, code="invalid_input", message=limit_err)

    try:
        complete, pending = append_framed(
            bytes(state.pending_bytes),
            segment,
            max_pending_bytes=opts.max_pending_bytes,
            max_line_bytes=opts.max_line_bytes,
        )
    except ValueError:
        return state, _error_update(
            state, code=_STREAM_BUFFER_LIMIT, message=_MSG_BUFFER_LIMIT
        )

    if not complete and pending == bytes(state.pending_bytes):
        # No new complete lines and pending unchanged beyond what we already held
        # after merging segment that was entirely incomplete.
        if pending == bytes(state.pending_bytes) + segment or (
            not segment and pending == bytes(state.pending_bytes)
        ):
            # Pending advanced without complete lines — update pending only if changed.
            if pending == bytes(state.pending_bytes):
                return state, _unchanged_update(state)
            new_state = _clone_state(state)
            new_state.pending_bytes = bytearray(pending)
            pos = new_state.cursor.position
            if isinstance(pos, BytePosition):
                new_state.cursor = StreamCursor(
                    source=new_state.cursor.source,
                    group_id=new_state.cursor.group_id,
                    generation=new_state.cursor.generation,
                    position=BytePosition(
                        next_byte_offset=pos.next_byte_offset,
                        pending_byte_length=len(pending),
                    ),
                    source_revision=new_state.cursor.source_revision,
                    prefix_sha256=new_state.cursor.prefix_sha256,
                )
            # Visible snapshot unchanged → unchanged kind still correct? Spec:
            # accepted apply that changes only pending may be unchanged for
            # visible records. Return unchanged with updated cursor pending length.
            return new_state, _unchanged_update(new_state)

    new_prefix = bytes(state.committed_prefix) + complete
    rev = source_revision if source_revision is not None else (
        state.cursor.source_revision or ""
    )
    # Build a temporary state with empty pending then snapshot-apply full prefix.
    # Snapshot path re-splits lines; feed only complete prefix + track pending.
    snap_state = _clone_state(state)
    snap_state.pending_bytes = bytearray()
    # Use require_complete_lines path on the full prefix (no pending inside material).
    material = new_prefix  # all complete lines already
    new_state, update = apply_snapshot(
        snap_state,
        material,
        source_revision=rev,
        cursor=None,
    )
    if update.kind == "updated" or update.kind == "unchanged":
        # Restore pending from append framing.
        if update.kind == "updated":
            # Re-bind pending onto the updated state.
            pos = new_state.cursor.position
            if isinstance(pos, BytePosition):
                new_state.cursor = StreamCursor(
                    source=new_state.cursor.source,
                    group_id=new_state.cursor.group_id,
                    generation=new_state.cursor.generation,
                    position=BytePosition(
                        next_byte_offset=pos.next_byte_offset,
                        pending_byte_length=len(pending),
                    ),
                    source_revision=new_state.cursor.source_revision,
                    prefix_sha256=new_state.cursor.prefix_sha256,
                )
            new_state.pending_bytes = bytearray(pending)
            if update.snapshot is not None or update.delta is not None:
                # Rebuild update cursor to include pending length.
                update = StreamUpdate(
                    kind=update.kind,
                    revision=update.revision,
                    cursor=new_state.cursor,
                    snapshot=update.snapshot,
                    delta=update.delta,
                    diagnostics=update.diagnostics,
                    provisional=update.provisional,
                    consumed=update.consumed,
                    reset=update.reset,
                    error=update.error,
                )
        else:
            new_state.pending_bytes = bytearray(pending)
    return new_state, update


def finish_stream(state: StreamState) -> tuple[StreamState, StreamUpdate]:
    """End-of-stream: optionally commit final unterminated line; finalize records."""
    if state.finished:
        return state, _unchanged_update(state)

    opts = state.options
    material = bytes(state.committed_prefix)
    pending = bytes(state.pending_bytes)
    if pending and not pending.isspace():
        # Commit one final non-empty unterminated line once.
        material = material + pending + b"\n"
        pending = b""

    new_state, update = apply_snapshot(
        state,
        material if not opts.require_complete_lines else material,
        source_revision=state.cursor.source_revision or "finish",
        cursor=None,
    )
    # Force complete-line path: material ends with LF after we appended one.
    if update.kind not in {"updated", "unchanged"}:
        return new_state, update

    # Finalize records when configured.
    base_snapshot = new_state.snapshot
    if base_snapshot is None:
        new_state.finished = True
        return new_state, update

    if opts.finalize_on_close:
        finalized: list[StreamRecord] = []
        for rec in base_snapshot.records:
            if rec.status != "final":
                finalized.append(
                    StreamRecord(
                        status="final",
                        record=rec.record,
                        provisional_id=rec.provisional_id,
                        replaces_provisional_id=rec.replaces_provisional_id,
                        finalizes_provisional_id=rec.finalizes_provisional_id
                        or rec.provisional_id,
                    )
                )
            else:
                finalized.append(rec)
    else:
        finalized = list(base_snapshot.records)

    generation = new_state.generation
    parent_revision_id = base_snapshot.revision.revision_id
    revision_num = new_state.next_revision
    prefix_sha = new_state.cursor.prefix_sha256 or sha256_hex(b"")
    revision_id = _revision_id(
        generation=generation,
        revision=revision_num,
        source=new_state.cursor.source,
        group_id=base_snapshot.group_id,
        prefix_sha=prefix_sha,
        record_ids=tuple(r.record["id"] for r in finalized),
    )
    revision = StreamRevision(
        revision=revision_num,
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        complete=True,
        generation=generation,
    )
    snapshot = StreamSnapshot(
        source=base_snapshot.source,
        group_id=base_snapshot.group_id,
        revision=revision,
        records=tuple(finalized),
        diagnostics=base_snapshot.diagnostics,
        complete=True,
    )
    delta = diff_snapshots(base_snapshot, snapshot, revision=revision)
    out_snapshot, out_delta = _apply_delivery(
        snapshot, delta, opts.delivery
    )
    cursor = StreamCursor(
        source=new_state.cursor.source,
        group_id=new_state.cursor.group_id,
        generation=generation,
        position=BytePosition(
            next_byte_offset=len(bytes(new_state.committed_prefix))
            if not pending
            else len(material.rstrip(b"\n")) + (1 if material.endswith(b"\n") else 0),
            pending_byte_length=0,
        ),
        source_revision=new_state.cursor.source_revision,
        prefix_sha256=prefix_sha,
    )
    # Simpler: after finish, committed is material without forcing odd math.
    committed = material if material.endswith(b"\n") or not material else material + b"\n"
    # Re-snapshot for clean committed state
    new_state2 = _clone_state(new_state)
    new_state2.finished = True
    new_state2.pending_bytes = bytearray()
    new_state2.committed_prefix = bytearray(
        material if not pending else material
    )
    new_state2.snapshot = snapshot
    new_state2.cursor = StreamCursor(
        source=new_state.cursor.source,
        group_id=snapshot.group_id,
        generation=generation,
        position=BytePosition(
            next_byte_offset=len(new_state2.committed_prefix),
            pending_byte_length=0,
        ),
        source_revision=new_state.cursor.source_revision,
        prefix_sha256=sha256_hex(bytes(new_state2.committed_prefix))
        if new_state2.committed_prefix
        else sha256_hex(b""),
    )
    new_state2.next_revision = revision_num + 1
    update = StreamUpdate(
        kind="updated",
        revision=revision,
        cursor=new_state2.cursor,
        snapshot=out_snapshot,
        delta=out_delta,
        diagnostics=snapshot.diagnostics,
        provisional=StreamProvisionalInfo(
            include=opts.include_provisional,
            provisional_ids=(),
            finalized_ids=tuple(
                r.finalizes_provisional_id
                for r in finalized
                if r.finalizes_provisional_id
            ),
        ),
        consumed=StreamConsumed(
            complete_records=len(finalized),
            bytes=len(new_state2.committed_prefix),
        ),
    )
    return new_state2, update


def reset_stream(
    state: StreamState, request: StreamResetRequest
) -> tuple[StreamState, StreamUpdate]:
    """Install a new generation after reset-required or manual restart."""
    generation = (
        request.generation
        if request.generation is not None
        else state.generation + 1
    )
    new_state = _clone_state(state)
    new_state.generation = generation
    new_state.next_revision = 0
    new_state.finished = False
    new_state.pending_bytes = bytearray()
    new_state.committed_prefix = bytearray()
    new_state.snapshot = None
    new_state.group_locked = False
    group_id = state.options.group_id or state.cursor.group_id
    new_state.cursor = StreamCursor(
        source=state.cursor.source,
        group_id=group_id,
        generation=generation,
        position=BytePosition(next_byte_offset=0, pending_byte_length=0),
        source_revision=request.source_revision,
        prefix_sha256=None,
    )
    dropped = tuple(
        r.record["id"] for r in (state.snapshot.records if state.snapshot else ())
    )
    reset_meta = StreamReset(
        reason=request.reason,
        prior_cursor=request.prior_cursor or state.cursor,
        requires_snapshot=request.material is None,
        dropped_record_ids=dropped,
    )
    if request.material is not None:
        new_state, update = apply_snapshot(
            new_state,
            request.material,
            source_revision=request.source_revision or "",
            cursor=None,
        )
        if update.kind not in {"updated", "unchanged"}:
            return new_state, update
        # Merge reset envelope onto successful post-reset snapshot update.
        delta = update.delta
        if delta is not None:
            from hypabolic_trajectory.streaming.types import StreamDeltaOperation

            reset_op = StreamDeltaOperation(
                op="reset",
                payload={"reset": reset_meta.to_dict()},
            )
            delta = StreamDelta(
                base_revision_id=delta.base_revision_id,
                revision=delta.revision,
                operations=(reset_op, *delta.operations),
            )
        update = StreamUpdate(
            kind=update.kind,
            revision=update.revision,
            cursor=update.cursor,
            snapshot=update.snapshot,
            delta=delta,
            diagnostics=update.diagnostics,
            provisional=update.provisional,
            consumed=update.consumed,
            reset=reset_meta,
            error=update.error,
        )
        return new_state, update
    revision = StreamRevision(
        revision=0,
        revision_id=_revision_id(
            generation=generation,
            revision=0,
            source=new_state.cursor.source,
            group_id=group_id,
            prefix_sha=sha256_hex(b""),
            record_ids=(),
        ),
        parent_revision_id=None,
        complete=False,
        generation=generation,
    )
    # Empty reset with no material → updated empty snapshot of new generation.
    snapshot = StreamSnapshot(
        source=new_state.cursor.source,
        group_id=group_id,
        revision=revision,
        records=(),
        diagnostics=(),
        complete=False,
    )
    delta = diff_snapshots(None, snapshot, revision=revision)
    # Insert leading reset op.
    from hypabolic_trajectory.streaming.types import StreamDeltaOperation

    reset_op = StreamDeltaOperation(
        op="reset",
        payload={"reset": reset_meta.to_dict()},
    )
    delta = StreamDelta(
        base_revision_id=None,
        revision=revision,
        operations=(reset_op, *delta.operations),
    )
    out_snapshot, out_delta = _apply_delivery(
        snapshot, delta, state.options.delivery
    )
    new_state.snapshot = snapshot
    new_state.next_revision = 1
    new_state.cursor = StreamCursor(
        source=new_state.cursor.source,
        group_id=group_id,
        generation=generation,
        position=BytePosition(next_byte_offset=0, pending_byte_length=0),
        source_revision=request.source_revision,
        prefix_sha256=sha256_hex(b""),
    )
    update = StreamUpdate(
        kind="updated",
        revision=revision,
        cursor=new_state.cursor,
        snapshot=out_snapshot,
        delta=out_delta,
        diagnostics=(),
        provisional=StreamProvisionalInfo(include=state.options.include_provisional),
        consumed=StreamConsumed(complete_records=0, bytes=0),
        reset=reset_meta,
    )
    return new_state, update


class TrajectoryStream:
    """Mutable façade over StreamState."""

    def __init__(self, state: StreamState) -> None:
        self._state = state

    @classmethod
    def create(cls, options: StreamOptions) -> TrajectoryStream:
        return cls(create_stream(options))

    @property
    def cursor(self) -> StreamCursor:
        return self._state.cursor

    @property
    def state(self) -> StreamState:
        return self._state

    def apply_snapshot(
        self,
        data: bytes,
        *,
        source_revision: str,
        cursor: StreamCursor | None = None,
    ) -> StreamUpdate:
        self._state, update = apply_snapshot(
            self._state, data, source_revision=source_revision, cursor=cursor
        )
        return update

    def apply_append(
        self,
        data: bytes,
        *,
        cursor: StreamCursor | None = None,
        source_revision: str | None = None,
    ) -> StreamUpdate:
        self._state, update = apply_append(
            self._state, data, cursor=cursor, source_revision=source_revision
        )
        return update

    def finish(self) -> StreamUpdate:
        self._state, update = finish_stream(self._state)
        return update

    def reset(self, request: StreamResetRequest) -> StreamUpdate:
        self._state, update = reset_stream(self._state, request)
        return update

    def apply(self, input: StreamInput) -> StreamUpdate:
        self._state, update = apply_stream(self._state, input)
        return update


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _clone_state(state: StreamState) -> StreamState:
    return StreamState(
        options=state.options,
        cursor=state.cursor,
        pending_bytes=bytearray(state.pending_bytes),
        committed_prefix=bytearray(state.committed_prefix),
        snapshot=state.snapshot,
        generation=state.generation,
        next_revision=state.next_revision,
        finished=state.finished,
        group_locked=state.group_locked,
    )


_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_MSG_BUFFER_LIMIT_DOMAIN = "Stream buffer limits must be non-negative int64 values."


def _is_non_negative_int64(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _INT64_MAX


def _validate_buffer_limits(opts: StreamOptions) -> str | None:
    """Return fixed invalid_input message when max_* limits are out of domain."""
    for value in (opts.max_pending_bytes, opts.max_line_bytes):
        if value is None:
            continue
        if not _is_non_negative_int64(value):
            return _MSG_BUFFER_LIMIT_DOMAIN
    return None


def _cursor_conflict(
    state: StreamState, cursor: StreamCursor | None
) -> StreamUpdate | None:
    if cursor is None:
        return None
    current = state.cursor
    if cursor.source != current.source:
        return _reset_required(
            state, reason="cursor-mismatch", diagnostic_code=_STREAM_CURSOR_CONFLICT
        )
    if cursor.generation != current.generation:
        return _reset_required(
            state, reason="cursor-mismatch", diagnostic_code=_STREAM_CURSOR_CONFLICT
        )
    if cursor.group_id != current.group_id and state.group_locked:
        return _reset_required(
            state, reason="group-changed", diagnostic_code=_STREAM_CURSOR_CONFLICT
        )
    # Byte position: supplied next_byte_offset must match committed cursor.
    if isinstance(cursor.position, BytePosition) and isinstance(
        current.position, BytePosition
    ):
        # Domain: non-negative int64 byte positions (streaming-cursor-v1).
        if (
            not _is_non_negative_int64(cursor.position.next_byte_offset)
            or not _is_non_negative_int64(cursor.position.pending_byte_length)
        ):
            return _error_update(
                state,
                code="invalid_input",
                message="Stream cursor byte positions must be non-negative int64 values.",
            )
        if cursor.position.next_byte_offset != current.position.next_byte_offset:
            return _reset_required(
                state,
                reason="cursor-mismatch",
                diagnostic_code=_STREAM_CURSOR_CONFLICT,
            )
    return None


def _build_records_from_prefix(
    state: StreamState, committed: bytes
) -> tuple[tuple[StreamRecord, ...], tuple[StreamDiagnostic, ...], str] | StreamUpdate:
    """Normalize committed prefix into stream records. Empty prefix → empty records."""
    source = resolve_source(state.options.source)
    group_hint = state.options.group_id
    if state.group_locked:
        group_hint = state.cursor.group_id

    if not committed:
        group_id = group_hint or state.cursor.group_id
        return (), (), group_id

    try:
        ir = normalize_to_ir(
            NormalizeRequest(
                source=source,
                transcript=committed,
                source_context=SourceContext(
                    group_id=group_hint,
                    base_byte_offset=0,
                    partial=True,
                ),
                options=state.options.normalize,
            )
        )
    except TrajectoryError as err:
        if err.code == FATAL_SOURCE_GROUP_CONFLICT:
            return _reset_required(
                state,
                reason="group-changed",
                diagnostic_code=_STREAM_SOURCE_RESET,
            )
        return _error_update(state, code=err.code, message=err.message)

    hyp = project_hypabolic(ir)
    raw_records = hyp.get("records") or []
    records = tuple(
        StreamRecord(status="stable", record=dict(r))
        for r in raw_records
        if isinstance(r, dict)
    )
    diagnostics = tuple(
        StreamDiagnostic(
            code=d.code,
            message=d.message,
            input_line=d.input_line,
            record_index=d.record_index,
            count=d.count,
        )
        for d in ir.diagnostics
    )
    return records, diagnostics, ir.group_id


def _revision_id(
    *,
    generation: int,
    revision: int,
    source: str,
    group_id: str,
    prefix_sha: str,
    record_ids: tuple[str, ...],
) -> str:
    # Deterministic, content-safe: no raw transcript bytes.
    payload = (
        f"{generation}|{revision}|{source}|{group_id}|{prefix_sha}|"
        + ",".join(record_ids)
    )
    return sha256_hex(payload)


def _any_line_too_long(data: bytes, max_line_bytes: int) -> bool:
    start = 0
    for i, b in enumerate(data):
        if b == 0x0A:
            if i - start + 1 > max_line_bytes:
                return True
            start = i + 1
    return False


def _apply_delivery(
    snapshot: StreamSnapshot,
    delta: StreamDelta,
    delivery: str,
) -> tuple[StreamSnapshot | None, StreamDelta | None]:
    if delivery == "both":
        return snapshot, delta
    if delivery == "snapshot":
        return snapshot, None
    if delivery == "delta":
        return None, delta
    return snapshot, delta


def _filter_delivery_delta(delta: StreamDelta, delivery: str) -> StreamDelta:
    return delta


def _unchanged_update(state: StreamState) -> StreamUpdate:
    rev = (
        state.snapshot.revision
        if state.snapshot is not None
        else StreamRevision(
            revision=max(0, state.next_revision - 1),
            revision_id="unchanged",
            parent_revision_id=None,
            complete=state.finished,
            generation=state.generation,
        )
    )
    return StreamUpdate(
        kind="unchanged",
        revision=rev,
        cursor=state.cursor,
        snapshot=None,
        delta=None,
        diagnostics=(),
        provisional=StreamProvisionalInfo(include=state.options.include_provisional),
        consumed=StreamConsumed(complete_records=0, bytes=0),
    )


def _error_update(state: StreamState, *, code: str, message: str) -> StreamUpdate:
    rev = (
        state.snapshot.revision
        if state.snapshot is not None
        else StreamRevision(
            revision=0,
            revision_id="error",
            parent_revision_id=None,
            complete=False,
            generation=state.generation,
        )
    )
    return StreamUpdate(
        kind="error",
        revision=rev,
        cursor=state.cursor,
        snapshot=None,
        delta=None,
        diagnostics=(),
        provisional=StreamProvisionalInfo(include=state.options.include_provisional),
        consumed=StreamConsumed(complete_records=0, bytes=0),
        error=StreamError(code=code, message=message),
    )


def _reset_required(
    state: StreamState,
    *,
    reason: str,
    diagnostic_code: str,
) -> StreamUpdate:
    rev = (
        state.snapshot.revision
        if state.snapshot is not None
        else StreamRevision(
            revision=0,
            revision_id="reset-required",
            parent_revision_id=None,
            complete=False,
            generation=state.generation,
        )
    )
    dropped = tuple(
        r.record["id"] for r in (state.snapshot.records if state.snapshot else ())
    )
    diag = StreamDiagnostic(
        code=diagnostic_code,
        message={
            "source-truncated": _MSG_SOURCE_TRUNCATED,
            "prefix-hash-mismatch": _MSG_PREFIX_MISMATCH,
            "group-changed": _MSG_GROUP_CHANGED,
            "cursor-mismatch": _MSG_CURSOR_CONFLICT,
        }.get(reason, _MSG_SOURCE_TRUNCATED),
    )
    return StreamUpdate(
        kind="reset-required",
        revision=rev,
        cursor=state.cursor,  # cursor unchanged (atomicity)
        snapshot=None,
        delta=None,
        diagnostics=(diag,),
        provisional=StreamProvisionalInfo(include=state.options.include_provisional),
        consumed=StreamConsumed(complete_records=0, bytes=0),
        reset=StreamReset(
            reason=reason,  # type: ignore[arg-type]
            prior_cursor=state.cursor,
            requires_snapshot=True,
            dropped_record_ids=dropped,
        ),
    )
