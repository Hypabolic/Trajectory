using System.Text;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory.Ahp;
using Hypabolic.Trajectory.Streaming;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

/// <summary>LS-10: optional AHP client fake-host tests.</summary>
public sealed class AhpClientTests
{
    private const string Chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";

    private static List<JsonObject> LoadActions(string caseId, string name)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", "streaming", caseId, name);
        var list = new List<JsonObject>();
        foreach (var line in File.ReadAllLines(path))
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            list.Add(JsonNode.Parse(line)!.AsObject());
        }
        return list;
    }

    private static JsonObject EmptySnapshot() => new()
    {
        ["ahpProtocolVersion"] = "0.7.0",
        ["chat"] = new JsonObject
        {
            ["id"] = Chat,
            ["turns"] = new JsonArray(),
            ["activeTurn"] = null,
        },
    };

    [Fact]
    public void Subscribe_actions_feed_core()
    {
        var pair = new InMemoryAhpTransportPair();
        var actions = LoadActions("ahp-action-turn-flow", "step-actions.jsonl");
        var host = new FakeAhpHost(pair.Host, new FakeAhpHostScript { InitialActions = actions }, Chat);
        var events = new List<AhpClientEvent>();
        var client = new AhpStreamClient(pair.Client, new AhpClientOptions { ChatChannel = Chat }, events.Add);
        client.Start();
        Assert.Contains(events, e => e.Kind == AhpClientEventKind.Ready);
        var updates = events.Where(e => e.Kind == AhpClientEventKind.StreamUpdate).ToList();
        Assert.NotEmpty(updates);
        Assert.Equal("updated", updates[^1].Update!.Kind);
        Assert.IsType<AhpServerSeqPosition>(client.Cursor.Position);
        Assert.Equal(5, ((AhpServerSeqPosition)client.Cursor.Position).LastServerSeq);
        host.Close();
        client.Cancel();
    }

    [Fact]
    public void Auth_failure()
    {
        var pair = new InMemoryAhpTransportPair();
        var host = new FakeAhpHost(
            pair.Host,
            new FakeAhpHostScript { RequireAuth = true, AcceptToken = "good" },
            Chat);
        var events = new List<AhpClientEvent>();
        var client = new AhpStreamClient(
            pair.Client,
            new AhpClientOptions
            {
                ChatChannel = Chat,
                Auth = _ => new AhpAuthCredentials("bad"),
            },
            events.Add);
        client.Start();
        Assert.Contains(events, e => e.Kind == AhpClientEventKind.AuthRequired);
        Assert.Contains(events, e => e.Kind == AhpClientEventKind.AuthFailed);
        Assert.Equal(1, host.AuthAttempts);
        Assert.DoesNotContain(events, e => e.Kind == AhpClientEventKind.Ready);
        client.Cancel();
    }

    [Fact]
    public void Auth_success_then_subscribe_no_token_in_update()
    {
        var pair = new InMemoryAhpTransportPair();
        var host = new FakeAhpHost(
            pair.Host,
            new FakeAhpHostScript
            {
                RequireAuth = true,
                AcceptToken = "secret-token-xyz",
                InitialSnapshot = EmptySnapshot(),
            },
            Chat);
        var events = new List<AhpClientEvent>();
        var client = new AhpStreamClient(
            pair.Client,
            new AhpClientOptions
            {
                ChatChannel = Chat,
                Auth = _ => new AhpAuthCredentials("secret-token-xyz"),
            },
            events.Add);
        client.Start();
        Assert.Contains(events, e => e.Kind == AhpClientEventKind.Ready);
        foreach (var e in events.Where(x => x.Kind == AhpClientEventKind.StreamUpdate && x.Update is not null))
        {
            var json = System.Text.Json.JsonSerializer.Serialize(e.Update);
            Assert.DoesNotContain("secret-token-xyz", json);
        }
        client.Cancel();
        _ = host;
    }

    [Fact]
    public void Sequence_gap_triggers_resync()
    {
        var pair = new InMemoryAhpTransportPair();
        var actions = LoadActions("ahp-action-turn-flow", "step-actions.jsonl");
        var host = new FakeAhpHost(
            pair.Host,
            new FakeAhpHostScript
            {
                InitialActions = actions,
                InitialSnapshot = EmptySnapshot(),
            },
            Chat);
        var events = new List<AhpClientEvent>();
        var client = new AhpStreamClient(
            pair.Client,
            new AhpClientOptions { ChatChannel = Chat, AutoResync = true },
            events.Add);
        client.Start();
        var gap = LoadActions("ahp-action-sequence-gap", "step-gap.jsonl");
        host.PushActions(gap);
        Assert.Contains(events, e => e.Kind == AhpClientEventKind.ResyncRequired);
        Assert.True(host.ResyncCount >= 1);
        client.Cancel();
        Assert.True(client.IsCancelled);
    }

    [Fact]
    public void Duplicate_action_replay_does_not_crash()
    {
        var pair = new InMemoryAhpTransportPair();
        var actions = LoadActions("ahp-action-turn-flow", "step-actions.jsonl");
        var host = new FakeAhpHost(pair.Host, new FakeAhpHostScript { InitialActions = actions }, Chat);
        var events = new List<AhpClientEvent>();
        var client = new AhpStreamClient(pair.Client, new AhpClientOptions { ChatChannel = Chat }, events.Add);
        client.Start();
        host.PushActions(actions);
        var updates = events.Where(e => e.Kind == AhpClientEventKind.StreamUpdate).ToList();
        Assert.All(updates, e =>
            Assert.Contains(e.Update!.Kind, new[] { "updated", "unchanged", "reset-required", "error" }));
        client.Cancel();
    }

    [Fact]
    public void Backpressure()
    {
        var pair = new InMemoryAhpTransportPair();
        var host = new FakeAhpHost(
            pair.Host,
            new FakeAhpHostScript { InitialSnapshot = EmptySnapshot() },
            Chat);
        var events = new List<AhpClientEvent>();
        var client = new AhpStreamClient(
            pair.Client,
            new AhpClientOptions { ChatChannel = Chat, MaxBufferedActions = 2 },
            events.Add);
        client.Start();
        client.SetPausedForTest(true);
        for (var i = 0; i < 5; i++)
        {
            host.PushAction(new JsonObject
            {
                ["channel"] = Chat,
                ["serverSeq"] = 100 + i,
                ["origin"] = new JsonObject { ["kind"] = "server" },
                ["action"] = new JsonObject
                {
                    ["type"] = "chat/activityChanged",
                    ["activity"] = "thinking",
                },
            });
        }
        Assert.Contains(events, e => e.Kind == AhpClientEventKind.Backpressure);
        client.Cancel();
    }

    [Fact]
    public void Cancel_keeps_cursor()
    {
        var pair = new InMemoryAhpTransportPair();
        var actions = LoadActions("ahp-action-turn-flow", "step-actions.jsonl");
        var host = new FakeAhpHost(pair.Host, new FakeAhpHostScript { InitialActions = actions }, Chat);
        var events = new List<AhpClientEvent>();
        var client = new AhpStreamClient(pair.Client, new AhpClientOptions { ChatChannel = Chat }, events.Add);
        client.Start();
        var cur = client.Cursor;
        Assert.IsType<AhpServerSeqPosition>(cur.Position);
        var last = ((AhpServerSeqPosition)cur.Position).LastServerSeq;
        client.Cancel();
        Assert.True(client.IsCancelled);
        Assert.Equal(cur.Generation, client.Cursor.Generation);
        Assert.Equal(last, ((AhpServerSeqPosition)client.Cursor.Position).LastServerSeq);
        host.Close();
    }
}
