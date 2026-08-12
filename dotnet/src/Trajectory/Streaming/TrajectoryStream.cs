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
            if (cursor.Source != state.Cursor.Source ||
                cursor.Generation != state.Cursor.Generation ||
                cursor.Position.NextByteOffset != state.Cursor.Position.NextByteOffset)
            {
                return (state, ResetRequired(
                    state,
                    "cursor-mismatch",
                    "stream_cursor_conflict",
                    "Supplied stream cursor does not match stream state."));
            }
        }

        var (committed, pending) = state.Options.RequireCompleteLines
            ? SplitCompleteLines(material.Span)
            : (material.ToArray(), Array.Empty<byte>());

        if (state.Options.MaxPendingBytes is long maxPending && pending.LongLength > maxPending)
        {
            return (state, ErrorUpdate(state, "stream_buffer_limit", "Stream buffer limit exceeded."));
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
            return (state, ResetRequired(
                state,
                "source-truncated",
                "stream_source_reset",
                "Source material is shorter than the committed cursor."));
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
        var update = new StreamUpdate
        {
            Kind = "updated",
            Revision = revision,
            Cursor = newCursor,
            Snapshot = outSnap,
            Delta = outDelta,
            Diagnostics = diagnostics,
        };
        newState.Cursor = newCursor;
        newState.Snapshot = snapshot;
        newState.PendingBytes = pending;
        newState.CommittedPrefix = committed;
        newState.NextRevision = revisionNum + 1;
        return (newState, update);
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
            var transcript = Encoding.UTF8.GetString(committed);
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
            var records = hyp.Records.Select(r => new StreamRecord
            {
                Status = "stable",
                Record = HypabolicRecordToDict(r),
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
            Error = (code, message),
        };

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
        JsonSerializer.Serialize(a) == JsonSerializer.Serialize(b);

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
}
