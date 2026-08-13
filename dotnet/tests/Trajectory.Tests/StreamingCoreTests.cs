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

    private static BytePosition BytePos(StreamCursor cursor) =>
        Assert.IsType<BytePosition>(cursor.Position);

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
        var priorOffset = BytePos(state1.Cursor).NextByteOffset;
        var (state2, u2) = TrajectoryStream.ApplySnapshot(state1, m2, "gen-0");
        Assert.Equal("reset-required", u2.Kind);
        Assert.Equal("group-changed", u2.Reset!.Reason);
        Assert.Equal(priorOffset, BytePos(state2.Cursor).NextByteOffset);
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
            Position = BytePos(state1.Cursor) with { NextByteOffset = 99 },
        };
        var (state2, update) = TrajectoryStream.ApplySnapshot(state1, ReadOnlyMemory<byte>.Empty, "gen-0", bad);
        Assert.Equal("reset-required", update.Kind);
        Assert.Equal("cursor-mismatch", update.Reset!.Reason);
        Assert.Equal(BytePos(state1.Cursor).NextByteOffset, BytePos(state2.Cursor).NextByteOffset);
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
            Position = BytePos(state1.Cursor) with { NextByteOffset = -1 },
        };
        var (state2, update) = TrajectoryStream.ApplySnapshot(state1, ReadOnlyMemory<byte>.Empty, "gen-0", bad);
        Assert.Equal("error", update.Kind);
        Assert.Equal("invalid_input", update.Error!.Value.Code);
        Assert.Equal(BytePos(state1.Cursor).NextByteOffset, BytePos(state2.Cursor).NextByteOffset);
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

    [Fact]
    public void ApplyAppend_PendingOnly_AdvancesCursorOnStateAndUpdate()
    {
        var incomplete = ReadFixture("unterminated-line-held", "step-incomplete.txt");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-unterminated-line-held",
        });
        var (state1, update) = TrajectoryStream.ApplyAppend(state, incomplete, sourceRevision: "gen-0");
        Assert.Equal("unchanged", update.Kind);
        Assert.Equal(incomplete.LongLength, BytePos(state1.Cursor).PendingByteLength);
        Assert.Equal(incomplete.LongLength, BytePos(update.Cursor).PendingByteLength);
        Assert.Equal(incomplete.Length, state1.PendingBytes.Length);

        var partial = ReadFixture("utf8-byte-boundary", "step-partial-utf8.bin");
        var tail = ReadFixture("utf8-byte-boundary", "step-utf8-tail.bin");
        state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-utf8-byte-boundary",
        });
        var (state2, u2) = TrajectoryStream.ApplyAppend(state, partial, sourceRevision: "gen-0");
        Assert.Equal("unchanged", u2.Kind);
        Assert.Equal(partial.LongLength, BytePos(u2.Cursor).PendingByteLength);
        var (state3, u3) = TrajectoryStream.ApplyAppend(state2, tail, sourceRevision: "gen-0");
        Assert.Equal("updated", u3.Kind);
        Assert.Equal(0, BytePos(u3.Cursor).PendingByteLength);
        Assert.Equal(0, BytePos(state3.Cursor).PendingByteLength);
    }

    [Fact]
    public void ApplyAppend_EnforcesBufferLimits()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
            MaxPendingBytes = 5,
        });
        var (_, update) = TrajectoryStream.ApplyAppend(
            state,
            Encoding.UTF8.GetBytes("{\"a\":1"),
            sourceRevision: "gen-0");
        Assert.Equal("error", update.Kind);
        Assert.Equal("stream_buffer_limit", update.Error!.Value.Code);
    }

    [Fact]
    public void AppendEqualsPrefixOracle()
    {
        var c1 = ReadFixture("append-equals-prefix-oracle", "step-chunk-1.jsonl");
        var c2 = ReadFixture("append-equals-prefix-oracle", "step-chunk-2.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-append-equals-prefix-oracle",
        });
        var (state1, a1) = TrajectoryStream.ApplyAppend(state, c1, sourceRevision: "gen-0");
        Assert.Equal("updated", a1.Kind);
        var (state2, a2) = TrajectoryStream.ApplyAppend(state1, c2, sourceRevision: "gen-0");
        Assert.Equal("updated", a2.Kind);
        Assert.NotNull(a2.Snapshot);
        var appendIds = a2.Snapshot!.Records
            .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
            .ToArray();

        var full = new byte[c1.Length + c2.Length];
        Buffer.BlockCopy(c1, 0, full, 0, c1.Length);
        Buffer.BlockCopy(c2, 0, full, c1.Length, c2.Length);
        var oracleState = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-append-equals-prefix-oracle",
        });
        var (_, snap) = TrajectoryStream.ApplySnapshot(oracleState, full, "gen-0");
        Assert.Equal("updated", snap.Kind);
        var snapIds = snap.Snapshot!.Records
            .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
            .ToArray();
        Assert.Equal(snapIds, appendIds);
        Assert.Equal(BytePos(snap.Cursor).NextByteOffset, BytePos(state2.Cursor).NextByteOffset);
        Assert.Equal(snap.Cursor.PrefixSha256, state2.Cursor.PrefixSha256);
    }

    [Fact]
    public void FileCompaction_ReturnsSourceCompacted()
    {
        var original = ReadFixture("file-compaction-reset", "step-original.jsonl");
        var compacted = ReadFixture("file-compaction-reset", "step-compacted.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.GrokBuild,
            GroupId = "stream-file-compaction-reset",
        });
        var (state1, u1) = TrajectoryStream.ApplySnapshot(state, original, "gen-0");
        Assert.Equal("updated", u1.Kind);
        var prior = BytePos(state1.Cursor).NextByteOffset;
        var (state2, u2) = TrajectoryStream.ApplySnapshot(state1, compacted, "gen-compact");
        Assert.Equal("reset-required", u2.Kind);
        Assert.Equal("source-compacted", u2.Reset!.Reason);
        Assert.Equal(prior, BytePos(state2.Cursor).NextByteOffset);
    }

    [Fact]
    public void FileSourceReplaced_ReturnsSourceReplaced()
    {
        var original = ReadFixture("file-source-replaced-reset", "step-original.jsonl");
        var replaced = ReadFixture("file-source-replaced-reset", "step-replaced.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-source-replaced-reset",
        });
        var (state1, u1) = TrajectoryStream.ApplySnapshot(state, original, "gen-0");
        Assert.Equal("updated", u1.Kind);
        var prior = BytePos(state1.Cursor).NextByteOffset;
        var (state2, u2) = TrajectoryStream.ApplySnapshot(state1, replaced, "gen-replaced");
        Assert.Equal("reset-required", u2.Kind);
        Assert.Equal("source-replaced", u2.Reset!.Reason);
        Assert.Equal(prior, BytePos(state2.Cursor).NextByteOffset);
    }

    [Fact]
    public void DuplicateAppendInput_IsIdempotent()
    {
        var line = ReadFixture("duplicate-input-idempotent", "step-line.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-duplicate-input-idempotent",
        });
        var preCursor = state.Cursor;
        var (state1, u1) = TrajectoryStream.ApplyAppend(state, line, sourceRevision: "gen-0");
        Assert.Equal("updated", u1.Kind);
        var prior = BytePos(state1.Cursor).NextByteOffset;
        // True replay requires the pre-apply cursor; content alone is not enough.
        var (state2, u2) = TrajectoryStream.ApplyAppend(state1, line, preCursor, sourceRevision: "gen-0");
        Assert.Equal("unchanged", u2.Kind);
        Assert.Equal(prior, BytePos(state2.Cursor).NextByteOffset);
    }

    [Fact]
    public void IdenticalSuccessiveAppends_BothCommit()
    {
        var line = ReadFixture("identical-successive-appends", "step-line.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-identical-successive-appends",
        });
        var (state1, u1) = TrajectoryStream.ApplyAppend(state, line, sourceRevision: "gen-0");
        Assert.Equal("updated", u1.Kind);
        var (state2, u2) = TrajectoryStream.ApplyAppend(state1, line, sourceRevision: "gen-0");
        Assert.Equal("updated", u2.Kind);
        Assert.Equal(line.Length * 2, state2.CommittedPrefix.Length);
        Assert.Equal(line.LongLength * 2, BytePos(state2.Cursor).NextByteOffset);
    }

    [Theory]
    [InlineData(TrajectorySource.Pi, "pi-append-sequence", "stream-pi-append-sequence", 3)]
    [InlineData(TrajectorySource.ClaudeCode, "claude-code-append-sequence", "stream-claude-code-append-sequence", 2)]
    [InlineData(TrajectorySource.Codex, "codex-append-sequence", "stream-codex-append", 3)]
    [InlineData(TrajectorySource.OpenClaw, "openclaw-append-sequence", "stream-openclaw-append", 3)]
    [InlineData(TrajectorySource.GrokBuild, "grok-build-append-sequence", "stream-grok-build-append-sequence", 3)]
    public void PerSource_AppendOracleParity(
        TrajectorySource source,
        string caseId,
        string groupId,
        int steps)
    {
        var chunks = Enumerable.Range(1, steps)
            .Select(i => ReadFixture(caseId, $"step-{i}.jsonl"))
            .ToArray();
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = source,
            GroupId = groupId,
        });
        foreach (var chunk in chunks)
        {
            var (next, update) = TrajectoryStream.ApplyAppend(state, chunk, sourceRevision: "gen-0");
            Assert.Equal("updated", update.Kind);
            state = next;
        }

        Assert.NotNull(state.Snapshot);
        var appendIds = state.Snapshot!.Records
            .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
            .ToArray();
        var fullLen = chunks.Sum(c => c.Length);
        var full = new byte[fullLen];
        var offset = 0;
        foreach (var c in chunks)
        {
            Buffer.BlockCopy(c, 0, full, offset, c.Length);
            offset += c.Length;
        }

        var oracleState = TrajectoryStream.Create(new StreamOptions
        {
            Source = source,
            GroupId = groupId,
        });
        var (_, snap) = TrajectoryStream.ApplySnapshot(oracleState, full, "gen-0");
        var snapIds = snap.Snapshot!.Records
            .Select(r => r.Record.TryGetValue("id", out var id) ? id?.ToString() ?? "" : "")
            .ToArray();
        Assert.Equal(snapIds, appendIds);
    }

    [Fact]
    public void GrokBackendTool_ProvisionalThenStable()
    {
        var step1 = ReadFixture("grok-build-backend-provisional", "step-1.jsonl");
        var step2 = ReadFixture("grok-build-backend-provisional", "step-2.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.GrokBuild,
            GroupId = "stream-grok-build-backend-provisional",
        });
        var (state1, u1) = TrajectoryStream.ApplyAppend(state, step1, sourceRevision: "gen-0");
        Assert.Equal("updated", u1.Kind);
        var provisional = u1.Snapshot!.Records.Where(r => r.Status == "provisional").ToList();
        Assert.Single(provisional);
        Assert.StartsWith("[backend ", provisional[0].Record["content"]?.ToString() ?? "");
        var (state2, u2) = TrajectoryStream.ApplyAppend(state1, step2, sourceRevision: "gen-0");
        Assert.Equal("updated", u2.Kind);
        Assert.All(u2.Snapshot!.Records, r => Assert.Equal("stable", r.Status));
        var tool = u2.Snapshot.Records
            .Where(r => r.Record.TryGetValue("role", out var role) && role?.ToString() == "tool")
            .ToList();
        Assert.Single(tool);
        Assert.Equal("real later result", tool[0].Record["content"]?.ToString());
    }

    [Fact]
    public void StreamDiagnostics_ContentSafeSentinels()
    {
        const string secretTool = "SECRET_TOOL_ID_xyzzy_do_not_leak";
        const string secretPath = "/Users/SECRET_PATH_xyzzy/private.jsonl";
        const string secretAhp = "SECRET_AHP_BODY_xyzzy_do_not_leak";

        var session = System.Text.Encoding.UTF8.GetBytes(
            """{"type":"session","version":3,"id":"g","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}""" + "\n");
        var user = System.Text.Encoding.UTF8.GetBytes(
            """{"type":"message","id":"m1","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hi"}],"timestamp":"2026-01-01T00:00:01.000Z"}}""" + "\n");

        byte[] ToolCall(string mid, string tid) =>
            System.Text.Encoding.UTF8.GetBytes(
                "{\"type\":\"message\",\"id\":\"" + mid +
                "\",\"timestamp\":\"2026-01-01T00:00:02.000Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"toolCall\",\"id\":\"" +
                tid +
                "\",\"name\":\"read\",\"arguments\":{\"path\":\"/tmp/x\"}}],\"timestamp\":\"2026-01-01T00:00:02.000Z\"}}\n");

        static void AssertDiagSafe(StreamUpdate update, params string[] sentinels)
        {
            foreach (var d in update.Diagnostics)
            {
                foreach (var s in sentinels)
                {
                    Assert.DoesNotContain(s, d.Message);
                }
            }

            if (update.Snapshot is not null)
            {
                foreach (var d in update.Snapshot.Diagnostics)
                {
                    foreach (var s in sentinels)
                    {
                        Assert.DoesNotContain(s, d.Message);
                    }
                }
            }

            if (update.Delta is not null)
            {
                foreach (var op in update.Delta.Operations)
                {
                    if (!op.Payload.TryGetValue("diagnostic", out var diagObj) || diagObj is null)
                    {
                        continue;
                    }

                    var msg = diagObj.ToString() ?? "";
                    foreach (var s in sentinels)
                    {
                        Assert.DoesNotContain(s, msg);
                    }
                }
            }

            if (update.Error is { } err)
            {
                foreach (var s in sentinels)
                {
                    Assert.DoesNotContain(s, err.Message);
                }
            }
        }

        var material = session.Concat(user).Concat(ToolCall("a1", secretTool)).Concat(ToolCall("a2", secretTool)).ToArray();
        var (_, u1) = TrajectoryStream.ApplySnapshot(
            TrajectoryStream.Create(new StreamOptions { Source = TrajectorySource.Pi, GroupId = "g" }),
            material,
            "gen-0");
        Assert.Equal("updated", u1.Kind);
        Assert.Contains(u1.Diagnostics, d => d.Code == "duplicate_tool_call_id");
        AssertDiagSafe(u1, secretTool);

        var badLine = System.Text.Encoding.UTF8.GetBytes(
            "{not-json contains " + secretPath + " and " + secretTool + "}\n");
        var (_, u2) = TrajectoryStream.ApplySnapshot(
            TrajectoryStream.Create(new StreamOptions { Source = TrajectorySource.Pi, GroupId = "g" }),
            session.Concat(user).Concat(badLine).ToArray(),
            "gen-0");
        Assert.Equal("updated", u2.Kind);
        Assert.Contains(u2.Diagnostics, d => d.Code == "invalid_json_line");
        var wire = System.Text.Json.JsonSerializer.Serialize(u2.Snapshot!.Diagnostics);
        Assert.DoesNotContain(secretPath, wire);
        Assert.DoesNotContain(secretTool, wire);
        AssertDiagSafe(u2, secretTool, secretPath);

        var (_, u3) = TrajectoryStream.ApplyAhpSnapshot(
            TrajectoryStream.Create(new StreamOptions { Source = TrajectorySource.Ahp, GroupId = "g" }),
            System.Text.Encoding.UTF8.GetBytes("{\"not-valid\":\"" + secretAhp + "\"}"),
            "gen-0");
        Assert.Equal("error", u3.Kind);
        Assert.NotNull(u3.Error);
        Assert.DoesNotContain(secretAhp, u3.Error!.Value.Message);
    }

    [Fact]
    public void DefaultResetPolicy_ReturnsResetRequiredOnTruncate()
    {
        var longBytes = ReadFixture("file-truncate-reset", "step-long.jsonl");
        var shortBytes = ReadFixture("file-truncate-reset", "step-truncated.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-truncate-reset",
        });
        var (state1, _) = TrajectoryStream.ApplySnapshot(state, longBytes, "gen-0");
        var priorGen = state1.Cursor.Generation;
        var (state2, u2) = TrajectoryStream.ApplySnapshot(state1, shortBytes, "gen-1");
        Assert.Equal("reset-required", u2.Kind);
        Assert.Equal("source-truncated", u2.Reset!.Reason);
        Assert.Equal(priorGen, state2.Cursor.Generation);
    }

    [Fact]
    public void AutoReset_WithReplacementMaterial_InstallsGeneration()
    {
        var longBytes = ReadFixture("file-truncate-reset", "step-long.jsonl");
        var shortBytes = ReadFixture("file-truncate-reset", "step-truncated.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-truncate-reset",
            ResetPolicy = StreamResetPolicy.AutoReset,
        });
        var (state1, _) = TrajectoryStream.ApplySnapshot(state, longBytes, "gen-0");
        var (state2, u2) = TrajectoryStream.ApplySnapshot(state1, shortBytes, "gen-1");
        Assert.Equal("updated", u2.Kind);
        Assert.Equal(1ul, state2.Generation);
        Assert.Equal(1ul, state2.Cursor.Generation);
        Assert.Equal("source-truncated", u2.Reset!.Reason);
        Assert.False(u2.Reset.RequiresSnapshot);
        Assert.Equal("gen-1", state2.Cursor.SourceRevision);
        Assert.Equal(shortBytes.LongLength, BytePos(state2.Cursor).NextByteOffset);
    }

    [Fact]
    public void AutoReset_WithoutMaterial_StillResetRequired()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
            ResetPolicy = StreamResetPolicy.AutoReset,
        });
        var (state1, u1) = TrajectoryStream.ApplyAhpActions(
            state,
            ReadFixture("ahp-action-turn-flow", "step-actions.jsonl"));
        Assert.Equal("updated", u1.Kind);
        var priorGen = state1.Cursor.Generation;
        var (state2, ug) = TrajectoryStream.ApplyAhpActions(
            state1,
            ReadFixture("ahp-action-sequence-gap", "step-gap.jsonl"));
        Assert.Equal("reset-required", ug.Kind);
        Assert.Equal("sequence-gap", ug.Reset!.Reason);
        Assert.Equal(priorGen, state2.Cursor.Generation);
    }

    [Fact]
    public void UnknownDeltaOp_IsInvalidInput_AndLeavesPriorUnchanged()
    {
        var prior = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_id"] = "trajectory-stream-v1",
            ["source"] = "pi",
            ["group_id"] = "g",
            ["revision"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["revision"] = 1ul,
                ["revision_id"] = "rev-1",
                ["parent_revision_id"] = null,
                ["complete"] = false,
                ["generation"] = 0ul,
            },
            ["records"] = new List<object?>(),
            ["diagnostics"] = new List<object?>(),
            ["complete"] = false,
        };
        var delta = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_id"] = "trajectory-stream-v1",
            ["base_revision_id"] = "rev-1",
            ["revision"] = prior["revision"],
            ["operations"] = new List<object?>
            {
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    ["op"] = "merge",
                    ["record_id"] = "x",
                },
            },
        };
        var ex = Assert.Throws<TrajectoryNormalizationException>(
            () => TrajectoryStream.ApplyDeltaToSnapshot(prior, delta));
        Assert.Equal(NormalizationErrorCode.InvalidInput, ex.Code);
        Assert.Empty((List<object?>)prior["records"]!);
    }
}
