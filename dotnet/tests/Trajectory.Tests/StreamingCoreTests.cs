using System.Text;
using Hypabolic.Trajectory.Streaming;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

/// <summary>LS-03 / LS-04: stream state, snapshot apply, delta-apply equivalence.</summary>
public sealed class StreamingCoreTests
{
    private static string FixturesRoot =>
        Path.Combine(AppContext.BaseDirectory, "Fixtures", "streaming");

    private static byte[] ReadFixture(string caseId, string name)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", "streaming", caseId, name);
        Assert.True(File.Exists(path), $"missing fixture {path}");
        return File.ReadAllBytes(path);
    }

    [Fact]
    public void EmptyPrefix_ProducesEmptySnapshotAndIdempotentReplay()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-empty-prefix",
        });
        var (state1, update) = TrajectoryStream.ApplySnapshot(state, ReadOnlyMemory<byte>.Empty, "gen-0");
        Assert.Equal("updated", update.Kind);
        Assert.NotNull(update.Snapshot);
        Assert.NotNull(update.Delta);
        Assert.Empty(update.Snapshot!.Records);
        Assert.False(update.Snapshot.Complete);

        var (_, update2) = TrajectoryStream.ApplySnapshot(state1, ReadOnlyMemory<byte>.Empty, "gen-0");
        Assert.Equal("unchanged", update2.Kind);
    }

    [Fact]
    public void SnapshotDeltaEquivalence_Holds()
    {
        var a = ReadFixture("snapshot-delta-equivalence", "step-a.jsonl");
        var b = ReadFixture("snapshot-delta-equivalence", "step-b.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-snapshot-delta-equivalence",
        });
        var (state1, u1) = TrajectoryStream.ApplySnapshot(state, a, "gen-0");
        Assert.Equal("updated", u1.Kind);
        Assert.NotNull(u1.Snapshot);
        Assert.NotNull(u1.Delta);

        var prior = TrajectoryStream.SnapshotToDict(u1.Snapshot!);
        var (state2, u2) = TrajectoryStream.ApplySnapshot(state1, b, "gen-0");
        Assert.Equal("updated", u2.Kind);
        var delta = TrajectoryStream.DeltaToDict(u2.Delta!);
        var recon = TrajectoryStream.ApplyDeltaToSnapshot(prior, delta);
        var snap = TrajectoryStream.SnapshotToDict(u2.Snapshot!);
        Assert.Equal(
            System.Text.Json.JsonSerializer.Serialize(snap["records"]),
            System.Text.Json.JsonSerializer.Serialize(recon["records"]));
    }

    [Fact]
    public void SourceGroupConflict_ReturnsResetRequired()
    {
        var m1 = ReadFixture("source-group-conflict", "step-matching.jsonl");
        var m2 = ReadFixture("source-group-conflict", "step-foreign-group.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-expected-group",
        });
        var (state1, u1) = TrajectoryStream.ApplySnapshot(state, m1, "gen-0");
        Assert.Equal("updated", u1.Kind);
        var priorOffset = state1.Cursor.Position.NextByteOffset;
        var (state2, u2) = TrajectoryStream.ApplySnapshot(state1, m2, "gen-0");
        Assert.Equal("reset-required", u2.Kind);
        Assert.Equal("group-changed", u2.Reset!.Reason);
        Assert.Equal(priorOffset, state2.Cursor.Position.NextByteOffset);
    }

    [Fact]
    public void FileTruncate_ReturnsSourceTruncated()
    {
        var longBytes = ReadFixture("file-truncate-reset", "step-long.jsonl");
        var shortBytes = ReadFixture("file-truncate-reset", "step-truncated.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-truncate-reset",
        });
        var (state1, _) = TrajectoryStream.ApplySnapshot(state, longBytes, "gen-0");
        var (_, u2) = TrajectoryStream.ApplySnapshot(state1, shortBytes, "gen-0");
        Assert.Equal("reset-required", u2.Kind);
        Assert.Equal("source-truncated", u2.Reset!.Reason);
    }

    [Fact]
    public void SplitCompleteLines_HoldsUnterminated()
    {
        var (committed, pending) = TrajectoryStream.SplitCompleteLines(
            Encoding.UTF8.GetBytes("{\"a\":1}\n{\"b\":"));
        Assert.Equal("{\"a\":1}\n", Encoding.UTF8.GetString(committed));
        Assert.Equal("{\"b\":", Encoding.UTF8.GetString(pending));
    }

    [Fact]
    public void SessionFacade_ApplySnapshot()
    {
        var session = TrajectoryStreamSession.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-empty-prefix",
        });
        var update = session.ApplySnapshot(ReadOnlyMemory<byte>.Empty, "gen-0");
        Assert.Equal("updated", update.Kind);
        Assert.Equal("stream-empty-prefix", session.Cursor.GroupId);
        Assert.True(update.Provisional.Include);
        Assert.Equal(0UL, update.Consumed.CompleteRecords);
    }

    [Fact]
    public void MaxLineBytes_ReturnsStreamBufferLimit()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
            MaxLineBytes = 4,
        });
        var material = Encoding.UTF8.GetBytes("{\"a\":1}\n");
        var (_, update) = TrajectoryStream.ApplySnapshot(state, material, "gen-0");
        Assert.Equal("error", update.Kind);
        Assert.Equal("stream_buffer_limit", update.Error!.Value.Code);
        Assert.Equal("Stream buffer limit exceeded.", update.Error.Value.Message);
    }

    [Fact]
    public void CursorMismatch_Atomic()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
        });
        var (state1, _) = TrajectoryStream.ApplySnapshot(state, ReadOnlyMemory<byte>.Empty, "gen-0");
        var bad = state1.Cursor with
        {
            Position = state1.Cursor.Position with { NextByteOffset = 99 },
        };
        var (state2, update) = TrajectoryStream.ApplySnapshot(state1, ReadOnlyMemory<byte>.Empty, "gen-0", bad);
        Assert.Equal("reset-required", update.Kind);
        Assert.Equal("cursor-mismatch", update.Reset!.Reason);
        Assert.Equal(state1.Cursor.Position.NextByteOffset, state2.Cursor.Position.NextByteOffset);
    }

    [Fact]
    public void NegativeNextByteOffset_IsInvalidInput()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
        });
        var (state1, _) = TrajectoryStream.ApplySnapshot(state, ReadOnlyMemory<byte>.Empty, "gen-0");
        var bad = state1.Cursor with
        {
            Position = state1.Cursor.Position with { NextByteOffset = -1 },
        };
        var (state2, update) = TrajectoryStream.ApplySnapshot(state1, ReadOnlyMemory<byte>.Empty, "gen-0", bad);
        Assert.Equal("error", update.Kind);
        Assert.Equal("invalid_input", update.Error!.Value.Code);
        Assert.Equal(state1.Cursor.Position.NextByteOffset, state2.Cursor.Position.NextByteOffset);
    }

    [Fact]
    public void Reset_WithMaterial_AttachesResetEnvelope()
    {
        var longBytes = ReadFixture("file-truncate-reset", "step-long.jsonl");
        var shortBytes = ReadFixture("file-truncate-reset", "step-truncated.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-truncate-reset",
        });
        var (state1, _) = TrajectoryStream.ApplySnapshot(state, longBytes, "gen-0");
        var (state2, update) = TrajectoryStream.Reset(state1, new StreamResetRequest
        {
            Reason = "source-truncated",
            Generation = 1,
            SourceRevision = "gen-1",
            Material = shortBytes,
        });
        Assert.Equal("updated", update.Kind);
        Assert.Equal(1UL, state2.Generation);
        Assert.Equal(1UL, state2.Cursor.Generation);
        Assert.NotNull(update.Reset);
        Assert.Equal("source-truncated", update.Reset!.Reason);
        Assert.False(update.Reset.RequiresSnapshot);
    }

    [Fact]
    public void NegativeMaxLineBytes_IsInvalidInput()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
            MaxLineBytes = -1,
        });
        var material = Encoding.UTF8.GetBytes("{\"a\":1}\n");
        var (_, update) = TrajectoryStream.ApplySnapshot(state, material, "gen-0");
        Assert.Equal("error", update.Kind);
        Assert.Equal("invalid_input", update.Error!.Value.Code);
    }

    [Fact]
    public void Finish_MarksComplete()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
        });
        var (state1, _) = TrajectoryStream.ApplySnapshot(state, ReadOnlyMemory<byte>.Empty, "gen-0");
        var (state2, update) = TrajectoryStream.Finish(state1);
        Assert.Equal("updated", update.Kind);
        Assert.True(state2.Finished);
        Assert.True(update.Revision.Complete);
    }

    [Fact]
    public void SessionFacade_ResetAndApply()
    {
        var session = TrajectoryStreamSession.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
        });
        session.ApplySnapshot(ReadOnlyMemory<byte>.Empty, "gen-0");
        var update = session.Reset(new StreamResetRequest { Reason = "manual" });
        Assert.Equal("updated", update.Kind);
        Assert.NotNull(update.Reset);
        Assert.Equal("manual", update.Reset!.Reason);
        Assert.Equal(1UL, session.Cursor.Generation);

        var finish = session.Apply(new StreamInput { Kind = "finish" });
        Assert.Equal("updated", finish.Kind);
        Assert.True(session.State.Finished);
    }
}
