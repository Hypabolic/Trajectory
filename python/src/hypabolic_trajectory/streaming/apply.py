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
from hypabolic_trajectory.streaming.ahp_reducer import (
    detect_sequence_gap,
    empty_chat_state,
    parse_action_batch,
    reduce_ahp_actions,
    shape_a_bytes,
)
from hypabolic_trajectory.streaming.delta import diagnostic_key, diff_snapshots
from hypabolic_trajectory.streaming.framing import append_framed, split_complete_lines
from hypabolic_trajectory.streaming.types import (
    STREAM_SCHEMA_ID,
    AhpServerSeqPosition,
    BytePosition,
    HermesRowPosition,
    SnapshotRevisionPosition,
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
_MSG_SOURCE_COMPACTED = "Source material was compacted relative to the committed cursor."
_MSG_SOURCE_REPLACED = "Source material was replaced relative to the committed cursor."
_MSG_PREFIX_MISMATCH = "Committed prefix hash does not match supplied material."
_MSG_UNSUPPORTED_INPUT = "Stream input kind is not supported for this source."
_MSG_AHP_SOURCE_REQUIRED = "AHP stream apply requires source ahp."
_MSG_HERMES_SOURCE_REQUIRED = "Hermes export stream apply requires source hermes."
_MSG_INVALID_HERMES_EXPORT = "Hermes export material is not valid session-export JSON."
_MSG_FINISHED = "Stream is already finished."
_MSG_INVALID_SOURCE = "Unknown or invalid stream source."
_MSG_SEQUENCE_GAP = "AHP action-log serverSeq gap requires snapshot resync."
_MSG_INVALID_AHP_ACTIONS = "AHP action batch could not be parsed."
_MSG_INVALID_AHP_SNAPSHOT = "AHP snapshot material is not valid Shape A JSON."

_STREAM_BUFFER_LIMIT = "stream_buffer_limit"
_STREAM_CURSOR_CONFLICT = "stream_cursor_conflict"
_STREAM_SOURCE_RESET = "stream_source_reset"
_STREAM_RESYNC_REQUIRED = "stream_resync_required"
_STREAM_SEQUENCE_GAP = "stream_sequence_gap"
_DIAG_BACKEND_TOOL_SYNTH = "backend_tool_result_synthesized"
_BACKEND_SYNTH_PREFIX = "[backend "


def create_stream(options: StreamOptions) -> StreamState:
    """Create a new pure stream state (generation 0, empty cursor)."""
    source = resolve_source(options.source)
    group_id = options.group_id if options.group_id else "default"
    if source == TrajectorySource.AHP:
        position: Any = SnapshotRevisionPosition(revision="", content_sha256=None)
    elif source == TrajectorySource.HERMES:
        position = HermesRowPosition(
            database_generation="",
            last_row_id=None,
            change_token=None,
        )
    else:
        position = BytePosition(next_byte_offset=0, pending_byte_length=0)
    cursor = StreamCursor(
        source=source.value,
        group_id=group_id,
        generation=0,
        position=position,
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
    if input.kind == "ahp-snapshot":
        return apply_ahp_snapshot(
            state,
            input.data,
            source_revision=input.source_revision or "",
            cursor=input.cursor,
        )
    if input.kind == "ahp-actions":
        return apply_ahp_actions(state, input.data, cursor=input.cursor)
    if input.kind == "hermes-export":
        return apply_hermes_export(
            state,
            input.data,
            change_token=input.change_token,
            database_generation=input.database_generation,
            source_revision=input.source_revision,
            cursor=input.cursor,
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

    # Shrink / rewrite against prior byte cursor (same group). Mid-file rewrites
    # of equal-or-longer snapshots remain valid full re-normalizes (delta shows
    # upserts/removes). Shorter material requires reset with a precise reason.
    prior_pos = state.cursor.position
    if (
        isinstance(prior_pos, BytePosition)
        and state.snapshot is not None
        and len(committed) < prior_pos.next_byte_offset
    ):
        return state, _reset_required(
            state,
            reason=_shrink_reset_reason(state, committed),
            diagnostic_code=_STREAM_SOURCE_RESET,
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
    # Snapshot replaces committed material; clear append-replay fingerprint.
    new_state.last_append_segment = None
    new_state.last_append_pre_offset = None
    return new_state, update


def apply_ahp_snapshot(
    state: StreamState,
    material: bytes,
    *,
    source_revision: str,
    cursor: StreamCursor | None = None,
) -> tuple[StreamState, StreamUpdate]:
    """Apply a successive AHP Shape A snapshot (LS-06).

    Cursor family: snapshot-revision. activeTurn records are provisional with
    stable provisional ids ``prov-active-turn-{n}``.
    """
    if type(material) is not bytes:
        raise TypeError("material must be bytes")
    if state.finished:
        return state, _error_update(state, code="invalid_input", message=_MSG_FINISHED)

    source = resolve_source(state.options.source)
    if source != TrajectorySource.AHP:
        return state, _error_update(
            state, code="invalid_input", message=_MSG_AHP_SOURCE_REQUIRED
        )

    conflict = _cursor_conflict(state, cursor)
    if conflict is not None:
        return state, conflict

    content_sha = sha256_hex(material)
    # Idempotent duplicate host revision (+ same content fingerprint).
    if (
        state.snapshot is not None
        and state.ahp_last_snapshot_revision == source_revision
        and state.ahp_last_content_sha256 == content_sha
    ):
        return state, _unchanged_update(state)
    if (
        state.snapshot is not None
        and isinstance(state.cursor.position, SnapshotRevisionPosition)
        and state.cursor.position.revision == source_revision
        and state.cursor.position.content_sha256 == content_sha
    ):
        return state, _unchanged_update(state)

    built = _build_ahp_records(state, material)
    if isinstance(built, StreamUpdate):
        return state, built
    records, diagnostics, group_id, chat_state, session, protocol_version = built

    if not state.options.include_provisional:
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
        prefix_sha=content_sha,
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
    out_snapshot, out_delta = _apply_delivery(snapshot, delta, state.options.delivery)

    # Preserve server-seq authority when actions established it; otherwise
    # snapshot-revision cursor.
    if isinstance(new_state.cursor.position, AhpServerSeqPosition) or (
        new_state.ahp_last_server_seq is not None
    ):
        last_seq = (
            new_state.ahp_last_server_seq
            if new_state.ahp_last_server_seq is not None
            else -1
        )
        position: Any = AhpServerSeqPosition(
            next_server_seq=last_seq + 1,
            last_server_seq=last_seq,
        )
    else:
        position = SnapshotRevisionPosition(
            revision=source_revision,
            content_sha256=content_sha,
        )

    cursor_out = StreamCursor(
        source=new_state.cursor.source,
        group_id=group_id,
        generation=generation,
        position=position,
        source_revision=source_revision,
        prefix_sha256=content_sha,
    )
    provisional_ids = tuple(
        r.provisional_id for r in records if r.provisional_id is not None
    )
    prior_provisional = {
        r.provisional_id
        for r in (state.snapshot.records if state.snapshot else ())
        if r.provisional_id
    }
    finalized_ids = tuple(
        sorted(
            pid
            for pid in prior_provisional
            if pid not in set(provisional_ids)
        )
    )
    update = StreamUpdate(
        kind="updated",
        revision=revision,
        cursor=cursor_out,
        snapshot=out_snapshot,
        delta=out_delta,
        diagnostics=diagnostics,
        provisional=StreamProvisionalInfo(
            include=state.options.include_provisional,
            provisional_ids=provisional_ids,
            finalized_ids=finalized_ids,
        ),
        consumed=StreamConsumed(
            complete_records=len(records),
            bytes=len(material),
            first_source_position=0 if material else None,
            last_source_position=(len(material) - 1) if material else None,
        ),
    )

    new_state.cursor = cursor_out
    new_state.snapshot = snapshot
    new_state.next_revision = revision_num + 1
    new_state.ahp_chat_state = chat_state
    new_state.ahp_session = session
    new_state.ahp_protocol_version = protocol_version
    new_state.ahp_target_channel = (
        group_id if isinstance(group_id, str) and group_id.startswith("ahp-chat:") else new_state.ahp_target_channel
    )
    new_state.ahp_last_snapshot_revision = source_revision
    new_state.ahp_last_content_sha256 = content_sha
    # Snapshot material is not a JSONL prefix.
    new_state.committed_prefix = bytearray()
    new_state.pending_bytes = bytearray()
    new_state.last_append_segment = None
    new_state.last_append_pre_offset = None
    return new_state, update


def apply_ahp_actions(
    state: StreamState,
    data: bytes,
    *,
    cursor: StreamCursor | None = None,
) -> tuple[StreamState, StreamUpdate]:
    """Apply an AHP Shape B action-log batch (LS-07).

    Cursor authority is ``serverSeq``. Gaps never silently advance the cursor
    (``reset-required`` / ``sequence-gap``). Unknown actions → content-safe
    diagnostic; foreign channels are ignored.
    """
    if type(data) is not bytes:
        raise TypeError("data must be bytes")
    if state.finished:
        return state, _error_update(state, code="invalid_input", message=_MSG_FINISHED)

    source = resolve_source(state.options.source)
    if source != TrajectorySource.AHP:
        return state, _error_update(
            state, code="invalid_input", message=_MSG_AHP_SOURCE_REQUIRED
        )

    actions_sha = sha256_hex(data)
    pre_seq = state.ahp_last_server_seq

    # Idempotent true-replay of the same batch (pre-apply cursor or already applied).
    if (
        state.last_ahp_actions_sha256 is not None
        and state.last_ahp_actions_sha256 == actions_sha
    ):
        if cursor is None:
            return state, _unchanged_update(state)
        if isinstance(cursor.position, AhpServerSeqPosition):
            if (
                state.last_ahp_actions_pre_seq is not None
                and cursor.position.last_server_seq == state.last_ahp_actions_pre_seq
            ):
                return state, _unchanged_update(state)
            # Post-apply cursor re-supply of the same batch: already committed.
            if (
                isinstance(state.cursor.position, AhpServerSeqPosition)
                and cursor.position.last_server_seq == state.cursor.position.last_server_seq
            ):
                return state, _unchanged_update(state)
        elif isinstance(cursor.position, SnapshotRevisionPosition):
            # First action batch was applied from a snapshot-revision cursor.
            if state.last_ahp_actions_pre_seq in (None, -1):
                return state, _unchanged_update(state)

    # Cursor checks: only enforce when caller supplies ahp-server-seq that
    # disagrees with committed authority. Snapshot-revision cursors are ignored
    # once the stream is on server-seq (upgrade path / double-invoke pre-cursor).
    if cursor is not None and isinstance(cursor.position, AhpServerSeqPosition):
        conflict = _cursor_conflict(state, cursor)
        if conflict is not None:
            return state, conflict
    elif cursor is not None and not isinstance(
        cursor.position, (AhpServerSeqPosition, SnapshotRevisionPosition)
    ):
        conflict = _cursor_conflict(state, cursor)
        if conflict is not None:
            return state, conflict

    try:
        envelopes = parse_action_batch(data)
    except (ValueError, UnicodeDecodeError):
        return state, _error_update(
            state, code="invalid_input", message=_MSG_INVALID_AHP_ACTIONS
        )

    if not envelopes:
        return state, _unchanged_update(state)

    target = state.ahp_target_channel
    if target is None and state.group_locked and isinstance(state.cursor.group_id, str):
        if state.cursor.group_id.startswith("ahp-chat:"):
            target = state.cursor.group_id

    gap = detect_sequence_gap(
        envelopes,
        last_server_seq=state.ahp_last_server_seq,
        target_channel=target,
    )
    if gap is not None:
        return state, _reset_required(
            state,
            reason="sequence-gap",
            diagnostic_code=_STREAM_SEQUENCE_GAP,
        )

    chat_in = state.ahp_chat_state
    if chat_in is None:
        chat_in = empty_chat_state(resource=target)

    reduced, new_last_seq, reduce_diags, applied = reduce_ahp_actions(
        chat_in,
        envelopes,
        target_channel=target,
        last_server_seq=state.ahp_last_server_seq,
    )

    if not applied and new_last_seq == state.ahp_last_server_seq:
        # Pure foreign/unknown/already-applied batch with no state change.
        # Still surface reduce diagnostics on an unchanged outcome only when
        # there is no visible change — return unchanged.
        if not reduce_diags:
            return state, _unchanged_update(state)

    protocol = (
        state.ahp_protocol_version
        or state.options.ahp_protocol_version
        or "0.7.0"
    )
    material = shape_a_bytes(
        reduced,
        protocol_version=protocol,
        session=state.ahp_session,
    )
    # Source revision for action-driven snapshots is seq-authoritative.
    if new_last_seq is not None:
        rev = f"seq:{new_last_seq}"
    else:
        rev = state.cursor.source_revision or "seq:0"

    # Apply via snapshot path, then rewrite cursor to ahp-server-seq.
    snap_state = _clone_state(state)
    snap_state.ahp_chat_state = reduced
    snap_state.ahp_last_server_seq = new_last_seq
    # Clear snapshot-revision idempotence keys so seq advances always emit.
    snap_state.ahp_last_snapshot_revision = None
    snap_state.ahp_last_content_sha256 = None

    new_state, update = apply_ahp_snapshot(
        snap_state,
        material,
        source_revision=rev,
        cursor=None,
    )
    if update.kind not in {"updated", "unchanged"}:
        return state, update

    # Merge reducer diagnostics (unknown action / foreign channel).
    extra = tuple(
        StreamDiagnostic(code=d["code"], message=d["message"]) for d in reduce_diags
    )
    # Deduplicate by code+message for stable envelopes.
    # Sort by diagnostic_key so snapshot order matches key-sorted
    # diagnostic_add ops under the delta-apply law (streaming.md §7).
    seen: set[tuple[str, str]] = set()
    merged_diags: list[StreamDiagnostic] = []
    for d in list(update.diagnostics) + list(extra):
        key = (d.code, d.message)
        if key in seen:
            continue
        seen.add(key)
        merged_diags.append(d)
    diagnostics = tuple(sorted(merged_diags, key=diagnostic_key))

    last_seq = new_last_seq if new_last_seq is not None else -1
    seq_cursor = StreamCursor(
        source=new_state.cursor.source,
        group_id=new_state.cursor.group_id,
        generation=new_state.cursor.generation,
        position=AhpServerSeqPosition(
            next_server_seq=last_seq + 1,
            last_server_seq=last_seq,
            next_byte_offset=len(data) if data else None,
        ),
        source_revision=rev,
        prefix_sha256=new_state.cursor.prefix_sha256,
    )
    new_state.cursor = seq_cursor
    new_state.ahp_last_server_seq = new_last_seq
    new_state.ahp_chat_state = reduced
    new_state.ahp_target_channel = (
        new_state.cursor.group_id
        if new_state.cursor.group_id.startswith("ahp-chat:")
        else target
    )
    new_state.last_ahp_actions_sha256 = actions_sha
    new_state.last_ahp_actions_pre_seq = pre_seq if pre_seq is not None else -1

    if update.kind == "unchanged" and not extra:
        return new_state, _unchanged_update(new_state)

    # Rebuild update with seq cursor and merged diagnostics.
    snap = update.snapshot
    if snap is not None and diagnostics != snap.diagnostics:
        snap = StreamSnapshot(
            source=snap.source,
            group_id=snap.group_id,
            revision=snap.revision,
            records=snap.records,
            diagnostics=diagnostics,
            complete=snap.complete,
        )
        # Re-diff so diagnostic ops appear.
        delta = diff_snapshots(state.snapshot, snap, revision=snap.revision)
        out_snapshot, out_delta = _apply_delivery(
            snap, delta, state.options.delivery
        )
    else:
        out_snapshot, out_delta = update.snapshot, update.delta

    if update.kind == "unchanged" and extra:
        # Diagnostics-only change: still emit updated when diagnostics appear.
        # Fall through with a synthetic updated if we have a snapshot.
        if new_state.snapshot is None:
            return new_state, _unchanged_update(new_state)

    update = StreamUpdate(
        kind="updated" if update.kind == "updated" or extra else update.kind,
        revision=update.revision if update.kind != "unchanged" else (
            new_state.snapshot.revision if new_state.snapshot else update.revision
        ),
        cursor=seq_cursor,
        snapshot=out_snapshot if update.kind == "updated" or extra else update.snapshot,
        delta=out_delta if update.kind == "updated" or extra else update.delta,
        diagnostics=diagnostics if update.kind == "updated" or extra else (),
        provisional=update.provisional,
        consumed=StreamConsumed(
            complete_records=update.consumed.complete_records,
            bytes=len(data),
            first_source_position=0 if data else None,
            last_source_position=(len(data) - 1) if data else None,
        ),
        reset=update.reset,
        error=update.error,
    )
    if update.kind == "updated" and new_state.snapshot is not None:
        # Keep internal snapshot diagnostics aligned.
        new_state.snapshot = StreamSnapshot(
            source=new_state.snapshot.source,
            group_id=new_state.snapshot.group_id,
            revision=new_state.snapshot.revision,
            records=new_state.snapshot.records,
            diagnostics=diagnostics,
            complete=new_state.snapshot.complete,
        )
    return new_state, update


def apply_hermes_export(
    state: StreamState,
    material: bytes,
    *,
    change_token: str | None = None,
    database_generation: str | None = None,
    source_revision: str | None = None,
    cursor: StreamCursor | None = None,
) -> tuple[StreamState, StreamUpdate]:
    """Apply a Hermes session export (array or {session, messages}) — LS-07h.

    Cursor family: ``hermes-row``. Core stays SQLite-free: callers (optional
    provider packages) supply export JSON + change token. Prior-row mutation
    (soft-delete / rewrite) requires reset when ordered active-row fingerprints
    are not a pure prefix of the new export.
    """
    if type(material) is not bytes:
        raise TypeError("material must be bytes")
    if state.finished:
        return state, _error_update(state, code="invalid_input", message=_MSG_FINISHED)

    source = resolve_source(state.options.source)
    if source != TrajectorySource.HERMES:
        return state, _error_update(
            state, code="invalid_input", message=_MSG_HERMES_SOURCE_REQUIRED
        )

    conflict = _cursor_conflict(state, cursor)
    if conflict is not None:
        return state, conflict

    content_sha = sha256_hex(material)
    meta = _hermes_export_meta(material)
    if meta is None:
        return state, _error_update(
            state, code="invalid_input", message=_MSG_INVALID_HERMES_EXPORT
        )
    row_fps, last_row_id = meta
    gen = (
        database_generation
        if database_generation is not None and database_generation != ""
        else (
            source_revision
            if source_revision is not None and source_revision != ""
            else "0"
        )
    )
    token = change_token if change_token is not None and change_token != "" else content_sha

    # Idempotent duplicate: same generation + token + content fingerprint.
    if (
        state.snapshot is not None
        and state.hermes_last_export_sha == content_sha
        and isinstance(state.cursor.position, HermesRowPosition)
        and state.cursor.position.database_generation == gen
        and state.cursor.position.change_token == token
    ):
        return state, _unchanged_update(state)

    # Database generation change → full resync.
    if (
        state.snapshot is not None
        and isinstance(state.cursor.position, HermesRowPosition)
        and state.cursor.position.database_generation
        and state.cursor.position.database_generation != gen
    ):
        return state, _reset_required(
            state,
            reason="source-replaced",
            diagnostic_code=_STREAM_SOURCE_RESET,
        )

    # Prior-row mutation / soft-delete: committed fingerprints must be a pure
    # ordered prefix of the new active-row fingerprint sequence.
    prior_fps = state.hermes_row_fingerprints
    if state.snapshot is not None and prior_fps is not None:
        n = len(prior_fps)
        if len(row_fps) < n or row_fps[:n] != prior_fps:
            return state, _reset_required(
                state,
                reason="source-replaced",
                diagnostic_code=_STREAM_SOURCE_RESET,
            )

    built = _build_records_from_prefix(state, material)
    if isinstance(built, StreamUpdate):
        return state, built
    records, diagnostics, group_id = built

    if not state.options.include_provisional:
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
        prefix_sha=content_sha,
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
    out_snapshot, out_delta = _apply_delivery(snapshot, delta, state.options.delivery)

    cursor_out = StreamCursor(
        source=new_state.cursor.source,
        group_id=group_id,
        generation=generation,
        position=HermesRowPosition(
            database_generation=gen,
            last_row_id=last_row_id,
            change_token=token,
        ),
        source_revision=source_revision if source_revision is not None else gen,
        prefix_sha256=content_sha,
    )
    provisional_ids = tuple(
        r.provisional_id for r in records if r.provisional_id is not None
    )
    update = StreamUpdate(
        kind="updated",
        revision=revision,
        cursor=cursor_out,
        snapshot=out_snapshot,
        delta=out_delta,
        diagnostics=diagnostics,
        provisional=StreamProvisionalInfo(
            include=state.options.include_provisional,
            provisional_ids=provisional_ids,
            finalized_ids=(),
        ),
        consumed=StreamConsumed(
            complete_records=len(records),
            bytes=len(material),
            first_source_position=0 if material else None,
            last_source_position=(len(material) - 1) if material else None,
        ),
    )

    new_state.cursor = cursor_out
    new_state.snapshot = snapshot
    new_state.next_revision = revision_num + 1
    new_state.committed_prefix = bytearray()
    new_state.pending_bytes = bytearray()
    new_state.last_append_segment = None
    new_state.last_append_pre_offset = None
    new_state.hermes_row_fingerprints = row_fps
    new_state.hermes_last_export_sha = content_sha
    return new_state, update


def apply_append(
    state: StreamState,
    segment: bytes,
    *,
    cursor: StreamCursor | None = None,
    source_revision: str | None = None,
) -> tuple[StreamState, StreamUpdate]:
    """Append complete-line segment for file JSONL sources.

    Steady-state path frames the segment against the pending buffer, extends the
    committed prefix, then re-normalizes the full committed prefix (oracle path).
    That guarantees append == full-prefix snapshot for every shared fixture.
    There is no separate incremental decoder in this slice: the oracle path *is*
    the implementation, so no performance fallback is required.
    """
    if type(segment) is not bytes:
        raise TypeError("segment must be bytes")
    if state.finished:
        return state, _error_update(state, code="invalid_input", message=_MSG_FINISHED)

    opts = state.options
    limit_err = _validate_buffer_limits(opts)
    if limit_err is not None:
        return state, _error_update(state, code="invalid_input", message=limit_err)

    # Empty segment with no pending change is a pure no-op.
    if not segment and not state.pending_bytes:
        return state, _unchanged_update(state)

    # True append replay: same segment re-supplied with the pre-apply cursor.
    # Content equality alone is not enough — successive identical growth segments
    # (e.g. two identical JSONL lines) must both commit after the cursor advances.
    pre_offset: int | None = None
    if isinstance(state.cursor.position, BytePosition):
        pre_offset = state.cursor.position.next_byte_offset
    if (
        state.last_append_segment is not None
        and state.last_append_pre_offset is not None
        and segment == state.last_append_segment
        and cursor is not None
        and isinstance(cursor.position, BytePosition)
        and cursor.position.next_byte_offset == state.last_append_pre_offset
        and cursor.source == state.cursor.source
        and cursor.generation == state.cursor.generation
        and cursor.group_id == state.cursor.group_id
    ):
        return state, _unchanged_update(state)

    conflict = _cursor_conflict(state, cursor)
    if conflict is not None:
        return state, conflict

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

    # No complete lines: only pending advanced (incomplete line / mid-UTF-8).
    # Visible records unchanged → kind=unchanged with patched pending cursor.
    if not complete:
        if pending == bytes(state.pending_bytes):
            return state, _unchanged_update(state)
        new_state = _clone_state(state)
        new_state.pending_bytes = bytearray(pending)
        new_state.last_append_segment = segment
        new_state.last_append_pre_offset = pre_offset
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
        return new_state, _unchanged_update(new_state)

    new_prefix = bytes(state.committed_prefix) + complete
    rev = source_revision if source_revision is not None else (
        state.cursor.source_revision or ""
    )
    # Oracle: full re-normalize of the committed prefix via apply_snapshot.
    # Snapshot path re-splits lines; feed only complete prefix + track pending.
    snap_state = _clone_state(state)
    snap_state.pending_bytes = bytearray()
    material = new_prefix  # all complete lines already
    new_state, update = apply_snapshot(
        snap_state,
        material,
        source_revision=rev,
        cursor=None,
    )
    # Failure-atomic: failed/reset snapshot leaves prior state and pending intact.
    if update.kind not in {"updated", "unchanged"}:
        return state, update

    # Restore pending from append framing; always copy onto StreamUpdate.cursor.
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
    new_state.last_append_segment = segment
    new_state.last_append_pre_offset = pre_offset
    # Consumed bytes for append = newly framed complete segment only.
    consumed = StreamConsumed(
        complete_records=update.consumed.complete_records,
        bytes=len(complete),
        first_source_position=(
            len(bytes(state.committed_prefix)) if complete else None
        ),
        last_source_position=(
            (len(new_prefix) - 1) if complete else None
        ),
    )
    update = StreamUpdate(
        kind=update.kind,
        revision=update.revision,
        cursor=new_state.cursor,
        snapshot=update.snapshot,
        delta=update.delta,
        diagnostics=update.diagnostics,
        provisional=update.provisional,
        consumed=consumed if update.kind == "updated" else update.consumed,
        reset=update.reset,
        error=update.error,
    )
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
    new_state.last_append_segment = None
    new_state.last_append_pre_offset = None
    new_state.ahp_chat_state = None
    new_state.ahp_session = None
    new_state.ahp_protocol_version = None
    new_state.ahp_last_server_seq = None
    new_state.ahp_target_channel = None
    new_state.ahp_last_snapshot_revision = None
    new_state.ahp_last_content_sha256 = None
    new_state.last_ahp_actions_sha256 = None
    new_state.last_ahp_actions_pre_seq = None
    new_state.hermes_row_fingerprints = None
    new_state.hermes_last_export_sha = None
    group_id = state.options.group_id or state.cursor.group_id
    source = resolve_source(state.options.source)
    if source == TrajectorySource.AHP:
        pos: Any = SnapshotRevisionPosition(revision=request.source_revision or "", content_sha256=None)
    elif source == TrajectorySource.HERMES:
        pos = HermesRowPosition(
            database_generation=request.source_revision or "",
            last_row_id=None,
            change_token=request.change_token,
        )
    else:
        pos = BytePosition(next_byte_offset=0, pending_byte_length=0)
    new_state.cursor = StreamCursor(
        source=state.cursor.source,
        group_id=group_id,
        generation=generation,
        position=pos,
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
        if source == TrajectorySource.HERMES:
            new_state, update = apply_hermes_export(
                new_state,
                request.material,
                change_token=request.change_token,
                database_generation=request.source_revision,
                source_revision=request.source_revision,
                cursor=None,
            )
        else:
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
        position=pos,
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

    def apply_ahp_snapshot(
        self,
        data: bytes,
        *,
        source_revision: str,
        cursor: StreamCursor | None = None,
    ) -> StreamUpdate:
        self._state, update = apply_ahp_snapshot(
            self._state, data, source_revision=source_revision, cursor=cursor
        )
        return update

    def apply_ahp_actions(
        self,
        data: bytes,
        *,
        cursor: StreamCursor | None = None,
    ) -> StreamUpdate:
        self._state, update = apply_ahp_actions(self._state, data, cursor=cursor)
        return update

    def apply_hermes_export(
        self,
        data: bytes,
        *,
        change_token: str | None = None,
        database_generation: str | None = None,
        source_revision: str | None = None,
        cursor: StreamCursor | None = None,
    ) -> StreamUpdate:
        self._state, update = apply_hermes_export(
            self._state,
            data,
            change_token=change_token,
            database_generation=database_generation,
            source_revision=source_revision,
            cursor=cursor,
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
    import copy

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
        last_append_segment=state.last_append_segment,
        last_append_pre_offset=state.last_append_pre_offset,
        ahp_chat_state=copy.deepcopy(state.ahp_chat_state),
        ahp_session=copy.deepcopy(state.ahp_session),
        ahp_protocol_version=state.ahp_protocol_version,
        ahp_last_server_seq=state.ahp_last_server_seq,
        ahp_target_channel=state.ahp_target_channel,
        ahp_last_snapshot_revision=state.ahp_last_snapshot_revision,
        ahp_last_content_sha256=state.ahp_last_content_sha256,
        last_ahp_actions_sha256=state.last_ahp_actions_sha256,
        last_ahp_actions_pre_seq=state.last_ahp_actions_pre_seq,
        hermes_row_fingerprints=state.hermes_row_fingerprints,
        hermes_last_export_sha=state.hermes_last_export_sha,
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
    # AHP server-seq cursor
    if isinstance(cursor.position, AhpServerSeqPosition) and isinstance(
        current.position, AhpServerSeqPosition
    ):
        if (
            not _is_non_negative_int64(cursor.position.next_server_seq)
            or not _is_non_negative_int64(cursor.position.last_server_seq)
        ):
            return _error_update(
                state,
                code="invalid_input",
                message="Stream cursor serverSeq positions must be non-negative int64 values.",
            )
        if (
            cursor.position.last_server_seq != current.position.last_server_seq
            or cursor.position.next_server_seq != current.position.next_server_seq
        ):
            return _reset_required(
                state,
                reason="cursor-mismatch",
                diagnostic_code=_STREAM_CURSOR_CONFLICT,
            )
    # Hermes-row cursor
    if isinstance(cursor.position, HermesRowPosition) and isinstance(
        current.position, HermesRowPosition
    ):
        if (
            cursor.position.database_generation != current.position.database_generation
            or cursor.position.last_row_id != current.position.last_row_id
            or cursor.position.change_token != current.position.change_token
        ):
            return _reset_required(
                state,
                reason="cursor-mismatch",
                diagnostic_code=_STREAM_CURSOR_CONFLICT,
            )
    # Snapshot-revision cursor
    if isinstance(cursor.position, SnapshotRevisionPosition) and isinstance(
        current.position, SnapshotRevisionPosition
    ):
        if cursor.position.revision != current.position.revision:
            return _reset_required(
                state,
                reason="cursor-mismatch",
                diagnostic_code=_STREAM_CURSOR_CONFLICT,
            )
    # Kind mismatch after stream is live (both have committed positions of different families)
    if (
        state.snapshot is not None
        and type(cursor.position) is not type(current.position)
        and not (
            # Allow first AHP actions after snapshot-revision (upgrade to server-seq)
            isinstance(current.position, SnapshotRevisionPosition)
            and isinstance(cursor.position, AhpServerSeqPosition)
        )
        and not (
            isinstance(current.position, BytePosition)
            and current.position.next_byte_offset == 0
        )
    ):
        # Different families on a live stream: treat as cursor mismatch when
        # both sides are AHP-ish and disagree, otherwise ignore kind when the
        # caller omits a matching cursor (cursor is optional on snapshot).
        if isinstance(cursor.position, (AhpServerSeqPosition, SnapshotRevisionPosition)) and isinstance(
            current.position, (AhpServerSeqPosition, SnapshotRevisionPosition)
        ):
            # snapshot-revision → server-seq is an allowed upgrade path
            if not (
                isinstance(current.position, SnapshotRevisionPosition)
                and isinstance(cursor.position, AhpServerSeqPosition)
            ):
                return _reset_required(
                    state,
                    reason="cursor-mismatch",
                    diagnostic_code=_STREAM_CURSOR_CONFLICT,
                )
    return None


def _build_ahp_records(
    state: StreamState, material: bytes
) -> (
    tuple[
        tuple[StreamRecord, ...],
        tuple[StreamDiagnostic, ...],
        str,
        dict[str, Any] | None,
        dict[str, Any] | None,
        str | None,
    ]
    | StreamUpdate
):
    """Normalize Shape A material with provisional activeTurn mapping."""
    import json

    try:
        root = json.loads(material.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return _error_update(
            state, code="invalid_input", message=_MSG_INVALID_AHP_SNAPSHOT
        )
    if not isinstance(root, dict) or not isinstance(root.get("chat"), dict):
        return _error_update(
            state, code="invalid_input", message=_MSG_INVALID_AHP_SNAPSHOT
        )

    chat = root["chat"]
    session_raw = root.get("session")
    session = session_raw if isinstance(session_raw, dict) else None
    protocol = root.get("ahpProtocolVersion")
    protocol_version = protocol if isinstance(protocol, str) else None

    active = chat.get("activeTurn")
    active_native_ids = _ahp_active_turn_native_ids(active)

    # Do not pass stream group_id hint: native chat.resource is authority.
    # After lock, verify native group matches locked stream group.
    group_hint = None
    if state.group_locked and isinstance(state.cursor.group_id, str):
        if state.cursor.group_id.startswith("ahp-chat:"):
            group_hint = state.cursor.group_id

    try:
        ir_full = normalize_to_ir(
            NormalizeRequest(
                source=TrajectorySource.AHP,
                transcript=material,
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

    hyp = project_hypabolic(ir_full)
    raw_records = hyp.get("records") or []
    built: list[StreamRecord] = []
    prov_n = 0
    for r in raw_records:
        if not isinstance(r, dict):
            continue
        role = r.get("role")
        is_prov = role != "meta" and _record_from_active_turn(r, active_native_ids)
        provisional_id: str | None = None
        status = "stable"
        if is_prov:
            prov_n += 1
            status = "provisional"
            provisional_id = f"prov-active-turn-{prov_n}"
        built.append(
            StreamRecord(
                status=status,  # type: ignore[arg-type]
                record=dict(r),
                provisional_id=provisional_id,
            )
        )
    records = tuple(built)
    diagnostics = tuple(
        StreamDiagnostic(
            code=d.code,
            message=d.message,
            input_line=d.input_line,
            record_index=d.record_index,
            count=d.count,
        )
        for d in ir_full.diagnostics
        if d.code != "ahp_active_turn_omitted"
    )
    return records, diagnostics, ir_full.group_id, chat, session, protocol_version


def _ahp_active_turn_native_ids(active: Any) -> set[str]:
    """Collect native ids belonging to ChatState.activeTurn."""
    if not isinstance(active, dict):
        return set()
    ids: set[str] = set()
    tid = active.get("id")
    if isinstance(tid, str) and tid:
        ids.add(tid)
    parts = active.get("responseParts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            pid = part.get("id")
            if isinstance(pid, str) and pid:
                ids.add(pid)
            tc = part.get("toolCall")
            if isinstance(tc, dict):
                tcid = tc.get("toolCallId")
                if isinstance(tcid, str) and tcid:
                    ids.add(tcid)
    return ids


def _record_from_active_turn(record: dict[str, Any], active_ids: set[str]) -> bool:
    if not active_ids:
        return False
    prov = record.get("provenance")
    if not isinstance(prov, dict):
        return False
    for key in ("native_record_id", "stable_source_record_id"):
        val = prov.get(key)
        if isinstance(val, str) and val in active_ids:
            return True
    return False


def _hermes_export_meta(
    material: bytes,
) -> tuple[tuple[str, ...], int | None] | None:
    """Parse Hermes export → ordered active-row fingerprints + last numeric row id.

    Soft-deleted (active=0/false) rows are excluded, matching the decode path.
    Returns None when the payload is not a valid export shape.
    """
    import json

    try:
        parsed = json.loads(material.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None

    if isinstance(parsed, list):
        messages = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
        messages = parsed["messages"]
    else:
        return None

    if not all(isinstance(m, dict) for m in messages):
        return None

    active: list[dict[str, Any]] = []
    for row in messages:
        active_flag = row.get("active", 1)
        if active_flag in (0, False, "0"):
            continue
        active.append(row)

    # Order by numeric id when every active row has a numeric id (decode peer).
    if active and all(_hermes_is_number_id(r.get("id")) for r in active):
        indexed = list(enumerate(active))
        indexed.sort(key=lambda item: (int(item[1]["id"]), item[0]))
        active = [r for _, r in indexed]
        last_row_id: int | None = int(active[-1]["id"]) if active else None
    else:
        last_row_id = None

    fps: list[str] = []
    for row in active:
        # Content-safe fingerprint: no raw prose in stream state beyond hashes.
        fps.append(sha256_hex(_hermes_row_fingerprint_bytes(row)))
    return tuple(fps), last_row_id


def _hermes_is_number_id(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return False


def _hermes_row_fingerprint_bytes(row: dict[str, Any]) -> bytes:
    """Stable bytes for one Hermes message row (id + active-relevant fields)."""
    import json

    # Compact deterministic subset — identity + payload fields the decoder uses.
    subset = {
        "id": row.get("id"),
        "role": row.get("role"),
        "content": row.get("content"),
        "tool_call_id": row.get("tool_call_id"),
        "tool_name": row.get("tool_name"),
        "tool_calls": row.get("tool_calls"),
        "finish_reason": row.get("finish_reason"),
        "timestamp": row.get("timestamp"),
        "active": row.get("active", 1),
    }
    return json.dumps(subset, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )


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
    has_backend_synth = any(
        d.code == _DIAG_BACKEND_TOOL_SYNTH for d in ir.diagnostics
    )
    mark_provisional = (
        has_backend_synth and source == TrajectorySource.GROK_BUILD
    )
    built_records: list[StreamRecord] = []
    for r in raw_records:
        if not isinstance(r, dict):
            continue
        status: str = "stable"
        provisional_id: str | None = None
        if mark_provisional and _is_synthetic_backend_tool_result(r):
            status = "provisional"
            rid = r.get("id")
            provisional_id = rid if isinstance(rid, str) and rid else None
        built_records.append(
            StreamRecord(
                status=status,  # type: ignore[arg-type]
                record=dict(r),
                provisional_id=provisional_id,
            )
        )
    records = tuple(built_records)
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


def _shrink_reset_reason(state: StreamState, committed: bytes) -> str:
    """Classify shorter snapshot material: truncate vs compact vs replace.

    Pure prefix of the prior committed bytes → source-truncated.
    Non-prefix rewrite on grok-build → source-compacted (first-class).
    Non-prefix rewrite on other JSONL sources → source-replaced.
    """
    prior = bytes(state.committed_prefix)
    if prior.startswith(committed):
        return "source-truncated"
    source = resolve_source(state.options.source)
    if source == TrajectorySource.GROK_BUILD:
        return "source-compacted"
    return "source-replaced"


def _is_synthetic_backend_tool_result(record: dict[str, Any]) -> bool:
    """Grok Build synthetic backend tool results use a fixed content prefix."""
    role = record.get("role")
    content = record.get("content")
    if role != "tool" or not isinstance(content, str):
        return False
    return content.startswith(_BACKEND_SYNTH_PREFIX)


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
            "source-compacted": _MSG_SOURCE_COMPACTED,
            "source-replaced": _MSG_SOURCE_REPLACED,
            "prefix-hash-mismatch": _MSG_PREFIX_MISMATCH,
            "group-changed": _MSG_GROUP_CHANGED,
            "cursor-mismatch": _MSG_CURSOR_CONFLICT,
            "sequence-gap": _MSG_SEQUENCE_GAP,
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
