using System.Text;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory.Hermes;
using Hypabolic.Trajectory.Streaming;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class StreamingHermesTests
{
    private static byte[] FixtureBytes()
    {
        var candidates = new[]
        {
            Path.Combine(AppContext.BaseDirectory, "Fixtures", "cases", "hermes", "tool-calls", "input.json"),
            Path.Combine(AppContext.BaseDirectory, "Fixtures", "hermes", "tool-calls", "input.json"),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "conformance", "cases", "hermes", "tool-calls", "input.json")),
        };
        foreach (var path in candidates)
        {
            if (File.Exists(path))
            {
                return File.ReadAllBytes(path);
            }
        }

        throw new FileNotFoundException(
            "Hermes tool-calls fixture not found under Fixtures or conformance/cases.");
    }

    [Fact]
    public void HermesExport_SnapshotAndIdempotent()
    {
        var material = FixtureBytes();
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Hermes,
            GroupId = "hermes-session-0001",
        });
        Assert.IsType<HermesRowPosition>(state.Cursor.Position);

        var (state1, u1) = TrajectoryStream.ApplyHermesExport(
            state, material, "tok-1", "db-1", "db-1");
        Assert.Equal("updated", u1.Kind);
        Assert.NotNull(u1.Snapshot);
        Assert.NotNull(u1.Delta);
        Assert.True(u1.Snapshot!.Records.Count >= 2);
        var pos = Assert.IsType<HermesRowPosition>(u1.Cursor.Position);
        Assert.Equal("db-1", pos.DatabaseGeneration);
        Assert.Equal(104, pos.LastRowId);

        var (_, uDup) = TrajectoryStream.ApplyHermesExport(
            state1, material, "tok-1", "db-1", "db-1");
        Assert.Equal("unchanged", uDup.Kind);
    }

    [Fact]
    public void HermesExport_SoftDeleteRequiresReset()
    {
        var baseJson = JsonNode.Parse(FixtureBytes())!.AsObject();
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Hermes,
            GroupId = "hermes-session-0001",
        });
        var material = Encoding.UTF8.GetBytes(baseJson.ToJsonString());
        var (state1, u1) = TrajectoryStream.ApplyHermesExport(state, material, "t1", "db-1");
        Assert.Equal("updated", u1.Kind);
        var prior = state1.Cursor;

        var mutated = JsonNode.Parse(baseJson.ToJsonString())!.AsObject();
        mutated["messages"]![0]!["active"] = 0;
        var mutBytes = Encoding.UTF8.GetBytes(mutated.ToJsonString());
        var (state2, u2) = TrajectoryStream.ApplyHermesExport(state1, mutBytes, "t2", "db-1");
        Assert.Equal("reset-required", u2.Kind);
        Assert.Equal("source-replaced", u2.Reset!.Reason);
        Assert.Equal(
            ((HermesRowPosition)prior.Position).ChangeToken,
            ((HermesRowPosition)state2.Cursor.Position).ChangeToken);

        var (state3, u3) = TrajectoryStream.Reset(state1, new StreamResetRequest
        {
            Reason = "source-replaced",
            SourceRevision = "db-1",
            Material = mutBytes,
            ChangeToken = "t2",
        });
        Assert.Equal("updated", u3.Kind);
        Assert.NotNull(u3.Reset);
        Assert.True(state3.Generation >= 1);
    }

    [Fact]
    public void HermesExport_NonNumericIds()
    {
        var export = new JsonObject
        {
            ["session"] = new JsonObject
            {
                ["id"] = "s-nonnum",
                ["source"] = "tui",
                ["started_at"] = 1.0,
            },
            ["messages"] = new JsonArray
            {
                new JsonObject
                {
                    ["id"] = "msg-a",
                    ["session_id"] = "s-nonnum",
                    ["role"] = "user",
                    ["content"] = "hello",
                    ["timestamp"] = 1.0,
                    ["active"] = 1,
                },
                new JsonObject
                {
                    ["id"] = "msg-b",
                    ["session_id"] = "s-nonnum",
                    ["role"] = "assistant",
                    ["content"] = "world",
                    ["timestamp"] = 2.0,
                    ["active"] = 1,
                    ["finish_reason"] = "stop",
                },
            },
        };
        var material = Encoding.UTF8.GetBytes(export.ToJsonString());
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Hermes,
            GroupId = "s-nonnum",
        });
        var (_, u) = TrajectoryStream.ApplyHermesExport(state, material, "nn-1", "db-nn");
        Assert.Equal("updated", u.Kind);
        var pos = Assert.IsType<HermesRowPosition>(u.Cursor.Position);
        Assert.Null(pos.LastRowId);
        Assert.Contains(u.Snapshot!.Records, r =>
            r.Record.TryGetValue("role", out var role) && role?.ToString() == "user");
    }

    [Fact]
    public void HermesExport_WrongSource()
    {
        var state = TrajectoryStream.Create(new StreamOptions
        {
            Source = TrajectorySource.Pi,
            GroupId = "g",
        });
        var (_, u) = TrajectoryStream.ApplyHermesExport(
            state, Encoding.UTF8.GetBytes("[]"), "x", "g");
        Assert.Equal("error", u.Kind);
        Assert.Equal("invalid_input", u.Error!.Value.Code);
        Assert.Contains("hermes", u.Error.Value.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void MemoryProvider_SnapshotInsertSoftDelete()
    {
        var store = new MemoryHermesStore { DatabaseGeneration = "mem-1" };
        store.UpsertSession(new JsonObject
        {
            ["id"] = "sess-mem",
            ["source"] = "tui",
            ["model"] = "gpt-test",
            ["started_at"] = 100.0,
            ["title"] = "mem",
        });
        store.AppendMessage("sess-mem", new JsonObject
        {
            ["id"] = 1,
            ["role"] = "user",
            ["content"] = "hi",
            ["timestamp"] = 101.0,
            ["active"] = 1,
        });

        var stream = HermesProviderStream.Open(new HermesProviderOptions
        {
            SessionId = "sess-mem",
            Store = store,
            GroupId = "sess-mem",
        });
        Assert.Single(stream.ListSessions());
        var u0 = stream.Poll();
        Assert.Equal("updated", u0!.Kind);
        var n0 = u0.Snapshot!.Records.Count;

        store.AppendMessage("sess-mem", new JsonObject
        {
            ["id"] = 2,
            ["role"] = "assistant",
            ["content"] = "hello",
            ["timestamp"] = 102.0,
            ["active"] = 1,
            ["finish_reason"] = "stop",
        });
        var u1 = stream.Poll();
        Assert.Equal("updated", u1!.Kind);
        Assert.True(u1.Snapshot!.Records.Count > n0);

        store.SoftDeleteMessage("sess-mem", 1);
        var u2 = stream.Poll();
        Assert.Equal("reset-required", u2!.Kind);
    }

    [Fact]
    public void SqliteProvider_RoundTrip()
    {
        var dir = Path.Combine(Path.GetTempPath(), "traj-hermes-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        try
        {
            var db = Path.Combine(dir, "state.db");
            var provider = new SqliteHermesProvider(db, "sql-1");
            provider.InitializeSchema();
            provider.InsertSession(new JsonObject
            {
                ["id"] = "sess-sql",
                ["source"] = "tui",
                ["model"] = "gpt-test",
                ["title"] = "sql",
                ["started_at"] = 200.0,
            });
            provider.InsertMessage("sess-sql", new JsonObject
            {
                ["id"] = 10,
                ["role"] = "user",
                ["content"] = "from sqlite",
                ["timestamp"] = 201.0,
                ["active"] = 1,
            });
            provider.InsertMessage("sess-sql", new JsonObject
            {
                ["id"] = 11,
                ["role"] = "assistant",
                ["content"] = "ok",
                ["timestamp"] = 202.0,
                ["active"] = 1,
                ["finish_reason"] = "stop",
            });

            Assert.Single(provider.ListSessions());
            var stream = HermesProviderStream.Open(new HermesProviderOptions
            {
                SessionId = "sess-sql",
                Store = provider,
                GroupId = "sess-sql",
            });
            var u = stream.Poll();
            Assert.Equal("updated", u!.Kind);
            Assert.Contains(u.Snapshot!.Records, r =>
                r.Record.TryGetValue("role", out var role) && role?.ToString() == "user");
            var pos = Assert.IsType<HermesRowPosition>(u.Cursor.Position);
            Assert.Equal(11, pos.LastRowId);

            provider.SoftDeleteMessage("sess-sql", 10);
            var u2 = stream.Poll();
            Assert.Equal("reset-required", u2!.Kind);
        }
        finally
        {
            try
            {
                Directory.Delete(dir, recursive: true);
            }
            catch
            {
                // best-effort cleanup
            }
        }
    }
}
