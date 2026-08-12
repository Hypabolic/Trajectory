using System.Text.Json.Nodes;

namespace Hypabolic.Trajectory.Streaming;

/// <summary>Delivery preference for stream updates. Default is both snapshot and delta.</summary>
public enum StreamDelivery
{
    Both = 0,
    Snapshot = 1,
    Delta = 2,
}

/// <summary>Public stream options (pure core; no I/O).</summary>
public sealed record StreamOptions
{
    public required TrajectorySource Source { get; init; }
    public string? GroupId { get; init; }
    public StreamDelivery Delivery { get; init; } = StreamDelivery.Both;
    public bool IncludeProvisional { get; init; } = true;
    public bool RequireCompleteLines { get; init; } = true;
    public bool FinalizeOnClose { get; init; } = true;
    public NormalizeOptions? Normalize { get; init; }
    public long? MaxPendingBytes { get; init; }
    public long? MaxLineBytes { get; init; }
    /// <summary>Pinned AHP protocol version for Shape B → Shape A materialization (default 0.7.0).</summary>
    public string? AhpProtocolVersion { get; init; }
}

/// <summary>Discriminated stream cursor position (cursor_version 1).</summary>
public abstract record StreamPosition
{
    public abstract string Kind { get; }
}

/// <summary>Byte cursor position for file JSONL sources.</summary>
public sealed record BytePosition : StreamPosition
{
    public override string Kind => "byte";
    public long NextByteOffset { get; init; }
    public long PendingByteLength { get; init; }
}

/// <summary>AHP serverSeq cursor position (Shape B action-log authority).</summary>
public sealed record AhpServerSeqPosition : StreamPosition
{
    public override string Kind => "ahp-server-seq";
    public long NextServerSeq { get; init; }
    public long LastServerSeq { get; init; }
    public long? NextByteOffset { get; init; }
}

/// <summary>AHP snapshot-revision cursor position (Shape A snapshot authority).</summary>
public sealed record SnapshotRevisionPosition : StreamPosition
{
    public override string Kind => "snapshot-revision";
    public required string Revision { get; init; }
    public string? ContentSha256 { get; init; }
}

/// <summary>Hermes provider row cursor (LS-07h).</summary>
public sealed record HermesRowPosition : StreamPosition
{
    public override string Kind => "hermes-row";
    public required string DatabaseGeneration { get; init; }
    public long? LastRowId { get; init; }
    public string? ChangeToken { get; init; }
}

/// <summary>Public serializable stream position checkpoint.</summary>
public sealed record StreamCursor
{
    public int CursorVersion { get; init; } = 1;
    public required string Source { get; init; }
    public required string GroupId { get; init; }
    public ulong Generation { get; init; }
    public required StreamPosition Position { get; init; }
    public string? SourceRevision { get; init; }
    public string? PrefixSha256 { get; init; }
}

/// <summary>Stream-local revision metadata.</summary>
public sealed record StreamRevision
{
    public ulong Revision { get; init; }
    public required string RevisionId { get; init; }
    public string? ParentRevisionId { get; init; }
    public bool Complete { get; init; }
    public ulong Generation { get; init; }
}

/// <summary>Content-safe stream diagnostic (snake_case wire fields).</summary>
public sealed record StreamDiagnostic
{
    public required string Code { get; init; }
    public required string Message { get; init; }
    public int? InputLine { get; init; }
    public int? RecordIndex { get; init; }
    public int? Count { get; init; }
}

/// <summary>Stream record with lifecycle status.</summary>
public sealed record StreamRecord
{
    public required string Status { get; init; }
    public required Dictionary<string, object?> Record { get; init; }
    public string? ProvisionalId { get; init; }
    public string? ReplacesProvisionalId { get; init; }
    public string? FinalizesProvisionalId { get; init; }
}

/// <summary>Full stream snapshot.</summary>
public sealed record StreamSnapshot
{
    public string SchemaId { get; init; } = "trajectory-stream-v1";
    public required string Source { get; init; }
    public required string GroupId { get; init; }
    public required StreamRevision Revision { get; init; }
    public required IReadOnlyList<StreamRecord> Records { get; init; }
    public required IReadOnlyList<StreamDiagnostic> Diagnostics { get; init; }
    public bool Complete { get; init; }
}

/// <summary>One delta operation.</summary>
public sealed record StreamDeltaOperation
{
    public required string Op { get; init; }
    public Dictionary<string, object?> Payload { get; init; } = new(StringComparer.Ordinal);
}

/// <summary>Ordered stream delta.</summary>
public sealed record StreamDelta
{
    public string SchemaId { get; init; } = "trajectory-stream-v1";
    public string? BaseRevisionId { get; init; }
    public required StreamRevision Revision { get; init; }
    public required IReadOnlyList<StreamDeltaOperation> Operations { get; init; }
}

/// <summary>Reset metadata on reset-required results.</summary>
public sealed record StreamReset
{
    public required string Reason { get; init; }
    public StreamCursor? PriorCursor { get; init; }
    public bool RequiresSnapshot { get; init; }
    public IReadOnlyList<string> DroppedRecordIds { get; init; } = Array.Empty<string>();
}

/// <summary>Caller reset request.</summary>
public sealed record StreamResetRequest
{
    public required string Reason { get; init; }
    public ulong? Generation { get; init; }
    public string? SourceRevision { get; init; }
    public StreamCursor? PriorCursor { get; init; }
    public ReadOnlyMemory<byte>? Material { get; init; }
    public string? ChangeToken { get; init; }
}

/// <summary>Discriminated stream input for pure apply(state, input).</summary>
public sealed record StreamInput
{
    /// <summary>
    /// One of: append-bytes, snapshot-bytes, finish, reset,
    /// ahp-actions, ahp-snapshot, hermes-export.
    /// </summary>
    public required string Kind { get; init; }
    public ReadOnlyMemory<byte>? Data { get; init; }
    public string? SourceRevision { get; init; }
    public StreamCursor? Cursor { get; init; }
    public StreamResetRequest? Reset { get; init; }
    public string? ChangeToken { get; init; }
    public string? DatabaseGeneration { get; init; }
}

/// <summary>Provisional lifecycle summary on a stream update envelope.</summary>
public sealed record StreamProvisionalInfo
{
    public bool Include { get; init; } = true;
    public IReadOnlyList<string> ProvisionalIds { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> FinalizedIds { get; init; } = Array.Empty<string>();
}

/// <summary>Consumed-progress summary on a stream update envelope.</summary>
public sealed record StreamConsumed
{
    public ulong CompleteRecords { get; init; }
    public ulong Bytes { get; init; }
    public long? FirstSourcePosition { get; init; }
    public long? LastSourcePosition { get; init; }
}

/// <summary>Stream update envelope.</summary>
public sealed record StreamUpdate
{
    public required string Kind { get; init; }
    public required StreamRevision Revision { get; init; }
    public required StreamCursor Cursor { get; init; }
    public StreamSnapshot? Snapshot { get; init; }
    public StreamDelta? Delta { get; init; }
    public IReadOnlyList<StreamDiagnostic> Diagnostics { get; init; } = Array.Empty<StreamDiagnostic>();
    public StreamProvisionalInfo Provisional { get; init; } = new();
    public StreamConsumed Consumed { get; init; } = new();
    public StreamReset? Reset { get; init; }
    public (string Code, string Message)? Error { get; init; }
}

/// <summary>Runtime-local stream state (not a wire format).</summary>
public sealed class StreamState
{
    public required StreamOptions Options { get; init; }
    public StreamCursor Cursor { get; set; } = null!;
    public byte[] PendingBytes { get; set; } = Array.Empty<byte>();
    public byte[] CommittedPrefix { get; set; } = Array.Empty<byte>();
    public StreamSnapshot? Snapshot { get; set; }
    public ulong Generation { get; set; }
    public ulong NextRevision { get; set; }
    public bool Finished { get; set; }
    public bool GroupLocked { get; set; }
    /// <summary>
    /// Last accepted append-bytes segment + pre-apply next_byte_offset.
    /// True replay requires re-supply with that pre-apply cursor (not content alone).
    /// </summary>
    public byte[]? LastAppendSegment { get; set; }
    public long? LastAppendPreOffset { get; set; }

    // ---- AHP stream state (LS-06 / LS-07) ----
    public JsonObject? AhpChatState { get; set; }
    public JsonObject? AhpSession { get; set; }
    public string? AhpProtocolVersion { get; set; }
    public long? AhpLastServerSeq { get; set; }
    public string? AhpTargetChannel { get; set; }
    public string? AhpLastSnapshotRevision { get; set; }
    public string? AhpLastContentSha256 { get; set; }
    public string? LastAhpActionsSha256 { get; set; }
    public long? LastAhpActionsPreSeq { get; set; }

    // ---- Hermes export stream state (LS-07h) ----
    public IReadOnlyList<string>? HermesRowFingerprints { get; set; }
    public string? HermesLastExportSha { get; set; }
}
