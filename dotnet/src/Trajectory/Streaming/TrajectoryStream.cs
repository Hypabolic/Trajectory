using System.Collections;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Hypabolic.Trajectory.Adapters.Hypabolic;

namespace Hypabolic.Trajectory.Streaming;

/// <summary>
/// Pure stream algorithm: create, apply_snapshot, framing, stable-id diff.
/// No filesystem watchers, network, or SQLite.
/// </summary>
public static class TrajectoryStream
{
    public const string SchemaId = "trajectory-stream-v1";

    public static StreamState Create(StreamOptions options)
    {
        ArgumentNullException.ThrowIfNull(options);
        var groupId = string.IsNullOrEmpty(options.GroupId) ? "default" : options.GroupId;
        var source = SourceWireName(options.Source);
        return new StreamState
        {
            Options = options,
            Cursor = new StreamCursor
            {
                Source = source,
                GroupId = groupId,
                Generation = 0,
                Position = new BytePosition { NextByteOffset = 0, PendingByteLength = 0 },
                SourceRevision = null,
                PrefixSha256 = null,
            },
            Generation = 0,
            NextRevision = 0,
        };
    }

    /// <summary>Split LF-terminated committed prefix from pending incomplete tail.</summary>
    public static (byte[] Committed, byte[] Pending) SplitCompleteLines(ReadOnlySpan<byte> data)
    {
        if (data.IsEmpty)
        {
            return (Array.Empty<byte>(), Array.Empty<byte>());
        }

        var lastLf = data.LastIndexOf((byte)'\n');
        if (lastLf < 0)
        {
            return (Array.Empty<byte>(), data.ToArray());
        }

        return (data[..(lastLf + 1)].ToArray(), data[(lastLf + 1)..].ToArray());
    }

    public static string MatchKey(StreamRecord record)
    {
        if (!string.IsNullOrEmpty(record.ProvisionalId))
        {
            return record.ProvisionalId;
        }

        if (record.Record.TryGetValue("id", out var id) && id is string s && s.Length > 0)
        {
            return s;
        }

        throw new InvalidOperationException("stream record missing match key");
    }

    public static string MatchKey(JsonElement record)
    {
        if (record.TryGetProperty("provisional_id", out var pid) &&
            pid.ValueKind == JsonValueKind.String &&
            pid.GetString() is { Length: > 0 } provisional)
        {
            return provisional;
        }

        if (record.TryGetProperty("record", out var body) &&
            body.TryGetProperty("id", out var id) &&
            id.GetString() is { Length: > 0 } rid)
        {
            return rid;
        }

        throw new InvalidOperationException("stream record missing match key");
    }

    public static string DiagnosticKey(StreamDiagnostic d)
    {
        var line = d.InputLine?.ToString() ?? "-";
        var index = d.RecordIndex?.ToString() ?? "-";
        return $"{d.Code}|{line}|{index}";
    }

    public static (StreamState State, StreamUpdate Update) ApplySnapshot(
        StreamState state,
        ReadOnlyMemory<byte> material,
        string sourceRevision,
        StreamCursor? cursor = null)
    {
        ArgumentNullException.ThrowIfNull(state);
        sourceRevision ??= "";

        if (state.Finished)
        {
            return (state, ErrorUpdate(state, "invalid_input", "Stream is already finished."));
        }

        if (cursor is not null)
        {
            var conflict = CursorConflict(state, cursor);
            if (conflict is not null)
            {
                return (state, conflict);
            }
        }

        var (committed, pending) = state.Options.RequireCompleteLines
            ? SplitCompleteLines(material.Span)
            : (material.ToArray(), Array.Empty<byte>());

        if (state.Options.MaxPendingBytes is long maxPending)
        {
            if (maxPending < 0)
            {
                return (state, ErrorUpdate(
                    state,
                    "invalid_input",
                    "Stream buffer limits must be non-negative int64 values."));
            }

            if (pending.LongLength > maxPending)
            {
                return (state, ErrorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."));
            }
        }

        if (state.Options.MaxLineBytes is long maxLine)
        {
            if (maxLine < 0)
            {
                return (state, ErrorUpdate(
                    state,
                    "invalid_input",
                    "Stream buffer limits must be non-negative int64 values."));
            }

            if (AnyLineTooLong(committed, maxLine) || pending.LongLength > maxLine)
            {
                return (state, ErrorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."));
            }
        }

        var built = BuildRecords(state, committed);
        if (built.Update is not null)
        {
            return (state, built.Update);
        }

        var records = built.Records!;
        var diagnostics = built.Diagnostics!;
        var groupId = built.GroupId!;

        if (!state.Options.IncludeProvisional)
        {
            records = records.Where(r => r.Status != "provisional").ToList();
        }

        if (state.Snapshot is not null && committed.LongLength < state.Cursor.Position.NextByteOffset)
        {
            var (reason, message) = ShrinkResetReason(state, committed);
            return (state, ResetRequired(
                state,
                reason,
                "stream_source_reset",
                message));
        }

        var effectivePrefixSha = Sha256Hex(committed);

        if (state.Snapshot is not null &&
            state.Cursor.SourceRevision == sourceRevision &&
            state.Cursor.PrefixSha256 == effectivePrefixSha &&
            state.PendingBytes.AsSpan().SequenceEqual(pending))
        {
            return (state, UnchangedUpdate(state));
        }

        var newState = Clone(state);
        newState.GroupLocked = true;
        var generation = newState.Generation;
        var parentRevisionId = newState.Snapshot?.Revision.RevisionId;
        var revisionNum = newState.NextRevision;
        var recordIds = records
            .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
            .Where(s => s.Length > 0)
            .ToArray();
        var revisionId = RevisionId(
            generation,
            revisionNum,
            newState.Cursor.Source,
            groupId,
            effectivePrefixSha,
            recordIds);
        var revision = new StreamRevision
        {
            Revision = revisionNum,
            RevisionId = revisionId,
            ParentRevisionId = parentRevisionId,
            Complete = false,
            Generation = generation,
        };
        var snapshot = new StreamSnapshot
        {
            Source = newState.Cursor.Source,
            GroupId = groupId,
            Revision = revision,
            Records = records,
            Diagnostics = diagnostics,
            Complete = false,
        };
        var delta = DiffSnapshots(newState.Snapshot, snapshot, revision);
        var (outSnap, outDelta) = ApplyDelivery(snapshot, delta, newState.Options.Delivery);
        var newCursor = new StreamCursor
        {
            Source = newState.Cursor.Source,
            GroupId = groupId,
            Generation = generation,
            Position = new BytePosition
            {
                NextByteOffset = committed.LongLength,
                PendingByteLength = pending.LongLength,
            },
            SourceRevision = sourceRevision,
            PrefixSha256 = effectivePrefixSha,
        };
        var provisionalIds = records
            .Where(r => !string.IsNullOrEmpty(r.ProvisionalId))
            .Select(r => r.ProvisionalId!)
            .ToArray();
        var update = new StreamUpdate
        {
            Kind = "updated",
            Revision = revision,
            Cursor = newCursor,
            Snapshot = outSnap,
            Delta = outDelta,
            Diagnostics = diagnostics,
            Provisional = new StreamProvisionalInfo
            {
                Include = state.Options.IncludeProvisional,
                ProvisionalIds = provisionalIds,
                FinalizedIds = Array.Empty<string>(),
            },
            Consumed = new StreamConsumed
            {
                CompleteRecords = (ulong)records.Count,
                Bytes = (ulong)committed.LongLength,
                FirstSourcePosition = committed.Length == 0 ? null : 0,
                LastSourcePosition = committed.Length == 0 ? null : committed.LongLength - 1,
            },
        };
        newState.Cursor = newCursor;
        newState.Snapshot = snapshot;
        newState.PendingBytes = pending;
        newState.CommittedPrefix = committed;
        newState.NextRevision = revisionNum + 1;
        // Snapshot replaces committed material; clear append-replay fingerprint.
        newState.LastAppendSegment = null;
        return (newState, update);
    }

    /// <summary>
    /// Append complete-line segment for file JSONL sources.
    /// Frames against the pending buffer, extends the committed prefix, then
    /// re-normalizes the full committed prefix (oracle path). Append equals
    /// full-prefix snapshot on every shared fixture. The oracle path is the
    /// steady-state implementation (O(committed_prefix)); no separate incremental
    /// decoder requires a performance fallback in this slice.
    /// </summary>
    public static (StreamState State, StreamUpdate Update) ApplyAppend(
        StreamState state,
        ReadOnlyMemory<byte> segment,
        StreamCursor? cursor = null,
        string? sourceRevision = null)
    {
        ArgumentNullException.ThrowIfNull(state);
        if (state.Finished)
        {
            return (state, ErrorUpdate(state, "invalid_input", "Stream is already finished."));
        }

        if (cursor is not null)
        {
            var conflict = CursorConflict(state, cursor);
            if (conflict is not null)
            {
                return (state, conflict);
            }
        }

        if (state.Options.MaxPendingBytes is long maxPending && maxPending < 0)
        {
            return (state, ErrorUpdate(
                state,
                "invalid_input",
                "Stream buffer limits must be non-negative int64 values."));
        }

        if (state.Options.MaxLineBytes is long maxLine && maxLine < 0)
        {
            return (state, ErrorUpdate(
                state,
                "invalid_input",
                "Stream buffer limits must be non-negative int64 values."));
        }

        if (segment.Length == 0 && state.PendingBytes.Length == 0)
        {
            return (state, UnchangedUpdate(state));
        }

        // Replay of already-accepted append input is idempotent (unchanged).
        if (state.LastAppendSegment is not null &&
            segment.Length == state.LastAppendSegment.Length &&
            segment.Span.SequenceEqual(state.LastAppendSegment))
        {
            return (state, UnchangedUpdate(state));
        }

        var combined = new byte[state.PendingBytes.Length + segment.Length];
        Buffer.BlockCopy(state.PendingBytes, 0, combined, 0, state.PendingBytes.Length);
        segment.Span.CopyTo(combined.AsSpan(state.PendingBytes.Length));
        var (complete, newPending) = SplitCompleteLines(combined);

        if (state.Options.MaxPendingBytes is long maxP && newPending.LongLength > maxP)
        {
            return (state, ErrorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."));
        }

        if (state.Options.MaxLineBytes is long maxL &&
            (AnyLineTooLong(complete, maxL) || newPending.LongLength > maxL))
        {
            return (state, ErrorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."));
        }

        // No complete lines: only pending advanced (incomplete line / mid-UTF-8).
        // Visible records unchanged → kind=unchanged with patched pending cursor.
        if (complete.Length == 0)
        {
            if (newPending.AsSpan().SequenceEqual(state.PendingBytes))
            {
                return (state, UnchangedUpdate(state));
            }

            var pendingOnly = Clone(state);
            pendingOnly.PendingBytes = newPending;
            pendingOnly.LastAppendSegment = segment.ToArray();
            pendingOnly.Cursor = pendingOnly.Cursor with
            {
                Position = pendingOnly.Cursor.Position with
                {
                    PendingByteLength = newPending.LongLength,
                },
            };
            return (pendingOnly, UnchangedUpdate(pendingOnly));
        }

        var newPrefix = new byte[state.CommittedPrefix.Length + complete.Length];
        Buffer.BlockCopy(state.CommittedPrefix, 0, newPrefix, 0, state.CommittedPrefix.Length);
        Buffer.BlockCopy(complete, 0, newPrefix, state.CommittedPrefix.Length, complete.Length);

        var tmp = Clone(state);
        tmp.PendingBytes = Array.Empty<byte>();
        var rev = sourceRevision ?? state.Cursor.SourceRevision ?? "";
        var (newState, update) = ApplySnapshot(tmp, newPrefix, rev, cursor: null);
        // Failure-atomic: failed/reset snapshot leaves prior state and pending intact.
        if (update.Kind is not ("updated" or "unchanged"))
        {
            return (state, update);
        }

        newState.PendingBytes = newPending;
        newState.LastAppendSegment = segment.ToArray();
        newState.Cursor = newState.Cursor with
        {
            Position = newState.Cursor.Position with
            {
                PendingByteLength = newPending.LongLength,
            },
        };
        // Always copy patched cursor onto StreamUpdate (updated and unchanged).
        update = update with { Cursor = newState.Cursor };
        if (update.Kind == "updated")
        {
            var priorLen = state.CommittedPrefix.LongLength;
            var completeLen = complete.LongLength;
            update = update with
            {
                Consumed = new StreamConsumed
                {
                    CompleteRecords = update.Consumed.CompleteRecords,
                    Bytes = (ulong)completeLen,
                    FirstSourcePosition = completeLen > 0 ? priorLen : null,
                    LastSourcePosition = completeLen > 0 ? priorLen + completeLen - 1 : null,
                },
            };
        }

        return (newState, update);
    }

    /// <summary>End-of-stream: optionally commit final unterminated line; finalize records.</summary>
    public static (StreamState State, StreamUpdate Update) Finish(StreamState state)
    {
        ArgumentNullException.ThrowIfNull(state);
        if (state.Finished)
        {
            return (state, UnchangedUpdate(state));
        }

        var material = state.CommittedPrefix;
        var pending = state.PendingBytes;
        if (pending.Length > 0 && !IsWhitespaceOnly(pending))
        {
            var withNl = new byte[material.Length + pending.Length + 1];
            Buffer.BlockCopy(material, 0, withNl, 0, material.Length);
            Buffer.BlockCopy(pending, 0, withNl, material.Length, pending.Length);
            withNl[^1] = (byte)'\n';
            material = withNl;
            pending = Array.Empty<byte>();
        }

        var (midState, midUpdate) = ApplySnapshot(
            state,
            material,
            state.Cursor.SourceRevision ?? "finish",
            cursor: null);
        if (midUpdate.Kind is not ("updated" or "unchanged"))
        {
            return (midState, midUpdate);
        }

        var baseSnapshot = midState.Snapshot;
        if (baseSnapshot is null)
        {
            midState.Finished = true;
            return (midState, midUpdate);
        }

        List<StreamRecord> finalized;
        if (state.Options.FinalizeOnClose)
        {
            finalized = baseSnapshot.Records.Select(rec =>
            {
                if (rec.Status == "final")
                {
                    return rec;
                }

                return rec with
                {
                    Status = "final",
                    FinalizesProvisionalId = rec.FinalizesProvisionalId ?? rec.ProvisionalId,
                };
            }).ToList();
        }
        else
        {
            finalized = baseSnapshot.Records.ToList();
        }

        var generation = midState.Generation;
        var parentRevisionId = baseSnapshot.Revision.RevisionId;
        var revisionNum = midState.NextRevision;
        var prefixSha = midState.Cursor.PrefixSha256 ?? Sha256Hex(ReadOnlySpan<byte>.Empty);
        var recordIds = finalized
            .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
            .Where(s => s.Length > 0)
            .ToArray();
        var revId = RevisionId(
            generation,
            revisionNum,
            midState.Cursor.Source,
            baseSnapshot.GroupId,
            prefixSha,
            recordIds);
        var revision = new StreamRevision
        {
            Revision = revisionNum,
            RevisionId = revId,
            ParentRevisionId = parentRevisionId,
            Complete = true,
            Generation = generation,
        };
        var snapshot = new StreamSnapshot
        {
            Source = baseSnapshot.Source,
            GroupId = baseSnapshot.GroupId,
            Revision = revision,
            Records = finalized,
            Diagnostics = baseSnapshot.Diagnostics,
            Complete = true,
        };
        var delta = DiffSnapshots(baseSnapshot, snapshot, revision);
        var (outSnap, outDelta) = ApplyDelivery(snapshot, delta, state.Options.Delivery);
        var newState = Clone(midState);
        newState.Finished = true;
        newState.PendingBytes = Array.Empty<byte>();
        newState.CommittedPrefix = material;
        newState.Snapshot = snapshot;
        newState.Cursor = new StreamCursor
        {
            Source = midState.Cursor.Source,
            GroupId = snapshot.GroupId,
            Generation = generation,
            Position = new BytePosition
            {
                NextByteOffset = material.LongLength,
                PendingByteLength = 0,
            },
            SourceRevision = midState.Cursor.SourceRevision,
            PrefixSha256 = Sha256Hex(material),
        };
        newState.NextRevision = revisionNum + 1;
        var update = new StreamUpdate
        {
            Kind = "updated",
            Revision = revision,
            Cursor = newState.Cursor,
            Snapshot = outSnap,
            Delta = outDelta,
            Diagnostics = snapshot.Diagnostics,
            Provisional = new StreamProvisionalInfo
            {
                Include = state.Options.IncludeProvisional,
                ProvisionalIds = Array.Empty<string>(),
                FinalizedIds = finalized
                    .Where(r => !string.IsNullOrEmpty(r.FinalizesProvisionalId))
                    .Select(r => r.FinalizesProvisionalId!)
                    .ToArray(),
            },
            Consumed = new StreamConsumed
            {
                CompleteRecords = (ulong)finalized.Count,
                Bytes = (ulong)material.LongLength,
            },
        };
        return (newState, update);
    }

    /// <summary>Install a new generation after reset-required or manual restart.</summary>
    public static (StreamState State, StreamUpdate Update) Reset(
        StreamState state,
        StreamResetRequest request)
    {
        ArgumentNullException.ThrowIfNull(state);
        ArgumentNullException.ThrowIfNull(request);

        var generation = request.Generation ?? state.Generation + 1;
        var groupId = state.Options.GroupId ?? state.Cursor.GroupId;
        var newState = Clone(state);
        newState.Generation = generation;
        newState.NextRevision = 0;
        newState.Finished = false;
        newState.PendingBytes = Array.Empty<byte>();
        newState.CommittedPrefix = Array.Empty<byte>();
        newState.Snapshot = null;
        newState.GroupLocked = false;
        newState.LastAppendSegment = null;
        newState.Cursor = new StreamCursor
        {
            Source = state.Cursor.Source,
            GroupId = groupId,
            Generation = generation,
            Position = new BytePosition { NextByteOffset = 0, PendingByteLength = 0 },
            SourceRevision = request.SourceRevision,
            PrefixSha256 = null,
        };

        var dropped = state.Snapshot?.Records
            .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
            .Where(s => s.Length > 0)
            .ToArray() ?? Array.Empty<string>();
        var resetMeta = new StreamReset
        {
            Reason = request.Reason,
            PriorCursor = request.PriorCursor ?? state.Cursor,
            RequiresSnapshot = request.Material is null,
            DroppedRecordIds = dropped,
        };

        if (request.Material is { } material)
        {
            var (applied, update) = ApplySnapshot(
                newState,
                material,
                request.SourceRevision ?? "",
                cursor: null);
            if (update.Kind is not ("updated" or "unchanged"))
            {
                return (applied, update);
            }

            StreamDelta? delta = update.Delta;
            if (delta is not null)
            {
                var ops = new List<StreamDeltaOperation>
                {
                    new()
                    {
                        Op = "reset",
                        Payload = new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            ["reset"] = ResetToDict(resetMeta),
                        },
                    },
                };
                ops.AddRange(delta.Operations);
                delta = delta with { Operations = ops };
            }

            return (applied, update with { Delta = delta, Reset = resetMeta });
        }

        // Empty reset with no material → updated empty snapshot of new generation.
        var emptySha = Sha256Hex(ReadOnlySpan<byte>.Empty);
        var revision = new StreamRevision
        {
            Revision = 0,
            RevisionId = RevisionId(generation, 0, newState.Cursor.Source, groupId, emptySha, Array.Empty<string>()),
            ParentRevisionId = null,
            Complete = false,
            Generation = generation,
        };
        var snapshot = new StreamSnapshot
        {
            Source = newState.Cursor.Source,
            GroupId = groupId,
            Revision = revision,
            Records = Array.Empty<StreamRecord>(),
            Diagnostics = Array.Empty<StreamDiagnostic>(),
            Complete = false,
        };
        var baseDelta = DiffSnapshots(null, snapshot, revision);
        var resetOps = new List<StreamDeltaOperation>
        {
            new()
            {
                Op = "reset",
                Payload = new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    ["reset"] = ResetToDict(resetMeta),
                },
            },
        };
        resetOps.AddRange(baseDelta.Operations);
        var fullDelta = baseDelta with { Operations = resetOps };
        var (outSnap, outDelta) = ApplyDelivery(snapshot, fullDelta, state.Options.Delivery);
        newState.Snapshot = snapshot;
        newState.NextRevision = 1;
        newState.Cursor = new StreamCursor
        {
            Source = newState.Cursor.Source,
            GroupId = groupId,
            Generation = generation,
            Position = new BytePosition { NextByteOffset = 0, PendingByteLength = 0 },
            SourceRevision = request.SourceRevision,
            PrefixSha256 = emptySha,
        };
        return (newState, new StreamUpdate
        {
            Kind = "updated",
            Revision = revision,
            Cursor = newState.Cursor,
            Snapshot = outSnap,
            Delta = outDelta,
            Provisional = EmptyProvisional(state),
            Consumed = EmptyConsumed(),
            Reset = resetMeta,
        });
    }

    /// <summary>Pure apply(state, input) → (state, update). Failed apply leaves state unchanged when possible.</summary>
    public static (StreamState State, StreamUpdate Update) Apply(
        StreamState state,
        StreamInput input)
    {
        ArgumentNullException.ThrowIfNull(state);
        ArgumentNullException.ThrowIfNull(input);

        return input.Kind switch
        {
            "snapshot-bytes" => ApplySnapshot(
                state,
                input.Data ?? ReadOnlyMemory<byte>.Empty,
                input.SourceRevision ?? "",
                input.Cursor),
            "append-bytes" => ApplyAppend(
                state,
                input.Data ?? ReadOnlyMemory<byte>.Empty,
                input.Cursor,
                input.SourceRevision),
            "finish" => Finish(state),
            "reset" => input.Reset is null
                ? (state, ErrorUpdate(state, "invalid_input", "reset input requires a StreamResetRequest."))
                : Reset(state, input.Reset),
            "ahp-actions" or "ahp-snapshot" => (
                state,
                ErrorUpdate(state, "stream_resync_required", "AHP stream apply is not available in this slice.")),
            "hermes-export" => (
                state,
                ErrorUpdate(
                    state,
                    "stream_resync_required",
                    "Hermes export stream apply requires an optional provider.")),
            _ => (state, ErrorUpdate(state, "invalid_input", "Stream input kind is not supported for this source.")),
        };
    }

    public static StreamDelta DiffSnapshots(
        StreamSnapshot? prior,
        StreamSnapshot current,
        StreamRevision revision)
    {
        var priorRecords = prior?.Records ?? Array.Empty<StreamRecord>();
        var currRecords = current.Records;
        var priorByKey = priorRecords.ToDictionary(MatchKey, StringComparer.Ordinal);
        var currByKey = currRecords.ToDictionary(MatchKey, StringComparer.Ordinal);
        var ops = new List<StreamDeltaOperation>();

        foreach (var key in priorByKey.Keys.Where(k => !currByKey.ContainsKey(k)).Order(StringComparer.Ordinal))
        {
            ops.Add(new StreamDeltaOperation
            {
                Op = "remove",
                Payload = new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    ["record_id"] = key,
                    ["reason"] = "source-rewrite",
                },
            });
        }

        foreach (var rec in currRecords)
        {
            var key = MatchKey(rec);
            if (!priorByKey.TryGetValue(key, out var prev) ||
                !RecordBodyEqual(prev.Record, rec.Record))
            {
                ops.Add(new StreamDeltaOperation
                {
                    Op = "upsert",
                    Payload = new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        ["record"] = RecordToDict(rec),
                    },
                });
            }
            else if (prev.Status != rec.Status)
            {
                ops.Add(new StreamDeltaOperation
                {
                    Op = "state_change",
                    Payload = new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        ["record_id"] = key,
                        ["status"] = rec.Status,
                    },
                });
            }
        }

        var priorDiags = (prior?.Diagnostics ?? Array.Empty<StreamDiagnostic>())
            .ToDictionary(DiagnosticKey, StringComparer.Ordinal);
        var currDiags = current.Diagnostics.ToDictionary(DiagnosticKey, StringComparer.Ordinal);
        foreach (var key in priorDiags.Keys.Where(k => !currDiags.ContainsKey(k)).Order(StringComparer.Ordinal))
        {
            ops.Add(new StreamDeltaOperation
            {
                Op = "diagnostic_remove",
                Payload = new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    ["diagnostic_key"] = key,
                },
            });
        }

        foreach (var key in currDiags.Keys.Order(StringComparer.Ordinal))
        {
            var d = currDiags[key];
            if (!priorDiags.TryGetValue(key, out var prev) ||
                !DiagnosticEqual(prev, d))
            {
                ops.Add(new StreamDeltaOperation
                {
                    Op = "diagnostic_add",
                    Payload = new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        ["diagnostic"] = DiagnosticToDict(d),
                    },
                });
            }
        }

        return new StreamDelta
        {
            BaseRevisionId = prior?.Revision.RevisionId,
            Revision = revision,
            Operations = ops,
        };
    }

    /// <summary>Apply delta ops to a prior snapshot dict (delta-apply law).</summary>
    public static Dictionary<string, object?> ApplyDeltaToSnapshot(
        Dictionary<string, object?>? prior,
        Dictionary<string, object?> delta)
    {
        var baseSnap = prior is null
            ? new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["schema_id"] = SchemaId,
                ["records"] = new List<object?>(),
                ["diagnostics"] = new List<object?>(),
            }
            : DeepClone(prior);

        var records = (List<object?>)(baseSnap["records"] ??= new List<object?>());
        var diagnostics = (List<object?>)(baseSnap["diagnostics"] ??= new List<object?>());
        var ops = (IEnumerable<object?>)delta["operations"]!;

        foreach (var opObj in ops)
        {
            var op = (Dictionary<string, object?>)opObj!;
            var kind = (string)op["op"]!;
            if (kind == "upsert")
            {
                var entry = (Dictionary<string, object?>)op["record"]!;
                var key = MatchKeyDict(entry);
                var idx = records.FindIndex(r => MatchKeyDict((Dictionary<string, object?>)r!) == key);
                if (idx >= 0)
                {
                    records[idx] = entry;
                }
                else
                {
                    records.Add(entry);
                }
            }
            else if (kind == "remove")
            {
                var rid = (string)op["record_id"]!;
                records.RemoveAll(r => MatchKeyDict((Dictionary<string, object?>)r!) == rid);
            }
            else if (kind == "state_change")
            {
                var rid = (string)op["record_id"]!;
                var status = (string)op["status"]!;
                var idx = records.FindIndex(r => MatchKeyDict((Dictionary<string, object?>)r!) == rid);
                if (idx >= 0)
                {
                    var clone = DeepClone((Dictionary<string, object?>)records[idx]!);
                    clone["status"] = status;
                    records[idx] = clone;
                }
            }
            else if (kind == "diagnostic_add")
            {
                var d = (Dictionary<string, object?>)op["diagnostic"]!;
                var key = DiagnosticKeyDict(d);
                diagnostics.RemoveAll(x => DiagnosticKeyDict((Dictionary<string, object?>)x!) == key);
                diagnostics.Add(d);
            }
            else if (kind == "diagnostic_remove")
            {
                var key = (string)op["diagnostic_key"]!;
                diagnostics.RemoveAll(x => DiagnosticKeyDict((Dictionary<string, object?>)x!) == key);
            }
            else if (kind == "finalize")
            {
                var pid = (string)op["provisional_id"]!;
                records.RemoveAll(r =>
                {
                    var dict = (Dictionary<string, object?>)r!;
                    return (dict.TryGetValue("provisional_id", out var p) && p is string s && s == pid)
                           || MatchKeyDict(dict) == pid;
                });
                var entry = (Dictionary<string, object?>)op["record"]!;
                var key = MatchKeyDict(entry);
                var idx = records.FindIndex(r => MatchKeyDict((Dictionary<string, object?>)r!) == key);
                if (idx >= 0)
                {
                    records[idx] = entry;
                }
                else
                {
                    records.Add(entry);
                }
            }
            else if (kind == "reset")
            {
                records.Clear();
                diagnostics.Clear();
            }
        }

        if (delta.TryGetValue("revision", out var rev) && rev is not null)
        {
            baseSnap["revision"] = rev;
            if (rev is Dictionary<string, object?> revDict &&
                revDict.TryGetValue("complete", out var complete))
            {
                baseSnap["complete"] = complete;
            }
        }

        return baseSnap;
    }

    public static Dictionary<string, object?> SnapshotToDict(StreamSnapshot snapshot)
    {
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_id"] = snapshot.SchemaId,
            ["source"] = snapshot.Source,
            ["group_id"] = snapshot.GroupId,
            ["revision"] = RevisionToDict(snapshot.Revision),
            ["records"] = snapshot.Records.Select(RecordToDict).Cast<object?>().ToList(),
            ["diagnostics"] = snapshot.Diagnostics.Select(DiagnosticToDict).Cast<object?>().ToList(),
            ["complete"] = snapshot.Complete,
        };
    }

    /// <summary>Serialize a stream update to wire snake_case JSON objects.</summary>
    public static Dictionary<string, object?> UpdateToDict(StreamUpdate update)
    {
        ArgumentNullException.ThrowIfNull(update);
        var consumed = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["complete_records"] = update.Consumed.CompleteRecords,
            ["bytes"] = update.Consumed.Bytes,
        };
        if (update.Consumed.FirstSourcePosition is not null)
        {
            consumed["first_source_position"] = update.Consumed.FirstSourcePosition;
        }

        if (update.Consumed.LastSourcePosition is not null)
        {
            consumed["last_source_position"] = update.Consumed.LastSourcePosition;
        }

        var provisional = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["include"] = update.Provisional.Include,
            ["provisional_ids"] = update.Provisional.ProvisionalIds.ToList(),
            ["finalized_ids"] = update.Provisional.FinalizedIds.ToList(),
        };
        var dict = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["kind"] = update.Kind,
            ["revision"] = RevisionToDict(update.Revision),
            ["cursor"] = CursorToDict(update.Cursor),
            ["snapshot"] = update.Snapshot is null ? null : SnapshotToDict(update.Snapshot),
            ["delta"] = update.Delta is null ? null : DeltaToDict(update.Delta),
            ["diagnostics"] = update.Diagnostics.Select(DiagnosticToDict).Cast<object?>().ToList(),
            ["provisional"] = provisional,
            ["consumed"] = consumed,
        };
        if (update.Reset is not null)
        {
            dict["reset"] = ResetToDict(update.Reset);
        }

        if (update.Error is { } err)
        {
            dict["error"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["code"] = err.Code,
                ["message"] = err.Message,
            };
        }

        return dict;
    }

    public static Dictionary<string, object?> DeltaToDict(StreamDelta delta)
    {
        var ops = new List<object?>();
        foreach (var op in delta.Operations)
        {
            var dict = new Dictionary<string, object?>(op.Payload, StringComparer.Ordinal)
            {
                ["op"] = op.Op,
            };
            ops.Add(dict);
        }

        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_id"] = delta.SchemaId,
            ["base_revision_id"] = delta.BaseRevisionId,
            ["revision"] = RevisionToDict(delta.Revision),
            ["operations"] = ops,
        };
    }

    // ---- internals ----

    private static (List<StreamRecord>? Records, List<StreamDiagnostic>? Diagnostics, string? GroupId, StreamUpdate? Update)
        BuildRecords(StreamState state, byte[] committed)
    {
        var groupHint = state.GroupLocked ? state.Cursor.GroupId : state.Options.GroupId;
        if (committed.Length == 0)
        {
            return ([], [], groupHint ?? state.Cursor.GroupId, null);
        }

        try
        {
            var engine = TrajectoryEngine.CreateDefault();
            // Decode per LF-terminated line (strict UTF-8). Invalid complete lines
            // become adapter invalid_json_line diagnostics rather than a whole-stream
            // error, without U+FFFD substitution on valid wire bytes.
            var transcript = DecodeCommittedLinesForNormalize(committed);

            var ir = engine.NormalizeToIR(new NormalizeInput
            {
                Source = state.Options.Source,
                Transcript = transcript,
                SourceContext = new SourceContext
                {
                    GroupId = groupHint,
                    BaseByteOffset = 0,
                    Partial = true,
                },
                Options = state.Options.Normalize,
            });

            var hyp = engine.Project<HypabolicTrajectoryV1>(ir, OutputSchemaIds.HypabolicTrajectoryV1);
            var hasBackendSynth = ir.Diagnostics.Any(d => d.Code == "backend_tool_result_synthesized");
            var markProvisional = hasBackendSynth && state.Options.Source == TrajectorySource.GrokBuild;
            var records = hyp.Records.Select(r =>
            {
                var dict = HypabolicRecordToDict(r);
                if (markProvisional && IsSyntheticBackendToolResult(dict))
                {
                    var provisionalId = dict.TryGetValue("id", out var id) ? id?.ToString() : null;
                    return new StreamRecord
                    {
                        Status = "provisional",
                        Record = dict,
                        ProvisionalId = string.IsNullOrEmpty(provisionalId) ? null : provisionalId,
                    };
                }

                return new StreamRecord
                {
                    Status = "stable",
                    Record = dict,
                };
            }).ToList();
            var diagnostics = ir.Diagnostics.Select(d => new StreamDiagnostic
            {
                Code = d.Code,
                Message = d.Message,
                InputLine = d.InputLine,
                RecordIndex = d.RecordIndex,
                Count = d.Count,
            }).ToList();
            return (records, diagnostics, ir.GroupId, null);
        }
        catch (TrajectoryNormalizationException ex) when (ex.Code == NormalizationErrorCode.SourceGroupConflict)
        {
            return (null, null, null, ResetRequired(
                state,
                "group-changed",
                "stream_source_reset",
                "Source group changed relative to the active stream."));
        }
        catch (TrajectoryNormalizationException ex)
        {
            var wire = ex.Code switch
            {
                NormalizationErrorCode.InvalidInput => "invalid_input",
                NormalizationErrorCode.UnknownSource => "unknown_source",
                NormalizationErrorCode.MissingUserRecords => "missing_user_records",
                NormalizationErrorCode.MissingAssistantRecords => "missing_assistant_records",
                NormalizationErrorCode.SourceGroupConflict => "source_group_conflict",
                NormalizationErrorCode.SourceGroupRequired => "source_group_required",
                _ => "invalid_input",
            };
            return (null, null, null, ErrorUpdate(state, wire, ex.Message));
        }
    }

    /// <summary>
    /// Strict UTF-8 decode of each LF-terminated complete line. Invalid lines are
    /// replaced with a non-JSON placeholder so adapters emit <c>invalid_json_line</c>
    /// rather than failing the whole apply. Valid lines round-trip without U+FFFD.
    /// </summary>
    private static string DecodeCommittedLinesForNormalize(byte[] committed)
    {
        var utf8Strict = Encoding.GetEncoding(
            "utf-8",
            EncoderFallback.ExceptionFallback,
            DecoderFallback.ExceptionFallback);
        var sb = new StringBuilder(committed.Length);
        var start = 0;
        for (var i = 0; i < committed.Length; i++)
        {
            if (committed[i] != (byte)'\n')
            {
                continue;
            }

            var lineLen = i - start + 1;
            try
            {
                sb.Append(utf8Strict.GetString(committed, start, lineLen));
            }
            catch (DecoderFallbackException)
            {
                // Non-whitespace invalid JSON token + LF keeps line numbering aligned.
                sb.Append("!\n");
            }

            start = i + 1;
        }

        if (start < committed.Length)
        {
            // Framed committed prefixes end on LF; handle a trailing remainder strictly.
            try
            {
                sb.Append(utf8Strict.GetString(committed, start, committed.Length - start));
            }
            catch (DecoderFallbackException)
            {
                sb.Append('!');
            }
        }

        return sb.ToString();
    }

    private static Dictionary<string, object?> HypabolicRecordToDict(HypabolicRecordV1 record)
    {
        static string? Ts(DateTimeOffset? value) =>
            value?.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'");

        var dict = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["id"] = record.Id,
            ["kind"] = record.Kind,
            ["role"] = record.Role,
            ["order"] = record.Order,
            ["source_timestamp"] = Ts(record.SourceTimestamp),
            ["timestamp"] = Ts(record.Timestamp),
        };
        if (record.SourceName is not null)
        {
            dict["source_name"] = record.SourceName;
        }

        if (record.Cwd is not null)
        {
            dict["cwd"] = record.Cwd;
        }

        if (record.GitBranch is not null)
        {
            dict["git_branch"] = record.GitBranch;
        }

        if (record.Model is not null)
        {
            dict["model"] = record.Model;
        }

        if (record.ProducerVersion is not null)
        {
            dict["producer_version"] = record.ProducerVersion;
        }

        if (record.Kind == "assistant_tool_calls")
        {
            dict["content"] = null;
            dict["tool_calls"] = (record.ToolCalls ?? [])
                .Select(call => new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    ["id"] = call.Id,
                    ["name"] = call.Name,
                    ["arguments_json"] = call.ArgumentsJson,
                })
                .Cast<object?>()
                .ToList();
        }
        else if (record.Content is not null)
        {
            dict["content"] = record.Content;
        }

        if (record.ToolCallId is not null)
        {
            dict["tool_call_id"] = record.ToolCallId;
        }

        if (record.ToolName is not null)
        {
            dict["tool_name"] = record.ToolName;
        }

        if (record.IsError is { } isError)
        {
            dict["is_error"] = isError;
        }

        var provenance = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["stable_source_record_id"] = record.Provenance.StableSourceRecordId,
            ["source_identity_kind"] = record.Provenance.SourceIdentityKind,
            ["source_order_id"] = record.Provenance.SourceOrderId,
            ["component_key"] = record.Provenance.ComponentKey,
            ["component_index"] = record.Provenance.ComponentIndex,
            ["component_type_ordinal"] = record.Provenance.ComponentTypeOrdinal,
        };
        if (record.Provenance.ProducerVersion is not null)
        {
            provenance["producer_version"] = record.Provenance.ProducerVersion;
        }

        if (record.Provenance.NativeRecordId is not null)
        {
            provenance["native_record_id"] = record.Provenance.NativeRecordId;
        }

        if (record.Provenance.SourceSequence is { } seq)
        {
            provenance["source_sequence"] = seq;
        }

        if (record.Provenance.SourceOffset is { } off)
        {
            provenance["source_offset"] = off;
        }

        if (record.Provenance.SourceAnchorKind is not null)
        {
            provenance["source_anchor_kind"] = record.Provenance.SourceAnchorKind;
        }

        dict["provenance"] = provenance;
        dict["hashes"] = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["content_sha256"] = record.Hashes.ContentSha256,
            ["record_sha256"] = record.Hashes.RecordSha256,
        };
        return dict;
    }

    private static StreamState Clone(StreamState state) =>
        new()
        {
            Options = state.Options,
            Cursor = state.Cursor,
            PendingBytes = state.PendingBytes.ToArray(),
            CommittedPrefix = state.CommittedPrefix.ToArray(),
            Snapshot = state.Snapshot,
            Generation = state.Generation,
            NextRevision = state.NextRevision,
            Finished = state.Finished,
            GroupLocked = state.GroupLocked,
            LastAppendSegment = state.LastAppendSegment is null
                ? null
                : state.LastAppendSegment.ToArray(),
        };

    private static (StreamSnapshot? Snapshot, StreamDelta? Delta) ApplyDelivery(
        StreamSnapshot snapshot,
        StreamDelta delta,
        StreamDelivery delivery) =>
        delivery switch
        {
            StreamDelivery.Snapshot => (snapshot, null),
            StreamDelivery.Delta => (null, delta),
            _ => (snapshot, delta),
        };

    private static StreamProvisionalInfo EmptyProvisional(StreamState state) =>
        new()
        {
            Include = state.Options.IncludeProvisional,
            ProvisionalIds = Array.Empty<string>(),
            FinalizedIds = Array.Empty<string>(),
        };

    private static StreamConsumed EmptyConsumed() => new();

    /// <summary>True when any complete LF-terminated line exceeds <paramref name="maxLineBytes"/>.</summary>
    internal static bool AnyLineTooLong(ReadOnlySpan<byte> data, long maxLineBytes)
    {
        var start = 0;
        for (var i = 0; i < data.Length; i++)
        {
            if (data[i] == (byte)'\n')
            {
                var lineLen = i - start + 1L;
                if (lineLen > maxLineBytes)
                {
                    return true;
                }

                start = i + 1;
            }
        }

        return false;
    }

    private static StreamUpdate UnchangedUpdate(StreamState state) =>
        new()
        {
            Kind = "unchanged",
            Revision = state.Snapshot?.Revision ?? new StreamRevision
            {
                Revision = 0,
                RevisionId = "unchanged",
                ParentRevisionId = null,
                Complete = state.Finished,
                Generation = state.Generation,
            },
            Cursor = state.Cursor,
            Provisional = EmptyProvisional(state),
            Consumed = EmptyConsumed(),
        };

    private static StreamUpdate ErrorUpdate(StreamState state, string code, string message) =>
        new()
        {
            Kind = "error",
            Revision = state.Snapshot?.Revision ?? new StreamRevision
            {
                Revision = 0,
                RevisionId = "error",
                ParentRevisionId = null,
                Complete = false,
                Generation = state.Generation,
            },
            Cursor = state.Cursor,
            Provisional = EmptyProvisional(state),
            Consumed = EmptyConsumed(),
            Error = (code, message),
        };

    private static StreamUpdate? CursorConflict(StreamState state, StreamCursor cursor)
    {
        if (cursor.Source != state.Cursor.Source ||
            cursor.Generation != state.Cursor.Generation)
        {
            return ResetRequired(
                state,
                "cursor-mismatch",
                "stream_cursor_conflict",
                "Supplied stream cursor does not match stream state.");
        }

        if (state.GroupLocked && cursor.GroupId != state.Cursor.GroupId)
        {
            return ResetRequired(
                state,
                "group-changed",
                "stream_cursor_conflict",
                "Supplied stream cursor does not match stream state.");
        }

        // Domain: non-negative int64 byte positions (streaming-cursor-v1).
        // Checked before position equality so out-of-domain offsets are invalid_input,
        // not cursor-mismatch (parity with Python/TS).
        if (cursor.Position.NextByteOffset < 0 || cursor.Position.PendingByteLength < 0)
        {
            return ErrorUpdate(
                state,
                "invalid_input",
                "Stream cursor byte positions must be non-negative int64 values.");
        }

        if (cursor.Position.NextByteOffset != state.Cursor.Position.NextByteOffset)
        {
            return ResetRequired(
                state,
                "cursor-mismatch",
                "stream_cursor_conflict",
                "Supplied stream cursor does not match stream state.");
        }

        return null;
    }

    private static bool IsWhitespaceOnly(ReadOnlySpan<byte> data)
    {
        foreach (var b in data)
        {
            if (b is not (byte)' ' and not (byte)'\t' and not (byte)'\r' and not (byte)'\n')
            {
                return false;
            }
        }

        return true;
    }

    private static Dictionary<string, object?> ResetToDict(StreamReset reset) =>
        new(StringComparer.Ordinal)
        {
            ["reason"] = reset.Reason,
            ["prior_cursor"] = reset.PriorCursor is null ? null : CursorToDict(reset.PriorCursor),
            ["requires_snapshot"] = reset.RequiresSnapshot,
            ["dropped_record_ids"] = reset.DroppedRecordIds.ToList(),
        };

    private static Dictionary<string, object?> CursorToDict(StreamCursor c) =>
        new(StringComparer.Ordinal)
        {
            ["cursor_version"] = c.CursorVersion,
            ["source"] = c.Source,
            ["group_id"] = c.GroupId,
            ["generation"] = c.Generation,
            ["position"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["kind"] = c.Position.Kind,
                ["next_byte_offset"] = c.Position.NextByteOffset,
                ["pending_byte_length"] = c.Position.PendingByteLength,
            },
            ["source_revision"] = c.SourceRevision,
            ["prefix_sha256"] = c.PrefixSha256,
        };

    /// <summary>
    /// Classify shorter snapshot material: pure prefix → truncate; non-prefix
    /// rewrite on Grok Build → compacted; other sources → replaced.
    /// </summary>
    private static (string Reason, string Message) ShrinkResetReason(StreamState state, byte[] committed)
    {
        if (StartsWith(state.CommittedPrefix, committed))
        {
            return ("source-truncated", "Source material is shorter than the committed cursor.");
        }

        if (state.Options.Source == TrajectorySource.GrokBuild)
        {
            return ("source-compacted", "Source material was compacted relative to the committed cursor.");
        }

        return ("source-replaced", "Source material was replaced relative to the committed cursor.");
    }

    private static bool StartsWith(byte[] haystack, byte[] prefix)
    {
        if (prefix.Length > haystack.Length)
        {
            return false;
        }

        return haystack.AsSpan(0, prefix.Length).SequenceEqual(prefix);
    }

    private static bool IsSyntheticBackendToolResult(IReadOnlyDictionary<string, object?> record)
    {
        if (!record.TryGetValue("role", out var role) || role is not string roleStr || roleStr != "tool")
        {
            return false;
        }

        if (!record.TryGetValue("content", out var content) || content is not string text)
        {
            return false;
        }

        return text.StartsWith("[backend ", StringComparison.Ordinal);
    }

    private static StreamUpdate ResetRequired(
        StreamState state,
        string reason,
        string code,
        string message) =>
        new()
        {
            Kind = "reset-required",
            Revision = state.Snapshot?.Revision ?? new StreamRevision
            {
                Revision = 0,
                RevisionId = "reset-required",
                ParentRevisionId = null,
                Complete = false,
                Generation = state.Generation,
            },
            Cursor = state.Cursor,
            Diagnostics =
            [
                new StreamDiagnostic { Code = code, Message = message },
            ],
            Provisional = EmptyProvisional(state),
            Consumed = EmptyConsumed(),
            Reset = new StreamReset
            {
                Reason = reason,
                PriorCursor = state.Cursor,
                RequiresSnapshot = true,
                DroppedRecordIds = state.Snapshot?.Records
                    .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
                    .Where(s => s.Length > 0)
                    .ToArray() ?? Array.Empty<string>(),
            },
        };

    private static string RevisionId(
        ulong generation,
        ulong revision,
        string source,
        string groupId,
        string prefixSha,
        IReadOnlyList<string> recordIds) =>
        DeterministicIdentity.Sha256Hex($"{generation}|{revision}|{source}|{groupId}|{prefixSha}|{string.Join(",", recordIds)}");

    private static string Sha256Hex(ReadOnlySpan<byte> data) =>
        DeterministicIdentity.Sha256Hex(data);

    private static string SourceWireName(TrajectorySource source) =>
        source switch
        {
            TrajectorySource.Pi => "pi",
            TrajectorySource.ClaudeCode => "claude-code",
            TrajectorySource.Codex => "codex",
            TrajectorySource.OpenClaw => "openclaw",
            TrajectorySource.Hermes => "hermes",
            TrajectorySource.Ahp => "ahp",
            TrajectorySource.GrokBuild => "grok-build",
            _ => source.ToString().ToLowerInvariant(),
        };

    private static Dictionary<string, object?> RecordToDict(StreamRecord r)
    {
        var d = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["status"] = r.Status,
            ["record"] = r.Record,
        };
        if (r.ProvisionalId is not null)
        {
            d["provisional_id"] = r.ProvisionalId;
        }

        return d;
    }

    private static Dictionary<string, object?> DiagnosticToDict(StreamDiagnostic d)
    {
        var dict = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["code"] = d.Code,
            ["message"] = d.Message,
        };
        if (d.InputLine is not null)
        {
            dict["input_line"] = d.InputLine;
        }

        if (d.RecordIndex is not null)
        {
            dict["record_index"] = d.RecordIndex;
        }

        if (d.Count is not null)
        {
            dict["count"] = d.Count;
        }

        return dict;
    }

    private static Dictionary<string, object?> RevisionToDict(StreamRevision r) =>
        new(StringComparer.Ordinal)
        {
            ["revision"] = r.Revision,
            ["revision_id"] = r.RevisionId,
            ["parent_revision_id"] = r.ParentRevisionId,
            ["complete"] = r.Complete,
            ["generation"] = r.Generation,
        };

    private static string MatchKeyDict(Dictionary<string, object?> record)
    {
        if (record.TryGetValue("provisional_id", out var p) && p is string pid && pid.Length > 0)
        {
            return pid;
        }

        if (record.TryGetValue("record", out var body) &&
            body is Dictionary<string, object?> b &&
            b.TryGetValue("id", out var id) &&
            id is string s)
        {
            return s;
        }

        throw new InvalidOperationException("stream record missing match key");
    }

    private static string DiagnosticKeyDict(Dictionary<string, object?> d)
    {
        var code = d["code"]?.ToString() ?? "";
        var line = d.TryGetValue("input_line", out var l) && l is not null ? l.ToString() : "-";
        var index = d.TryGetValue("record_index", out var i) && i is not null ? i.ToString() : "-";
        return $"{code}|{line}|{index}";
    }

    private static bool RecordBodyEqual(Dictionary<string, object?> a, Dictionary<string, object?> b) =>
        DeepValueEqual(a, b);

    private static bool DeepValueEqual(object? left, object? right)
    {
        if (ReferenceEquals(left, right))
        {
            return true;
        }

        if (left is null || right is null)
        {
            return false;
        }

        if (left is Dictionary<string, object?> ld && right is Dictionary<string, object?> rd)
        {
            if (ld.Count != rd.Count)
            {
                return false;
            }

            foreach (var (key, lv) in ld)
            {
                if (!rd.TryGetValue(key, out var rv) || !DeepValueEqual(lv, rv))
                {
                    return false;
                }
            }

            return true;
        }

        if (left is IList ll && right is IList rl && left is not string && right is not string)
        {
            if (ll.Count != rl.Count)
            {
                return false;
            }

            for (var i = 0; i < ll.Count; i++)
            {
                if (!DeepValueEqual(ll[i], rl[i]))
                {
                    return false;
                }
            }

            return true;
        }

        if (left is JsonElement jl && right is JsonElement jr)
        {
            return jl.ToString() == jr.ToString();
        }

        return left.Equals(right) ||
               string.Equals(Convert.ToString(left, CultureInfo.InvariantCulture),
                   Convert.ToString(right, CultureInfo.InvariantCulture),
                   StringComparison.Ordinal);
    }

    private static bool DiagnosticEqual(StreamDiagnostic a, StreamDiagnostic b) =>
        a.Code == b.Code && a.Message == b.Message && a.InputLine == b.InputLine &&
        a.RecordIndex == b.RecordIndex && a.Count == b.Count;

    private static Dictionary<string, object?> DeepClone(Dictionary<string, object?> source)
    {
        var clone = new Dictionary<string, object?>(StringComparer.Ordinal);
        foreach (var (key, value) in source)
        {
            clone[key] = DeepCloneValue(value);
        }

        return clone;
    }

    private static object? DeepCloneValue(object? value) =>
        value switch
        {
            null => null,
            Dictionary<string, object?> dict => DeepClone(dict),
            List<object?> list => list.Select(DeepCloneValue).ToList(),
            IList<object?> list => list.Select(DeepCloneValue).Cast<object?>().ToList(),
            JsonElement el => JsonElementToObject(el),
            _ => value,
        };

    private static object? JsonElementToObject(JsonElement el) =>
        el.ValueKind switch
        {
            JsonValueKind.Object => el.EnumerateObject()
                .ToDictionary(p => p.Name, p => JsonElementToObject(p.Value), StringComparer.Ordinal),
            JsonValueKind.Array => el.EnumerateArray().Select(JsonElementToObject).Cast<object?>().ToList(),
            JsonValueKind.String => el.GetString(),
            JsonValueKind.Number => el.TryGetInt64(out var l) ? l : el.GetDouble(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.Null => null,
            _ => el.ToString(),
        };
}

/// <summary>Mutable façade over <see cref="StreamState"/>.</summary>
public sealed class TrajectoryStreamSession
{
    private StreamState _state;

    private TrajectoryStreamSession(StreamState state) => _state = state;

    public static TrajectoryStreamSession Create(StreamOptions options) =>
        new(TrajectoryStream.Create(options));

    public StreamCursor Cursor => _state.Cursor;
    public StreamState State => _state;

    public StreamUpdate ApplySnapshot(
        ReadOnlyMemory<byte> prefix,
        string sourceRevision,
        StreamCursor? cursor = null)
    {
        var (state, update) = TrajectoryStream.ApplySnapshot(_state, prefix, sourceRevision, cursor);
        _state = state;
        return update;
    }

    public StreamUpdate ApplyAppend(
        ReadOnlyMemory<byte> segment,
        StreamCursor? cursor = null,
        string? sourceRevision = null)
    {
        var (state, update) = TrajectoryStream.ApplyAppend(_state, segment, cursor, sourceRevision);
        _state = state;
        return update;
    }

    public StreamUpdate Finish()
    {
        var (state, update) = TrajectoryStream.Finish(_state);
        _state = state;
        return update;
    }

    public StreamUpdate Reset(StreamResetRequest request)
    {
        var (state, update) = TrajectoryStream.Reset(_state, request);
        _state = state;
        return update;
    }

    public StreamUpdate Apply(StreamInput input)
    {
        var (state, update) = TrajectoryStream.Apply(_state, input);
        _state = state;
        return update;
    }
}
