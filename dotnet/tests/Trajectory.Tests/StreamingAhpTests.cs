using Hypabolic.Trajectory.Streaming;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

/// <summary>LS-06 / LS-07: AHP snapshot streaming and Shape B action-log reducer.</summary>
public sealed class StreamingAhpTests
{
    private static byte[] ReadFixture(string caseId, string name)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", "streaming", caseId, name);
        Assert.True(File.Exists(path), $"missing fixture {path}");
        return File.ReadAllBytes(path);
    }

    [Fact]
    public void AhpSnapshot_ProvisionalActiveTurn()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000b1";
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        Assert.IsType<SnapshotRevisionPosition>(state.Cursor.Position);
        Assert.Equal("", ((SnapshotRevisionPosition)state.Cursor.Position).Revision);

        var (state1, u1) = TrajectoryStream.ApplyAhpSnapshot(
            state,
            ReadFixture("provisional-to-stable", "step-provisional.json"),
            "ahp-rev-1");
        Assert.Equal("updated", u1.Kind);
        Assert.Equal(new[] { "prov-active:part-md-active-1" }, u1.Provisional.ProvisionalIds);
        Assert.IsType<SnapshotRevisionPosition>(u1.Cursor.Position);
        Assert.Equal("ahp-rev-1", ((SnapshotRevisionPosition)u1.Cursor.Position).Revision);
        Assert.Contains(u1.Snapshot!.Records, r => r.Status == "provisional");

        // Idempotent duplicate revision
        var (_, uDup) = TrajectoryStream.ApplyAhpSnapshot(
            state1,
            ReadFixture("provisional-to-stable", "step-provisional.json"),
            "ahp-rev-1");
        Assert.Equal("unchanged", uDup.Kind);

        var (state2, u2) = TrajectoryStream.ApplyAhpSnapshot(
            state1,
            ReadFixture("provisional-to-stable", "step-stable.json"),
            "ahp-rev-2");
        Assert.Equal("updated", u2.Kind);
        Assert.Empty(u2.Provisional.ProvisionalIds);
        Assert.Contains("prov-active:part-md-active-1", u2.Provisional.FinalizedIds);
        Assert.NotNull(u1.Snapshot);
        Assert.NotNull(u2.Snapshot);
        Assert.NotNull(u2.Delta);
        var recon = TrajectoryStream.ApplyDeltaToSnapshot(
            TrajectoryStream.SnapshotToDict(u1.Snapshot!),
            TrajectoryStream.DeltaToDict(u2.Delta!));
        Assert.Equal(
            System.Text.Json.JsonSerializer.Serialize(
                TrajectoryStream.SnapshotToDict(u2.Snapshot!)["records"]),
            System.Text.Json.JsonSerializer.Serialize(recon["records"]));
        _ = state2;
    }

    [Fact]
    public void AhpAction_TurnFlowAndGap()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        var (state1, u) = TrajectoryStream.ApplyAhpActions(
            state,
            ReadFixture("ahp-action-turn-flow", "step-actions.jsonl"));
        Assert.Equal("updated", u.Kind);
        Assert.IsType<AhpServerSeqPosition>(u.Cursor.Position);
        var seq = (AhpServerSeqPosition)u.Cursor.Position;
        Assert.Equal(5, seq.LastServerSeq);
        Assert.Equal(6, seq.NextServerSeq);
        Assert.NotNull(u.Snapshot);
        var roles = u.Snapshot!.Records.Select(r => r.Record.TryGetValue("role", out var role)
            ? role?.ToString()
            : null).ToList();
        Assert.Contains("user", roles);
        Assert.Contains("assistant", roles);
        Assert.All(
            u.Snapshot.Records.Where(r =>
                r.Record.TryGetValue("role", out var role) && role?.ToString() != "meta"),
            r => Assert.Equal("stable", r.Status));

        // Sequence gap → reset-required, cursor unchanged
        var priorCursor = state1.Cursor;
        var (state2, ug) = TrajectoryStream.ApplyAhpActions(
            state1,
            ReadFixture("ahp-action-sequence-gap", "step-gap.jsonl"));
        Assert.Equal("reset-required", ug.Kind);
        Assert.Equal("sequence-gap", ug.Reset!.Reason);
        Assert.Equal(priorCursor.Position, state2.Cursor.Position);
        Assert.Equal(priorCursor.Position, ug.Cursor.Position);
    }

    [Fact]
    public void AhpAction_UnknownAndForeignChannel()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        var (state1, _) = TrajectoryStream.ApplyAhpActions(
            state,
            ReadFixture("ahp-action-unknown-foreign", "step-baseline.jsonl"));
        var (_, u) = TrajectoryStream.ApplyAhpActions(
            state1,
            ReadFixture("ahp-action-unknown-foreign", "step-mixed.jsonl"));
        Assert.Equal("updated", u.Kind);
        var codes = u.Diagnostics.Select(d => d.Code).ToHashSet(StringComparer.Ordinal);
        Assert.Contains("ahp_unknown_action", codes);
        Assert.Contains("ahp_foreign_channel", codes);
        foreach (var d in u.Diagnostics)
        {
            Assert.DoesNotContain("notARealAction", d.Message);
            Assert.DoesNotContain("SECRET", d.Message);
        }
    }

    [Fact]
    public void AhpAction_EqualsSnapshot()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
        var actions = ReadFixture("ahp-action-equals-snapshot", "step-actions.jsonl");
        var snapshot = ReadFixture("ahp-action-equals-snapshot", "step-snapshot.json");

        var sAct = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        var (_, uAct) = TrajectoryStream.ApplyAhpActions(sAct, actions);
        Assert.Equal("updated", uAct.Kind);
        Assert.NotNull(uAct.Snapshot);

        var sSnap = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        var (_, uSnap) = TrajectoryStream.ApplyAhpSnapshot(sSnap, snapshot, "ahp-equiv-1");
        Assert.Equal("updated", uSnap.Kind);
        Assert.NotNull(uSnap.Snapshot);

        var actIds = uAct.Snapshot!.Records
            .Select(r => (
                r.Record.TryGetValue("id", out var id) ? id?.ToString() : null,
                r.Status))
            .ToList();
        var snapIds = uSnap.Snapshot!.Records
            .Select(r => (
                r.Record.TryGetValue("id", out var id) ? id?.ToString() : null,
                r.Status))
            .ToList();
        Assert.Equal(actIds, snapIds);

        var actContent = uAct.Snapshot.Records
            .Where(r => r.Record.TryGetValue("role", out var role) && role?.ToString() != "meta")
            .Select(r => (
                r.Record.TryGetValue("role", out var role) ? role?.ToString() : null,
                r.Record.TryGetValue("content", out var content) ? content?.ToString() : null))
            .ToList();
        var snapContent = uSnap.Snapshot.Records
            .Where(r => r.Record.TryGetValue("role", out var role) && role?.ToString() != "meta")
            .Select(r => (
                r.Record.TryGetValue("role", out var role) ? role?.ToString() : null,
                r.Record.TryGetValue("content", out var content) ? content?.ToString() : null))
            .ToList();
        Assert.Equal(actContent, snapContent);
    }

    [Fact]
    public void AhpAction_IdempotentReplay()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
        var data = ReadFixture("ahp-action-turn-flow", "step-actions.jsonl");
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        var pre = state.Cursor;
        var (state1, u1) = TrajectoryStream.ApplyAhpActions(state, data);
        Assert.Equal("updated", u1.Kind);
        var (_, u2) = TrajectoryStream.ApplyAhpActions(state1, data, pre);
        Assert.Equal("unchanged", u2.Kind);
    }

    [Fact]
    public void Apply_WiresAhpKinds()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000b1";
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        var (state1, u1) = TrajectoryStream.Apply(state, new StreamInput
        {
            Kind = "ahp-snapshot",
            Data = ReadFixture("provisional-to-stable", "step-provisional.json"),
            SourceRevision = "ahp-rev-1",
        });
        Assert.Equal("updated", u1.Kind);

        var (state2, u2) = TrajectoryStream.Apply(state1, new StreamInput
        {
            Kind = "ahp-actions",
            Data = ReadFixture("ahp-action-turn-flow", "step-actions.jsonl"),
        });
        // May be updated or sequence continues from empty seq baseline on new channel.
        Assert.True(u2.Kind is "updated" or "unchanged" or "reset-required");
        _ = state2;
    }

    [Fact]
    public void AhpAction_RejectsNonMonotonicBatch()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c2";
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
        });
        var data = System.Text.Encoding.UTF8.GetBytes(
            "{\"channel\":\"" + chat + "\",\"serverSeq\":2,\"action\":{\"type\":\"chat/activityChanged\",\"activity\":\"a\"}}\n" +
            "{\"channel\":\"" + chat + "\",\"serverSeq\":1,\"action\":{\"type\":\"chat/activityChanged\",\"activity\":\"b\"}}\n");
        var (state2, u) = TrajectoryStream.ApplyAhpActions(state, data);
        Assert.Equal("error", u.Kind);
        Assert.NotNull(u.Error);
        Assert.Equal("invalid_input", u.Error!.Value.Code);
        Assert.Null(state2.AhpLastServerSeq);
    }

    [Fact]
    public void AhpSnapshot_MultipartProvisionalIdsStable()
    {
        const string chat = "ahp-chat:/00000000-0000-4000-8000-0000000000b2";
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = chat,
            AhpProtocolVersion = "0.7.0",
        });
        var (s1, u1) = TrajectoryStream.ApplyAhpSnapshot(
            state,
            ReadFixture("ahp-snapshot-active-turn-multipart", "step-1.json"),
            "ahp-mp-1");
        Assert.Contains("prov-active:part-md-multi-1", u1.Provisional.ProvisionalIds);
        var (s2, u2) = TrajectoryStream.ApplyAhpSnapshot(
            s1,
            ReadFixture("ahp-snapshot-active-turn-multipart", "step-2.json"),
            "ahp-mp-2");
        Assert.Contains("prov-active:part-md-multi-1", u2.Provisional.ProvisionalIds);
        Assert.Contains("prov-active:tool-call-multi-1", u2.Provisional.ProvisionalIds);
        var (_, u3) = TrajectoryStream.ApplyAhpSnapshot(
            s2,
            ReadFixture("ahp-snapshot-active-turn-multipart", "step-3.json"),
            "ahp-mp-3");
        Assert.Contains("prov-active:part-md-multi-1", u3.Provisional.FinalizedIds);
        Assert.Contains("prov-active:tool-call-multi-1", u3.Provisional.FinalizedIds);
    }
}
